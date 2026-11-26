"""Douyin downloader: resolve short links, render the page, pull the real MP4.

Why not yt-dlp: its Douyin extractor relies on a web API that now requires an
`a_bogus` signature, so it returns 403 even with cookies. The page itself still
serves direct `video/tos` URLs once rendered, and some pages only start loading
the stream after the player is clicked.
"""

import asyncio
import html as html_lib
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

from .config import DOWNLOAD_DIR
from .douyin_auth import load_cookies, login_state

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)
_VIDEO_URL_RE = re.compile(r"https://[^\"\s\\]*?video/tos[^\"\s\\]*")
_SHARE_ID_RE = re.compile(r"/share/(video|note)/(\d{15,25})")
_VIDEO_ID_RE = re.compile(r"/(?:video|note)/(\d{15,25})")
_BR_RE = re.compile(r"[?&]br=(\d+)")


class DouyinError(Exception):
    pass


def is_short_link(url: str) -> bool:
    return "v.douyin.com" in url


def video_id(url: str) -> str:
    match = _VIDEO_ID_RE.search(url)
    return match.group(1) if match else ""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _resolve_short_link(short_url: str) -> str:
    """Turn v.douyin.com/xxx into a full video URL, or explain why it can't."""
    opener = urllib.request.build_opener(_NoRedirect)
    request = urllib.request.Request(short_url, headers={"User-Agent": _UA})
    location = ""
    try:
        with opener.open(request, timeout=15) as response:
            location = response.headers.get("Location") or ""
    except urllib.error.HTTPError as error:
        location = error.headers.get("Location") or ""

    if not location:
        raise DouyinError("短链解析失败，请检查链接是否完整")

    match = _SHARE_ID_RE.search(location)
    if match:
        kind, vid = match.groups()
        if kind == "note":
            raise DouyinError("这是图文作品，暂不支持下载")
        return f"https://www.douyin.com/video/{vid}"

    # Valid short links land on iesdouyin share pages; dead ones fall back home.
    if "douyin.com" in location:
        raise DouyinError("链接已失效：视频可能被删除或设为私密，请在抖音 App 里重新分享")
    raise DouyinError("无法解析该短链")


async def _render(url: str) -> tuple[str, str, str, list[str]]:
    """Return (title, html, cover, network video URLs) after rendering the page."""
    from playwright.async_api import async_playwright

    media_urls: list[str] = []
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(channel="msedge", headless=True)
        try:
            context = await browser.new_context(
                viewport={"width": 1280, "height": 800},
                user_agent=_UA,
            )
            saved = load_cookies()
            if saved:
                await context.add_cookies(saved)
            page = await context.new_page()

            def on_response(response: Any) -> None:
                if "video/tos" in response.url:
                    media_urls.append(response.url)

            page.on("response", on_response)
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(3500)

            title = (await page.title()).replace(" - 抖音", "").strip()
            content = await page.content()

            # Some pages don't put the stream in the HTML until playback starts.
            if not _VIDEO_URL_RE.search(content) and not media_urls:
                for selector in ("video", ".xg-video-container", "[data-e2e='video-detail']"):
                    try:
                        element = page.locator(selector).first
                        if await element.count():
                            await element.click(timeout=3000)
                            break
                    except Exception:
                        continue
                await page.wait_for_timeout(5000)

            cover = ""
            match = re.search(
                r'<meta[^>]+property="og:image"[^>]+content="([^"]+)"', content
            )
            if match:
                cover = html_lib.unescape(match.group(1))
            return title, content, cover, media_urls
        finally:
            await browser.close()


def _clean(url: str) -> str:
    return html_lib.unescape(url).replace("\\/", "/").replace("\\u0026", "&")


def _pick_video_url(content: str, extra: list[str] | None = None) -> tuple[str, list[str]]:
    urls: list[str] = []
    for match in _VIDEO_URL_RE.finditer(content):
        url = _clean(match.group(0))
        if url not in urls:
            urls.append(url)
    for raw in extra or []:
        url = _clean(raw)
        if url not in urls:
            urls.append(url)
    if not urls:
        raise DouyinError("没找到视频地址，视频可能不可播放或需要登录")

    def bitrate(url: str) -> int:
        match = _BR_RE.search(url)
        return int(match.group(1)) if match else 0

    urls.sort(key=bitrate, reverse=True)
    return urls[0], urls


def _safe_title(title: str) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|\r\n\t]+', "_", title or "").strip(" ._")
    return cleaned[:90] or "douyin_video"


def _download_sync(
    url: str,
    destination: Path,
    on_progress: Callable[[float, int, int], None] | None,
) -> None:
    request = urllib.request.Request(
        url,
        headers={
            "Referer": "https://www.douyin.com/",
            "User-Agent": _UA,
            "Accept": "*/*",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        total = int(response.headers.get("content-length") or 0)
        done = 0
        with destination.open("wb") as handle:
            while True:
                chunk = response.read(256 * 1024)
                if not chunk:
                    break
                handle.write(chunk)
                done += len(chunk)
                if on_progress and total:
                    on_progress(min(99.0, done / total * 100), done, total)


async def download(
    url: str,
    on_progress: Callable[..., None] | None = None,
) -> dict[str, Any]:
    def report(**kwargs: Any) -> None:
        if on_progress:
            on_progress(**kwargs)

    state = login_state()
    if not state["logged_in"]:
        raise DouyinError(f"{state['message']}，请先扫码登录抖音")

    if is_short_link(url):
        report(stage="解析短链", progress=2)
        url = await asyncio.to_thread(_resolve_short_link, url)

    match = _VIDEO_ID_RE.search(url)
    if "iesdouyin.com" in url and match:
        url = f"https://www.douyin.com/video/{match.group(1)}"

    report(stage="打开视频页", progress=4)
    title, content, cover, media_urls = await _render(url)
    report(stage="提取视频地址", progress=12)

    try:
        best_url, all_urls = _pick_video_url(content, media_urls)
    except DouyinError:
        if login_state()["logged_in"]:
            raise DouyinError("没找到视频地址，登录可能已过期，请重新扫码登录") from None
        raise
    vid = video_id(url)
    filename = f"{_safe_title(title)}_{vid}.mp4" if vid else f"{_safe_title(title)}.mp4"
    destination = DOWNLOAD_DIR / filename

    report(stage="下载中", progress=15, title=title, thumbnail=cover)
    await asyncio.to_thread(
        _download_sync,
        best_url,
        destination,
        lambda percent, done, total: report(
            stage="下载中", progress=15 + percent * 0.85, filesize=total
        ),
    )

    return {
        "title": title or "抖音视频",
        "filepath": str(destination),
        "thumbnail": cover,
        "url": url,
        "extractor": "douyin",
        "filesize": destination.stat().st_size,
        "duration": None,
        "description": "",
        "alternatives": len(all_urls),
    }

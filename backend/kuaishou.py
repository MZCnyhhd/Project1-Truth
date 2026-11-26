"""Kuaishou downloader: read the <video> source from the rendered page.

Headless Chromium gets a degraded shell ("browser too old"), so this uses a
visible Edge window. It also needs a click on 重试 before the player loads.
"""

import asyncio
import re
import urllib.request
from pathlib import Path
from typing import Any, Callable

from .config import DOWNLOAD_DIR
from .douyin_auth import load_cookies

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
)
_ID_RE = re.compile(r"/short-video/([A-Za-z0-9_-]+)")


class KuaishouError(Exception):
    pass


def _canonical(url: str) -> str:
    match = _ID_RE.search(url)
    if match:
        return f"https://www.kuaishou.com/short-video/{match.group(1)}"
    return url


async def _render(url: str) -> tuple[str, str]:
    cookies = load_cookies("kuaishou")
    if not cookies:
        raise KuaishouError("快手未登录，请先扫码登录")

    from playwright.async_api import async_playwright

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(channel="msedge", headless=True)
        try:
            context = await browser.new_context(
                viewport={"width": 1280, "height": 800}, user_agent=_UA
            )
            await context.add_cookies(cookies)
            page = await context.new_page()

            last_error: Exception | None = None
            for _ in range(2):
                try:
                    await page.goto(url, wait_until="domcontentloaded", timeout=60000)
                    break
                except Exception as exc:  # retry once on transient throttling
                    last_error = exc
                    await page.wait_for_timeout(3000)
            else:
                raise KuaishouError("快手访问超时，请稍后重试")

            await page.wait_for_timeout(5000)

            for label in ("点击重试", "重试"):
                try:
                    element = page.get_by_text(label).first
                    if await element.count():
                        await element.click(timeout=2500)
                        break
                except Exception:
                    continue

            source = ""
            for _ in range(12):
                await page.wait_for_timeout(1500)
                try:
                    source = (
                        await page.eval_on_selector("video", "el => el.currentSrc || el.src")
                        or ""
                    )
                except Exception:
                    source = ""
                if source:
                    break

            title = (await page.title()).replace(" -快手", "").replace("-快手", "").strip()
            if not source:
                raise KuaishouError("快手没有返回视频地址，登录可能已过期，请重新扫码登录")
            return title, source
        finally:
            await browser.close()


def _safe_title(title: str) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|\r\n\t]+', "_", title or "").strip(" ._")
    return cleaned[:90] or "kuaishou_video"


def _download_sync(
    url: str,
    destination: Path,
    on_progress: Callable[[float, int, int], None] | None,
) -> None:
    request = urllib.request.Request(
        url,
        headers={
            "Referer": "https://www.kuaishou.com/",
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

    report(stage="打开视频页", progress=4)
    title, source = await _render(_canonical(url))
    if not source or not source.startswith("http"):
        raise KuaishouError("没找到视频地址，快手页面可能触发了风控，请重试")

    match = _ID_RE.search(url)
    video_id = match.group(1) if match else ""
    filename = f"{_safe_title(title)}_{video_id}.mp4" if video_id else f"{_safe_title(title)}.mp4"
    destination = DOWNLOAD_DIR / filename

    report(stage="下载中", progress=15, title=title, thumbnail="")
    await asyncio.to_thread(
        _download_sync,
        source,
        destination,
        lambda percent, done, total: report(
            stage="下载中", progress=15 + percent * 0.85, filesize=total
        ),
    )

    return {
        "title": title or "快手视频",
        "filepath": str(destination),
        "thumbnail": "",
        "url": url,
        "extractor": "kuaishou",
        "filesize": destination.stat().st_size,
        "duration": None,
        "description": "",
    }

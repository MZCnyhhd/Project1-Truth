"""Per-platform login state: QR login, cookie persistence, expiry detection.

Cookies are stored as data/cookies_<platform>.txt so each platform keeps its
own session. Only Douyin has a QR login flow for now.
"""

import asyncio
import time
from pathlib import Path
from typing import Any

from .config import DATA_DIR

_LOGIN_COOKIE_NAMES = {"sessionid_ss", "sessionid", "sid_tt"}
_STATE: dict[str, str] = {"status": "idle", "message": "", "platform": "douyin"}
_TASK: asyncio.Task | None = None


def cookie_file(platform: str = "douyin") -> Path:
    return DATA_DIR / f"cookies_{platform}.txt"


def load_cookies(platform: str = "douyin") -> list[dict[str, Any]]:
    path = cookie_file(platform)
    if not path.is_file():
        return []
    cookies: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) < 7:
            continue
        domain, _, cookie_path, secure, expires, name, value = parts[:7]
        cookie: dict[str, Any] = {
            "name": name,
            "value": value,
            "domain": domain,
            "path": cookie_path or "/",
            "secure": secure.upper() == "TRUE",
            "httpOnly": False,
        }
        try:
            expiry = int(expires)
            if expiry > 0:
                cookie["expires"] = expiry
        except ValueError:
            pass
        cookies.append(cookie)
    return cookies


def save_cookies(cookies: list[dict[str, Any]], platform: str = "douyin") -> int:
    lines = ["# Netscape HTTP Cookie File"]
    saved = 0
    for cookie in cookies:
        domain = cookie.get("domain") or ""
        name = cookie.get("name") or ""
        if not domain or not name:
            continue
        prefix = "" if domain.startswith(".") else "."
        expires = cookie.get("expires")
        expires_text = "0" if expires in (None, -1) else str(int(expires))
        secure = "TRUE" if cookie.get("secure") else "FALSE"
        lines.append(
            f"{prefix}{domain}\tTRUE\t/\t{secure}\t{expires_text}\t{name}\t{cookie.get('value') or ''}"
        )
        saved += 1
    cookie_file(platform).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return saved


def _session_cookies(platform: str) -> list[dict[str, Any]]:
    return [c for c in load_cookies(platform) if c.get("name") in _LOGIN_COOKIE_NAMES]


def login_state(platform: str = "douyin") -> dict[str, Any]:
    sessions = _session_cookies(platform)
    if not sessions:
        return {"logged_in": False, "message": "未登录"}
    now = time.time()
    living = [c for c in sessions if not c.get("expires") or c["expires"] > now]
    if not living:
        return {"logged_in": False, "message": "登录已过期，请重新扫码"}
    return {"logged_in": True, "message": "已登录"}


def get_status(platform: str = "douyin") -> dict[str, Any]:
    state = login_state(platform)
    running = _STATE.get("platform") == platform
    return {
        **state,
        "status": _STATE.get("status", "idle") if running else "idle",
        "login_message": _STATE.get("message", "") if running else "",
    }


def _has_login_cookie(cookies: list[dict[str, Any]]) -> bool:
    return any(c.get("name") in _LOGIN_COOKIE_NAMES for c in cookies)


async def _worker(platform: str, url: str, timeout: int) -> None:
    global _TASK
    browser = None
    try:
        from playwright.async_api import async_playwright

        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(channel="msedge", headless=False)
            context = await browser.new_context(viewport={"width": 1100, "height": 760})
            existing = load_cookies(platform)
            if existing:
                await context.add_cookies(existing)
            page = await context.new_page()
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)

            try:
                button = page.get_by_text("登录", exact=True).first
                if await button.count():
                    await button.click(timeout=3000)
            except Exception:
                pass

            loop = asyncio.get_event_loop()
            deadline = loop.time() + timeout
            while loop.time() < deadline:
                if _STATE.get("status") == "cancelling":
                    break
                cookies = await context.cookies()
                if _has_login_cookie(cookies):
                    count = save_cookies(cookies, platform)
                    _STATE.update(status="success", message=f"登录成功，已保存 {count} 条 cookies")
                    break
                await asyncio.sleep(2)
            else:
                _STATE.update(status="timeout", message="等待扫码超时，请重试")

            if browser is not None:
                await browser.close()
                browser = None
    except Exception as exc:  # noqa: BLE001 - surface the reason in the UI
        _STATE.update(status="error", message=str(exc)[:300])
    finally:
        if browser is not None:
            try:
                await browser.close()
            except Exception:
                pass
        if _STATE.get("status") == "cancelling":
            _STATE.update(status="idle", message="已取消登录")
        _TASK = None


async def start_login(
    platform: str = "douyin",
    url: str = "https://www.douyin.com/",
    timeout: int = 180,
) -> dict[str, Any]:
    global _TASK
    if _TASK is not None and not _TASK.done():
        return get_status(platform)
    _STATE.update(
        status="waiting",
        message="已打开浏览器窗口，请扫码登录",
        platform=platform,
    )
    _TASK = asyncio.create_task(_worker(platform, url, timeout))
    return get_status(platform)


def cancel_login(platform: str = "douyin") -> dict[str, Any]:
    if _TASK is not None and not _TASK.done():
        _STATE.update(status="cancelling", message="正在关闭浏览器…")
    else:
        _STATE.update(status="idle", message="")
    return get_status(platform)

"""WeChat Channels downloader: delegates to the local skill's fetch script.

The video is AES-encrypted behind a signed WeChat API, so plain HTTP can't
reach it. The skill drives a real browser through a parser page instead.
"""

import asyncio
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

from .config import DOWNLOAD_DIR

_SKILL_SCRIPT = (
    Path.home()
    / ".workbuddy"
    / "skills"
    / "wechat-channels-video-download"
    / "scripts"
    / "fetch_by_link.py"
)


class WeChatError(Exception):
    pass


def _run_script(url: str) -> dict[str, Any]:
    if not _SKILL_SCRIPT.is_file():
        raise WeChatError("视频号下载脚本不存在，请确认 skill 已安装")
    completed = subprocess.run(
        [sys.executable, str(_SKILL_SCRIPT), url, "--out", str(DOWNLOAD_DIR)],
        capture_output=True,
        text=True,
        timeout=300,
        encoding="utf-8",
        errors="replace",
    )
    if completed.returncode != 0:
        raise WeChatError((completed.stderr or completed.stdout or "脚本执行失败")[-300:])
    payload = (completed.stdout or "").strip()
    start = payload.find("{")
    if start < 0:
        raise WeChatError("脚本没有返回有效结果")
    return json.loads(payload[start:])


async def download(
    url: str,
    on_progress: Callable[..., None] | None = None,
) -> dict[str, Any]:
    def report(**kwargs: Any) -> None:
        if on_progress:
            on_progress(**kwargs)

    report(stage="解析视频号", progress=5)
    result = await asyncio.to_thread(_run_script, url)
    if not result.get("ok"):
        raise WeChatError(result.get("error") or "视频号解析失败")

    filepath = result.get("file") or ""
    if not filepath or not Path(filepath).is_file():
        raise WeChatError("视频号没有返回可用的视频文件")

    meta = result.get("metadata") or {}
    description = (meta.get("description") or "").strip()
    title = description[:48] or Path(filepath).stem
    cover = (result.get("cover") or {}).get("file") or ""

    report(
        stage="下载完成",
        progress=100,
        title=title,
        thumbnail=cover,
        filesize=Path(filepath).stat().st_size,
    )

    return {
        "title": title,
        "filepath": filepath,
        "thumbnail": cover,
        "url": url,
        "extractor": "wechat_channels",
        "filesize": Path(filepath).stat().st_size,
        "duration": None,
        "description": description,
        "uploader": meta.get("author") or "",
    }

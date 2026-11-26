import asyncio
import shutil
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

import yt_dlp

from . import douyin, kuaishou, wechat_channels
from .config import DOWNLOAD_DIR
from .db import save_item
from .douyin_auth import cookie_file

TASKS: dict[str, dict[str, Any]] = {}
_LOCK = threading.Lock()

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _update(task_id: str, **patch: Any) -> None:
    with _LOCK:
        task = TASKS.get(task_id)
        if task is None:
            return
        task.update(patch)
        task["updated_at"] = _now()


def _base_opts() -> dict[str, Any]:
    # YouTube needs cookies for high-res direct formats here, so fall back to HLS
    # at a smaller size. Concurrency makes fragment fetching tolerable.
    video_format = "bestvideo[height<=480]+bestaudio/best[height<=480]/best"
    options: dict[str, Any] = {
        "format": video_format,
        "merge_output_format": "mp4",
        "outtmpl": str(DOWNLOAD_DIR / "%(extractor)s_%(id)s.%(ext)s"),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "retries": 2,
        "socket_timeout": 20,
        "concurrent_fragment_downloads": 4,
        "http_headers": {"User-Agent": _UA},
    }
    return options


async def _run_ytdlp(task_id: str, url: str) -> None:
    opts = _base_opts()
    if "youtube.com" in url or "youtu.be" in url:
        youtube_cookies = cookie_file("youtube")
        if youtube_cookies.is_file():
            opts["cookiefile"] = str(youtube_cookies)

    def hook(data: dict[str, Any]) -> None:
        status = data.get("status")
        if status == "downloading":
            total = data.get("total_bytes") or data.get("total_bytes_estimate")
            done = data.get("downloaded_bytes") or 0
            percent = data.get("percent")
            if percent is None and total:
                percent = done / total * 100
            _update(
                task_id,
                progress=max(2, min(99, float(percent or 2))),
                stage="下载中",
                speed=data.get("speed") or 0,
                eta=data.get("eta") or 0,
            )
        elif status == "finished":
            _update(task_id, progress=98, stage="整理文件")

    opts["progress_hooks"] = [hook]

    def run() -> dict[str, Any]:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            filepath = ""
            if info.get("requested_downloads"):
                filepath = info["requested_downloads"][0].get("filepath", "")
            if not filepath:
                filepath = ydl.prepare_filename(info)
            return {"info": info, "filepath": filepath}

    result = await asyncio.to_thread(run)
    info = result["info"]
    filepath = Path(result["filepath"])
    title = info.get("title") or filepath.stem

    if not filepath.is_file():
        raise RuntimeError("下载未完成，没有生成视频文件")
    if info.get("vcodec") == "none":
        raise RuntimeError("平台只返回了音频流，需要登录 cookies 才能下载视频")

    _update(
        task_id,
        status="done",
        progress=100,
        stage="完成",
        title=title,
        filepath=str(filepath),
        message="",
    )
    save_item(
        {
            "task_id": task_id,
            "title": title,
            "uploader": info.get("uploader") or info.get("channel") or "",
            "duration": info.get("duration"),
            "url": url,
            "thumbnail": info.get("thumbnail") or "",
            "description": (info.get("description") or "")[:2000],
            "extractor": info.get("extractor") or "",
            "filepath": str(filepath),
            "subtitle_path": "",
            "filesize": filepath.stat().st_size if filepath.is_file() else 0,
        }
    )


async def _run_download(task_id: str, url: str) -> None:
    _update(task_id, status="running", stage="解析中", progress=1)
    try:
        report = lambda **patch: _update(task_id, **patch)  # noqa: E731
        if "douyin.com" in url:
            result = await douyin.download(url, on_progress=report)
        elif "kuaishou.com" in url:
            result = await kuaishou.download(url, on_progress=report)
        elif "weixin.qq.com/sph" in url or "channels.weixin.qq.com" in url:
            result = await wechat_channels.download(url, on_progress=report)
        else:
            await _run_ytdlp(task_id, url)
            return

        _update(
            task_id,
            status="done",
            progress=100,
            stage="完成",
            title=result["title"],
            filepath=result["filepath"],
            thumbnail=result.get("thumbnail") or "",
            message="",
        )
        save_item(
            {
                "task_id": task_id,
                "title": result["title"],
                "uploader": result.get("uploader") or "",
                "duration": result.get("duration"),
                "url": url,
                "thumbnail": result.get("thumbnail") or "",
                "description": result.get("description") or "",
                "extractor": result.get("extractor") or "",
                "filepath": result["filepath"],
                "subtitle_path": "",
                "filesize": result.get("filesize") or 0,
            }
        )
    except (Exception, SystemExit) as exc:  # noqa: BLE001 - SystemExit keeps the server alive
        message = str(exc) or "下载失败"
        if "Sign in to confirm" in message and ("youtube" in url or "youtu.be" in url):
            message = "YouTube 要求登录验证，请把浏览器导出的 cookies 存为 data/cookies_youtube.txt 后重试"
        _update(task_id, status="error", stage="失败", message=message[:500])


def start_download(url: str) -> dict[str, Any]:
    task_id = uuid.uuid4().hex
    TASKS[task_id] = {
        "id": task_id,
        "url": url,
        "status": "queued",
        "stage": "排队中",
        "progress": 0,
        "speed": 0,
        "eta": 0,
        "title": "",
        "filepath": "",
        "thumbnail": "",
        "message": "",
        "created_at": _now(),
        "updated_at": _now(),
    }
    asyncio.create_task(_run_download(task_id, url))
    return dict(TASKS[task_id])


def get_task(task_id: str) -> dict[str, Any] | None:
    with _LOCK:
        task = TASKS.get(task_id)
        return dict(task) if task else None


def list_tasks(limit: int = 50) -> list[dict[str, Any]]:
    with _LOCK:
        tasks = sorted(
            TASKS.values(), key=lambda task: task.get("created_at", ""), reverse=True
        )
        return [dict(task) for task in tasks[:limit]]

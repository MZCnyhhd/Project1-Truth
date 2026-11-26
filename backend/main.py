import asyncio
import re
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import platforms
from .config import DOWNLOAD_DIR, FRONTEND_DIR
from .db import init_db, list_items
from .douyin_auth import cancel_login, get_status as auth_status, start_login
from .downloader import get_task, list_tasks, start_download
from .explain import ExplainError, explain_video

init_db()

app = FastAPI(title="Truth")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def no_cache(request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/app/") or request.url.path == "/":
        response.headers["Cache-Control"] = "no-store, must-revalidate"
    return response


class UrlPayload(BaseModel):
    url: str


class LoginPayload(BaseModel):
    platform: str = "douyin"


_URL_PATTERN = re.compile(r"https?://[^\s\u4e00-\u9fff，。；、！？）)】]+")


def _checked_url(text: str) -> str:
    value = (text or "").strip()
    match = _URL_PATTERN.search(value)
    candidate = match.group(0) if match else value
    if not (candidate.startswith("http://") or candidate.startswith("https://")):
        raise HTTPException(status_code=400, detail="没有找到有效的 http/https 链接")
    return candidate


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/platform")
async def platform_info(url: str = "") -> dict:
    return {"ok": True, "data": platforms.detect(url)}


@app.get("/api/auth/status")
async def get_auth_status(platform: str = "douyin") -> dict:
    info = platforms.get(platform)
    return {
        "ok": True,
        "data": {
            **auth_status(platform),
            "platform": platform,
            "name": info["name"],
            "login_supported": info["login_supported"],
        },
    }


@app.post("/api/auth/login/start")
async def login_start(payload: LoginPayload | None = None) -> dict:
    platform = payload.platform if payload else "douyin"
    info = platforms.get(platform)
    if not info["login_supported"]:
        raise HTTPException(status_code=400, detail=f"{info['name']}暂不支持扫码登录")
    login_urls = {
        "douyin": "https://www.douyin.com/",
        "kuaishou": "https://www.kuaishou.com/",
    }
    login_url = login_urls.get(platform, "https://www.douyin.com/")
    return {"ok": True, "data": await start_login(platform, login_url)}


@app.post("/api/auth/login/cancel")
async def login_cancel(payload: LoginPayload | None = None) -> dict:
    platform = payload.platform if payload else "douyin"
    return {"ok": True, "data": cancel_login(platform)}


@app.post("/api/tasks")
async def create_task(payload: UrlPayload) -> dict:
    url = _checked_url(payload.url)
    info = platforms.detect(url)
    if info["login_required"]:
        state = auth_status(info["key"])
        if not state["logged_in"]:
            raise HTTPException(
                status_code=400, detail=f"{info['name']}{state['message']}，请先扫码登录"
            )
    task = start_download(url)
    return {"ok": True, "data": task}


@app.get("/api/tasks")
async def tasks() -> dict:
    return {"ok": True, "data": list_tasks()}


@app.get("/api/history")
async def history(limit: int = 60) -> dict:
    items = [item for item in list_items("", limit) if item.get("filepath")]
    return {"ok": True, "data": items}


class ExplainPayload(BaseModel):
    filepath: str


@app.post("/api/explain")
async def explain(payload: ExplainPayload) -> dict:
    path = Path(payload.filepath)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="视频文件不存在")
    try:
        path.relative_to(DOWNLOAD_DIR)
    except ValueError:
        raise HTTPException(status_code=400, detail="只能解释已下载的视频") from None

    try:
        result = await asyncio.to_thread(explain_video, str(path))
    except ExplainError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - surface the reason in the UI
        raise HTTPException(status_code=500, detail=f"解析失败：{str(exc)[:200]}") from exc
    return {"ok": True, "data": result}


@app.get("/api/tasks/{task_id}")
async def task_detail(task_id: str) -> dict:
    task = get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    return {"ok": True, "data": task}


app.mount("/media", StaticFiles(directory=str(DOWNLOAD_DIR)), name="media")
app.mount("/app", StaticFiles(directory=str(FRONTEND_DIR)), name="app")


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(FRONTEND_DIR / "index.html")

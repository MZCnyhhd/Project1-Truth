"""Platform detection and per-platform login requirements."""

from typing import Any

PLATFORMS: dict[str, dict[str, Any]] = {
    "douyin": {
        "name": "抖音",
        "domains": ("douyin.com", "iesdouyin.com"),
        "login_required": True,
        "login_supported": True,
    },
    "bilibili": {
        "name": "B站",
        "domains": ("bilibili.com", "b23.tv"),
        "login_required": False,
        "login_supported": False,
    },
    "xiaohongshu": {
        "name": "小红书",
        "domains": ("xiaohongshu.com", "xhslink.com"),
        "login_required": False,
        "login_supported": False,
    },
    "kuaishou": {
        "name": "快手",
        "domains": ("kuaishou.com",),
        "login_required": True,
        "login_supported": True,
    },
    "wechat_channels": {
        "name": "微信视频号",
        "domains": ("weixin.qq.com/sph", "channels.weixin.qq.com"),
        "login_required": False,
        "login_supported": False,
    },
    "weibo": {
        "name": "微博",
        "domains": ("weibo.com", "weibo.cn"),
        "login_required": False,
        "login_supported": False,
    },
    "youtube": {
        "name": "YouTube",
        "domains": ("youtube.com", "youtu.be"),
        "login_required": False,
        "login_supported": False,
    },
    "tiktok": {
        "name": "TikTok",
        "domains": ("tiktok.com",),
        "login_required": False,
        "login_supported": False,
    },
}

_GENERIC: dict[str, Any] = {
    "key": "generic",
    "name": "通用链接",
    "login_required": False,
    "login_supported": False,
}


def detect(url: str) -> dict[str, Any]:
    text = (url or "").lower()
    for key, info in PLATFORMS.items():
        if any(domain in text for domain in info["domains"]):
            return {
                "key": key,
                "name": info["name"],
                "login_required": info["login_required"],
                "login_supported": info["login_supported"],
            }
    return dict(_GENERIC)


def get(key: str) -> dict[str, Any]:
    info = PLATFORMS.get(key)
    if info is None:
        return dict(_GENERIC)
    return {
        "key": key,
        "name": info["name"],
        "login_required": info["login_required"],
        "login_supported": info["login_supported"],
    }

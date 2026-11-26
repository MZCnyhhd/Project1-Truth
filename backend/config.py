import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOWNLOAD_DIR = ROOT / "downloads"
DATA_DIR = ROOT / "data"
FRONTEND_DIR = ROOT / "frontend"
DB_PATH = DATA_DIR / "library.db"

_cookies = os.getenv("VIDEO_COOKIES_FILE", "").strip()
COOKIES_FILE = Path(_cookies) if _cookies else None
DEFAULT_COOKIES_FILE = DATA_DIR / "cookies.txt"

for _directory in (DOWNLOAD_DIR, DATA_DIR, FRONTEND_DIR):
    _directory.mkdir(parents=True, exist_ok=True)

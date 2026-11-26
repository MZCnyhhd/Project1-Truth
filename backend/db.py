import sqlite3
from typing import Any

from .config import DB_PATH


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with get_conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT UNIQUE,
                title TEXT NOT NULL DEFAULT '',
                uploader TEXT NOT NULL DEFAULT '',
                duration INTEGER,
                url TEXT NOT NULL DEFAULT '',
                thumbnail TEXT NOT NULL DEFAULT '',
                description TEXT NOT NULL DEFAULT '',
                extractor TEXT NOT NULL DEFAULT '',
                filepath TEXT NOT NULL DEFAULT '',
                subtitle_path TEXT NOT NULL DEFAULT '',
                filesize INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_items_created ON items(created_at DESC)"
        )


def save_item(item: dict[str, Any]) -> None:
    fields = [
        "task_id",
        "title",
        "uploader",
        "duration",
        "url",
        "thumbnail",
        "description",
        "extractor",
        "filepath",
        "subtitle_path",
        "filesize",
    ]
    values = [item.get(key) for key in fields]
    updates = ",".join(f"{key}=excluded.{key}" for key in fields if key != "task_id")
    with get_conn() as conn:
        conn.execute(
            f"""
            INSERT INTO items ({','.join(fields)})
            VALUES ({','.join('?' for _ in fields)})
            ON CONFLICT(task_id) DO UPDATE SET {updates}
            """,
            values,
        )


def list_items(query: str = "", limit: int = 200) -> list[dict[str, Any]]:
    keyword = (query or "").strip()
    like = f"%{keyword}%"
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT * FROM items
            WHERE ? = ''
               OR title LIKE ?
               OR uploader LIKE ?
               OR description LIKE ?
               OR url LIKE ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (keyword, like, like, like, like, limit),
        ).fetchall()
    return [dict(row) for row in rows]


def get_item(item_id: int) -> dict[str, Any] | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
    return dict(row) if row else None

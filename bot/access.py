from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject, User


DENIED_TEXT = "извините бот не работает"


def normalize_username(value: str | None) -> str:
    if not value:
        return ""
    return value.strip().lstrip("@").lower()


def parse_username_arg(raw: str | None) -> str | None:
    if not raw:
        return None
    token = raw.strip().split()[0] if raw.strip() else ""
    name = normalize_username(token)
    return name or None


class Access:
    def __init__(self, db_path: Path, admin_usernames: list[str]):
        self.admins = {normalize_username(name) for name in admin_usernames if name}
        self.admins.discard("")
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS allowed_users (
                username TEXT PRIMARY KEY,
                added_by TEXT,
                added_at TEXT
            )
            """
        )
        self.conn.commit()

    def is_admin(self, user: User | None) -> bool:
        if user is None:
            return False
        return normalize_username(user.username) in self.admins

    def is_allowed(self, user: User | None) -> bool:
        if user is None:
            return False
        name = normalize_username(user.username)
        if not name:
            return False
        if name in self.admins:
            return True
        row = self.conn.execute(
            "SELECT 1 FROM allowed_users WHERE username = ?",
            (name,),
        ).fetchone()
        return row is not None

    def add(self, username: str, added_by: str) -> bool:
        name = normalize_username(username)
        if not name:
            return False
        now = datetime.now(timezone.utc).isoformat()
        self.conn.execute(
            """
            INSERT INTO allowed_users(username, added_by, added_at)
            VALUES (?, ?, ?)
            ON CONFLICT(username) DO UPDATE SET
                added_by = excluded.added_by,
                added_at = excluded.added_at
            """,
            (name, normalize_username(added_by), now),
        )
        self.conn.commit()
        return True

    def remove(self, username: str) -> bool:
        name = normalize_username(username)
        if not name:
            return False
        cur = self.conn.execute(
            "DELETE FROM allowed_users WHERE username = ?",
            (name,),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def list_users(self) -> list[str]:
        rows = self.conn.execute(
            "SELECT username FROM allowed_users ORDER BY username"
        ).fetchall()
        return [row["username"] for row in rows]


class AccessMiddleware(BaseMiddleware):
    def __init__(self, access: Access):
        super().__init__()
        self.access = access

    async def __call__(self, handler, event: TelegramObject, data: dict):
        user: User | None = data.get("event_from_user")
        if not self.access.is_allowed(user):
            if isinstance(event, Message):
                await event.answer(DENIED_TEXT)
            elif isinstance(event, CallbackQuery):
                await event.answer(DENIED_TEXT, show_alert=True)
            return None
        data["is_admin"] = self.access.is_admin(user)
        return await handler(event, data)

"""
db.py — SQLite member store for group moderation.

Permanently persists group members so the bot can resolve @username
and display names for users who have NEVER sent a message in the chat.

Table:
    members(chat_id INTEGER, user_id INTEGER, username TEXT, first_name TEXT)
    PRIMARY KEY (chat_id, user_id)

All writes are upserts — safe to call on every message / join event.
Queries hit indexed columns, so lookups are sub-millisecond.
"""

import sqlite3
import os
import logging

logger = logging.getLogger(__name__)

_DB_PATH = os.path.join(os.path.dirname(__file__), "members.db")
_conn: sqlite3.Connection | None = None


def _get_conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(_DB_PATH, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
    return _conn


def init_db() -> None:
    """Create tables and indexes. Call once at startup before polling begins."""
    conn = _get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS members (
            chat_id    INTEGER NOT NULL,
            user_id    INTEGER NOT NULL,
            username   TEXT,
            first_name TEXT,
            PRIMARY KEY (chat_id, user_id)
        );
        CREATE INDEX IF NOT EXISTS idx_members_username
            ON members (chat_id, LOWER(username));
        CREATE INDEX IF NOT EXISTS idx_members_name
            ON members (chat_id, LOWER(first_name));
    """)
    conn.commit()
    logger.info("DB ready: %s", _DB_PATH)


def upsert_member(
    chat_id: int,
    user_id: int,
    username: str | None,
    first_name: str | None,
) -> None:
    """Insert or update a member record. Safe to call on every message/join."""
    conn = _get_conn()
    conn.execute(
        """
        INSERT INTO members (chat_id, user_id, username, first_name)
        VALUES (?, ?, ?, ?)
        ON CONFLICT (chat_id, user_id) DO UPDATE SET
            username   = excluded.username,
            first_name = excluded.first_name
        """,
        (chat_id, user_id, username, first_name),
    )
    conn.commit()


def find_by_username(chat_id: int, username: str) -> int | None:
    """
    Return user_id for the member with this @username in *chat_id*, or None.
    Case-insensitive. Pure local lookup — faster than any API call.
    """
    row = _get_conn().execute(
        "SELECT user_id FROM members WHERE chat_id = ? AND LOWER(username) = LOWER(?)",
        (chat_id, username),
    ).fetchone()
    return int(row["user_id"]) if row else None


def find_by_name(chat_id: int, name: str) -> int | None:
    """
    Return user_id of the first member whose first_name contains *name*
    (case-insensitive substring), or None.
    Used to target users with no @username by display name.
    """
    row = _get_conn().execute(
        "SELECT user_id FROM members WHERE chat_id = ? AND LOWER(first_name) LIKE LOWER(?)",
        (chat_id, f"%{name}%"),
    ).fetchone()
    return int(row["user_id"]) if row else None


def known_chats() -> list[int]:
    """Return all distinct chat_ids stored in the DB (used for startup admin sync)."""
    rows = _get_conn().execute("SELECT DISTINCT chat_id FROM members").fetchall()
    return [int(r["chat_id"]) for r in rows]

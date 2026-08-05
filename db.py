"""
db.py — SQLite store for group moderation and auto-replies.

Tables
──────
members(chat_id, user_id, username, first_name)
    Permanently persists group members so the bot can resolve @username
    and display names for users who have NEVER sent a message in the chat.
    PRIMARY KEY (chat_id, user_id)

replies(chat_id, trigger, response)
    Stores trigger→response pairs for the auto-reply system.
    chat_id = 0  means a *global* reply set by an admin via private DM —
                 it matches in every group.
    chat_id = <group_id>  means a reply specific to that group only.
    PRIMARY KEY (chat_id, trigger)  — one response per trigger per scope.
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

        CREATE TABLE IF NOT EXISTS replies (
            chat_id  INTEGER NOT NULL,
            trigger  TEXT    NOT NULL,
            response TEXT    NOT NULL,
            PRIMARY KEY (chat_id, trigger)
        );
        CREATE INDEX IF NOT EXISTS idx_replies_trigger
            ON replies (chat_id, LOWER(trigger));
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


# ── Replies ────────────────────────────────────────────────────────────────────

def upsert_reply(chat_id: int, trigger: str, response: str) -> None:
    """
    Save or overwrite a trigger→response pair for *chat_id*.
    Use chat_id=0 for global replies (added via private DM by bot admin).
    """
    _get_conn().execute(
        """
        INSERT INTO replies (chat_id, trigger, response)
        VALUES (?, ?, ?)
        ON CONFLICT (chat_id, trigger) DO UPDATE SET
            response = excluded.response
        """,
        (chat_id, trigger, response),
    )
    _get_conn().commit()


def find_reply(chat_id: int, text: str) -> str | None:
    """
    Return the response for the first trigger whose text is found (case-insensitive
    substring) inside *text*, checking group-specific replies first then global ones.

    Lookup order:
      1. chat_id = <group_id>  — group-specific replies take priority.
      2. chat_id = 0           — global replies set by admin via private DM.

    Returns the response string, or None if no trigger matches.
    """
    text_lower = text.lower()
    rows = _get_conn().execute(
        """
        SELECT trigger, response
        FROM   replies
        WHERE  chat_id IN (?, 0)
        ORDER BY CASE WHEN chat_id = ? THEN 0 ELSE 1 END
        """,
        (chat_id, chat_id),
    ).fetchall()
    for row in rows:
        if row["trigger"].lower() in text_lower:
            return row["response"]
    return None


def delete_reply(chat_id: int, trigger: str) -> bool:
    """Delete a reply. Returns True if a row was actually removed."""
    cur = _get_conn().execute(
        "DELETE FROM replies WHERE chat_id = ? AND LOWER(trigger) = LOWER(?)",
        (chat_id, trigger),
    )
    _get_conn().commit()
    return cur.rowcount > 0


def list_replies(chat_id: int) -> list[tuple[str, str]]:
    """Return all (trigger, response) pairs for *chat_id* and global (0)."""
    rows = _get_conn().execute(
        """
        SELECT trigger, response
        FROM   replies
        WHERE  chat_id IN (?, 0)
        ORDER BY chat_id DESC, LOWER(trigger)
        """,
        (chat_id,),
    ).fetchall()
    return [(r["trigger"], r["response"]) for r in rows]

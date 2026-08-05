"""
config.py — Configuration loader.

Required:
    BOT_TOKEN   — from @BotFather.

Optional (enables Pyrogram assistant + full member scan):
    API_ID                  — from my.telegram.org/apps
    API_HASH                — from my.telegram.org/apps
    ASSISTANT_SESSION_STRING — generated once with generate_session.py
"""

import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN: str = os.environ.get("BOT_TOKEN", "")

# Optional Pyrogram assistant credentials
API_ID: int = int(os.getenv("API_ID") or 0)
API_HASH: str = os.getenv("API_HASH", "")
ASSISTANT_SESSION_STRING: str = os.getenv("ASSISTANT_SESSION_STRING", "")

# True only when all three Pyrogram credentials are present
ASSISTANT_ENABLED: bool = bool(API_ID and API_HASH and ASSISTANT_SESSION_STRING)

# Bot admin user ID — the only user allowed to add replies via private DM.
# Set BOT_ADMIN_ID in Replit Secrets (integer Telegram user ID).
_admin_id_raw = os.environ.get("BOT_ADMIN_ID", "")
BOT_ADMIN_ID: int = int(_admin_id_raw) if _admin_id_raw.isdigit() else 0


def validate_config() -> None:
    """Fail fast if BOT_TOKEN is missing."""
    if not BOT_TOKEN:
        raise EnvironmentError(
            "BOT_TOKEN is not set.\n"
            "Add it to your Replit Secrets (key: BOT_TOKEN)."
        )

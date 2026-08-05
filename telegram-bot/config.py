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
_api_id_raw = os.environ.get("API_ID", "")
API_ID: int = int(_api_id_raw) if _api_id_raw.isdigit() else 0
API_HASH: str = os.environ.get("API_HASH", "")
ASSISTANT_SESSION_STRING: str = os.environ.get("ASSISTANT_SESSION_STRING", "")

# True only when all three Pyrogram credentials are present
ASSISTANT_ENABLED: bool = bool(API_ID and API_HASH and ASSISTANT_SESSION_STRING)


def validate_config() -> None:
    """Fail fast if BOT_TOKEN is missing."""
    if not BOT_TOKEN:
        raise EnvironmentError(
            "BOT_TOKEN is not set.\n"
            "Add it to your Replit Secrets (key: BOT_TOKEN)."
        )

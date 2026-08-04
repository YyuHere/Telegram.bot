"""
config.py — Configuration loader (simplified bot-only mode).

Only BOT_TOKEN is required. python-telegram-bot does not need API_ID / API_HASH.
"""

import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN: str = os.environ.get("BOT_TOKEN", "")


def validate_config() -> None:
    """Fail fast if BOT_TOKEN is missing."""
    if not BOT_TOKEN:
        raise EnvironmentError(
            "BOT_TOKEN is not set.\n"
            "Add it to your Replit Secrets (key: BOT_TOKEN)."
        )

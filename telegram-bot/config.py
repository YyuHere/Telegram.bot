"""
config.py — Centralized configuration loader.
Reads environment variables from .env (local) or Railway environment.
"""

import os
from dotenv import load_dotenv

# Load .env file if present (local development)
load_dotenv()

# ─── Telegram API credentials ────────────────────────────────────────────────
API_ID: int = int(os.environ.get("API_ID", 0))
API_HASH: str = os.environ.get("API_HASH", "")

# ─── Main Bot ─────────────────────────────────────────────────────────────────
BOT_TOKEN: str = os.environ.get("BOT_TOKEN", "")

# ─── Assistant Userbot (string session from Pyrogram) ─────────────────────────
ASSISTANT_SESSION_STRING: str = os.environ.get("ASSISTANT_SESSION_STRING", "")

# ─── Validation ───────────────────────────────────────────────────────────────
def validate_config() -> None:
    """Raise a descriptive error if any required variable is missing."""
    missing = []
    if not API_ID:
        missing.append("API_ID")
    if not API_HASH:
        missing.append("API_HASH")
    if not BOT_TOKEN:
        missing.append("BOT_TOKEN")
    if not ASSISTANT_SESSION_STRING:
        missing.append("ASSISTANT_SESSION_STRING")
    if missing:
        raise EnvironmentError(
            f"Missing required environment variables: {', '.join(missing)}\n"
            "Copy .env.example to .env and fill in all values."
        )

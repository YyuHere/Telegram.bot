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

# ── Spotify Web API credentials (optional — enables Stage 2 fallback) ─────────
# Create a free app at https://developer.spotify.com/dashboard and set these.
SPOTIFY_CLIENT_ID: str     = os.getenv("SPOTIFY_CLIENT_ID", "")
SPOTIFY_CLIENT_SECRET: str = os.getenv("SPOTIFY_CLIENT_SECRET", "")

# Optional yt-dlp authentication cookie jar. The default is the Git-ignored
# root-level cookies.txt file for local/development use. Keep the cookie data
# out of source control and set this to another mounted path in deployment.
YTDLP_COOKIE_FILE: str = os.getenv("YTDLP_COOKIE_FILE", "cookies.txt")

# Shared yt-dlp options. Keep this exact baseline in one place so searches,
# direct links, and stream extraction all use the same YouTube client pair and
# mobile browser identity.
YTDL_OPTIONS: dict = {
    "format": "bestaudio/best",
    "noplaylist": True,
    "extractor_args": {
        "youtube": {
            "player_client": ["android", "web"],
        },
    },
    "http_headers": {
        "User-Agent": (
            "Mozilla/5.0 (Linux; Android 10; K) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Mobile Safari/537.36"
        ),
    },
}

# Keep provider requests bounded. These values control the per-request timeout
# and the short cooldown after YouTube blocks a request.
YTDLP_REQUEST_DELAY: float = float(os.getenv("YTDLP_REQUEST_DELAY", "1.5"))
YTDLP_SOCKET_TIMEOUT: float = float(os.getenv("YTDLP_SOCKET_TIMEOUT", "8"))
YTDLP_BLOCK_COOLDOWN: float = float(os.getenv("YTDLP_BLOCK_COOLDOWN", "60"))
_requested_player_client = (
    os.getenv("YTDLP_PLAYER_CLIENT", "android").strip().casefold()
)
YTDLP_PLAYER_CLIENT: str = (
    _requested_player_client
    if _requested_player_client in {"android", "android_vr", "mweb"}
    else "android"
)

# FFmpeg transport timeout for direct CDN streams. This is separate from the
# yt-dlp extraction timeout because extraction has already completed by the
# time PyTgCalls starts reading the stream.
YTDLP_STREAM_TIMEOUT: float = float(os.getenv("YTDLP_STREAM_TIMEOUT", "15"))

# Bot admin user ID — the only user allowed to add replies via private DM.
# Set BOT_ADMIN_ID in Replit Secrets (integer Telegram user ID).
_admin_id_raw = os.environ.get("BOT_ADMIN_ID", "")
BOT_ADMIN_ID: int = int(_admin_id_raw) if _admin_id_raw.isdigit() else 0

# ── Fixed assistant account for voice chat streaming ──────────────────────────
# This is the Pyrogram userbot that joins group calls on behalf of the bot.
# These values are fixed and do not need to be set via environment variables.
ASSISTANT_USER_ID: int = 7769827870
ASSISTANT_USERNAME: str = "HelpQaed"  # without @


def validate_config() -> None:
    """Fail fast if BOT_TOKEN is missing."""
    if not BOT_TOKEN:
        raise EnvironmentError(
            "BOT_TOKEN is not set.\n"
            "Add it to your Replit Secrets (key: BOT_TOKEN)."
        )

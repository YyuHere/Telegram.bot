"""
main.py — Entry point for the Telegram Bot (simplified bot-only mode).

Stack: python-telegram-bot v21 (async polling).
Requires only: BOT_TOKEN (set as a Replit Secret or in .env).

Assistant userbot and pytgcalls are disabled until the music system
is re-enabled in a later phase.
"""

import logging
from telegram.ext import Application

import config
from config import BOT_TOKEN
from handlers import general, ludo
from music import commands as music_commands

# ── Logging ────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


def main() -> None:
    # 1. Validate — fails loudly if BOT_TOKEN is missing
    config.validate_config()

    # 2. Build the Application
    app = Application.builder().token(BOT_TOKEN).build()

    # 3. Register all handlers
    general.register(app)   # /start, /help, /ping
    ludo.register(app)      # Ludo Club code extractor
    music_commands.register(app)  # /play, /stop, /pause, /resume, /skip (placeholders)

    # 4. Start polling
    logger.info("Bot is starting — polling for updates…")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()

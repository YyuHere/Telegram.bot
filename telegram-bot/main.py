"""
main.py — Entry point for the Telegram Bot.

Stack: python-telegram-bot v21 (async polling).
Requires only: BOT_TOKEN (Replit Secret).

Features active:
  - Group moderation commands (ban/unban/kick/mute/unmute)
  - Arabic text triggers for all moderation actions
  - Ludo Club referral code extractor
  - General commands: /start, /help, /ping
"""

import logging
from telegram.ext import Application

import config
from config import BOT_TOKEN
from handlers import general, ludo, moderation
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

    # 3. Register handlers (order matters: more specific handlers first)
    general.register(app)          # /start, /help, /ping
    moderation.register(app)       # /ban /unban /kick /mute /unmute + Arabic triggers
    ludo.register(app)             # Ludo Club code extractor
    music_commands.register(app)   # /play etc. — placeholder responses

    # 4. Start polling
    logger.info("Bot is starting — polling for updates…")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()

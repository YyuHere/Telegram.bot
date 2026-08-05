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
import db
from handlers import general, ludo, moderation, replies
from music import commands as music_commands

# ── Logging ────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


async def _startup(app: Application) -> None:
    """
    Runs once after the bot connects, before polling starts.
    Syncs the admin list for every chat already known in the DB so that
    previously seen members survive a bot restart immediately.
    """
    chats = db.known_chats()
    if not chats:
        return
    logger.info("Startup: syncing admin lists for %d known chat(s)…", len(chats))
    for chat_id in chats:
        try:
            admins = await app.bot.get_chat_administrators(chat_id)
            for admin in admins:
                if not admin.user.is_bot:
                    db.upsert_member(
                        chat_id,
                        admin.user.id,
                        admin.user.username,
                        admin.user.first_name,
                    )
        except Exception as exc:
            logger.debug("Admin sync failed for chat %s: %s", chat_id, exc)


def main() -> None:
    # 1. Validate — fails loudly if BOT_TOKEN is missing
    config.validate_config()

    # 2. Initialise local SQLite DB (creates file + tables if they don't exist)
    db.init_db()

    # 3. Build the Application with startup hook
    app = Application.builder().token(BOT_TOKEN).post_init(_startup).build()

    # 4. Register handlers (order matters: more specific handlers first)
    general.register(app)          # /start, /help, /ping
    moderation.register(app)       # /ban /unban /kick /mute /unmute + Arabic triggers
    ludo.register(app)             # Ludo Club code extractor
    replies.register(app)          # اضافه رد interactive flow + auto-reply
    music_commands.register(app)   # /play etc. — placeholder responses

    # 5. Start polling
    logger.info("Bot is starting — polling for updates…")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()

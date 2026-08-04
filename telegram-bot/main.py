"""
main.py — Entry point for the Telegram Bot.

Simplified mode: runs the Main Bot only.
Assistant userbot and pytgcalls are commented out until the music
system is re-enabled.
"""

import asyncio
import logging
import signal

from pyrogram import Client

import config
from config import API_ID, API_HASH, BOT_TOKEN
from handlers import general, ludo
from music import commands as music_commands

# ─── Assistant / pytgcalls imports — DISABLED ─────────────────────────────────
# from music.player import call_py
# from assistant.userbot import assistant

# ─── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


async def main() -> None:
    # 1. Validate environment (only BOT_TOKEN, API_ID, API_HASH required)
    config.validate_config()

    # 2. Create the main bot client
    bot = Client(
        name="main_bot",
        api_id=API_ID,
        api_hash=API_HASH,
        bot_token=BOT_TOKEN,
    )

    # 3. Register handlers
    general.register(bot)
    ludo.register(bot)
    music_commands.register(bot)   # placeholder responses only for now

    # ─── Assistant / pytgcalls startup — DISABLED ─────────────────────────────
    # await assistant.start()
    # await call_py.start()

    # 4. Start the main bot
    logger.info("Starting main bot…")
    await bot.start()

    me = await bot.get_me()
    logger.info("Bot running as @%s (id=%s)", me.username, me.id)

    # 5. Keep running until interrupted
    stop_event = asyncio.Event()

    def _shutdown(*_):
        logger.info("Shutdown signal received.")
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, _shutdown)

    await stop_event.wait()

    # 6. Graceful shutdown
    logger.info("Stopping bot…")
    await bot.stop()
    logger.info("Bye!")


if __name__ == "__main__":
    asyncio.run(main())

"""
main.py — Entry point for the Telegram Bot + Assistant Userbot.

Start order:
  1. Validate config (fail fast on missing env vars).
  2. Create the main Bot client.
  3. Register all handlers (general, ludo, music).
  4. Start the Assistant userbot client.
  5. Start the pytgcalls instance (attached to the Assistant).
  6. Run both clients until Ctrl-C or SIGTERM.
"""

import asyncio
import logging
import signal

from pyrogram import Client

import config
from config import API_ID, API_HASH, BOT_TOKEN
from handlers import general, ludo
from music import commands as music_commands
from music.player import call_py
from assistant.userbot import assistant

# ─── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


async def main() -> None:
    # 1. Validate environment
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
    music_commands.register(bot)

    # 4 & 5. Start clients
    logger.info("Starting Assistant userbot…")
    await assistant.start()

    logger.info("Starting pytgcalls…")
    await call_py.start()

    logger.info("Starting main bot…")
    await bot.start()

    me = await bot.get_me()
    logger.info("Bot running as @%s (id=%s)", me.username, me.id)

    # 6. Keep running until interrupted
    stop_event = asyncio.Event()

    def _shutdown(*_):
        logger.info("Shutdown signal received.")
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, _shutdown)

    await stop_event.wait()

    # ─── Graceful shutdown ────────────────────────────────────────────────────
    logger.info("Stopping clients…")
    await bot.stop()
    await assistant.stop()
    logger.info("Bye!")


if __name__ == "__main__":
    asyncio.run(main())

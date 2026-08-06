"""
main.py — Entry point for the Telegram Bot.

Stack: python-telegram-bot v21 (async polling) + Pyrogram assistant (MTProto).
Requires: BOT_TOKEN
Optional: API_ID, API_HASH, ASSISTANT_SESSION_STRING (enables full member scan)

Features active:
  - Group moderation commands (ban/unban/kick/mute/unmute)
  - Arabic text triggers for all moderation actions
  - Ludo Club referral code extractor
  - General commands: /start, /help, /ping
  - Auto-reply system (اضافه رد)
  - Assistant userbot for 100% member sync via Pyrogram
"""

import logging
from telegram.ext import Application

import config
from config import BOT_TOKEN
import db
from assistant.userbot import assistant as _assistant
from handlers import general, ludo, moderation, replies
from music import commands as music_commands
from music import player as music_player

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

    Startup order matters:
    ──────────────────────
    py-tgcalls 2.x owns the Pyrogram client lifecycle.  ``call_py.start()``
    starts both the pytgcalls engine *and* the underlying Pyrogram session.
    Calling ``_assistant.start()`` separately beforehand causes a double-start
    error ("Client is already started") that leaves the session in an
    inconsistent / disconnected state.

    Therefore:
      1. ``ensure_pytgcalls_started()`` → ``call_py.start()`` — starts Pyrogram
         AND pytgcalls in one shot.
      2. Only when call_py is None (pytgcalls unavailable) do we fall back to
         starting the Pyrogram client on its own (for member scanning only).
      3. We verify the session is live with ``_assistant.is_connected`` before
         using it anywhere.
    """
    # ── Run health check BEFORE starting — surfaces ffmpeg / import issues ────
    hc = music_player.health_check()
    if hc["all_ok"]:
        logger.info(
            "Health check OK — ffmpeg=%s, pytgcalls=%s, userbot=%s",
            hc.get("ffmpeg_version", "?"),
            hc.get("pytgcalls_version", "?"),
            hc.get("userbot"),
        )
    else:
        logger.error(
            "Health check FAILED — ffmpeg=%s, pytgcalls=%s, userbot=%s\n"
            "  ffmpeg_error: %s\n  pytgcalls_error: %s\n  userbot_error: %s",
            hc["ffmpeg"], hc["pytgcalls"], hc["userbot"],
            hc.get("ffmpeg_error", "—"),
            hc.get("pytgcalls_error", "—"),
            hc.get("userbot_error", "—"),
        )

    # ── Start PyTgCalls (also boots the Pyrogram assistant inside) ────────────
    await music_player.ensure_pytgcalls_started()

    # ── Fallback: no pytgcalls, but still need assistant for member scanning ──
    # Only executed when pytgcalls is unavailable (session string missing,
    # tgcalls native lib not installed, etc.).  In the normal path call_py.start()
    # already started the assistant above, so we must not call .start() again.
    if music_player.call_py is None and _assistant is not None:
        try:
            await _assistant.start()
            logger.info("Pyrogram assistant started (member-scan mode, no pytgcalls).")
        except Exception as exc:
            logger.warning("Could not start Pyrogram assistant: %s", exc)

    # ── Confirm the session is live ───────────────────────────────────────────
    if _assistant is not None and _assistant.is_connected:
        try:
            me = await _assistant.get_me()
            logger.info("Pyrogram assistant ready — logged in as @%s (id=%s)", me.username, me.id)
        except Exception as exc:
            logger.warning("assistant.get_me() failed despite is_connected=True: %s", exc)
    elif _assistant is not None:
        logger.warning(
            "Pyrogram assistant was configured but is NOT connected after startup. "
            "Voice chat streaming and full member sync will be unavailable. "
            "Check ASSISTANT_SESSION_STRING, API_ID, and API_HASH."
        )

    # ── Sync known chats ───────────────────────────────────────────────────────
    chats = db.known_chats()
    if not chats:
        return

    logger.info("Startup: syncing %d known chat(s)…", len(chats))
    for chat_id in chats:
        # Full member scan via assistant when available
        if _assistant is not None and _assistant.is_connected:
            try:
                count = 0
                async for member in _assistant.get_chat_members(chat_id):
                    user = member.user
                    if user and not user.is_bot:
                        db.upsert_member(chat_id, user.id, user.username, user.first_name)
                        count += 1
                logger.info("Startup full-sync: %d members in chat %s", count, chat_id)
                continue
            except Exception as exc:
                logger.debug("Startup full-sync failed for chat %s: %s — using admin fallback", chat_id, exc)

        # Fallback: admin list only
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


async def _shutdown(app: Application) -> None:
    """Gracefully stop the Pyrogram assistant client on bot shutdown."""
    if _assistant is not None and _assistant.is_connected:
        try:
            await _assistant.stop()
            logger.info("Pyrogram assistant stopped.")
        except Exception as exc:
            logger.warning("Error stopping Pyrogram assistant: %s", exc)


def main() -> None:
    # 1. Validate — fails loudly if BOT_TOKEN is missing
    config.validate_config()

    # 2. Initialise local SQLite DB (creates file + tables if they don't exist)
    db.init_db()

    # 3. Build the Application with startup + shutdown hooks
    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(_startup)
        .post_shutdown(_shutdown)
        .build()
    )

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

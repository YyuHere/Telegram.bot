"""
music/commands.py — Telegram command handlers for music playback.

Registers: /play, /stop, /pause, /resume, /skip
"""

import logging
from pyrogram import Client, filters
from pyrogram.types import Message

from assistant.userbot import ensure_in_group
from music import player

logger = logging.getLogger(__name__)


def register(bot: Client) -> None:
    """Attach all music command handlers to the bot client."""

    # ─── /play ────────────────────────────────────────────────────────────────
    @bot.on_message(filters.command("play") & (filters.group | filters.supergroup))
    async def play_handler(client: Client, message: Message) -> None:
        """
        Usage: /play <song title or YouTube URL>

        1. Ensures the Assistant is in the group.
        2. Resolves the audio URL via yt-dlp.
        3. Joins the Voice Chat and starts streaming.
        """
        # Extract query (everything after /play)
        args = message.text.split(None, 1)
        if len(args) < 2 or not args[1].strip():
            await message.reply_text(
                "❓ Please provide a song name or URL.\n"
                "Example: `/play Shape of You`",
                quote=True,
            )
            return

        query = args[1].strip()
        chat_id = message.chat.id

        status_msg = await message.reply_text("🔍 Searching…", quote=True)

        # Step 1 — Make sure the assistant is in the group
        in_group = await ensure_in_group(bot, chat_id)
        if not in_group:
            await status_msg.edit_text(
                "❌ Could not add the Assistant to this group.\n"
                "Please add it manually and try again."
            )
            return

        # Step 2 — Resolve & play
        try:
            result = await player.play(chat_id, query)
            await status_msg.edit_text(f"🎵 Now playing: **{result}**")
        except Exception as exc:
            logger.exception("Error during /play in chat %s", chat_id)
            await status_msg.edit_text(f"❌ Failed to play: `{exc}`")

    # ─── /stop ────────────────────────────────────────────────────────────────
    @bot.on_message(filters.command("stop") & (filters.group | filters.supergroup))
    async def stop_handler(client: Client, message: Message) -> None:
        """Stop playback and leave the voice chat."""
        try:
            await player.stop(message.chat.id)
            await message.reply_text("⏹ Stopped and left the voice chat.", quote=True)
        except Exception as exc:
            logger.exception("Error during /stop in chat %s", message.chat.id)
            await message.reply_text(f"❌ Error: `{exc}`", quote=True)

    # ─── /pause ───────────────────────────────────────────────────────────────
    @bot.on_message(filters.command("pause") & (filters.group | filters.supergroup))
    async def pause_handler(client: Client, message: Message) -> None:
        """Pause the currently playing track."""
        try:
            await player.pause(message.chat.id)
            await message.reply_text("⏸ Paused.", quote=True)
        except Exception as exc:
            logger.exception("Error during /pause in chat %s", message.chat.id)
            await message.reply_text(f"❌ Error: `{exc}`", quote=True)

    # ─── /resume ──────────────────────────────────────────────────────────────
    @bot.on_message(filters.command("resume") & (filters.group | filters.supergroup))
    async def resume_handler(client: Client, message: Message) -> None:
        """Resume a paused track."""
        try:
            await player.resume(message.chat.id)
            await message.reply_text("▶️ Resumed.", quote=True)
        except Exception as exc:
            logger.exception("Error during /resume in chat %s", message.chat.id)
            await message.reply_text(f"❌ Error: `{exc}`", quote=True)

    # ─── /skip ────────────────────────────────────────────────────────────────
    @bot.on_message(filters.command("skip") & (filters.group | filters.supergroup))
    async def skip_handler(client: Client, message: Message) -> None:
        """Skip the current track and play the next one in the queue."""
        try:
            next_title = await player.skip(message.chat.id)
            if next_title:
                await message.reply_text(f"⏭ Skipped! Now playing: **{next_title}**", quote=True)
            else:
                await message.reply_text("⏭ Skipped! No more tracks in the queue.", quote=True)
        except Exception as exc:
            logger.exception("Error during /skip in chat %s", message.chat.id)
            await message.reply_text(f"❌ Error: `{exc}`", quote=True)

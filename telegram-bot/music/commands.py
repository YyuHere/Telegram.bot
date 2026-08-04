"""
music/commands.py — Music command handlers (simplified / placeholder mode).

The full pytgcalls + Assistant streaming logic is commented out.
All music commands return a friendly "coming soon" message until
the music system is re-enabled.
"""

# ─── Imports needed when music is fully enabled — DISABLED ────────────────────
# from assistant.userbot import ensure_in_group
# from music import player

import logging
from pyrogram import Client, filters
from pyrogram.types import Message

logger = logging.getLogger(__name__)

_COMING_SOON = (
    "🎵 Music system will be enabled soon!\n"
    "Stay tuned for voice chat playback support."
)


def register(bot: Client) -> None:
    """Register placeholder music command handlers."""

    @bot.on_message(filters.command("play"))
    async def play_handler(client: Client, message: Message) -> None:
        await message.reply_text(_COMING_SOON, quote=True)

    @bot.on_message(filters.command("stop"))
    async def stop_handler(client: Client, message: Message) -> None:
        await message.reply_text(_COMING_SOON, quote=True)

    @bot.on_message(filters.command("pause"))
    async def pause_handler(client: Client, message: Message) -> None:
        await message.reply_text(_COMING_SOON, quote=True)

    @bot.on_message(filters.command("resume"))
    async def resume_handler(client: Client, message: Message) -> None:
        await message.reply_text(_COMING_SOON, quote=True)

    @bot.on_message(filters.command("skip"))
    async def skip_handler(client: Client, message: Message) -> None:
        await message.reply_text(_COMING_SOON, quote=True)

    # ─── Full music handlers — DISABLED ───────────────────────────────────────
    # Uncomment the block below and remove the placeholders above
    # to re-enable the full music system.
    #
    # @bot.on_message(filters.command("play") & (filters.group | filters.supergroup))
    # async def play_handler(client: Client, message: Message) -> None:
    #     args = message.text.split(None, 1)
    #     if len(args) < 2 or not args[1].strip():
    #         await message.reply_text("❓ Usage: `/play <song or URL>`", quote=True)
    #         return
    #     query = args[1].strip()
    #     chat_id = message.chat.id
    #     status_msg = await message.reply_text("🔍 Searching…", quote=True)
    #     in_group = await ensure_in_group(bot, chat_id)
    #     if not in_group:
    #         await status_msg.edit_text("❌ Could not add the Assistant to this group.")
    #         return
    #     try:
    #         result = await player.play(chat_id, query)
    #         await status_msg.edit_text(f"🎵 Now playing: **{result}**")
    #     except Exception as exc:
    #         logger.exception("Error during /play in chat %s", chat_id)
    #         await status_msg.edit_text(f"❌ Failed to play: `{exc}`")
    #
    # @bot.on_message(filters.command("stop") & (filters.group | filters.supergroup))
    # async def stop_handler(client: Client, message: Message) -> None:
    #     try:
    #         await player.stop(message.chat.id)
    #         await message.reply_text("⏹ Stopped and left the voice chat.", quote=True)
    #     except Exception as exc:
    #         await message.reply_text(f"❌ Error: `{exc}`", quote=True)
    #
    # @bot.on_message(filters.command("pause") & (filters.group | filters.supergroup))
    # async def pause_handler(client: Client, message: Message) -> None:
    #     try:
    #         await player.pause(message.chat.id)
    #         await message.reply_text("⏸ Paused.", quote=True)
    #     except Exception as exc:
    #         await message.reply_text(f"❌ Error: `{exc}`", quote=True)
    #
    # @bot.on_message(filters.command("resume") & (filters.group | filters.supergroup))
    # async def resume_handler(client: Client, message: Message) -> None:
    #     try:
    #         await player.resume(message.chat.id)
    #         await message.reply_text("▶️ Resumed.", quote=True)
    #     except Exception as exc:
    #         await message.reply_text(f"❌ Error: `{exc}`", quote=True)
    #
    # @bot.on_message(filters.command("skip") & (filters.group | filters.supergroup))
    # async def skip_handler(client: Client, message: Message) -> None:
    #     try:
    #         next_title = await player.skip(message.chat.id)
    #         if next_title:
    #             await message.reply_text(f"⏭ Skipped! Now playing: **{next_title}**", quote=True)
    #         else:
    #             await message.reply_text("⏭ Queue is now empty.", quote=True)
    #     except Exception as exc:
    #         await message.reply_text(f"❌ Error: `{exc}`", quote=True)

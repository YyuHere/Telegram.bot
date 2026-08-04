"""
handlers/general.py — General bot commands: /start, /help, /ping.
"""

import time
from pyrogram import Client, filters
from pyrogram.types import Message


def register(bot: Client) -> None:
    """Attach all general command handlers to the bot client."""

    # ─── /start ───────────────────────────────────────────────────────────────
    @bot.on_message(filters.command("start") & filters.private)
    async def start_handler(client: Client, message: Message) -> None:
        """Welcome the user and list bot features."""
        await message.reply_text(
            "👋 **Hello! I'm your Telegram Assistant Bot.**\n\n"
            "Here's what I can do:\n\n"
            "🎮 **Ludo Club**\n"
            "  • Automatically extract referral codes from messages.\n\n"
            "🎵 **Music (Voice Chat)**\n"
            "  • `/play <song or URL>` — Play audio in a voice chat.\n"
            "  • `/pause` — Pause playback.\n"
            "  • `/resume` — Resume playback.\n"
            "  • `/skip` — Skip the current track.\n"
            "  • `/stop` — Stop and leave the voice chat.\n\n"
            "🛠 **Utilities**\n"
            "  • `/ping` — Check my response time.\n"
            "  • `/help` — Show this help message.\n\n"
            "Add me to a group and I'll get to work! 🚀",
            quote=True,
        )

    # ─── /help ────────────────────────────────────────────────────────────────
    @bot.on_message(filters.command("help"))
    async def help_handler(client: Client, message: Message) -> None:
        """Display a concise command reference."""
        await message.reply_text(
            "📖 **Command Reference**\n\n"
            "`/start`   — Welcome & feature overview\n"
            "`/ping`    — Response latency\n"
            "`/help`    — This message\n\n"
            "`/play <song/URL>` — Play music in voice chat\n"
            "`/pause`   — Pause current track\n"
            "`/resume`  — Resume paused track\n"
            "`/skip`    — Skip to next track\n"
            "`/stop`    — Stop music & leave voice chat\n\n"
            "🎮 I also auto-extract Ludo Club codes — just share a link in the group!",
            quote=True,
        )

    # ─── /ping ────────────────────────────────────────────────────────────────
    @bot.on_message(filters.command("ping"))
    async def ping_handler(client: Client, message: Message) -> None:
        """Measure and report round-trip latency."""
        start = time.monotonic()
        sent = await message.reply_text("🏓 Pong!", quote=True)
        delta_ms = (time.monotonic() - start) * 1000
        await sent.edit_text(f"🏓 Pong! `{delta_ms:.1f} ms`")

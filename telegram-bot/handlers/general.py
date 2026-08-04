"""
handlers/general.py — General commands: /start, /help, /ping.
Uses python-telegram-bot v21 (async).
"""

import time
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Welcome message with feature overview."""
    await update.message.reply_text(
        "👋 *Hello! I'm your Telegram Assistant Bot.*\n\n"
        "Here's what I can do:\n\n"
        "🎮 *Ludo Club*\n"
        "  • Auto-extracts referral codes from invite links or messages\\.\n\n"
        "🎵 *Music \\(Voice Chat — coming soon\\)*\n"
        "  • `/play <song or URL>` — Play audio in a voice chat\\.\n"
        "  • `/pause`, `/resume`, `/skip`, `/stop`\n\n"
        "🛠 *Utilities*\n"
        "  • `/ping` — Check my response time\\.\n"
        "  • `/help` — Show this help message\\.",
        parse_mode="MarkdownV2",
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Concise command reference."""
    await update.message.reply_text(
        "📖 *Command Reference*\n\n"
        "`/start`  — Welcome & overview\n"
        "`/ping`   — Response latency\n"
        "`/help`   — This message\n\n"
        "`/play <song/URL>` — Play music \\(coming soon\\)\n"
        "`/pause` \\| `/resume` \\| `/skip` \\| `/stop`\n\n"
        "🎮 I also auto\\-extract Ludo Club codes — just share a link in the group\\!",
        parse_mode="MarkdownV2",
    )


async def ping(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Measure and report round-trip latency."""
    t0 = time.monotonic()
    msg = await update.message.reply_text("🏓 Pong\\!", parse_mode="MarkdownV2")
    elapsed_ms = (time.monotonic() - t0) * 1000
    await msg.edit_text(
        f"🏓 Pong\\! `{elapsed_ms:.1f} ms`",
        parse_mode="MarkdownV2",
    )


def register(app: Application) -> None:
    """Attach general command handlers to the application."""
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("ping", ping))

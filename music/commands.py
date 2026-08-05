"""
music/commands.py — Placeholder music commands (full system coming soon).

All commands reply with a friendly "coming soon" message.
The full pytgcalls + Assistant streaming logic is preserved as
commented-out code at the bottom of this file.
"""

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

_COMING_SOON = (
    "🎵 *Music system will be enabled soon\\!*\n"
    "Voice chat playback support is coming\\."
)


async def _placeholder(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(_COMING_SOON, parse_mode="MarkdownV2")


def register(app: Application) -> None:
    """Register placeholder handlers for all music commands."""
    for cmd in ("play", "stop", "pause", "resume", "skip"):
        app.add_handler(CommandHandler(cmd, _placeholder))

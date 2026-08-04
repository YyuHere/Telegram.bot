"""
handlers/ludo.py — Ludo Club referral code extractor.

Listens for messages in groups that contain either:
  • A Ludo Club invite URL: https://ludoclub.com/invite.html?...
  • Plain text like: "enter my Code: WAKL189"

Extracts the referral code with regex and replies with it as inline code
so users can copy it with a single tap.
"""

import re
from pyrogram import Client, filters
from pyrogram.types import Message

# ─── Patterns ─────────────────────────────────────────────────────────────────

# Matches the `code` query parameter in a Ludo Club invite URL.
# e.g. https://ludoclub.com/invite.html?code=WAKL189&...
_URL_PATTERN = re.compile(
    r"https?://(?:www\.)?ludoclub\.com/invite\.html[^\s]*[?&]code=([A-Za-z0-9]+)",
    re.IGNORECASE,
)

# Matches plain-text formats like:
#   "enter my Code: WAKL189"
#   "my referral code is ABC123"
#   "use code WAKL189"
_TEXT_PATTERN = re.compile(
    r"(?:enter\s+my\s+code|referral\s+code|use\s+code|code\s*[:\-]?)\s*:?\s*([A-Za-z0-9]{4,12})",
    re.IGNORECASE,
)


def _extract_code(text: str) -> str | None:
    """
    Try to extract a Ludo Club referral code from *text*.
    Returns the code string on success, or None if not found.
    """
    # Priority 1: URL-embedded code
    match = _URL_PATTERN.search(text)
    if match:
        return match.group(1)

    # Priority 2: Plain-text mention
    match = _TEXT_PATTERN.search(text)
    if match:
        return match.group(1)

    return None


def register(bot: Client) -> None:
    """Attach the Ludo Club code extractor handler to the bot client."""

    @bot.on_message(
        (filters.group | filters.channel)
        & filters.text
        & (
            filters.regex(r"ludoclub\.com/invite", re.IGNORECASE)
            | filters.regex(
                r"(?:enter\s+my\s+code|referral\s+code|use\s+code|code\s*[:\-]?)\s*:?\s*[A-Za-z0-9]{4,12}",
                re.IGNORECASE,
            )
        )
    )
    async def ludo_code_handler(client: Client, message: Message) -> None:
        """
        Intercept matching messages and reply with the extracted code.
        Only replies when a valid code is found; silently ignores false positives.
        """
        text = message.text or message.caption or ""
        code = _extract_code(text)

        if not code:
            return  # Pattern matched broadly; refined extraction found nothing

        await message.reply_text(
            f"🎮 **Ludo Club Code Detected!**\n\n"
            f"Tap to copy → `{code}`",
            quote=True,
        )

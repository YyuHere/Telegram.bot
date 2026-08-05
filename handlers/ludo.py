"""
handlers/ludo.py — Ludo Club referral code extractor.

Listens for group messages containing either:
  • A Ludo Club invite URL: https://ludoclub.com/invite.html?code=WAKL189
  • Plain text: "enter my Code: WAKL189" / "use code WAKL189"

Extracts the code with regex and replies with it as inline monospace
so users can copy it with a single tap.
"""

import re
import logging
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes

logger = logging.getLogger(__name__)

# ── Regex patterns ─────────────────────────────────────────────────────────────

# Matches the `code` query param in a Ludo Club invite URL
_URL_RE = re.compile(
    r"https?://(?:www\.)?ludoclub\.com/invite\.html[^\s]*[?&]code=([A-Za-z0-9]+)",
    re.IGNORECASE,
)

# Matches plain-text phrases like:
#   "enter my Code: WAKL189" | "use code ABC123" | "referral code: XYZ"
_TEXT_RE = re.compile(
    r"(?:enter\s+my\s+code|referral\s+code|use\s+code|code\s*[:\-]?)\s*:?\s*([A-Za-z0-9]{4,12})",
    re.IGNORECASE,
)

# Combined filter: message must mention ludoclub OR contain a code phrase
_LUDO_FILTER = filters.Regex(
    re.compile(
        r"ludoclub\.com/invite|"
        r"(?:enter\s+my\s+code|referral\s+code|use\s+code|code\s*[:\-]?)\s*:?\s*[A-Za-z0-9]{4,12}",
        re.IGNORECASE,
    )
)


def _extract_code(text: str) -> str | None:
    """Return the referral code found in *text*, or None."""
    m = _URL_RE.search(text)
    if m:
        return m.group(1)
    m = _TEXT_RE.search(text)
    if m:
        return m.group(1)
    return None


async def ludo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Reply with the extracted Ludo Club code, or silently ignore false positives."""
    text = update.message.text or update.message.caption or ""
    code = _extract_code(text)

    if not code:
        return  # Broad filter matched but refined regex found nothing

    logger.info("Ludo code extracted in chat %s: %s", update.message.chat_id, code)

    await update.message.reply_text(
        f"🎮 *Ludo Club Code Detected\\!*\n\n"
        f"Tap to copy → `{code}`",
        parse_mode="MarkdownV2",
        quote=True,
    )


def register(app: Application) -> None:
    """Attach the Ludo code extractor to group and supergroup messages."""
    app.add_handler(
        MessageHandler(
            (filters.ChatType.GROUPS | filters.ChatType.SUPERGROUP) & _LUDO_FILTER,
            ludo_handler,
        )
    )

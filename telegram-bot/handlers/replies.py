"""
handlers/replies.py — Interactive auto-reply system.

Flow (triggered by "اضافه رد"):
  Step 1 — Bot asks for the trigger word.
  Step 2 — Bot asks for the reply text.
  Step 3 — Saved to DB; bot confirms.

Permissions:
  • Group chat : ANY member may add a reply (group-scoped, chat_id = group).
  • Private DM : ONLY the bot admin (BOT_ADMIN_ID) may add replies (global scope,
                 chat_id = 0 → matches in every group).

Auto-reply:
  Every group message is checked against saved triggers (substring, case-insensitive).
  Group-specific triggers take priority over global ones.
"""

import logging
import re

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)

import db
from config import BOT_ADMIN_ID

logger = logging.getLogger(__name__)

# ── Conversation states ────────────────────────────────────────────────────────
WAITING_FOR_TRIGGER  = 1
WAITING_FOR_RESPONSE = 2

# ── Filters ───────────────────────────────────────────────────────────────────
_ADD_REPLY_RE     = re.compile(r"اضافه\s+رد")
_CANCEL_RE        = re.compile(r"^(الغاء|/cancel)$")
_ADD_REPLY_FILTER = filters.Regex(_ADD_REPLY_RE)
_CANCEL_FILTER    = filters.Regex(_CANCEL_RE)

_GROUP_FILTER   = filters.ChatType.GROUPS | filters.ChatType.SUPERGROUP
_PRIVATE_FILTER = filters.ChatType.PRIVATE


# ── Permission helper ─────────────────────────────────────────────────────────

def _is_bot_admin(user_id: int) -> bool:
    return BOT_ADMIN_ID != 0 and user_id == BOT_ADMIN_ID


# ── Conversation entry ────────────────────────────────────────────────────────

async def start_add_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Entry point: user typed 'اضافه رد'."""
    msg  = update.message
    chat = msg.chat

    # Private DM: only bot admin allowed
    if chat.type == "private" and not _is_bot_admin(msg.from_user.id):
        await msg.reply_text(
            "🚫 هذا الأمر متاح للمسؤول فقط في المحادثات الخاصة.\n"
            "يمكنك استخدامه داخل المجموعات."
        )
        return ConversationHandler.END

    await msg.reply_text("اكتب الكلمة المراد إضافة رد لها:")
    return WAITING_FOR_TRIGGER


# ── Step 1: receive trigger word ──────────────────────────────────────────────

async def receive_trigger(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    trigger = (update.message.text or "").strip()
    if not trigger:
        await update.message.reply_text("❌ الكلمة فارغة، اكتبها مجدداً:")
        return WAITING_FOR_TRIGGER

    context.user_data["reply_trigger"] = trigger
    await update.message.reply_text("اكتب الرد المطلوب:")
    return WAITING_FOR_RESPONSE


# ── Step 2: receive response text ─────────────────────────────────────────────

async def receive_response(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    response = (update.message.text or "").strip()
    trigger  = context.user_data.pop("reply_trigger", "")

    if not response or not trigger:
        await update.message.reply_text(
            "❌ حدث خطأ، حاول مجدداً بكتابة: اضافه رد"
        )
        return ConversationHandler.END

    msg  = update.message
    chat = msg.chat

    # Group replies are scoped to that group.
    # Private DM replies are global (chat_id = 0) — they fire in every group.
    scope_chat_id = 0 if chat.type == "private" else chat.id
    db.upsert_reply(scope_chat_id, trigger, response)

    scope_label = "عامة (تنطبق على كل المجموعات)" if scope_chat_id == 0 else "خاصة بهذه المجموعة"
    await msg.reply_text(
        f"✅ تم حفظ الرد بنجاح!\n"
        f"الكلمة: {trigger}\n"
        f"الرد: {response}\n"
        f"النطاق: {scope_label}"
    )
    logger.info("Reply saved — trigger=%r scope=%s", trigger, scope_chat_id)
    return ConversationHandler.END


# ── Cancellation ──────────────────────────────────────────────────────────────

async def cancel_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.pop("reply_trigger", None)
    await update.message.reply_text("❌ تم إلغاء إضافة الرد.")
    return ConversationHandler.END


# ── Auto-reply ────────────────────────────────────────────────────────────────

async def auto_reply_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Fires on every group text message.
    Checks message text against saved triggers (substring, case-insensitive).
    Group-specific triggers take priority over global ones.
    """
    msg = update.message
    if not msg or not msg.text:
        return

    response = db.find_reply(msg.chat.id, msg.text)
    if response:
        await msg.reply_text(response)
        logger.debug("Auto-replied in chat %s", msg.chat.id)


# ── Registration ──────────────────────────────────────────────────────────────

def register(app: Application) -> None:
    """Attach the reply conversation handler and the auto-reply listener."""

    # ConversationHandler works in both groups and private DMs
    entry_filter = (_GROUP_FILTER | _PRIVATE_FILTER) & _ADD_REPLY_FILTER

    conv = ConversationHandler(
        entry_points=[
            MessageHandler(entry_filter, start_add_reply),
        ],
        states={
            WAITING_FOR_TRIGGER: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND & ~_CANCEL_FILTER,
                    receive_trigger,
                ),
            ],
            WAITING_FOR_RESPONSE: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND & ~_CANCEL_FILTER,
                    receive_response,
                ),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cancel_reply),
            MessageHandler(_CANCEL_FILTER, cancel_reply),
        ],
        # Conversation is per (chat, user) — each user has their own flow
        per_user=True,
        per_chat=True,
        # Allow the conversation to continue even if other handlers run
        allow_reentry=True,
    )

    # Priority 0 (default) — ConversationHandler intercepts first
    app.add_handler(conv)

    # Auto-reply runs at group=1 so ConversationHandler (group=0) wins during
    # active flows and the trigger text itself doesn't fire an auto-reply.
    app.add_handler(
        MessageHandler(
            _GROUP_FILTER & filters.TEXT & ~filters.COMMAND,
            auto_reply_handler,
        ),
        group=1,
    )

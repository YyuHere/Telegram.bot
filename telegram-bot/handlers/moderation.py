"""
handlers/moderation.py — Group administration & moderation commands.

Supports both Arabic text triggers and English slash commands.
All actions are admin-only and protected against targeting other admins or the bot.

Arabic triggers (standalone text OR as a reply):
  بان / حظر              → Ban
  الغاء الحظر / الغاء بان → Unban
  طرد                    → Kick (ban + immediate unban so they can rejoin)
  كتم                    → Mute
  الغاء الكتم / الغاء كتم → Unmute

English slash commands (reply to a message OR include @username / user_id):
  /ban, /unban, /kick, /mute, /unmute
"""

import re
import logging
from telegram import Update, ChatPermissions, ChatMember
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)
from telegram.error import TelegramError

logger = logging.getLogger(__name__)

# ── Arabic trigger patterns ────────────────────────────────────────────────────
# Anchored to the START of the message so casual mid-sentence mentions are ignored.

_AR_BAN    = re.compile(r"^(بان|حظر)\b", re.IGNORECASE)
_AR_UNBAN  = re.compile(r"^(الغاء\s+الحظر|الغاء\s+بان)\b", re.IGNORECASE)
_AR_KICK   = re.compile(r"^طرد\b", re.IGNORECASE)
_AR_MUTE   = re.compile(r"^كتم\b", re.IGNORECASE)
_AR_UNMUTE = re.compile(r"^(الغاء\s+الكتم|الغاء\s+كتم)\b", re.IGNORECASE)

_AR_MOD_FILTER = filters.Regex(
    re.compile(
        r"^(بان|حظر|الغاء\s+الحظر|الغاء\s+بان|طرد|كتم|الغاء\s+الكتم|الغاء\s+كتم)\b",
        re.IGNORECASE,
    )
)

# Regex to find @username anywhere in plain text (Latin letters / digits / underscore)
_MENTION_RE = re.compile(r"@([A-Za-z][A-Za-z0-9_]{3,31})")

# ── Permission helpers ─────────────────────────────────────────────────────────

ADMIN_STATUSES = {ChatMember.ADMINISTRATOR, ChatMember.OWNER}


async def _is_admin(bot, chat_id: int, user_id: int) -> bool:
    """Return True if *user_id* is an admin or creator of *chat_id*."""
    try:
        member = await bot.get_chat_member(chat_id, user_id)
        return member.status in ADMIN_STATUSES
    except TelegramError:
        return False


async def _get_target(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Resolve the moderation target user from the update.

    Resolution order (most-reliable first):
      1. Replied-to message author.
      2. Telegram `mention` entity — uses parse_entity() which correctly handles
         UTF-16 offsets, so Arabic text before @username doesn't corrupt the slice.
      3. Telegram `text_mention` entity (tappable name for users without a username).
      4. Regex search for @username anywhere in the raw text (safety fallback).
      5. Bare numeric user-ID in the text.

    Returns a User/Chat object with .id and .first_name, or None.
    """
    msg = update.message

    # ── 1. Reply-to ───────────────────────────────────────────────────────────
    if msg.reply_to_message and msg.reply_to_message.from_user:
        return msg.reply_to_message.from_user

    # ── 2 & 3. Entity scan ───────────────────────────────────────────────────
    # parse_entity() uses the Bot API's UTF-16 offset/length correctly, meaning
    # Arabic characters (which are multi-byte in UTF-16) before a @mention will
    # not shift the slice and produce a garbled result.
    for entity in msg.entities or []:
        if entity.type == "mention":
            raw = msg.parse_entity(entity)        # e.g. "@username"
            username = raw.lstrip("@").strip()
            if username:
                try:
                    return await context.bot.get_chat(f"@{username}")
                except TelegramError as exc:
                    logger.debug("Could not resolve @%s: %s", username, exc)

        elif entity.type == "text_mention" and entity.user:
            # Users without a public username are referenced via text_mention
            return entity.user

    # ── 4. Regex fallback ─────────────────────────────────────────────────────
    # Handles edge cases: Telegram occasionally omits mention entities for
    # some clients, or the username is typed without triggering an entity.
    text = msg.text or msg.caption or ""
    m = _MENTION_RE.search(text)
    if m:
        username = m.group(1).strip()
        try:
            return await context.bot.get_chat(f"@{username}")
        except TelegramError as exc:
            logger.debug("Regex fallback: could not resolve @%s: %s", username, exc)

    # ── 5. Bare numeric user-ID ───────────────────────────────────────────────
    # Useful when an admin types: بان 123456789
    num_match = re.search(r"\b(\d{5,12})\b", text)
    if num_match:
        try:
            uid = int(num_match.group(1))
            return await context.bot.get_chat(uid)
        except TelegramError:
            pass

    return None


# ── Pre-condition guard ────────────────────────────────────────────────────────

async def _check_preconditions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Verify:
      - Command is used inside a group / supergroup.
      - Invoker is an admin or group creator.
      - A valid target can be resolved.
      - Target is not an admin, creator, or the bot itself.

    Returns (target, None) on success or (None, error_str) on failure.
    """
    msg = update.message
    chat = msg.chat
    invoker = msg.from_user
    bot_user = await context.bot.get_me()

    if chat.type not in ("group", "supergroup"):
        return None, "⚠️ هذا الأمر يعمل فقط داخل المجموعات.\n_This command only works in groups._"

    if not await _is_admin(context.bot, chat.id, invoker.id):
        return None, "🚫 هذا الأمر للمشرفين فقط.\n_Only group admins can use this command._"

    target = await _get_target(update, context)
    if target is None:
        return None, (
            "❓ لم يتم تحديد المستخدم.\n"
            "_Reply to their message, or write:_ `الأمر @username`"
        )

    if target.id == bot_user.id:
        return None, "😅 لا أستطيع تطبيق هذا الأمر على نفسي!\n_I can't apply this to myself!_"

    if await _is_admin(context.bot, chat.id, target.id):
        return None, "🛡 لا يمكن تطبيق هذا الأمر على المشرفين.\n_You cannot target an admin with this command._"

    return target, None


def _mention(user) -> str:
    """Build a Markdown inline mention for *user*."""
    name = getattr(user, "first_name", None) or getattr(user, "username", None) or str(user.id)
    # Escape special MarkdownV1 chars in the name
    name = name.replace("[", "\\[").replace("`", "\\`")
    return f"[{name}](tg://user?id={user.id})"


# ── Individual action handlers ─────────────────────────────────────────────────

async def _handle_ban(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    target, err = await _check_preconditions(update, context)
    if err:
        await update.message.reply_text(err, parse_mode="Markdown")
        return
    try:
        await context.bot.ban_chat_member(update.message.chat.id, target.id)
        await update.message.reply_text(
            f"🔨 تم حظر {_mention(target)} من المجموعة.\n_User has been banned._",
            parse_mode="Markdown",
        )
        logger.info("Banned user %s in chat %s", target.id, update.message.chat.id)
    except TelegramError as exc:
        await update.message.reply_text(f"❌ فشل الأمر: `{exc}`", parse_mode="Markdown")


async def _handle_unban(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    target, err = await _check_preconditions(update, context)
    if err:
        await update.message.reply_text(err, parse_mode="Markdown")
        return
    try:
        await context.bot.unban_chat_member(update.message.chat.id, target.id, only_if_banned=True)
        await update.message.reply_text(
            f"✅ تم رفع الحظر عن {_mention(target)}.\n_User has been unbanned._",
            parse_mode="Markdown",
        )
        logger.info("Unbanned user %s in chat %s", target.id, update.message.chat.id)
    except TelegramError as exc:
        await update.message.reply_text(f"❌ فشل الأمر: `{exc}`", parse_mode="Markdown")


async def _handle_kick(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    target, err = await _check_preconditions(update, context)
    if err:
        await update.message.reply_text(err, parse_mode="Markdown")
        return
    try:
        await context.bot.ban_chat_member(update.message.chat.id, target.id)
        await context.bot.unban_chat_member(update.message.chat.id, target.id)
        await update.message.reply_text(
            f"👢 تم طرد {_mention(target)} من المجموعة.\n"
            f"_User has been kicked. They may rejoin via invite link._",
            parse_mode="Markdown",
        )
        logger.info("Kicked user %s in chat %s", target.id, update.message.chat.id)
    except TelegramError as exc:
        await update.message.reply_text(f"❌ فشل الأمر: `{exc}`", parse_mode="Markdown")


async def _handle_mute(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    target, err = await _check_preconditions(update, context)
    if err:
        await update.message.reply_text(err, parse_mode="Markdown")
        return
    try:
        await context.bot.restrict_chat_member(
            update.message.chat.id,
            target.id,
            permissions=ChatPermissions(
                can_send_messages=False,
                can_send_polls=False,
                can_send_other_messages=False,
                can_add_web_page_previews=False,
                can_change_info=False,
                can_invite_users=False,
                can_pin_messages=False,
            ),
        )
        await update.message.reply_text(
            f"🔇 تم كتم {_mention(target)}.\n_User has been muted._",
            parse_mode="Markdown",
        )
        logger.info("Muted user %s in chat %s", target.id, update.message.chat.id)
    except TelegramError as exc:
        await update.message.reply_text(f"❌ فشل الأمر: `{exc}`", parse_mode="Markdown")


async def _handle_unmute(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    target, err = await _check_preconditions(update, context)
    if err:
        await update.message.reply_text(err, parse_mode="Markdown")
        return
    try:
        await context.bot.restrict_chat_member(
            update.message.chat.id,
            target.id,
            permissions=ChatPermissions(
                can_send_messages=True,
                can_send_polls=True,
                can_send_other_messages=True,
                can_add_web_page_previews=True,
                can_change_info=False,
                can_invite_users=True,
                can_pin_messages=False,
            ),
        )
        await update.message.reply_text(
            f"🔊 تم إلغاء كتم {_mention(target)}.\n_User has been unmuted._",
            parse_mode="Markdown",
        )
        logger.info("Unmuted user %s in chat %s", target.id, update.message.chat.id)
    except TelegramError as exc:
        await update.message.reply_text(f"❌ فشل الأمر: `{exc}`", parse_mode="Markdown")


# ── Arabic trigger dispatcher ──────────────────────────────────────────────────

async def arabic_trigger_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Route Arabic trigger words to the matching action handler."""
    text = (update.message.text or "").strip()

    if _AR_BAN.match(text):
        await _handle_ban(update, context)
    elif _AR_UNBAN.match(text):
        await _handle_unban(update, context)
    elif _AR_KICK.match(text):
        await _handle_kick(update, context)
    elif _AR_MUTE.match(text):
        await _handle_mute(update, context)
    elif _AR_UNMUTE.match(text):
        await _handle_unmute(update, context)


# ── Registration ───────────────────────────────────────────────────────────────

def register(app: Application) -> None:
    """Attach all moderation handlers to the application."""
    group_filter = filters.ChatType.GROUPS | filters.ChatType.SUPERGROUP

    # English slash commands
    app.add_handler(CommandHandler("ban",    _handle_ban,    filters=group_filter))
    app.add_handler(CommandHandler("unban",  _handle_unban,  filters=group_filter))
    app.add_handler(CommandHandler("kick",   _handle_kick,   filters=group_filter))
    app.add_handler(CommandHandler("mute",   _handle_mute,   filters=group_filter))
    app.add_handler(CommandHandler("unmute", _handle_unmute, filters=group_filter))

    # Arabic text triggers
    app.add_handler(
        MessageHandler(group_filter & _AR_MOD_FILTER, arabic_trigger_handler)
    )

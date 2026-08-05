"""
handlers/moderation.py — Group administration & moderation commands.

Supports Arabic text triggers and English slash commands.

User extraction order (applied to EVERY command):
  1. reply_to_message.from_user  — most reliable
  2. message.entities scan       — MENTION (@username) or TEXT_MENTION (inline user)
  3. text.split() scan           — any word starting with @ or a bare numeric user-ID

Inline undo buttons:
  • Mute  → "إلغاء كتم"  button  (callback: unmute_{user_id})
  • Ban   → "إلغاء الحظر" button  (callback: unban_{user_id})

Callback buttons only work for group admins.
"""

import re
import logging

from telegram import (
    Update,
    ChatPermissions,
    ChatMember,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)
from telegram.error import TelegramError

logger = logging.getLogger(__name__)

# ── Member cache ──────────────────────────────────────────────────────────────
# Bot API has no get_chat_members() equivalent. We build a per-chat cache
# organically: every message sent in a group stores the sender. Moderation
# commands can then search this cache by first name / full name.
# Structure: { chat_id: { user_id: User } }
_member_cache: dict[int, dict] = {}


def _cache_user(chat_id: int, user) -> None:
    """Store *user* in the cache for *chat_id*."""
    if not user or not user.id:
        return
    _member_cache.setdefault(chat_id, {})[user.id] = user


def _cache_search_username(chat_id: int, username: str):
    """Return a cached user whose username matches (case-insensitive), or None."""
    uname_lower = username.lower()
    for user in _member_cache.get(chat_id, {}).values():
        if (user.username or "").lower() == uname_lower:
            return user
    return None


def _cache_search_name(chat_id: int, query: str):
    """
    Return the first cached user whose first name or full name
    contains *query* (case-insensitive), or None.
    """
    q = query.lower()
    for user in _member_cache.get(chat_id, {}).values():
        first = (getattr(user, "first_name", None) or "").lower()
        last  = (getattr(user, "last_name",  None) or "").lower()
        full  = f"{first} {last}".strip()
        if q in full or q in first:
            return user
    return None


async def update_member_cache(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    MessageHandler that silently caches every group message sender.
    Registered with a low priority so it never interferes with other handlers.
    """
    msg = update.message
    if msg and msg.from_user:
        _cache_user(msg.chat.id, msg.from_user)


# ── Arabic trigger patterns ────────────────────────────────────────────────────

_AR_BAN    = re.compile(r"^(بان|حظر)\b")
_AR_UNBAN  = re.compile(r"^(الغاء\s+الحظر|الغاء\s+بان)\b")
_AR_KICK   = re.compile(r"^طرد\b")
_AR_MUTE   = re.compile(r"^كتم\b")
_AR_UNMUTE = re.compile(r"^(الغاء\s+الكتم|الغاء\s+كتم)\b")

_AR_MOD_FILTER = filters.Regex(
    re.compile(
        r"^(بان|حظر|الغاء\s+الحظر|الغاء\s+بان|طرد|كتم|الغاء\s+الكتم|الغاء\s+كتم)\b"
    )
)

ADMIN_STATUSES = {ChatMember.ADMINISTRATOR, ChatMember.OWNER}

# ── Admin helper ───────────────────────────────────────────────────────────────

async def _is_admin(bot, chat_id: int, user_id: int) -> bool:
    try:
        member = await bot.get_chat_member(chat_id, user_id)
        return member.status in ADMIN_STATUSES
    except TelegramError:
        return False

# ── Target extraction (3-priority) ────────────────────────────────────────────

async def _get_target(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Resolve the moderation target — works for users WITH and WITHOUT a @username.

    Priority 1 — Reply:
        reply_to_message.from_user. Always reliable, zero parsing needed.

    Priority 2 — Entity scan (TEXT_MENTION + MENTION):
        TEXT_MENTION: Telegram embeds the User object directly — the only
            reliable way to target users who have no @username set.
        MENTION: parse_entity() extracts the @username using UTF-16 offsets,
            immune to Arabic BiDi reordering. Falls back to member cache search
            if the Bot API can't resolve the username (e.g. privacy settings).

    Priority 3 — Text search after stripping the command word:
        Remove the trigger (كتم, /mute, بان…) from the raw text, then check
        the remainder:
          • @username    → Bot API lookup, then cache fallback
          • all-digits   → numeric user-ID lookup
          • anything else → display-name search in the member cache
            (cache is filled organically by update_member_cache handler)
        BiDi control characters (U+200B–U+200F, U+202A–U+202E, etc.) are
        stripped from every token before comparison.
    """
    msg = update.message

    # Invisible Unicode chars Telegram injects around LTR text in RTL context
    _BIDI = (
        "\u200b\u200c\u200d\u200e\u200f"
        "\u202a\u202b\u202c\u202d\u202e"
        "\u2066\u2067\u2068\u2069\ufeff"
    )

    # ── Priority 1: reply ─────────────────────────────────────────────────────
    if msg.reply_to_message and msg.reply_to_message.from_user:
        return msg.reply_to_message.from_user

    # ── Priority 2: entity scan ───────────────────────────────────────────────
    for entity in msg.entities or []:

        # TEXT_MENTION — user without @username; Telegram embeds User directly
        if entity.type == "text_mention" and entity.user:
            return entity.user

        # MENTION — @username present in text
        if entity.type == "mention":
            raw      = msg.parse_entity(entity)            # e.g. "@username"
            username = raw.lstrip("@").strip(_BIDI).strip()
            if not username:
                continue
            try:
                return await context.bot.get_chat(f"@{username}")
            except Exception as exc:
                logger.debug("entity MENTION @%s API failed: %s — trying cache", username, exc)
                # Fallback: maybe the user is in our member cache
                cached = _cache_search_username(msg.chat.id, username)
                if cached:
                    return cached

    # ── Priority 3: text search after stripping command word ──────────────────
    # Remove the trigger word / command so what remains is the search query.
    _COMMANDS = [
        "الغاء الكتم", "الغاء كتم",
        "الغاء الحظر", "الغاء بان",
        "كتم", "بان", "حظر", "طرد",
        "/unmute", "/unban", "/mute", "/ban", "/kick",
    ]
    search_query = (msg.text or "").strip(_BIDI)
    for cmd in _COMMANDS:
        if search_query.startswith(cmd):
            search_query = search_query[len(cmd):].strip(_BIDI).strip()
            break

    if not search_query:
        return None

    # Strip BiDi chars from the whole query too
    search_query = search_query.strip(_BIDI).strip()

    # @username
    if search_query.startswith("@"):
        username = search_query.lstrip("@").strip(_BIDI).strip()
        if username:
            try:
                return await context.bot.get_chat(f"@{username}")
            except Exception as exc:
                logger.debug("text @%s API failed: %s — trying cache", username, exc)
                cached = _cache_search_username(msg.chat.id, username)
                if cached:
                    return cached

    # Numeric user-ID
    elif search_query.isdigit():
        try:
            return await context.bot.get_chat(int(search_query))
        except Exception as exc:
            logger.debug("text user-id %s failed: %s", search_query, exc)

    # Display-name search in member cache (users without @username)
    else:
        found = _cache_search_name(msg.chat.id, search_query)
        if found:
            logger.debug("Resolved '%s' via name cache → %s", search_query, found.id)
            return found

    return None

# ── Pre-condition guard ────────────────────────────────────────────────────────

async def _check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Validate context before executing any moderation action.
    Returns (target_user, None) on success or (None, error_str) on failure.
    """
    msg   = update.message
    chat  = msg.chat
    bot_u = await context.bot.get_me()

    if chat.type not in ("group", "supergroup"):
        return None, "⚠️ هذا الأمر يعمل فقط داخل المجموعات."

    if not await _is_admin(context.bot, chat.id, msg.from_user.id):
        return None, "🚫 هذا الأمر للمشرفين فقط."

    target = await _get_target(update, context)
    if target is None:
        return None, "✨ لم يتم العثور على المستخدم"

    if target.id == bot_u.id:
        return None, "😅 لا أستطيع تطبيق هذا الأمر على نفسي!"

    if await _is_admin(context.bot, chat.id, target.id):
        return None, "🛡 لا يمكن تطبيق هذا الأمر على المشرفين."

    return target, None

# ── Mention helper ────────────────────────────────────────────────────────────

def _mention(user) -> str:
    name = (
        getattr(user, "first_name", None)
        or getattr(user, "username", None)
        or str(user.id)
    )
    return f"[{name}](tg://user?id={user.id})"

# ── Inline keyboard builders ──────────────────────────────────────────────────

def _unmute_kb(user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🔊 إلغاء كتم", callback_data=f"unmute_{user_id}")
    ]])

def _unban_kb(user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ إلغاء الحظر", callback_data=f"unban_{user_id}")
    ]])

# ── Action handlers ────────────────────────────────────────────────────────────

async def _handle_ban(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    target, err = await _check(update, context)
    if err:
        await update.message.reply_text(err)
        return
    try:
        await context.bot.ban_chat_member(update.message.chat.id, target.id)
        await update.message.reply_text(
            f"✅ تم حظر المستخدم بنجاح\n{_mention(target)}",
            parse_mode="Markdown",
            reply_markup=_unban_kb(target.id),
        )
        logger.info("Banned %s in chat %s", target.id, update.message.chat.id)
    except TelegramError as exc:
        await update.message.reply_text(f"❌ فشل الأمر: `{exc}`", parse_mode="Markdown")


async def _handle_unban(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    target, err = await _check(update, context)
    if err:
        await update.message.reply_text(err)
        return
    try:
        await context.bot.unban_chat_member(update.message.chat.id, target.id, only_if_banned=True)
        await update.message.reply_text(
            f"✅ تم رفع الحظر عن {_mention(target)} بنجاح",
            parse_mode="Markdown",
        )
        logger.info("Unbanned %s in chat %s", target.id, update.message.chat.id)
    except TelegramError as exc:
        await update.message.reply_text(f"❌ فشل الأمر: `{exc}`", parse_mode="Markdown")


async def _handle_kick(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    target, err = await _check(update, context)
    if err:
        await update.message.reply_text(err)
        return
    try:
        await context.bot.ban_chat_member(update.message.chat.id, target.id)
        await context.bot.unban_chat_member(update.message.chat.id, target.id)
        await update.message.reply_text(
            f"👢 تم طرد {_mention(target)} من المجموعة\n"
            "_يمكنه العودة عبر رابط دعوة_",
            parse_mode="Markdown",
        )
        logger.info("Kicked %s in chat %s", target.id, update.message.chat.id)
    except TelegramError as exc:
        await update.message.reply_text(f"❌ فشل الأمر: `{exc}`", parse_mode="Markdown")


async def _handle_mute(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    target, err = await _check(update, context)
    if err:
        await update.message.reply_text(err)
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
            f"✅ تم كتم المستخدم بنجاح\n{_mention(target)}",
            parse_mode="Markdown",
            reply_markup=_unmute_kb(target.id),
        )
        logger.info("Muted %s in chat %s", target.id, update.message.chat.id)
    except TelegramError as exc:
        await update.message.reply_text(f"❌ فشل الأمر: `{exc}`", parse_mode="Markdown")


async def _handle_unmute(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    target, err = await _check(update, context)
    if err:
        await update.message.reply_text(err)
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
            f"🔊 تم إلغاء كتم {_mention(target)} بنجاح",
            parse_mode="Markdown",
        )
        logger.info("Unmuted %s in chat %s", target.id, update.message.chat.id)
    except TelegramError as exc:
        await update.message.reply_text(f"❌ فشل الأمر: `{exc}`", parse_mode="Markdown")

# ── Inline button callbacks ────────────────────────────────────────────────────

async def _cb_unmute(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handles the 'إلغاء كتم' inline button.
    Only group admins may use it. Edits the original message to confirm.
    """
    query   = update.callback_query
    chat_id = query.message.chat.id
    admin   = query.from_user

    await query.answer()  # acknowledge immediately to stop the loading spinner

    # Admin check
    if not await _is_admin(context.bot, chat_id, admin.id):
        await query.answer("🚫 هذا الخيار للمشرفين فقط.", show_alert=True)
        return

    # Parse target user-ID from callback data: "unmute_<user_id>"
    try:
        target_id = int(query.data.split("_", 1)[1])
    except (IndexError, ValueError):
        await query.answer("❌ بيانات غير صالحة.", show_alert=True)
        return

    try:
        await context.bot.restrict_chat_member(
            chat_id,
            target_id,
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
        admin_name = admin.first_name or admin.username or str(admin.id)
        # Edit original message — remove button, show confirmation
        await query.edit_message_text(
            f"🔊 تم إلغاء الكتم بواسطة [{admin_name}](tg://user?id={admin.id})",
            parse_mode="Markdown",
            reply_markup=None,
        )
        logger.info("Unmuted %s via button by admin %s in chat %s", target_id, admin.id, chat_id)
    except TelegramError as exc:
        await query.answer(f"❌ فشل: {exc}", show_alert=True)


async def _cb_unban(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handles the 'إلغاء الحظر' inline button.
    Only group admins may use it. Edits the original message to confirm.
    """
    query   = update.callback_query
    chat_id = query.message.chat.id
    admin   = query.from_user

    await query.answer()

    if not await _is_admin(context.bot, chat_id, admin.id):
        await query.answer("🚫 هذا الخيار للمشرفين فقط.", show_alert=True)
        return

    try:
        target_id = int(query.data.split("_", 1)[1])
    except (IndexError, ValueError):
        await query.answer("❌ بيانات غير صالحة.", show_alert=True)
        return

    try:
        await context.bot.unban_chat_member(chat_id, target_id, only_if_banned=True)
        admin_name = admin.first_name or admin.username or str(admin.id)
        await query.edit_message_text(
            f"✅ تم رفع الحظر بواسطة [{admin_name}](tg://user?id={admin.id})",
            parse_mode="Markdown",
            reply_markup=None,
        )
        logger.info("Unbanned %s via button by admin %s in chat %s", target_id, admin.id, chat_id)
    except TelegramError as exc:
        await query.answer(f"❌ فشل: {exc}", show_alert=True)

# ── Arabic trigger dispatcher ──────────────────────────────────────────────────

async def arabic_trigger_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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
    """Attach all moderation handlers and callback query handlers."""
    group_filter = filters.ChatType.GROUPS | filters.ChatType.SUPERGROUP

    # Slash commands
    app.add_handler(CommandHandler("ban",    _handle_ban,    filters=group_filter))
    app.add_handler(CommandHandler("unban",  _handle_unban,  filters=group_filter))
    app.add_handler(CommandHandler("kick",   _handle_kick,   filters=group_filter))
    app.add_handler(CommandHandler("mute",   _handle_mute,   filters=group_filter))
    app.add_handler(CommandHandler("unmute", _handle_unmute, filters=group_filter))

    # Arabic text triggers
    app.add_handler(MessageHandler(group_filter & _AR_MOD_FILTER, arabic_trigger_handler))

    # Inline button callbacks
    app.add_handler(CallbackQueryHandler(_cb_unmute, pattern=r"^unmute_\d+$"))
    app.add_handler(CallbackQueryHandler(_cb_unban,  pattern=r"^unban_\d+$"))

    # Member cache — runs on EVERY group message (group=-1 = lowest priority,
    # so it never blocks moderation handlers from running first)
    app.add_handler(
        MessageHandler(group_filter & filters.ALL, update_member_cache),
        group=-1,
    )

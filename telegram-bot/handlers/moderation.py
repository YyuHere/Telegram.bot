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
    ChatMemberRestricted,
    ChatMemberBanned,
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

import db
from config import ASSISTANT_ENABLED

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
    MessageHandler: persists every group message sender to SQLite + in-memory cache.
    Registered at group=-1 so it never blocks moderation handlers.
    """
    msg = update.message
    if msg and msg.from_user:
        u = msg.from_user
        _cache_user(msg.chat.id, u)
        db.upsert_member(msg.chat.id, u.id, u.username, u.first_name)


async def on_new_chat_member(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Fires when someone joins the group. Saves them to DB immediately —
    BEFORE they ever send a message — so the bot can mute/ban them right away.
    """
    msg = update.message
    if not msg or not msg.new_chat_members:
        return
    for user in msg.new_chat_members:
        if not user.is_bot:
            _cache_user(msg.chat.id, user)
            db.upsert_member(msg.chat.id, user.id, user.username, user.first_name)
            logger.debug("Saved on join: %s (@%s) in chat %s", user.id, user.username, msg.chat.id)


# ── Arabic trigger patterns ────────────────────────────────────────────────────

_AR_BAN    = re.compile(r"^(بان|حظر)\b")
_AR_UNBAN  = re.compile(r"^(الغاء\s+الحظر|الغاء\s+بان)\b")
_AR_KICK   = re.compile(r"^طرد\b")
_AR_MUTE   = re.compile(r"^كتم\b")
_AR_UNMUTE = re.compile(r"^(الغاء\s+الكتم|الغاء\s+كتم)\b")
_AR_SYNC   = re.compile(r"^تحديث\s+الأعضاء\b")

_AR_MOD_FILTER = filters.Regex(
    re.compile(
        r"^(بان|حظر|الغاء\s+الحظر|الغاء\s+بان|طرد|كتم|الغاء\s+الكتم|الغاء\s+كتم)\b"
    )
)
_AR_SYNC_FILTER = filters.Regex(_AR_SYNC)

ADMIN_STATUSES = {ChatMember.ADMINISTRATOR, ChatMember.OWNER}

# ── Admin helper ───────────────────────────────────────────────────────────────

async def _is_admin(bot, chat_id: int, user_id: int) -> bool:
    try:
        member = await bot.get_chat_member(chat_id, user_id)
        return member.status in ADMIN_STATUSES
    except TelegramError:
        return False


async def _is_muted(bot, chat_id: int, user_id: int) -> bool:
    """Return True if the user is already restricted (cannot send messages)."""
    try:
        member = await bot.get_chat_member(chat_id, user_id)
        if isinstance(member, ChatMemberRestricted):
            return not member.can_send_messages
    except TelegramError:
        pass
    return False


async def _is_banned(bot, chat_id: int, user_id: int) -> bool:
    """Return True if the user is already banned from the chat."""
    try:
        member = await bot.get_chat_member(chat_id, user_id)
        return isinstance(member, ChatMemberBanned)
    except TelegramError:
        pass
    return False

# ── Target extraction (3-priority) ────────────────────────────────────────────

async def _resolve_username(bot, chat_id: int, username: str):
    """
    Resolve a @username to a User — works even for members who have NEVER typed.

    Bot API's get_chat_member(chat_id, user_id) is chat-scoped: Telegram resolves
    it server-side without requiring the bot to have the user cached locally.
    This is the equivalent of Pyrogram's get_chat_member(chat_id, "@username"),
    which fixes the PeerIdInvalid error on silent members.

    Steps (mirror the Pyrogram spec):
    A. get_chat("@username") → get_chat_member(chat_id, user.id)
       Two-step because Bot API get_chat_member only accepts integer user_id.
       get_chat_member is the authoritative chat-scoped lookup.
    B. get_chat("@username") alone — user exists globally but membership check failed.
    C. Admin list search — always accessible, no privacy restrictions.
    D. Member cache — organic fallback from group traffic.
    """
    uname_lower = username.lower()

    # Step A: resolve username globally via Bot API, then confirm via get_chat_member.
    # get_chat_member is chat-scoped — works for silent members Pyrogram-style.
    # We SAVE the user to DB immediately so every future lookup is instant.
    try:
        chat_user = await bot.get_chat(f"@{username}")
        # Persist right away — this is the most reliable data we have
        db.upsert_member(
            chat_id,
            chat_user.id,
            getattr(chat_user, "username", None),
            getattr(chat_user, "first_name", None),
        )
        _cache_user(chat_id, chat_user)
        try:
            member = await bot.get_chat_member(chat_id, chat_user.id)
            user = getattr(member, "user", chat_user)
            # Update DB with richer User object from ChatMember
            db.upsert_member(chat_id, user.id, user.username, user.first_name)
            _cache_user(chat_id, user)
            return user
        except Exception:
            return chat_user
    except Exception as exc:
        logger.debug("get_chat @%s failed: %s", username, exc)

    # Step B: admin list (always accessible, no privacy restrictions)
    try:
        admins = await bot.get_chat_administrators(chat_id)
        for admin in admins:
            if (admin.user.username or "").lower() == uname_lower:
                db.upsert_member(chat_id, admin.user.id, admin.user.username, admin.user.first_name)
                _cache_user(chat_id, admin.user)
                return admin.user
    except Exception as exc:
        logger.debug("get_chat_administrators for @%s failed: %s", username, exc)

    # Step C: SQLite DB — persists members who joined but never typed a message
    db_uid = db.find_by_username(chat_id, username)
    if db_uid:
        try:
            member = await bot.get_chat_member(chat_id, db_uid)
            user = getattr(member, "user", None)
            if user:
                db.upsert_member(chat_id, user.id, user.username, user.first_name)
                _cache_user(chat_id, user)
                return user
        except Exception as exc:
            logger.debug("DB uid %s get_chat_member failed: %s", db_uid, exc)

    # Step D: in-memory cache (organic fallback — users who have sent messages)
    return _cache_search_username(chat_id, username)


async def _get_target(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Resolve the moderation target for كتم @username, /mute @username, etc.
    Works for ANY group member — including users who have NEVER typed a message.

    Order mirrors the Pyrogram spec, adapted for python-telegram-bot:

    1. Reply          — reply_to_message.from_user (always reliable, zero parsing)
    2. Regex @username — re.search anywhere in raw text (immune to Arabic BiDi).
                         Resolved via _resolve_username() — chat-scoped lookup first.
    3. Regex user-ID  — bare numeric ID: get_chat_member(chat_id, id) FIRST
                         (chat-scoped, works for silent members), then get_chat fallback.
    4. TEXT_MENTION   — Telegram-embedded User object for members with no @username.
    """
    msg  = update.message
    text = msg.text or ""

    # ── 1. Reply ──────────────────────────────────────────────────────────────
    if msg.reply_to_message and msg.reply_to_message.from_user:
        return msg.reply_to_message.from_user

    # ── 2. Regex @username → _resolve_username() ─────────────────────────────
    username_match = re.search(r"@([a-zA-Z0-9_]+)", text)
    if username_match:
        result = await _resolve_username(context.bot, msg.chat.id, username_match.group(1))
        if result:
            return result

    # ── 3. Regex numeric user-ID ─────────────────────────────────────────────
    # get_chat_member(chat_id, int_id) is tried FIRST — it's chat-scoped and
    # works even if the user has never sent a message (fixes PeerIdInvalid).
    id_match = re.search(r"\b(\d{6,15})\b", text)
    if id_match:
        user_id = int(id_match.group(1))
        try:
            member = await context.bot.get_chat_member(msg.chat.id, user_id)
            return getattr(member, "user", None)
        except Exception as exc:
            logger.debug("get_chat_member id %s failed: %s — trying get_chat", user_id, exc)
        try:
            return await context.bot.get_chat(user_id)
        except Exception as exc:
            logger.debug("get_chat id %s also failed: %s", user_id, exc)

    # ── 4. TEXT_MENTION — member with no @username ────────────────────────────
    for entity in msg.entities or []:
        if entity.type == "text_mention" and entity.user:
            return entity.user

    # ── 5. Name-based SQLite lookup ───────────────────────────────────────────
    # Strip the command/trigger word (first token) and try the remainder as a name.
    # e.g. "كتم Ahmed" → query "Ahmed"; "/mute John Doe" → query "John Doe"
    tokens = text.split()
    if len(tokens) > 1:
        name_query = " ".join(tokens[1:]).strip()
        if name_query:
            db_uid = db.find_by_name(msg.chat.id, name_query)
            if db_uid:
                try:
                    member = await context.bot.get_chat_member(msg.chat.id, db_uid)
                    return getattr(member, "user", None)
                except Exception as exc:
                    logger.debug("DB name lookup uid %s get_chat_member failed: %s", db_uid, exc)
            # Also try in-memory cache by name as a final fallback
            cached = _cache_search_name(msg.chat.id, name_query)
            if cached:
                return cached

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
    if await _is_banned(context.bot, update.message.chat.id, target.id):
        await update.message.reply_text("⚠️ هذا العضو محظور بالفعل!")
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
    if await _is_muted(context.bot, update.message.chat.id, target.id):
        await update.message.reply_text("⚠️ هذا العضو مكتوم بالفعل!")
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

# ── Full member scan (for /sync and تحديث الأعضاء) ───────────────────────────

async def _scan_members(bot, chat_id: int) -> tuple[int, str]:
    """
    Enumerate group members and save every one to SQLite.

    Returns (count_saved, method_used).

    Strategy:
      1. Pyrogram assistant.get_chat_members() — enumerates ALL members
         (requires ASSISTANT_SESSION_STRING, API_ID, API_HASH).
      2. Bot API get_chat_administrators() — admins only; always available.
    """
    # ── Strategy 1: Pyrogram full scan ────────────────────────────────────────
    if ASSISTANT_ENABLED:
        try:
            from assistant.userbot import assistant as _assistant
            if _assistant is not None:
                if not _assistant.is_connected:
                    await _assistant.start()
                count = 0
                async for member in _assistant.get_chat_members(chat_id):
                    user = member.user
                    if user and not user.is_bot:
                        db.upsert_member(chat_id, user.id, user.username, user.first_name)
                        _cache_user(chat_id, user)
                        count += 1
                logger.info("Full scan: saved %d members for chat %s", count, chat_id)
                return count, "full"
        except Exception as exc:
            logger.warning("Pyrogram full scan failed for chat %s: %s — falling back to admin scan", chat_id, exc)

    # ── Strategy 2: Bot API admin-only scan ───────────────────────────────────
    count = 0
    try:
        admins = await bot.get_chat_administrators(chat_id)
        for admin in admins:
            if not admin.user.is_bot:
                db.upsert_member(chat_id, admin.user.id, admin.user.username, admin.user.first_name)
                _cache_user(chat_id, admin.user)
                count += 1
        logger.info("Admin scan: saved %d admins for chat %s", count, chat_id)
    except Exception as exc:
        logger.warning("Admin scan failed for chat %s: %s", chat_id, exc)

    return count, "admins_only"


async def _handle_sync(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /sync or تحديث الأعضاء — scan all group members and save to DB.
    Admin-only. Reports how many members were saved and which method was used.
    """
    msg = update.message
    if msg.chat.type not in ("group", "supergroup"):
        await msg.reply_text("⚠️ هذا الأمر يعمل فقط داخل المجموعات.")
        return
    if not await _is_admin(context.bot, msg.chat.id, msg.from_user.id):
        await msg.reply_text("🚫 هذا الأمر للمشرفين فقط.")
        return

    status_msg = await msg.reply_text("🔄 جاري مزامنة الأعضاء، يرجى الانتظار…")
    count, method = await _scan_members(context.bot, msg.chat.id)

    if method == "full":
        result = (
            f"✅ تمت المزامنة الكاملة!\n"
            f"📦 تم حفظ *{count}* عضو في قاعدة البيانات."
        )
    else:
        result = (
            f"✅ تمت مزامنة المشرفين ({count} عضو).\n"
            f"⚠️ للمزامنة الكاملة لجميع الأعضاء، أضف `ASSISTANT_SESSION_STRING` إلى الإعدادات."
        )

    await status_msg.edit_text(result, parse_mode="Markdown")

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
    app.add_handler(CommandHandler("sync",   _handle_sync,   filters=group_filter))

    # Arabic text triggers
    app.add_handler(MessageHandler(group_filter & _AR_MOD_FILTER, arabic_trigger_handler))
    app.add_handler(MessageHandler(group_filter & _AR_SYNC_FILTER, _handle_sync))

    # Inline button callbacks
    app.add_handler(CallbackQueryHandler(_cb_unmute, pattern=r"^unmute_\d+$"))
    app.add_handler(CallbackQueryHandler(_cb_unban,  pattern=r"^unban_\d+$"))

    # New chat members — saves joining users immediately (before they type anything)
    app.add_handler(
        MessageHandler(group_filter & filters.StatusUpdate.NEW_CHAT_MEMBERS, on_new_chat_member)
    )

    # Member cache — runs on EVERY group message (group=-1 = lowest priority,
    # so it never blocks moderation handlers from running first)
    app.add_handler(
        MessageHandler(group_filter & filters.ALL, update_member_cache),
        group=-1,
    )

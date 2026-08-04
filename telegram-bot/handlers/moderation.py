"""
handlers/moderation.py — Group administration & moderation commands.

Supports both Arabic text triggers and English slash commands.
All actions are admin-only and protected against targeting other admins or the bot.

Arabic triggers (can be sent as standalone text or reply):
  بان / حظر              → Ban
  الغاء الحظر / الغاء بان → Unban
  طرد                    → Kick (ban + immediate unban)
  كتم                    → Mute
  الغاء الكتم / الغاء كتم → Unmute

English slash commands (reply to a message OR include @username):
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
# Each pattern anchors to the start of the message so casual mentions
# of these words mid-sentence don't accidentally trigger moderation.

_AR_BAN    = re.compile(r"^(بان|حظر)\b", re.IGNORECASE)
_AR_UNBAN  = re.compile(r"^(الغاء\s+الحظر|الغاء\s+بان)\b", re.IGNORECASE)
_AR_KICK   = re.compile(r"^طرد\b", re.IGNORECASE)
_AR_MUTE   = re.compile(r"^كتم\b", re.IGNORECASE)
_AR_UNMUTE = re.compile(r"^(الغاء\s+الكتم|الغاء\s+كتم)\b", re.IGNORECASE)

# Combined filter: any message that starts with one of the Arabic triggers
_AR_MOD_FILTER = filters.Regex(
    re.compile(
        r"^(بان|حظر|الغاء\s+الحظر|الغاء\s+بان|طرد|كتم|الغاء\s+الكتم|الغاء\s+كتم)\b",
        re.IGNORECASE,
    )
)

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

    Priority:
      1. Replied-to message author.
      2. @username or user-id in the command/text arguments.

    Returns a telegram.User object or None if no target found.
    """
    msg = update.message

    # 1. Reply-to
    if msg.reply_to_message and msg.reply_to_message.from_user:
        return msg.reply_to_message.from_user

    # 2. Extract from text — grab first entity of type mention or the first arg
    if msg.entities:
        for entity in msg.entities:
            if entity.type == "mention":
                username = msg.text[entity.offset + 1: entity.offset + entity.length]
                try:
                    chat = await context.bot.get_chat(f"@{username}")
                    return chat  # Chat object has .id and .first_name like User
                except TelegramError:
                    pass
            elif entity.type == "text_mention" and entity.user:
                return entity.user

    # 3. Try bare numeric user-id in arguments
    args = context.args or []
    if args:
        try:
            uid = int(args[0])
            chat = await context.bot.get_chat(uid)
            return chat
        except (ValueError, TelegramError):
            pass

    return None


async def _check_preconditions(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> tuple:
    """
    Verify that:
      - The command is used inside a group / supergroup.
      - The invoker is an admin or creator.
      - A valid target is specified.
      - The target is not an admin, creator, or the bot itself.

    Returns (target_user, None) on success, or (None, error_message) on failure.
    """
    msg = update.message
    chat = msg.chat
    invoker = msg.from_user
    bot_user = await context.bot.get_me()

    # Must be in a group
    if chat.type not in ("group", "supergroup"):
        return None, "⚠️ هذا الأمر يعمل فقط في المجموعات.\n_This command only works in groups._"

    # Invoker must be admin
    if not await _is_admin(context.bot, chat.id, invoker.id):
        return None, "🚫 هذا الأمر للمشرفين فقط.\n_Only group admins can use this command._"

    # Resolve target
    target = await _get_target(update, context)
    if target is None:
        return None, (
            "❓ لم يتم تحديد المستخدم.\n"
            "_Please reply to a message or mention a user: `@username`_"
        )

    # Cannot target the bot itself
    if target.id == bot_user.id:
        return None, "😅 لا أستطيع تطبيق هذا الأمر على نفسي!\n_I can't apply this to myself!_"

    # Cannot target another admin
    if await _is_admin(context.bot, chat.id, target.id):
        return None, "🛡 لا يمكن تطبيق هذا الأمر على المشرفين.\n_You cannot use this command on an admin._"

    return target, None


def _user_mention(user) -> str:
    """Return a Markdown-safe mention or name for *user*."""
    name = getattr(user, "first_name", None) or getattr(user, "username", None) or str(user.id)
    if getattr(user, "username", None):
        return f"[{name}](tg://user?id={user.id})"
    return name


# ── Action handlers ────────────────────────────────────────────────────────────

async def _handle_ban(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Ban a user from the group permanently."""
    target, err = await _check_preconditions(update, context)
    if err:
        await update.message.reply_text(err, parse_mode="Markdown")
        return
    try:
        await context.bot.ban_chat_member(update.message.chat.id, target.id)
        await update.message.reply_text(
            f"🔨 تم حظر {_user_mention(target)} من المجموعة.\n"
            f"_User has been banned._",
            parse_mode="Markdown",
        )
        logger.info("Banned user %s in chat %s", target.id, update.message.chat.id)
    except TelegramError as e:
        await update.message.reply_text(f"❌ فشل الأمر: `{e}`", parse_mode="Markdown")


async def _handle_unban(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Unban a previously banned user (they can rejoin via invite)."""
    target, err = await _check_preconditions(update, context)
    if err:
        await update.message.reply_text(err, parse_mode="Markdown")
        return
    try:
        await context.bot.unban_chat_member(update.message.chat.id, target.id, only_if_banned=True)
        await update.message.reply_text(
            f"✅ تم رفع الحظر عن {_user_mention(target)}.\n"
            f"_User has been unbanned._",
            parse_mode="Markdown",
        )
        logger.info("Unbanned user %s in chat %s", target.id, update.message.chat.id)
    except TelegramError as e:
        await update.message.reply_text(f"❌ فشل الأمر: `{e}`", parse_mode="Markdown")


async def _handle_kick(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Kick a user (ban then immediately unban — they can rejoin via invite link)."""
    target, err = await _check_preconditions(update, context)
    if err:
        await update.message.reply_text(err, parse_mode="Markdown")
        return
    try:
        await context.bot.ban_chat_member(update.message.chat.id, target.id)
        await context.bot.unban_chat_member(update.message.chat.id, target.id)
        await update.message.reply_text(
            f"👢 تم طرد {_user_mention(target)} من المجموعة.\n"
            f"_User has been kicked. They can rejoin via invite link._",
            parse_mode="Markdown",
        )
        logger.info("Kicked user %s in chat %s", target.id, update.message.chat.id)
    except TelegramError as e:
        await update.message.reply_text(f"❌ فشل الأمر: `{e}`", parse_mode="Markdown")


async def _handle_mute(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Mute a user — restrict all messaging permissions."""
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
            f"🔇 تم كتم {_user_mention(target)}.\n"
            f"_User has been muted._",
            parse_mode="Markdown",
        )
        logger.info("Muted user %s in chat %s", target.id, update.message.chat.id)
    except TelegramError as e:
        await update.message.reply_text(f"❌ فشل الأمر: `{e}`", parse_mode="Markdown")


async def _handle_unmute(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Unmute a user — restore default group permissions."""
    target, err = await _check_preconditions(update, context)
    if err:
        await update.message.reply_text(err, parse_mode="Markdown")
        return
    try:
        # Restore the group's default permissions by passing can_send_messages=True
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
            f"🔊 تم إلغاء كتم {_user_mention(target)}.\n"
            f"_User has been unmuted._",
            parse_mode="Markdown",
        )
        logger.info("Unmuted user %s in chat %s", target.id, update.message.chat.id)
    except TelegramError as e:
        await update.message.reply_text(f"❌ فشل الأمر: `{e}`", parse_mode="Markdown")


# ── Arabic text trigger dispatcher ────────────────────────────────────────────

async def arabic_trigger_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Dispatch Arabic trigger words to the appropriate moderation action.
    Called for any group message that starts with a recognised Arabic trigger.
    """
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
    """Attach all moderation handlers (slash commands + Arabic triggers)."""
    group_filter = filters.ChatType.GROUPS | filters.ChatType.SUPERGROUP

    # English slash commands
    app.add_handler(CommandHandler("ban",    _handle_ban,    filters=group_filter))
    app.add_handler(CommandHandler("unban",  _handle_unban,  filters=group_filter))
    app.add_handler(CommandHandler("kick",   _handle_kick,   filters=group_filter))
    app.add_handler(CommandHandler("mute",   _handle_mute,   filters=group_filter))
    app.add_handler(CommandHandler("unmute", _handle_unmute, filters=group_filter))

    # Arabic text triggers (group messages only)
    app.add_handler(
        MessageHandler(group_filter & _AR_MOD_FILTER, arabic_trigger_handler)
    )

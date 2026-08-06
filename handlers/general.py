"""
handlers/general.py — General commands: /start, /help, /ping, bot-added welcome.
Uses python-telegram-bot v21 (async).
"""

import re
import time
import logging

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

logger = logging.getLogger(__name__)

# ── MarkdownV2 escape helper ───────────────────────────────────────────────────

def _esc(text: str) -> str:
    """Escape a plain string for safe embedding inside a MarkdownV2 message."""
    return re.sub(r'([_*\[\]()~`>#+\-=|{}.!])', r'\\\1', text)


# ── Help text (shared between /help and الاوامر) ──────────────────────────────

_HELP_TEXT = (
    "📖 *دليل الأوامر*\n"
    "━━━━━━━━━━━━━━━━━━━━\n\n"

    "🛡 *أوامر الإدارة* \\(للمشرفين فقط\\)\n\n"

    "🔨 *الحظر*\n"
    "`/ban` \\| `بان` \\| `حظر`\n"
    "حظر مستخدم نهائياً من المجموعة\\.\n"
    "_رد على رسالته أو اكتب_ `بان @username`\n\n"

    "✅ *رفع الحظر*\n"
    "`/unban` \\| `الغاء الحظر` \\| `الغاء بان`\n"
    "رفع الحظر والسماح للمستخدم بالعودة\\.\n"
    "_رد على رسالته أو اكتب_ `الغاء الحظر @username`\n\n"

    "👢 *الطرد*\n"
    "`/kick` \\| `طرد`\n"
    "طرد المستخدم مع إمكانية العودة عبر رابط دعوة\\.\n"
    "_رد على رسالته أو اكتب_ `طرد @username`\n\n"

    "🔇 *الكتم*\n"
    "`/mute` \\| `كتم`\n"
    "منع المستخدم من إرسال الرسائل\\.\n"
    "_رد على رسالته أو اكتب_ `كتم @username`\n\n"

    "🔊 *إلغاء الكتم*\n"
    "`/unmute` \\| `الغاء الكتم` \\| `الغاء كتم`\n"
    "إعادة صلاحية الإرسال للمستخدم\\.\n"
    "_رد على رسالته أو اكتب_ `الغاء الكتم @username`\n\n"

    "🔄 *مزامنة الأعضاء*\n"
    "`/sync` \\| `تحديث الأعضاء`\n"
    "حفظ جميع الأعضاء في قاعدة البيانات لتفادي خطأ 'المستخدم غير موجود'\\.\n\n"

    "🗑️ *مسح الرسائل*\n"
    "`/clear [n]` \\| `مسح [n]`\n"
    "حذف آخر *n* رسالة من الدردشة \\(الافتراضي 100، الحد الأقصى 100\\)\\.\n"
    "_مثال:_ `/clear 30` أو `مسح 30`\n\n"

    "━━━━━━━━━━━━━━━━━━━━\n"
    "💬 *نظام الردود التلقائية*\n\n"

    "💬 `اضافه رد`\n"
    "لإضافة رد جديد تلقائي \\(يتفاعل مع الكلمة في الجروب\\)\\.\n\n"

    "🗑️ `حذف رد \\[الكلمة\\]`\n"
    "لمسح رد مضاف سابقاً\\.\n\n"

    "📜 `الردود`\n"
    "لعرض كل الردود المضافة في البوت\\.\n\n"

    "━━━━━━━━━━━━━━━━━━━━\n"
    "🎮 *لودو كلوب*\n"
    "يقوم البوت تلقائياً باستخراج كود الدعوة من أي رسالة تحتوي على رابط لودو كلوب "
    "أو نص مثل `enter my Code: WAKL189`\\.\n\n"

    "━━━━━━━━━━━━━━━━━━━━\n"
    "🛠 *الأوامر العامة*\n"
    "`/start` — رسالة الترحيب\n"
    "`/ping` — اختبار سرعة الاستجابة\n"
    "`/help` \\| `الاوامر` — هذه القائمة"
)


# ── Welcome keyboard (bot added to group) ─────────────────────────────────────

def _welcome_keyboard(bot_username: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("القناة 📢", url="https://t.me/ELQAEDD"),
        ],
        [
            InlineKeyboardButton("المطور 1 👨‍💻", url="https://t.me/yyuyyuyu"),
            InlineKeyboardButton("المطور 2 👨‍💻", url="https://t.me/S_h_i_k_3"),
        ],
        [
            InlineKeyboardButton(
                "⚡️ ضف البوت الي مجموعتك او قناتك ➕",
                url=f"https://t.me/{bot_username}?startgroup=true",
            ),
        ],
    ])


# ── Handlers ──────────────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Welcome message with feature overview."""
    await update.message.reply_text(
        "👋 *أهلاً\\! أنا بوت إدارة المجموعات\\.*\n\n"
        "يمكنك استخدامي لـ:\n\n"
        "🛡 *إدارة المجموعة* — حظر، طرد، كتم، ورفع الحظر\\.\n"
        "🎮 *لودو كلوب* — استخراج أكواد الدعوة تلقائياً\\.\n"
        "💬 *الردود التلقائية* — اكتب `اضافه رد` لإضافة رد تلقائي\\.\n\n"
        "اكتب /help لعرض قائمة الأوامر الكاملة\\.",
        parse_mode="MarkdownV2",
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Full command reference — works in groups and private DMs."""
    await update.message.reply_text(_HELP_TEXT, parse_mode="MarkdownV2")


async def ping(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Measure and report round-trip latency."""
    t0  = time.monotonic()
    msg = await update.message.reply_text("🏓 Pong\\!", parse_mode="MarkdownV2")
    elapsed_ms = (time.monotonic() - t0) * 1000
    await msg.edit_text(
        f"🏓 Pong\\! `{elapsed_ms:.1f} ms`",
        parse_mode="MarkdownV2",
    )


async def on_bot_added(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Fires when the bot itself is added to a group.
    Sends a welcome photo (with the bot's own profile picture) + inline buttons.
    Falls back to a text-only message if no profile photo is set.
    """
    msg = update.message
    if not msg or not msg.new_chat_members:
        return

    bot_me = await context.bot.get_me()

    # Only react when the bot itself is among the newly added members
    if not any(u.id == bot_me.id for u in msg.new_chat_members):
        return

    chat        = msg.chat
    bot_uname   = bot_me.username or ""
    keyboard    = _welcome_keyboard(bot_uname)

    # MarkdownV2-safe caption — escape the dynamic chat title
    caption = (
        f"شكراً لإضافة البوت إلى مجموعتك *{_esc(chat.title)}* ⚡️\n\n"
        "قم بإعطاء البوت صلاحية مشرف ليتم تفعيله بالكامل\\."
    )

    # Attempt to fetch the bot's own profile photo
    try:
        photos = await context.bot.get_user_profile_photos(bot_me.id, limit=1)
        if photos.total_count > 0:
            # photos.photos is a list of size-variants lists; pick the largest
            file_id = photos.photos[0][-1].file_id
            await msg.reply_photo(
                photo=file_id,
                caption=caption,
                parse_mode="MarkdownV2",
                reply_markup=keyboard,
            )
            return
    except Exception as exc:
        logger.debug("Could not fetch bot profile photo: %s", exc)

    # Fallback: text-only welcome with the same buttons
    await msg.reply_text(
        caption,
        parse_mode="MarkdownV2",
        reply_markup=keyboard,
    )


# ── Registration ──────────────────────────────────────────────────────────────

_GROUP_AND_PRIVATE = (
    filters.ChatType.GROUPS
    | filters.ChatType.SUPERGROUP
    | filters.ChatType.PRIVATE
)
_GROUP_FILTER = filters.ChatType.GROUPS | filters.ChatType.SUPERGROUP

_AOWAMER_FILTER = filters.Regex(re.compile(r"^الاوامر$"))


def register(app: Application) -> None:
    """Attach general command handlers to the application."""
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help",  help_cmd))
    app.add_handler(CommandHandler("ping",  ping))

    # Arabic alias for /help — works in groups and private DMs
    app.add_handler(
        MessageHandler(_GROUP_AND_PRIVATE & _AOWAMER_FILTER, help_cmd)
    )

    # Bot-added welcome — runs at group=2 so it doesn't conflict with
    # moderation's on_new_chat_member (group=0, saves users to DB)
    app.add_handler(
        MessageHandler(
            _GROUP_FILTER & filters.StatusUpdate.NEW_CHAT_MEMBERS,
            on_bot_added,
        ),
        group=2,
    )

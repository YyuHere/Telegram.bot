"""
music/commands.py — Full music command handlers.

Triggers:
  • /play <query>  — slash command
  • تشغيل <query> — Arabic text trigger

Response: YouTube thumbnail photo + formatted caption + inline playback keyboard.
Callbacks: ▶️ ⏸️ 🔄 ⏭️ ⏹️ buttons wired to PyTgCalls controls.
"""

import asyncio
import logging
import re

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

import config
from music import player

logger = logging.getLogger(__name__)

# ─── Filters ──────────────────────────────────────────────────────────────────

_GROUP_FILTER   = filters.ChatType.GROUPS | filters.ChatType.SUPERGROUP
_AR_PLAY_RE     = re.compile(r"^تشغيل\b", re.UNICODE)
_AR_PLAY_FILTER = filters.Regex(_AR_PLAY_RE)

# ─── Callback data prefixes ───────────────────────────────────────────────────

_CB_PLAY   = "m_play"
_CB_PAUSE  = "m_pause"
_CB_REPEAT = "m_repeat"
_CB_SKIP   = "m_skip"
_CB_STOP   = "m_stop"


# ─── Keyboard builder ─────────────────────────────────────────────────────────

def _music_keyboard(chat_id: int, bot_username: str) -> InlineKeyboardMarkup:
    cid = str(chat_id)
    return InlineKeyboardMarkup([
        # Row 1 — playback controls
        [
            InlineKeyboardButton("▶️",  callback_data=f"{_CB_PLAY}_{cid}"),
            InlineKeyboardButton("⏸️",  callback_data=f"{_CB_PAUSE}_{cid}"),
            InlineKeyboardButton("🔄",  callback_data=f"{_CB_REPEAT}_{cid}"),
            InlineKeyboardButton("⏭️",  callback_data=f"{_CB_SKIP}_{cid}"),
            InlineKeyboardButton("⏹️",  callback_data=f"{_CB_STOP}_{cid}"),
        ],
        # Row 2 — channel
        [
            InlineKeyboardButton("القناة 📢", url="https://t.me/ELQAEDD"),
        ],
        # Row 3 — developers
        [
            InlineKeyboardButton("المطور 1 👨‍💻", url="https://t.me/yyuyyuyu"),
            InlineKeyboardButton("المطور 2 👨‍💻", url="https://t.me/S_h_i_k_3"),
        ],
        # Row 4 — add bot
        [
            InlineKeyboardButton(
                "⚡️ ضف البوت الي مجموعتك او قناتك ➕",
                url=f"https://t.me/{bot_username}?startgroup=true",
            ),
        ],
    ])


# ─── Caption builder ──────────────────────────────────────────────────────────

def _music_caption(title: str, duration: str, user_mention: str) -> str:
    return (
        "🔄 StaRTeD STReaMInG | ❤️\n\n"
        f"▶ Title: {title}\n"
        f"▶ Duration: {duration} 🔷\n"
        f"▶ Requested by: {user_mention}"
    )


def _mention(user) -> str:
    name = (
        getattr(user, "first_name", None)
        or getattr(user, "username", None)
        or str(user.id)
    )
    return f"[{name}](tg://user?id={user.id})"


# ─── Core play logic ──────────────────────────────────────────────────────────

async def _handle_play(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Shared handler for /play and the Arabic تشغيل trigger.
    Extracts the query, validates it, searches YouTube, and streams audio.
    """
    msg  = update.message
    text = msg.text or ""

    # Extract query — strip command/trigger word
    if text.startswith("/play"):
        # Handle /play@BotUsername form
        after_cmd = text[len("/play"):].lstrip()
        if after_cmd.startswith("@"):
            parts = after_cmd.split(None, 1)
            query = parts[1].strip() if len(parts) > 1 else ""
        else:
            query = after_cmd
    else:
        # Arabic trigger: "تشغيل <song name>"
        query = _AR_PLAY_RE.sub("", text).strip()

    if not query:
        await msg.reply_text("الرجاء كتابة اسم الاغنيه لتشغيلها")
        return

    # Check 1: env vars must be present
    if not config.ASSISTANT_ENABLED:
        await msg.reply_text(
            "⚠️ موسيقى الصوت غير متاحة.\n"
            "يجب ضبط `API_ID` و `API_HASH` و `ASSISTANT_SESSION_STRING` أولاً."
        )
        return

    # Check 2: PyTgCalls must have initialised successfully
    if player.call_py is None:
        await msg.reply_text(
            "⚠️ فشل تهيئة محرك الصوت (PyTgCalls).\n"
            "يرجى مراجعة سجلات الخادم."
        )
        return

    # Show searching feedback
    status = await msg.reply_text("🔍 جاري البحث…")

    # Resolve audio info in a thread (yt-dlp is blocking)
    try:
        track = await asyncio.to_thread(player.get_audio_info, query)
    except Exception as exc:
        logger.warning("yt-dlp failed for %r: %s", query, exc)
        await status.edit_text(f"❌ لم يتم العثور على النتيجة: {exc}")
        return

    chat_id = msg.chat.id
    user    = msg.from_user

    # Start streaming or queue
    try:
        started = await player.play(chat_id, track)
    except Exception as exc:
        logger.error("play() failed in chat %s: %s", chat_id, exc)
        await status.edit_text(f"❌ فشل تشغيل الصوت: {exc}")
        return

    await status.delete()

    if not started:
        # Track was queued, not immediately started
        await msg.reply_text(
            f"📥 تمت إضافة الأغنية إلى قائمة الانتظار:\n"
            f"▶ {track['title']} ({track['duration']})",
            parse_mode="Markdown",
        )
        return

    # Build response message
    bot_me       = await context.bot.get_me()
    bot_username = bot_me.username or ""
    caption      = _music_caption(track["title"], track["duration"], _mention(user))
    keyboard     = _music_keyboard(chat_id, bot_username)

    if track["thumbnail"]:
        try:
            await msg.reply_photo(
                photo=track["thumbnail"],
                caption=caption,
                parse_mode="Markdown",
                reply_markup=keyboard,
            )
            return
        except Exception as exc:
            logger.debug("Thumbnail send failed, falling back to text: %s", exc)

    # Fallback: text message without photo
    await msg.reply_text(caption, parse_mode="Markdown", reply_markup=keyboard)


# ─── Callback helpers ─────────────────────────────────────────────────────────

def _parse_chat_id(data: str, prefix: str) -> int | None:
    try:
        return int(data[len(prefix) + 1:])
    except (ValueError, IndexError):
        return None


# ─── Callback handlers ────────────────────────────────────────────────────────

async def _cb_play(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """▶️ Resume playback."""
    query   = update.callback_query
    chat_id = _parse_chat_id(query.data, _CB_PLAY)
    if chat_id is None:
        await query.answer()
        return
    try:
        await player.resume(chat_id)
        await query.answer("▶️ تم الاستئناف")
    except Exception as exc:
        await query.answer(f"❌ {exc}", show_alert=True)


async def _cb_pause(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """⏸️ Pause playback."""
    query   = update.callback_query
    chat_id = _parse_chat_id(query.data, _CB_PAUSE)
    if chat_id is None:
        await query.answer()
        return
    try:
        await player.pause(chat_id)
        await query.answer("⏸️ تم الإيقاف المؤقت")
    except Exception as exc:
        await query.answer(f"❌ {exc}", show_alert=True)


async def _cb_repeat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """🔄 Toggle repeat mode."""
    query   = update.callback_query
    chat_id = _parse_chat_id(query.data, _CB_REPEAT)
    if chat_id is None:
        await query.answer()
        return
    try:
        on    = await player.toggle_repeat(chat_id)
        label = "🔄 التكرار: مفعّل ✅" if on else "🔄 التكرار: معطّل ❌"
        await query.answer(label)
    except Exception as exc:
        await query.answer(f"❌ {exc}", show_alert=True)


async def _cb_skip(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """⏭️ Skip to next track."""
    query   = update.callback_query
    chat_id = _parse_chat_id(query.data, _CB_SKIP)
    if chat_id is None:
        await query.answer()
        return
    try:
        next_track = await player.skip(chat_id)
        if next_track:
            await query.answer(f"⏭️ {next_track['title']}")
        else:
            await query.answer("⏭️ انتهت قائمة الانتظار")
    except Exception as exc:
        await query.answer(f"❌ {exc}", show_alert=True)


async def _cb_stop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """⏹️ Stop playback and leave voice chat."""
    query   = update.callback_query
    chat_id = _parse_chat_id(query.data, _CB_STOP)
    if chat_id is None:
        await query.answer()
        return
    try:
        await player.stop(chat_id)
        await query.answer("⏹️ تم الإيقاف")
        # Remove keyboard since playback has ended
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
    except Exception as exc:
        await query.answer(f"❌ {exc}", show_alert=True)


# ─── Registration ──────────────────────────────────────────────────────────────

def register(app: Application) -> None:
    """Attach music command and callback handlers to the application."""

    # /play command — groups only
    app.add_handler(CommandHandler("play", _handle_play, filters=_GROUP_FILTER))

    # Arabic "تشغيل" trigger — groups only
    app.add_handler(
        MessageHandler(_GROUP_FILTER & _AR_PLAY_FILTER, _handle_play)
    )

    # Playback control callbacks
    app.add_handler(CallbackQueryHandler(_cb_play,   pattern=rf"^{_CB_PLAY}_-?\d+$"))
    app.add_handler(CallbackQueryHandler(_cb_pause,  pattern=rf"^{_CB_PAUSE}_-?\d+$"))
    app.add_handler(CallbackQueryHandler(_cb_repeat, pattern=rf"^{_CB_REPEAT}_-?\d+$"))
    app.add_handler(CallbackQueryHandler(_cb_skip,   pattern=rf"^{_CB_SKIP}_-?\d+$"))
    app.add_handler(CallbackQueryHandler(_cb_stop,   pattern=rf"^{_CB_STOP}_-?\d+$"))

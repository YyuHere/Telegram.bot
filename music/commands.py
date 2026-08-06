"""
music/commands.py — Full music command handlers.

Triggers:
  • /play <query>  — slash command
  • تشغيل <query> — Arabic text trigger

Search: SoundCloud (primary). Direct audio URLs and other yt-dlp-supported
        platform links are also accepted. YouTube is intentionally excluded.
Response: Thumbnail photo + formatted caption + inline playback keyboard.
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
# Queue-add callback: "m_queue_{chat_id}_{b64-encoded track url}"
# We encode only the URL; title/duration are fetched from current state.
_CB_QUEUE  = "m_queue"


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


# Pending-enqueue store: maps (chat_id, user_id) → TrackInfo
# Populated just before a "now playing" card is sent so the "Add to Queue"
# inline button in the *next* play request can reference the resolved track.
# Structure: { (chat_id, user_id): TrackInfo }
_pending_enqueue: dict[tuple[int, int], "player.TrackInfo"] = {}

# Bot username cache — fetched once on first play, reused thereafter.
_bot_username_cache: str = ""


async def _get_bot_username(bot) -> str:
    """Return the bot's username, fetching from the API only on the first call."""
    global _bot_username_cache
    if not _bot_username_cache:
        me = await bot.get_me()
        _bot_username_cache = me.username or ""
    return _bot_username_cache


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

async def _send_now_playing(
    msg,
    track: "player.TrackInfo",
    chat_id: int,
    user,
    bot_username: str,
) -> None:
    """Send the 'now playing' card (photo + caption + keyboard) for *track*."""
    caption  = _music_caption(track["title"], track["duration"], _mention(user))
    keyboard = _music_keyboard(chat_id, bot_username)

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

    await msg.reply_text(caption, parse_mode="Markdown", reply_markup=keyboard)


async def _handle_play(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Shared handler for /play and the Arabic تشغيل trigger.

    Always starts the requested track immediately:
      • Nothing playing  → join voice chat and start streaming.
      • Already playing  → replace current track instantly (change_stream),
                           clear the queue.  Mirrors X-Music / professional bot
                           behaviour — no silent queuing on /play.

    Users who want to queue without interrupting can use /queue <song>.
    """
    msg  = update.message
    text = msg.text or ""

    # ── Extract query ─────────────────────────────────────────────────────────
    if text.startswith("/play"):
        after_cmd = text[len("/play"):].lstrip()
        if after_cmd.startswith("@"):
            parts = after_cmd.split(None, 1)
            query = parts[1].strip() if len(parts) > 1 else ""
        else:
            query = after_cmd
    else:
        query = _AR_PLAY_RE.sub("", text).strip()

    if not query:
        await msg.reply_text("الرجاء كتابة اسم الاغنيه لتشغيلها")
        return

    # ── Pre-flight checks ─────────────────────────────────────────────────────
    if not config.ASSISTANT_ENABLED:
        await msg.reply_text(
            "⚠️ موسيقى الصوت غير متاحة.\n"
            "يجب ضبط `API_ID` و `API_HASH` و `ASSISTANT_SESSION_STRING` أولاً."
        )
        return

    if player.call_py is None:
        await msg.reply_text(
            "⚠️ فشل تهيئة محرك الصوت (PyTgCalls).\n"
            "يرجى مراجعة سجلات الخادم."
        )
        return

    chat_id = msg.chat.id
    user    = msg.from_user

    status = await msg.reply_text(f"🔍 جاري البحث عن: {query}")

    # ── Resolve audio + warm assistant peer cache concurrently ────────────────
    # yt-dlp search and Pyrogram peer-cache warmup are independent — run them
    # in parallel so the assistant is ready by the time the track URL arrives.
    try:
        track, _ = await asyncio.gather(
            asyncio.to_thread(player.get_audio_info, query),
            player.warm_assistant(chat_id),
        )
    except Exception as exc:
        logger.warning("Search failed for %r: %s", query, exc)
        await status.edit_text(f"❌ لم يتم العثور على الأغنية. جرب اسمًا آخر.")
        return

    # ── Show source-specific progress message ─────────────────────────────────
    _source_msgs = {
        player.SOURCE_YOUTUBE: "🎵 جاري التشغيل من YouTube...",
        player.SOURCE_SPOTIFY: "🎧 تم العثور عبر Spotify، جاري التشغيل...",
        player.SOURCE_ANGHAMI: "🎶 جاري التشغيل من Anghami...",
    }
    src_msg = _source_msgs.get(track.get("source", ""), "🎵 جاري التشغيل...")
    await status.edit_text(
        f"{src_msg}\n⏱️ مدة الأغنية: {track['duration']}"
    )

    # ── Play immediately (replace if already streaming) ───────────────────────
    try:
        await player.play(chat_id, track)
    except Exception as exc:
        logger.error("play() failed in chat %s: %s", chat_id, exc)
        await status.edit_text(f"❌ فشل تشغيل الصوت: {exc}")
        return

    await status.delete()

    bot_username = await _get_bot_username(context.bot)
    await _send_now_playing(msg, track, chat_id, user, bot_username)


async def _handle_queue(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /queue <song> — add a track to the end of the queue without interrupting
    whatever is currently playing.  If nothing is playing, starts immediately.
    """
    msg  = update.message
    text = msg.text or ""

    # Strip "/queue" or "/queue@BotName"
    after_cmd = text.split(None, 1)
    query = after_cmd[1].strip() if len(after_cmd) > 1 else ""

    # Strip bot-username suffix from the command word itself if present
    if query.startswith("@"):
        parts = query.split(None, 1)
        query = parts[1].strip() if len(parts) > 1 else ""

    if not query:
        await msg.reply_text("الرجاء كتابة اسم الاغنية لإضافتها إلى قائمة الانتظار")
        return

    if not config.ASSISTANT_ENABLED:
        await msg.reply_text(
            "⚠️ موسيقى الصوت غير متاحة.\n"
            "يجب ضبط `API_ID` و `API_HASH` و `ASSISTANT_SESSION_STRING` أولاً."
        )
        return

    if player.call_py is None:
        await msg.reply_text(
            "⚠️ فشل تهيئة محرك الصوت (PyTgCalls).\n"
            "يرجى مراجعة سجلات الخادم."
        )
        return

    status = await msg.reply_text("🔍 جاري البحث للإضافة إلى قائمة الانتظار…")

    try:
        track = await asyncio.to_thread(player.get_audio_info, query)
    except Exception as exc:
        logger.warning("yt-dlp (queue) failed for %r: %s", query, exc)
        await status.edit_text(f"❌ لم يتم العثور على النتيجة: {exc}")
        return

    chat_id = msg.chat.id
    user    = msg.from_user

    try:
        was_queued = await player.enqueue(chat_id, track)
    except Exception as exc:
        logger.error("enqueue() failed in chat %s: %s", chat_id, exc)
        await status.edit_text(f"❌ فشل الإضافة: {exc}")
        return

    await status.delete()

    if was_queued:
        queue_len = len(player._get_state(chat_id)["queue"])
        await msg.reply_text(
            f"📥 تمت إضافة الأغنية إلى قائمة الانتظار (#{queue_len}):\n"
            f"▶ *{track['title']}* ({track['duration']})",
            parse_mode="Markdown",
        )
    else:
        # Nothing was playing — started immediately
        bot_username = await _get_bot_username(context.bot)
        await _send_now_playing(msg, track, chat_id, user, bot_username)


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

    # Give the player a reference to the PTB bot so it can invite the assistant
    # into private groups when assistant.join_chat() alone is not enough.
    player.set_bot(app.bot)

    # /play — always plays immediately (replaces current track), groups only
    app.add_handler(CommandHandler("play", _handle_play, filters=_GROUP_FILTER))

    # Arabic "تشغيل" trigger — groups only
    app.add_handler(
        MessageHandler(_GROUP_FILTER & _AR_PLAY_FILTER, _handle_play)
    )

    # /queue — add to queue without interrupting current track, groups only
    app.add_handler(CommandHandler("queue", _handle_queue, filters=_GROUP_FILTER))

    # Playback control callbacks
    app.add_handler(CallbackQueryHandler(_cb_play,   pattern=rf"^{_CB_PLAY}_-?\d+$"))
    app.add_handler(CallbackQueryHandler(_cb_pause,  pattern=rf"^{_CB_PAUSE}_-?\d+$"))
    app.add_handler(CallbackQueryHandler(_cb_repeat, pattern=rf"^{_CB_REPEAT}_-?\d+$"))
    app.add_handler(CallbackQueryHandler(_cb_skip,   pattern=rf"^{_CB_SKIP}_-?\d+$"))
    app.add_handler(CallbackQueryHandler(_cb_stop,   pattern=rf"^{_CB_STOP}_-?\d+$"))

"""
music/player.py — Core audio streaming engine.

Uses:
  • yt-dlp   — Download / stream audio from YouTube and 500+ sites.
  • pytgcalls — Stream the audio into a Telegram Voice Chat.

State is tracked per chat_id in an in-memory dict so the bot can handle
multiple groups simultaneously.
"""

import asyncio
import logging
import yt_dlp
from pytgcalls import PyTgCalls
from pytgcalls.types import Update
from pytgcalls.types.input_stream import AudioPiped
from pytgcalls.types.input_stream.quality import HighQualityAudio

from assistant.userbot import assistant

logger = logging.getLogger(__name__)

# ─── pytgcalls instance attached to the Assistant userbot ─────────────────────
call_py = PyTgCalls(assistant)

# ─── Per-chat state ───────────────────────────────────────────────────────────
# Structure: { chat_id: {"queue": [...], "playing": bool} }
_chat_state: dict[int, dict] = {}


def _get_state(chat_id: int) -> dict:
    if chat_id not in _chat_state:
        _chat_state[chat_id] = {"queue": [], "playing": False}
    return _chat_state[chat_id]


# ─── yt-dlp helpers ───────────────────────────────────────────────────────────

def _ydl_opts() -> dict:
    """Return yt-dlp options for best audio extraction without downloading."""
    return {
        "format": "bestaudio/best",
        "quiet": True,
        "no_warnings": True,
        "default_search": "ytsearch",   # Search YouTube if given a bare title
        "noplaylist": True,
    }


def get_audio_url(query: str) -> tuple[str, str]:
    """
    Resolve *query* (title or direct URL) to a streamable audio URL.

    Returns (stream_url, title).
    Raises RuntimeError if no result is found.
    """
    with yt_dlp.YoutubeDL(_ydl_opts()) as ydl:
        info = ydl.extract_info(query, download=False)

        # Handle search results (list)
        if "entries" in info:
            info = info["entries"][0]

        url: str = info.get("url") or info.get("webpage_url", "")
        title: str = info.get("title", query)

        if not url:
            raise RuntimeError(f"Could not resolve audio URL for: {query}")

        return url, title


# ─── Playback control ─────────────────────────────────────────────────────────

async def play(chat_id: int, query: str) -> str:
    """
    Resolve *query*, join the voice chat if needed, and start streaming.

    Returns the track title on success.
    Raises on failure.
    """
    stream_url, title = await asyncio.to_thread(get_audio_url, query)
    state = _get_state(chat_id)

    if state["playing"]:
        # Enqueue instead of interrupting
        state["queue"].append((stream_url, title))
        return f"⏭ Added to queue: **{title}**"

    state["playing"] = True
    await call_py.join_group_call(
        chat_id,
        AudioPiped(stream_url, HighQualityAudio()),
        stream_type=None,
    )
    return title


async def stop(chat_id: int) -> None:
    """Stop playback and leave the voice chat."""
    state = _get_state(chat_id)
    state["playing"] = False
    state["queue"].clear()
    try:
        await call_py.leave_group_call(chat_id)
    except Exception as exc:
        logger.warning("stop: leave_group_call raised %s", exc)


async def pause(chat_id: int) -> None:
    """Pause the current stream."""
    await call_py.pause_stream(chat_id)


async def resume(chat_id: int) -> None:
    """Resume a paused stream."""
    await call_py.resume_stream(chat_id)


async def skip(chat_id: int) -> str | None:
    """
    Skip the current track and play the next one in the queue.

    Returns the next track title, or None if the queue was empty.
    """
    state = _get_state(chat_id)
    if not state["queue"]:
        await stop(chat_id)
        return None

    next_url, next_title = state["queue"].pop(0)
    await call_py.change_stream(
        chat_id,
        AudioPiped(next_url, HighQualityAudio()),
    )
    return next_title


# ─── pytgcalls event: stream ended ────────────────────────────────────────────

@call_py.on_stream_end()
async def on_stream_end(client: PyTgCalls, update: Update) -> None:
    """
    Automatically play the next queued track when a stream finishes,
    or mark the chat as idle if the queue is empty.
    """
    chat_id = update.chat_id
    state = _get_state(chat_id)

    if state["queue"]:
        next_url, next_title = state["queue"].pop(0)
        logger.info("Auto-playing next track in %s: %s", chat_id, next_title)
        await call_py.change_stream(
            chat_id,
            AudioPiped(next_url, HighQualityAudio()),
        )
    else:
        logger.info("Queue empty in %s — leaving voice chat", chat_id)
        state["playing"] = False
        try:
            await call_py.leave_group_call(chat_id)
        except Exception as exc:
            logger.warning("on_stream_end leave: %s", exc)

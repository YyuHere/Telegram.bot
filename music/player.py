"""
music/player.py — Core audio streaming engine.

Uses:
  • yt-dlp     — Download / stream audio from YouTube and 500+ sites.
  • pytgcalls  — Stream audio into a Telegram Voice Chat via the Assistant userbot.

Per-chat state tracks the queue, current track, repeat mode, and paused flag
so the bot can manage multiple groups simultaneously.
"""

import asyncio
import logging
from typing import TypedDict

import yt_dlp

from assistant.userbot import assistant

logger = logging.getLogger(__name__)

# ─── pytgcalls setup ──────────────────────────────────────────────────────────
# call_py is only created when the assistant (Pyrogram Client) exists.
# Any import or instantiation failure is logged with a full traceback so the
# root cause (missing native library, wrong package version, bad session, …)
# is immediately visible in the server logs instead of surfacing only as the
# Arabic "فشل تهيئة محرك الصوت" message in Telegram.
call_py = None
_AudioPiped = None
_HighQualityAudio = None

def _make_stream(url: str):
    """
    Build the pytgcalls stream object for *url*, compatible with whichever
    pytgcalls API version is installed:

      • pytgcalls 0.8.x  →  AudioPiped(url, HighQualityAudio())
      • py-tgcalls / pytgcalls ≥ 3.x  →  MediaStream(url)

    Resolved once at module load; all playback helpers call _make_stream().
    """
    return _stream_builder(url)


# ── Resolve the streaming API available in the installed pytgcalls package ──

def _resolve_stream_builder():
    """
    Detect which pytgcalls streaming constructor is available and return a
    callable ``url → stream_object``.

    Tries newer MediaStream first (py-tgcalls / pytgcalls ≥ 3.x), then falls
    back to AudioPiped / HighQualityAudio (pytgcalls 0.8.x).  Either way the
    full traceback is logged on failure so the exact import error is visible.
    """
    try:
        from pytgcalls.types import MediaStream  # py-tgcalls / 3.x
        logger.info("pytgcalls: using MediaStream API")
        return lambda url: MediaStream(url)
    except ImportError:
        pass

    try:
        from pytgcalls.types.input_stream import AudioPiped
        from pytgcalls.types.input_stream.quality import HighQualityAudio
        logger.info("pytgcalls: using AudioPiped / HighQualityAudio API (0.8.x)")
        return lambda url: AudioPiped(url, HighQualityAudio())
    except ImportError:
        pass

    logger.error(
        "pytgcalls: neither MediaStream nor AudioPiped could be imported. "
        "Check that pytgcalls / py-tgcalls is installed correctly."
    )
    return lambda url: None  # call_py will be None; commands will show error


if assistant is not None:
    try:
        # ── Compatibility patch ───────────────────────────────────────────────
        # py-tgcalls does `from pyrogram.errors import GroupcallForbidden`
        # inside pytgcalls/mtproto/pyrogram_client.py, but pyrogram 2.0.106
        # removed that error class.  Injecting a stub into the pyrogram.errors
        # namespace *before* pytgcalls is first imported prevents the
        # ImportError without changing any runtime behaviour — the exception is
        # used only in a bare `except` clause and is never raised by pyrogram
        # itself in this version.
        # py-tgcalls/mtproto/pyrogram_client.py does:
        #   from pyrogram.errors import GroupcallForbidden
        #   from pyrogram.errors import GroupcallInvalid
        # pyrogram 2.0.106 removed both (it has GroupCallInvalid with a
        # capital C instead).  Inject stubs before pytgcalls is first imported
        # so those module-level imports succeed.  The stubs are only ever used
        # in `except` clauses inside py-tgcalls — they are never raised by
        # pyrogram itself, so behaviour is unchanged.
        import pyrogram.errors as _pyr_errors
        for _missing in ('GroupcallForbidden', 'GroupcallInvalid'):
            if not hasattr(_pyr_errors, _missing):
                setattr(_pyr_errors, _missing, type(_missing, (Exception,), {}))
                logger.debug(
                    "Injected %s stub into pyrogram.errors "
                    "(removed in pyrogram 2.0.106, required by py-tgcalls).",
                    _missing,
                )

        from pytgcalls import PyTgCalls
        _stream_builder = _resolve_stream_builder()
        call_py = PyTgCalls(assistant)
        logger.info("PyTgCalls instance created successfully.")
    except Exception:
        # logger.exception() prints the full traceback, not just the message,
        # so the exact failure reason (import error, version mismatch, bad
        # session string, missing native library, …) is visible in the logs.
        logger.exception(
            "Failed to create PyTgCalls instance. "
            "Check that py-tgcalls, pyrogram==2.0.106, and tgcrypto are "
            "installed at compatible versions, and that "
            "ASSISTANT_SESSION_STRING is valid."
        )
        _stream_builder = lambda url: None
else:
    _stream_builder = lambda url: None


# ─── Track info TypedDict ─────────────────────────────────────────────────────

class TrackInfo(TypedDict):
    url: str
    title: str
    duration: str       # "m:ss"
    thumbnail: str      # URL


# ─── Per-chat state ───────────────────────────────────────────────────────────
# Structure: {
#   chat_id: {
#     "queue":   [TrackInfo, ...],
#     "current": TrackInfo | None,
#     "playing": bool,
#     "paused":  bool,
#     "repeat":  bool,
#   }
# }
_chat_state: dict[int, dict] = {}


def _get_state(chat_id: int) -> dict:
    if chat_id not in _chat_state:
        _chat_state[chat_id] = {
            "queue":   [],
            "current": None,
            "playing": False,
            "paused":  False,
            "repeat":  False,
        }
    return _chat_state[chat_id]


# ─── pytgcalls started flag ───────────────────────────────────────────────────
_pytgcalls_started = False


async def ensure_pytgcalls_started() -> None:
    """
    Start the PyTgCalls client once, attached to the Pyrogram assistant.
    Safe to call multiple times — subsequent calls are no-ops.

    If startup fails (e.g. the session string is expired or invalid, the
    Pyrogram Client was never connected, or a native library is missing),
    the full exception traceback is logged so the root cause is immediately
    visible in the server output.
    """
    global _pytgcalls_started
    if call_py is None or _pytgcalls_started:
        return
    try:
        await call_py.start()
        _pytgcalls_started = True
        logger.info("PyTgCalls client started successfully.")
    except Exception:
        # Use logger.exception() so the full stack trace is printed.
        # Common causes: session string expired/invalid, Pyrogram client not
        # yet connected, missing tgcalls native bindings, or version mismatch
        # between pyrogram / pytgcalls / tgcrypto.
        logger.exception(
            "PyTgCalls .start() failed — voice chat streaming will be "
            "unavailable. Check the ASSISTANT_SESSION_STRING secret and "
            "ensure pyrogram, pytgcalls, and tgcrypto are at compatible versions."
        )


# ─── yt-dlp helpers ───────────────────────────────────────────────────────────

def _ydl_opts() -> dict:
    """
    Build yt-dlp options that avoid YouTube's "Sign in to confirm you're not
    a bot" block on headless / server environments (Railway, Replit, VPS).

    Strategy (in priority order):
      1. Android player client  — YouTube's mobile API skips the bot challenge
         entirely.  ``extractor_args`` forces yt-dlp to request the Android
         InnerTube endpoint instead of the web one.
      2. Cookie file fallback   — If YTDLP_COOKIES_FILE is set in the
         environment (path to a Netscape-format cookies.txt exported from a
         logged-in browser), those cookies are passed through for extra
         resilience on heavily rate-limited IPs.

    The Android client trick is sufficient in the vast majority of cases;
    the cookie file is an optional belt-and-suspenders measure.
    """
    import os

    opts: dict = {
        "format": "bestaudio/best",
        "quiet": True,
        "no_warnings": True,
        "default_search": "ytsearch",
        "noplaylist": True,
        # Force the Android InnerTube player — bypasses the bot-check gate
        # that YouTube applies to headless web requests.
        "extractor_args": {
            "youtube": {
                "player_client": ["android", "web"],
            }
        },
        # A realistic mobile User-Agent reinforces the Android client identity.
        "http_headers": {
            "User-Agent": (
                "com.google.android.youtube/19.09.37 "
                "(Linux; U; Android 11) gzip"
            ),
        },
    }

    # Optional: path to a Netscape cookies.txt file exported from a browser.
    # Set YTDLP_COOKIES_FILE in Railway / Replit Secrets if the Android client
    # alone is blocked on your IP (e.g. heavily shared datacenter addresses).
    cookie_file = os.environ.get("YTDLP_COOKIES_FILE", "").strip()
    if cookie_file and os.path.isfile(cookie_file):
        opts["cookiefile"] = cookie_file
        logger.debug("yt-dlp: using cookie file %s", cookie_file)
    else:
        logger.debug(
            "yt-dlp: no YTDLP_COOKIES_FILE set (or file not found) — "
            "relying on Android client bypass"
        )

    return opts


def _fmt_duration(secs: int) -> str:
    """Convert seconds to m:ss string."""
    m, s = divmod(max(0, int(secs)), 60)
    if m >= 60:
        h, m = divmod(m, 60)
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def get_audio_info(query: str) -> TrackInfo:
    """
    Resolve *query* (title or direct URL) via yt-dlp.

    Returns a TrackInfo dict: url, title, duration (m:ss), thumbnail URL.
    Raises RuntimeError if nothing is found.
    """
    with yt_dlp.YoutubeDL(_ydl_opts()) as ydl:
        info = ydl.extract_info(query, download=False)
        if "entries" in info:
            entries = [e for e in (info["entries"] or []) if e]
            if not entries:
                raise RuntimeError(
                    f"No results found for: {query!r}. "
                    "Try a different search term or a direct YouTube URL."
                )
            info = entries[0]

        url: str = info.get("url") or info.get("webpage_url", "")
        if not url:
            raise RuntimeError(f"Could not resolve audio URL for: {query!r}")

        return TrackInfo(
            url=url,
            title=info.get("title", query),
            duration=_fmt_duration(info.get("duration") or 0),
            thumbnail=info.get("thumbnail", ""),
        )


# ─── Playback control ─────────────────────────────────────────────────────────

async def play(chat_id: int, track: TrackInfo) -> bool:
    """
    Join the voice chat and start streaming *track*.
    If already playing, enqueue the track instead.

    Returns True if playback started, False if queued.
    Raises if the assistant or PyTgCalls is not available.
    """
    if call_py is None:
        raise RuntimeError("Music assistant is not configured.")

    await ensure_pytgcalls_started()

    state = _get_state(chat_id)

    if state["playing"]:
        state["queue"].append(track)
        logger.info("Queued: %s in chat %s", track["title"], chat_id)
        return False  # queued

    state["current"] = track
    state["playing"] = True
    state["paused"] = False

    await call_py.join_group_call(
        chat_id,
        _make_stream(track["url"]),
    )
    logger.info("Started streaming: %s in chat %s", track["title"], chat_id)
    return True


async def stop(chat_id: int) -> None:
    """Stop playback, clear the queue, and leave the voice chat."""
    state = _get_state(chat_id)
    state["playing"] = False
    state["paused"] = False
    state["queue"].clear()
    state["current"] = None
    if call_py is not None:
        try:
            await call_py.leave_group_call(chat_id)
        except Exception as exc:
            logger.warning("stop: leave_group_call raised %s", exc)


async def pause(chat_id: int) -> None:
    """Pause the current stream."""
    state = _get_state(chat_id)
    if call_py is not None and state["playing"] and not state["paused"]:
        await call_py.pause_stream(chat_id)
        state["paused"] = True


async def resume(chat_id: int) -> None:
    """Resume a paused stream."""
    state = _get_state(chat_id)
    if call_py is not None and state["playing"] and state["paused"]:
        await call_py.resume_stream(chat_id)
        state["paused"] = False


async def skip(chat_id: int) -> TrackInfo | None:
    """
    Skip the current track.
    If the queue has a next track, start it and return its info.
    Otherwise stop and return None.
    """
    state = _get_state(chat_id)

    if not state["queue"]:
        await stop(chat_id)
        return None

    next_track = state["queue"].pop(0)
    state["current"] = next_track
    state["paused"] = False

    if call_py is not None:
        await call_py.change_stream(
            chat_id,
            _make_stream(next_track["url"]),
        )
    logger.info("Skipped to: %s in chat %s", next_track["title"], chat_id)
    return next_track


async def toggle_repeat(chat_id: int) -> bool:
    """Toggle repeat mode. Returns the new repeat state (True = on)."""
    state = _get_state(chat_id)
    state["repeat"] = not state["repeat"]
    logger.info("Repeat %s in chat %s", "ON" if state["repeat"] else "OFF", chat_id)
    return state["repeat"]


def is_playing(chat_id: int) -> bool:
    return _get_state(chat_id)["playing"]


def is_paused(chat_id: int) -> bool:
    return _get_state(chat_id)["paused"]


def is_repeat(chat_id: int) -> bool:
    return _get_state(chat_id)["repeat"]


def current_track(chat_id: int) -> TrackInfo | None:
    return _get_state(chat_id)["current"]


# ─── pytgcalls event: stream ended ────────────────────────────────────────────

if call_py is not None:
    @call_py.on_stream_end()
    async def _on_stream_end(client, update) -> None:  # type: ignore[misc]
        """
        Called by pytgcalls when the current stream finishes.
        Handles repeat mode and auto-play of the next queued track.
        """
        try:
            chat_id: int = update.chat_id
        except AttributeError:
            return

        state = _get_state(chat_id)

        # Repeat: replay the current track
        if state["repeat"] and state["current"]:
            track = state["current"]
            logger.info("Repeating: %s in chat %s", track["title"], chat_id)
            try:
                await call_py.change_stream(
                    chat_id,
                    _make_stream(track["url"]),
                )
            except Exception as exc:
                logger.warning("Repeat stream failed: %s", exc)
            return

        # Next in queue
        if state["queue"]:
            next_track = state["queue"].pop(0)
            state["current"] = next_track
            state["paused"] = False
            logger.info("Auto-playing next: %s in chat %s", next_track["title"], chat_id)
            try:
                await call_py.change_stream(
                    chat_id,
                    _make_stream(next_track["url"]),
                )
            except Exception as exc:
                logger.warning("Auto-play next failed: %s", exc)
            return

        # Queue empty — leave
        logger.info("Queue empty in chat %s — leaving voice chat", chat_id)
        state["playing"] = False
        state["paused"] = False
        state["current"] = None
        try:
            await call_py.leave_group_call(chat_id)
        except Exception as exc:
            logger.warning("on_stream_end leave: %s", exc)

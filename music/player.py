"""
music/player.py — Core audio streaming engine.

Uses:
  • yt-dlp     — Download / stream audio from YouTube and 500+ sites.
  • pytgcalls  — Stream audio into a Telegram Voice Chat via the Assistant userbot.

Per-chat state tracks the queue, current track, repeat mode, and paused flag
so the bot can manage multiple groups simultaneously.
"""

import asyncio
import concurrent.futures
import inspect
import logging
from pathlib import Path
import shlex
import shutil
import subprocess
import time
import unicodedata
from difflib import SequenceMatcher
from typing import TypedDict

import yt_dlp

from assistant.userbot import assistant
import config

logger = logging.getLogger(__name__)

# ─── One-time startup diagnostic: is a JS runtime actually on PATH? ───────────
# yt-dlp needs an external JS runtime (node/deno) to solve YouTube's
# signature/n-parameter challenges. Logged once at import time so this shows
# up right next to the other startup lines in Deploy Logs, instead of having
# to infer it indirectly from every later "Signature solving failed" warning.
_node_path = shutil.which("node")
if _node_path:
    try:
        _node_version = subprocess.run(
            [_node_path, "--version"], capture_output=True, text=True, timeout=5,
        ).stdout.strip()
    except Exception as _node_exc:
        _node_version = f"(version check failed: {_node_exc})"
    logger.info("JS runtime check: node found at %s (%s)", _node_path, _node_version)
else:
    logger.warning(
        "JS runtime check: 'node' NOT found on PATH — yt-dlp cannot solve "
        "YouTube's signature/n-parameter challenges without a JS runtime. "
        "Install nodejs in the Dockerfile/build image."
    )

# ─── pytgcalls setup ──────────────────────────────────────────────────────────
# call_py is only created when the assistant (Pyrogram Client) exists.
# Any import or instantiation failure is logged with a full traceback so the
# root cause (missing native library, wrong package version, bad session, …)
# is immediately visible in the server logs instead of surfacing only as the
# Arabic "فشل تهيئة محرك الصوت" message in Telegram.
call_py = None


def _make_stream(url: str):
    """
    Build the pytgcalls stream object for *url* and return it.
    Delegates to _stream_builder which is resolved once at module load.
    """
    stream = _stream_builder(url)
    if stream is None:
        raise RuntimeError(
            "No compatible PyTgCalls audio stream builder is available. "
            "Install py-tgcalls with its ntgcalls dependency and ensure ffmpeg "
            "is installed.",
        )
    return stream


def _resolve_stream_builder():
    """
    Return the correct ``url → stream_object`` callable for the installed
    py-tgcalls version.

    Detection order (most-specific first to avoid noisy ImportErrors):

      1. AudioPiped / HighQualityAudio  — py-tgcalls 0.9.x.
         Import path: pytgcalls.types.input_stream
         Pattern: AudioPiped(url, HighQualityAudio())

      2. Raw shell-backed AudioStream — py-tgcalls 2.x (the version pinned in
         requirements.txt). This explicitly makes FFmpeg emit stereo 48 kHz
         signed 16-bit PCM to stdout, which ntgcalls consumes as microphone
         audio.

      3. MediaStream — py-tgcalls 2.x fallback and newer forks.
         Import path: pytgcalls.types
         Pattern: MediaStream(url)

    If none succeed the engine logs a clear error and returns a no-op lambda;
    callers guard on call_py being None.
    """
    # ── py-tgcalls 0.9.x  ─────────────────────────────────────────────────────
    try:
        from pytgcalls.types.input_stream import AudioPiped          # noqa: PLC0415
        from pytgcalls.types.input_stream.quality import HighQualityAudio  # noqa: PLC0415
        logger.info("pytgcalls stream API: AudioPiped / HighQualityAudio (py-tgcalls 0.9.x)")
        return lambda url: AudioPiped(url, HighQualityAudio())
    except ImportError:
        pass

    # ── py-tgcalls 2.x: explicit FFmpeg → raw PCM pipeline ──────────────────
    try:
        from ntgcalls import MediaSource                         # noqa: PLC0415
        from pytgcalls.types.raw import AudioParameters            # noqa: PLC0415
        from pytgcalls.types.raw import AudioStream                # noqa: PLC0415
        from pytgcalls.types.raw import Stream                     # noqa: PLC0415

        def _raw_audio_stream(url: str):
            """
            Return a raw stream whose input is an FFmpeg shell command.

            PyTgCalls 2.x maps ``MediaSource.SHELL`` to an ntgcalls process
            and reads the command's stdout. The output format must match the
            AudioParameters exactly: signed little-endian 16-bit PCM, 48 kHz,
            stereo.
            """
            audio = AudioParameters(bitrate=48000, channels=2)
            command = [
                "ffmpeg",
                "-hide_banner",
                "-loglevel", "warning",
                "-nostdin",
                "-reconnect", "1",
                "-reconnect_at_eof", "1",
                "-reconnect_streamed", "1",
                "-reconnect_delay_max", "2",
                "-i", url,
                "-vn",
                "-sn",
                "-dn",
                "-f", "s16le",
                "-acodec", "pcm_s16le",
                "-ar", str(audio.bitrate),
                "-ac", str(audio.channels),
                "pipe:1",
            ]
            return Stream(
                microphone=AudioStream(
                    MediaSource.SHELL,
                    shlex.join(command),
                    audio,
                ),
            )

        logger.info(
            "pytgcalls stream API: raw AudioStream via FFmpeg "
            "(48 kHz stereo s16le PCM, py-tgcalls 2.x)",
        )
        return _raw_audio_stream
    except ImportError:
        pass

    # ── MediaStream fallback for newer forks ──────────────────────────────────
    try:
        from pytgcalls.types import MediaStream                       # noqa: PLC0415
        from pytgcalls.types import AudioQuality                       # noqa: PLC0415
        logger.info("pytgcalls stream API: MediaStream (newer/forked API)")
        return lambda url: MediaStream(
            url,
            audio_parameters=AudioQuality.HIGH,
            audio_flags=MediaStream.Flags.REQUIRED,
            video_flags=MediaStream.Flags.IGNORE,
        )
    except ImportError:
        pass

    logger.error(
        "pytgcalls stream API: could not import a raw AudioStream, AudioPiped, "
        "or MediaStream from any known path. Installed py-tgcalls version may "
        "be incompatible. Ensure py-tgcalls>=2.2.11,<3.0, ntgcalls, and "
        "tgcrypto are installed correctly."
    )
    return lambda url: None


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
        _missing_errors = (
            'GroupcallForbidden', 'GroupcallInvalid',
            'GroupcallForbiddenException', 'GroupcallInvalidException',
        )
        for _missing in _missing_errors:
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
    except Exception as _init_exc:
        _exc_str = str(_init_exc).lower()
        if "no module named" in _exc_str or "importerror" in _exc_str:
            logger.error(
                "PyTgCalls init failed — IMPORT ERROR: %s\n"
                "Ensure py-tgcalls>=2.2.11,<3.0, pyrogram==2.0.106, and tgcrypto "
                "are installed. Run: pip install py-tgcalls>=2.2.11,<3.0 "
                "pyrogram==2.0.106 tgcrypto",
                _init_exc,
            )
        elif "ffmpeg" in _exc_str:
            logger.error(
                "PyTgCalls init failed — FFMPEG NOT FOUND: %s\n"
                "Install ffmpeg on the system (apt install ffmpeg or add it "
                "to Dockerfile/replit.nix).",
                _init_exc,
            )
        elif "session" in _exc_str or "auth" in _exc_str:
            logger.error(
                "PyTgCalls init failed — USERBOT SESSION INVALID: %s\n"
                "Regenerate ASSISTANT_SESSION_STRING with generate_session.py.",
                _init_exc,
            )
        else:
            logger.exception(
                "Failed to create PyTgCalls instance (unexpected error). "
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
    source: str         # "youtube_music" | "youtube" | "soundcloud" | "spotify" | "anghami"


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
        if assistant is not None and not assistant.is_connected:
            raise RuntimeError(
                "PyTgCalls reported started, but the Pyrogram assistant is "
                "not connected.",
            )
    except Exception as exc:
        # Use logger.exception() so the full stack trace is printed.
        # Common causes: session string expired/invalid, Pyrogram client not
        # yet connected, missing tgcalls native bindings, or version mismatch
        # between pyrogram / pytgcalls / tgcrypto.
        logger.exception(
            "PyTgCalls .start() failed — voice chat streaming will be "
            "unavailable. Check the ASSISTANT_SESSION_STRING secret and "
            "ensure pyrogram, pytgcalls, and tgcrypto are at compatible versions."
        )
        raise RuntimeError(
            "PyTgCalls could not start; the assistant cannot join voice chats.",
        ) from exc


# ─── Health check ─────────────────────────────────────────────────────────────

def health_check() -> dict:
    """
    Verify that every dependency needed for voice chat streaming is available.

    Checks:
      1. ffmpeg is installed and callable on the system PATH.
      2. PyTgCalls instance was created (call_py is not None).
      3. Pyrogram assistant client exists and is connected.

    Returns a dict with boolean flags and human-readable detail strings.
    Never raises — safe to call at any time, including before the bot starts.
    """
    result: dict = {"ffmpeg": False, "pytgcalls": False, "userbot": False}

    # ── 1. ffmpeg ──────────────────────────────────────────────────────────────
    if shutil.which("ffmpeg") is not None:
        try:
            proc = subprocess.run(
                ["ffmpeg", "-version"],
                capture_output=True, text=True, timeout=5,
            )
            if proc.returncode == 0:
                result["ffmpeg"] = True
                result["ffmpeg_version"] = proc.stdout.split("\n")[0]
            else:
                result["ffmpeg_error"] = "ffmpeg -version returned non-zero"
        except Exception as exc:
            result["ffmpeg_error"] = str(exc)
    else:
        result["ffmpeg_error"] = (
            "ffmpeg not found on PATH. Install it via 'apt install ffmpeg' "
            "or add it to Dockerfile / replit.nix."
        )

    # ── 2. PyTgCalls ───────────────────────────────────────────────────────────
    if call_py is not None:
        result["pytgcalls"] = True
        try:
            import pytgcalls as _ptg
            result["pytgcalls_version"] = getattr(_ptg, "__version__", "unknown")
        except Exception:
            pass
    else:
        result["pytgcalls_error"] = (
            "PyTgCalls instance is None — initialization failed. "
            "Check server logs for the exact import / version error."
        )

    # ── 3. Userbot ──────────────────────────────────────────────────────────────
    if assistant is not None:
        try:
            result["userbot"] = bool(assistant.is_connected)
            if not assistant.is_connected:
                result["userbot_error"] = (
                    "Pyrogram assistant exists but is not connected. "
                    "The session may be invalid or .start() has not been called."
                )
        except Exception as exc:
            result["userbot_error"] = str(exc)
    else:
        result["userbot_error"] = (
            "Pyrogram assistant is None — ASSISTANT_SESSION_STRING, API_ID, "
            "or API_HASH is missing."
        )

    result["all_ok"] = all(
        result.get(k, False) for k in ("ffmpeg", "pytgcalls", "userbot")
    )
    return result


# ─── Source constants ─────────────────────────────────────────────────────────

SOURCE_YOUTUBE = "youtube"
SOURCE_YOUTUBE_MUSIC = "youtube_music"
SOURCE_SOUNDCLOUD = "soundcloud"
SOURCE_SPOTIFY = "spotify"   # Spotify metadata → YouTube stream
SOURCE_ANGHAMI = "anghami"  # Anghami metadata → YouTube stream


# ─── Helpers ──────────────────────────────────────────────────────────────────

import re as _re

def _fmt_duration(secs: int) -> str:
    """Convert seconds to m:ss string."""
    m, s = divmod(max(0, int(secs)), 60)
    if m >= 60:
        h, m = divmod(m, 60)
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


# Arabic diacritical marks (tashkeel / harakat) — strip before searching so
# "أُغنِيَة" matches the same result as "اغنية".
_TASHKEEL_RE = _re.compile(
    r"[\u0610-\u061A\u064B-\u065F\u0670\u06D6-\u06DC\u06DF-\u06E4\u06E7\u06E8\u06EA-\u06ED]"
)


def _strip_tashkeel(text: str) -> str:
    """Remove Arabic diacritical marks from *text* (no-op for non-Arabic)."""
    return _TASHKEEL_RE.sub("", text).strip()


# Keep provider searches broad enough to find regional releases and alternate
# spellings.  The old hard-coded ``5`` made a valid result disappear whenever
# the first few YouTube/SoundCloud hits were remixes, clips, or unavailable.
# Search only a small candidate set. The resolver now extracts one selected
# result instead of expanding every search entry, so a large result page only
# adds throttling without improving playback quality.
_SEARCH_RESULT_LIMIT = 5
_SEARCH_VARIANT_LIMIT = 1
_YOUTUBE_MUSIC_RESULT_LIMIT = 3
_SEARCH_PUNCTUATION_RE = _re.compile(r"[^\w\s\u0600-\u06FF]+", _re.UNICODE)


def _search_query_variants(query: str) -> list[str]:
    """
    Build a small, deterministic set of tolerant search queries.

    Search engines already handle some fuzzy matching, but punctuation,
    full-width characters, Arabic tashkeel, and long metadata suffixes can
    push the useful result out of the first page.  Keep the original query
    first, then progressively normalize it and finally add provider-friendly
    suffixes.  Duplicates are removed while preserving order.
    """
    variants: list[str] = []
    seen: set[str] = set()

    def _add(value: str) -> None:
        value = " ".join(value.split()).strip()
        if value and value.casefold() not in seen:
            seen.add(value.casefold())
            variants.append(value)

    original = query.strip()
    normalized = unicodedata.normalize("NFKC", original)
    stripped = _strip_tashkeel(normalized)
    # Remove bracketed release notes before punctuation normalization, since
    # the latter would otherwise erase the brackets and make the boundaries
    # impossible to detect.
    without_release_notes = _re.sub(
        r"\s*[\(\[\{][^)\]\}]*[\)\]\}]",
        " ",
        stripped,
    )
    punctuation_free = _SEARCH_PUNCTUATION_RE.sub(" ", stripped)
    punctuation_free = " ".join(punctuation_free.split())

    _add(original)
    _add(normalized)
    _add(stripped)
    _add(punctuation_free)

    # Also search without parenthesized/bracketed release notes such as
    # "(official video)" or "[نسخة كاملة]".
    clean_punctuation_free = _SEARCH_PUNCTUATION_RE.sub(" ", without_release_notes)
    clean_punctuation_free = " ".join(clean_punctuation_free.split())
    _add(clean_punctuation_free)

    words = clean_punctuation_free.split()
    if len(words) > 6:
        _add(" ".join(words[:6]))
    if len(words) > 4:
        _add(" ".join(words[:4]))

    # These are deliberately last: they help YouTube/SoundCloud rank a full
    # song over short clips without replacing the user's exact query.
    base = clean_punctuation_free or punctuation_free or stripped
    if base:
        _add(f"{base} audio")
        _add(f"{base} song")

    return variants


_PLATFORM_HOSTS = (
    "youtube.com", "www.youtube.com", "youtu.be", "m.youtube.com",
    "music.youtube.com",
    "soundcloud.com", "www.soundcloud.com",
    "spotify.com", "open.spotify.com",
    "anghami.com", "play.anghami.com",
)

_DIRECT_AUDIO_EXTS = (".mp3", ".m4a", ".ogg", ".opus", ".wav", ".flac", ".aac", ".webm")


def _is_url(text: str) -> bool:
    """Return True if *text* is a URL rather than a plain search query."""
    if text.startswith(("http://", "https://", "www.")):
        return True
    lower = text.lower()
    return any(lower.startswith(host + "/") or lower == host for host in _PLATFORM_HOSTS)


def _is_direct_audio_url(url: str) -> bool:
    """Return True when the URL path ends with a known audio extension."""
    path = url.split("?")[0].lower()
    return any(path.endswith(ext) for ext in _DIRECT_AUDIO_EXTS)


# Keep the client fallback deliberately small. Each client attempt can trigger
# several YouTube requests, so cycling through a long list amplifies 429s and
# bot challenges instead of improving reliability.
#
# android_vr is preferred because it avoids the tv client's DRM-only results.
# web_safari is the one bounded fallback that can make better use of a browser
# cookie jar. "web", "ios", "mweb", and embedded clients stay out of the hot
# path.
_CLIENT_FALLBACK_CHAIN: tuple[tuple[str, ...], ...] = (
    ("android_vr",),
)
_COOKIE_CLIENT_FALLBACK_CHAIN: tuple[tuple[str, ...], ...] = (
    *_CLIENT_FALLBACK_CHAIN,
    ("web_safari",),
)

_YOUTUBE_BLOCK_KEYWORDS = (
    "http error 429",
    "429 too many requests",
    "too many requests",
    "sign in to confirm",
    "confirm you're not a bot",
    "confirm you’re not a bot",
    "captcha",
    "login required",
)
_youtube_blocked_until = 0.0


class _YtdlpDiagnosticLogger:
    """
    Forwards yt-dlp's internal debug/warning/error messages into our own
    logger instead of letting them vanish behind quiet=True/no_warnings=True.

    Those internal messages ("YouTube is forcing SABR streaming for this
    client", "Some formats may be missing", PO Token complaints, access
    errors, etc.) are the ONLY place the *actual* reason a client failed
    shows up — the generic "Requested format is not available" exception
    string alone is not enough to tell SABR-block apart from a bad cookie,
    a genuinely unavailable video, or a PO Token requirement. debug() is
    intentionally dropped (too noisy — includes per-request URLs); warning()
    and error() are the ones worth keeping.
    """

    def __init__(self, client_label: str) -> None:
        self._client_label = client_label

    def debug(self, msg: str) -> None:
        # Almost all debug() calls are per-request noise (URLs, headers), so
        # those are still dropped. The exception: anything mentioning the JS
        # runtime / EJS solver — that's exactly the info needed to tell
        # whether yt-dlp found "node" at all, tried it, and why it did or
        # didn't work. Those lines are rare enough not to flood the log.
        lowered = msg.lower()
        if any(kw in lowered for kw in ("js runtime", "js-runtime", "js_runtime", "ejs", "deno", "node")):
            logger.warning("yt-dlp[%s] DEBUG: %s", self._client_label, msg)

    def warning(self, msg: str) -> None:
        logger.warning("yt-dlp[%s]: %s", self._client_label, msg)

    def error(self, msg: str) -> None:
        logger.warning("yt-dlp[%s] ERROR: %s", self._client_label, msg)


def _cookie_file_path() -> Path | None:
    """
    Return the configured cookie jar only when it is a usable local file.

    Cookie contents are never read into logs or application state. Resolving
    the path prevents a workflow working-directory change from silently
    disabling authentication.
    """
    configured = str(config.YTDLP_COOKIE_FILE or "").strip()
    if not configured:
        return None

    path = Path(configured).expanduser()
    try:
        if path.is_file() and path.stat().st_size > 0:
            return path.resolve()
    except OSError as exc:
        logger.warning("yt-dlp cookie file cannot be accessed: %s", exc)
    return None


def _youtube_target(target: str) -> bool:
    lowered = target.lower()
    return (
        lowered.startswith(("ytsearch:", "ytsearch"))
        or "youtube.com/" in lowered
        or "youtu.be/" in lowered
    )


def _is_youtube_block_error(exc: Exception) -> bool:
    message = str(exc).casefold()
    return any(keyword in message for keyword in _YOUTUBE_BLOCK_KEYWORDS)


def _mark_youtube_blocked(target: str, exc: Exception) -> None:
    """Pause further YouTube attempts briefly after a provider block."""
    global _youtube_blocked_until
    cooldown = max(0.0, config.YTDLP_BLOCK_COOLDOWN)
    _youtube_blocked_until = time.monotonic() + cooldown
    logger.warning(
        "YouTube blocked request %r; stopping client fallbacks for %.0fs: %s",
        target,
        cooldown,
        exc,
    )


def _youtube_is_blocked(target: str) -> bool:
    return _youtube_target(target) and time.monotonic() < _youtube_blocked_until


def _client_fallback_chain(
    *,
    use_cookies: bool,
) -> tuple[tuple[str, ...], ...]:
    """Return one client normally, or one bounded cookie-aware fallback."""
    if use_cookies and _cookie_file_path() is not None:
        return _COOKIE_CLIENT_FALLBACK_CHAIN
    return _CLIENT_FALLBACK_CHAIN


def _ydl_opts(
    extra: dict | None = None,
    player_clients: tuple[str, ...] = ("android_vr",),
    *,
    use_cookies: bool = False,
) -> dict:
    """
    Build yt-dlp options with realistic browser headers and a YouTube player
    client that survives SABR-only enforcement.

    YouTube began forcing SABR streaming on the "web" client in 2026, which
    removed the direct adaptiveFormats playback URLs yt-dlp relied on. "ios"
    still requires a PO Token to unlock formats. Which of the remaining
    clients (tv / android_vr / android / mweb) actually returns usable
    formats varies by video and changes over time, so no single format
    string can assume itag 18 will always be there — "best" is left as the
    final catch-all rather than hard-requiring it. See:
    https://github.com/yt-dlp/yt-dlp/issues/12482
    """
    opts: dict = {
        # No specific itag is guaranteed across clients, so prefer an
        # audio-only stream when one exists and otherwise fall through to
        # whatever the client actually returns.
        "format": "bestaudio/best[acodec!=none]/best",
        "quiet": True,
        # Messages are still captured — just routed through our own
        # logger (see _YtdlpDiagnosticLogger) instead of being discarded.
        "no_warnings": False,
        "logger": _YtdlpDiagnosticLogger(",".join(player_clients)),
        # yt-dlp only tries Deno for its JS challenge solver (EJS) by
        # default — it will NOT fall back to Node even if Node is on PATH.
        # The Dockerfile installs nodejs/npm (no Deno), so without this the
        # signature/n-parameter challenges silently fail on every client,
        # which is what was happening. https://github.com/yt-dlp/yt-dlp/wiki/EJS
        "js_runtimes": {"node": {}},
        # yt-dlp's Python package includes the solver core, but the current
        # YouTube player can require a matching external EJS release. Allow
        # yt-dlp to fetch and cache that release when the bundled solver cannot
        # handle a newly rotated player script.
        "remote_components": ["ejs:github"],
        "noplaylist": True,
        # Do not let yt-dlp internally retry a blocked request. The caller
        # owns the small, explicit client fallback below and can fail fast on
        # 429/challenge responses.
        "retries": 0,
        "fragment_retries": 0,
        "extractor_retries": 0,
        "file_access_retries": 0,
        "sleep_interval_requests": config.YTDLP_REQUEST_DELAY,
        "socket_timeout": config.YTDLP_SOCKET_TIMEOUT,
        # Search calls are metadata-only. The selected page is resolved in a
        # separate authenticated call, so yt-dlp never expands every result
        # just to inspect its formats.
        "extract_flat": "in_playlist",
        "skip_download": True,
        "writesubtitles": False,
        "writeautomaticsub": False,
        "writethumbnail": False,
        "writeinfojson": False,
        "getcomments": False,
        "writeannotations": False,
        # Extraction failures must raise so the fallback can classify them.
        "ignoreerrors": False,
        "extractor_args": {
            "youtube": {"player_client": list(player_clients)},
        },
        "http_headers": {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "ar,en-US;q=0.9,en;q=0.8",
        },
    }
    if extra:
        opts.update(extra)

    # Cookies are for authenticated page/stream resolution only. Metadata
    # searches intentionally omit the jar to avoid sending account cookies to
    # provider search endpoints and to keep catalog scans lightweight.
    if use_cookies:
        cookie_file = _cookie_file_path()
        if cookie_file is not None:
            opts["cookiefile"] = str(cookie_file)
            logger.debug("yt-dlp cookie file enabled: %s", cookie_file)
        else:
            logger.warning(
                "yt-dlp cookie authentication requested, but YTDLP_COOKIE_FILE "
                "does not point to a non-empty file; continuing without cookies."
            )

    return opts


def _extract_info_with_fallback(
    target: str,
    extra: dict | None = None,
    *,
    use_cookies: bool = True,
) -> dict | None:
    """
    Run yt_dlp.extract_info(target) through the bounded client chain, returning
    the first successful result. A YouTube 429 or bot challenge immediately
    opens a short circuit-breaker window instead of trying more clients.

    Centralizes the limited SABR fallback so every caller (search, direct URL
    resolution) gets the same fail-fast behavior. Returns None if the provider
    is blocked or every bounded client attempt fails.
    """
    last_exc: Exception | None = None
    for clients in _client_fallback_chain(use_cookies=use_cookies):
        if _youtube_is_blocked(target):
            logger.info("Skipping YouTube request during provider cooldown: %r", target)
            return None
        opts = _ydl_opts(
            extra,
            player_clients=clients,
            use_cookies=use_cookies,
        )
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                result = ydl.extract_info(target, download=False)
            if result is not None:
                return result
            logger.debug(
                "extract_info(%r) returned None with client(s) %s — trying next client",
                target, clients,
            )
        except Exception as exc:
            last_exc = exc
            if _youtube_target(target) and _is_youtube_block_error(exc):
                _mark_youtube_blocked(target, exc)
                return None
            logger.debug(
                "extract_info(%r) failed with client(s) %s: %s",
                target, clients, exc,
            )
    if last_exc is not None:
        logger.warning(
            "extract_info(%r) failed on all client fallbacks: %s",
            target, last_exc,
        )
    return None


def _search_track_with_client_fallback(
    target: str,
    query: str,
    source: str,
) -> "TrackInfo | None":
    """
    Search metadata without cookies, then resolve only the best candidate.

    The old implementation expanded up to eight entries for every client and
    query variant. That multiplies page/format requests and is a common cause
    of HTTP 429 responses. Search results are now flat metadata; only the
    selected page receives the authenticated extraction call.
    """
    for clients in _client_fallback_chain(use_cookies=False):
        if _youtube_is_blocked(target):
            logger.info("Skipping YouTube search during provider cooldown: %r", target)
            return None
        opts = _ydl_opts(
            player_clients=clients,
            use_cookies=False,
        )
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(target, download=False)
        except Exception as exc:
            if _youtube_target(target) and _is_youtube_block_error(exc):
                _mark_youtube_blocked(target, exc)
                return None
            logger.debug(
                "Search %r failed with client(s) %s: %s", query, clients, exc,
            )
            continue

        if not info:
            continue

        metadata_entries = [
            entry
            for entry in (info.get("entries") or [])
            if entry
        ] if "entries" in info else [info]
        metadata_tracks = [
            track
            for entry in metadata_entries[:_SEARCH_RESULT_LIMIT]
            if (track := _metadata_track_from_entry(entry, query, source))
        ]
        metadata_tracks.sort(
            key=lambda track: _match_score(query, track),
            reverse=True,
        )

        for candidate in metadata_tracks[:2]:
            resolved = _resolve_metadata_track(candidate, query)
            if resolved:
                return resolved

    return None


def _stream_url_from_info(info: dict) -> str:
    """
    Return a direct media URL from a yt-dlp info dict.

    Search results are not guaranteed to be fully expanded.  In particular,
    YouTube and SoundCloud may return an entry with only ``webpage_url`` while
    the actual audio URL lives in ``formats``.  Never hand a webpage URL to
    PyTgCalls: it expects a media stream that ffmpeg can open.
    """
    formats = info.get("formats") or []
    candidates = [
        fmt for fmt in formats
        if fmt.get("url")
        and fmt.get("acodec") not in (None, "none")
        and fmt.get("protocol") not in ("m3u8", "m3u8_native", "http_dash_segments")
    ]
    if candidates:
        # Prefer audio-only formats and then the highest audio bitrate.  The
        # latter is useful for SoundCloud, where formats often have no abr but
        # do expose tbr.
        candidates.sort(
            key=lambda fmt: (
                fmt.get("vcodec") in (None, "none"),
                fmt.get("abr") or 0,
                fmt.get("tbr") or 0,
            ),
            reverse=True,
        )
        return candidates[0]["url"]

    url = info.get("url") or ""
    webpage_url = info.get("webpage_url") or info.get("original_url") or ""
    if url and url != webpage_url:
        # A fully expanded yt-dlp entry carries codec/protocol metadata. The
        # URL can be an expiring CDN URL (or another provider's media URL), so
        # do not restrict it to a hard-coded hostname list.
        if (
            info.get("acodec") not in (None, "none")
            or info.get("protocol") in ("http", "https", "http_dash_segments")
            or _is_direct_audio_url(url)
        ):
            return url
    return ""


def _track_from_entry(entry: dict, query: str, source: str) -> "TrackInfo | None":
    """Build a track from one search entry, expanding flat entries when needed."""
    url = _stream_url_from_info(entry)

    # A search extractor can return a flat entry. Resolve its page to obtain
    # the expiring CDN URL before returning it to the voice-chat player.
    webpage_url = entry.get("webpage_url") or entry.get("original_url") or ""
    if not url and webpage_url:
        expanded = _extract_info_with_fallback(webpage_url)
        if expanded:
            url = _stream_url_from_info(expanded)
            if url:
                entry = expanded

    if not url:
        return None

    return TrackInfo(
        url=url,
        title=entry.get("title", query),
        duration=_fmt_duration(entry.get("duration") or 0),
        thumbnail=entry.get("thumbnail", ""),
        source=source,
    )


def _metadata_track_from_entry(
    entry: dict,
    query: str,
    source: str,
) -> "TrackInfo | None":
    """Build a flat metadata candidate without requesting its media formats."""
    webpage_url = entry.get("webpage_url") or entry.get("original_url") or ""
    if not webpage_url and entry.get("id") and source in (
        SOURCE_YOUTUBE,
        SOURCE_YOUTUBE_MUSIC,
    ):
        webpage_url = f"https://music.youtube.com/watch?v={entry['id']}" \
            if source == SOURCE_YOUTUBE_MUSIC \
            else f"https://www.youtube.com/watch?v={entry['id']}"
    if not webpage_url:
        return None

    return TrackInfo(
        url=webpage_url,
        title=entry.get("title") or query,
        duration=_fmt_duration(entry.get("duration") or 0),
        thumbnail=entry.get("thumbnail", ""),
        source=source,
    )


def _resolve_metadata_track(
    candidate: "TrackInfo",
    query: str,
) -> "TrackInfo | None":
    """Resolve one flat candidate through the authenticated stream path."""
    info = _extract_info_with_fallback(candidate["url"], use_cookies=True)
    if info is None:
        return None
    return _extract_track(info, query, candidate["source"])


def _title_tokens(value: str) -> set[str]:
    """Return comparable words from Arabic or Latin song text."""
    normalized = _strip_tashkeel(unicodedata.normalize("NFKC", value)).casefold()
    normalized = _SEARCH_PUNCTUATION_RE.sub(" ", normalized)
    return {token for token in normalized.split() if len(token) > 1}


def _normalized_title(value: str) -> str:
    """Normalize punctuation and spacing for exact song-title comparisons."""
    normalized = _strip_tashkeel(unicodedata.normalize("NFKC", value)).casefold()
    normalized = _SEARCH_PUNCTUATION_RE.sub(" ", normalized)
    return " ".join(normalized.split())


def _youtube_music_result_is_exact(result: dict, query: str) -> bool:
    """
    Return whether a YouTube Music result names the requested song exactly.

    The title is the primary identity. Artist-title combinations are also
    accepted because users commonly enter both fields in one request.
    """
    normalized_query = _normalized_title(query)
    title = _normalized_title(result.get("title") or "")
    if title == normalized_query:
        return True

    artists = " ".join(
        artist.get("name") or ""
        for artist in (result.get("artists") or [])
        if isinstance(artist, dict)
    )
    normalized_artists = _normalized_title(artists)
    if not normalized_artists:
        return False
    return (
        _normalized_title(f"{artists} {result.get('title') or ''}") == normalized_query
        or _normalized_title(f"{result.get('title') or ''} {artists}") == normalized_query
    )


def _match_score(query: str, track: "TrackInfo") -> float:
    """
    Score how closely a playable result matches the requested song name.

    Provider ordering is intentionally not part of this score. An exact
    SoundCloud/Anghami result should beat a vague YouTube result, and the
    same rule works for Arabic, transliterated Arabic, and Latin titles.
    """
    requested_tokens = _title_tokens(query)
    title_tokens = _title_tokens(track["title"])
    if not requested_tokens or not title_tokens:
        return 0.0

    requested = " ".join(sorted(requested_tokens))
    title = " ".join(sorted(title_tokens))
    sequence = SequenceMatcher(None, requested, title).ratio()
    overlap = len(requested_tokens & title_tokens) / len(requested_tokens)

    # Penalize common non-song uploads without rejecting them. This keeps an
    # exact official audio ahead of a similarly named short, live, or remix.
    lowered = track["title"].casefold()
    quality_penalty = sum(
        0.06 for marker in (
            "short", "shorts", "clip", "teaser", "trailer", "live",
            "remix", "sped up", "slowed", "cover", "karaoke",
        ) if marker in lowered
    )
    return max(0.0, (sequence * 0.45) + (overlap * 0.55) - quality_penalty)


def _extract_tracks(
    info: dict,
    query: str,
    source: str,
    limit: int = 8,
) -> list["TrackInfo"]:
    """Expand several search entries and retain only playable streams."""
    entries = [e for e in (info.get("entries") or []) if e] if "entries" in info else [info]
    tracks: list[TrackInfo] = []
    seen: set[str] = set()
    for entry in entries[:limit]:
        track = _track_from_entry(entry, query, source)
        if track and track["url"] not in seen:
            seen.add(track["url"])
            tracks.append(track)
    return tracks


def _extract_track(info: dict, query: str, source: str = SOURCE_YOUTUBE) -> "TrackInfo | None":
    """
    Pull the best matching usable entry from a yt-dlp result dict.
    """
    tracks = _extract_tracks(info, query, source, limit=8)
    return max(tracks, key=lambda item: _match_score(query, item), default=None)


# ─── Stage 1: YouTube ─────────────────────────────────────────────────────────

def search_youtube(query: str, source_tag: str = SOURCE_YOUTUBE) -> "TrackInfo | None":
    """
    Search standard YouTube for *query* via the literal ``ytsearch:`` fallback
    prefix and return its first playable result.

    YouTube Music is attempted before this function by ``get_audio_info``.
    Uses one query variant to avoid duplicate provider requests while retaining
    the exact user query as the first search form.

    Returns None when all variants fail so the caller can fall through to the
    next source.  Never raises.
    """
    for q in _search_query_variants(query)[:_SEARCH_VARIANT_LIMIT]:
        track = _search_track_with_client_fallback(
            f"ytsearch:{q}",
            q,
            source_tag,
        )
        if track:
            logger.info("YouTube search: found %r for query %r", track["title"], query)
            return track
    return None


def search_youtube_music(query: str) -> "TrackInfo | None":
    """
    Search the public YouTube Music catalog and resolve a playable track.

    ``ytmusicapi`` supplies song-focused results (rather than the broader
    YouTube video index). Each result is then extracted through a
    ``music.youtube.com`` URL so the primary source remains YouTube Music all
    the way through stream resolution. If the catalog or every candidate
    extraction fails, return ``None`` and let the caller use standard YouTube.

    The API is intentionally created lazily and unauthenticated: song search
    must not require a browser cookie, login session, or extra secret.
    """
    try:
        from ytmusicapi import YTMusic

        catalog = YTMusic()
        # A few normalized variants improve Arabic/punctuation tolerance while
        # avoiding the large number of extraction requests used by the broader
        # provider searches.
        variants = _search_query_variants(query)[:_SEARCH_VARIANT_LIMIT]
        results: list[dict] = []
        seen_video_ids: set[str] = set()

        for candidate_query in variants:
            catalog_results = catalog.search(
                candidate_query,
                filter="songs",
                limit=_YOUTUBE_MUSIC_RESULT_LIMIT,
            )
            for result in catalog_results or []:
                video_id = result.get("videoId")
                if video_id and video_id not in seen_video_ids:
                    seen_video_ids.add(video_id)
                    results.append(result)

        if not results:
            logger.info("YouTube Music search returned no songs for %r", query)
            return None

        # Keep the catalog's result order, but move exact title/artist-title
        # matches ahead of fuzzy matches. This avoids replacing the requested
        # festival/song with a more popular but differently named result.
        exact_results = [
            result for result in results
            if _youtube_music_result_is_exact(result, query)
        ]
        exact_ids = {result.get("videoId") for result in exact_results}
        results = exact_results + [
            result for result in results
            if result.get("videoId") not in exact_ids
        ]

        for result in results:
            video_id = result["videoId"]
            music_url = f"https://music.youtube.com/watch?v={video_id}"
            info = _extract_info_with_fallback(music_url, use_cookies=True)
            if info is None:
                continue

            track = _extract_track(info, query, SOURCE_YOUTUBE_MUSIC)
            if track:
                logger.info(
                    "YouTube Music search: found %r for query %r (exact=%s)",
                    track["title"],
                    query,
                    _youtube_music_result_is_exact(result, query),
                )
                return track

        logger.info(
            "YouTube Music found songs but none were playable for %r",
            query,
        )
    except Exception as exc:
        logger.warning("YouTube Music search failed for %r: %s", query, exc)

    return None


def search_soundcloud(query: str) -> "TrackInfo | None":
    """
    Search SoundCloud as a provider fallback.

    SoundCloud is deliberately resolved through yt-dlp rather than scraping
    HTML.  ``scsearch20`` returns multiple candidates; each candidate is
    expanded and checked for a direct CDN URL, so one unavailable result does
    not abort the whole search.
    """
    best: TrackInfo | None = None
    best_score = -1.0
    for candidate in _search_query_variants(query)[:_SEARCH_VARIANT_LIMIT]:
        track = _search_track_with_client_fallback(
            f"scsearch{_SEARCH_RESULT_LIMIT}:{candidate}",
            candidate,
            SOURCE_SOUNDCLOUD,
        )
        if track and _match_score(query, track) > best_score:
            best, best_score = track, _match_score(query, track)
    if best:
        logger.info("SoundCloud search: found %r for query %r", best["title"], query)
    return best


def get_best_audio_url(url_or_id: str) -> "TrackInfo | None":
    """
    Extract the best audio stream URL from a YouTube video URL or ID.

    Used for direct YouTube links and for Spotify/Anghami-guided lookups
    where we already know the exact video.  Never raises.
    """
    if not url_or_id.startswith("http"):
        url_or_id = f"https://www.youtube.com/watch?v={url_or_id}"

    info = _extract_info_with_fallback(url_or_id)
    if info is None:
        return None
    return _extract_track(info, url_or_id, SOURCE_YOUTUBE)


# ─── Stage 2: Spotify metadata ────────────────────────────────────────────────

def search_spotify(query: str) -> "str | None":
    """
    Search Spotify for *query* and return ``"Artist - Track Name"``.

    Requires ``SPOTIFY_CLIENT_ID`` and ``SPOTIFY_CLIENT_SECRET`` in config.
    Uses the Client Credentials flow — no user login required.
    Returns None when credentials are absent, the API is unreachable, or no
    results are found.  Never raises.
    """
    try:
        import config as _cfg
        if not (_cfg.SPOTIFY_CLIENT_ID and _cfg.SPOTIFY_CLIENT_SECRET):
            return None

        import spotipy
        from spotipy.oauth2 import SpotifyClientCredentials

        sp = spotipy.Spotify(
            auth_manager=SpotifyClientCredentials(
                client_id=_cfg.SPOTIFY_CLIENT_ID,
                client_secret=_cfg.SPOTIFY_CLIENT_SECRET,
            )
        )
        results = sp.search(q=query, limit=5, type="track")
        items = (results.get("tracks") or {}).get("items") or []
        if not items:
            return None

        track = items[0]
        artist = track["artists"][0]["name"] if track.get("artists") else ""
        title  = track.get("name", "")
        if title:
            refined = f"{artist} - {title}" if artist else title
            logger.info("Spotify metadata: %r for query %r", refined, query)
            return refined

    except Exception as exc:
        logger.warning("Spotify search failed for %r: %s", query, exc)

    return None


# ─── Stage 3: Anghami metadata ────────────────────────────────────────────────

def search_anghami(query: str) -> "str | None":
    """
    Search Anghami's public REST API for *query* and return
    ``"Artist - Track Name"`` to guide a refined YouTube search.

    Anghami is particularly strong for Arabic songs and mahraganat that
    SoundCloud / Spotify may not carry.  Returns None on any failure so the
    caller can skip gracefully.  Never raises.
    """
    try:
        import requests

        resp = requests.get(
            "https://api.anghami.com/rest/v1/search.json",
            params={
                "query": query,
                "limit": 5,
                "searchsections[]": 0,   # songs section only
            },
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; TelegramMusicBot/1.0)",
                "Accept": "application/json",
                "Accept-Language": "ar,en;q=0.8",
            },
            timeout=8,
        )
        if not resp.ok:
            logger.debug("Anghami API returned HTTP %s for %r", resp.status_code, query)
            return None

        data = resp.json()
        # Response shape: {"sections": [{"data": [{"title": ..., "artist": ...}]}]}
        for section in (data.get("sections") or []):
            for item in (section.get("data") or []):
                title  = item.get("title") or item.get("name") or ""
                artist = item.get("artist") or item.get("artistname") or ""
                if title:
                    refined = f"{artist} - {title}" if artist else title
                    logger.info("Anghami metadata: %r for query %r", refined, query)
                    return refined

    except Exception as exc:
        logger.warning("Anghami search failed for %r: %s", query, exc)

    return None


def _search_spotify_stream(query: str) -> "TrackInfo | None":
    """Use Spotify's text index to improve the query, then stream elsewhere."""
    refined = search_spotify(query)
    return search_youtube(refined, SOURCE_SPOTIFY) if refined else None


def _search_anghami_stream(query: str) -> "TrackInfo | None":
    """Use Anghami's Arabic text index to improve the query, then stream elsewhere."""
    refined = search_anghami(query)
    return search_youtube(refined, SOURCE_ANGHAMI) if refined else None


def _search_secondary_platforms(query: str) -> list["TrackInfo"]:
    """
    Search non-YouTube secondary indexes concurrently and return playable
    candidates.

    YouTube Music and standard YouTube are resolved before this function is
    called. Spotify and Anghami are metadata indexes only; the actual stream
    is still resolved through a public, yt-dlp-supported media result. This
    avoids depending on a login cookie, private API session, or a restricted
    page from any one site.
    """
    searches = {
        "soundcloud": search_soundcloud,
        "spotify": _search_spotify_stream,
        "anghami": _search_anghami_stream,
    }
    results: list[TrackInfo] = []

    with concurrent.futures.ThreadPoolExecutor(
        max_workers=len(searches),
        thread_name_prefix="music-search",
    ) as executor:
        futures = {
            executor.submit(search, query): provider
            for provider, search in searches.items()
        }
        for future in concurrent.futures.as_completed(futures):
            provider = futures[future]
            try:
                track = future.result()
            except Exception as exc:
                logger.warning("%s text search failed for %r: %s", provider, query, exc)
                continue
            if track:
                logger.info(
                    "Candidate from %s: %r (match=%.3f)",
                    provider,
                    track["title"],
                    _match_score(query, track),
                )
                results.append(track)

    return results


# ─── Main resolution function ─────────────────────────────────────────────────

def get_audio_info(query: str) -> TrackInfo:
    """
    Resolve *query* (song name or direct URL) to a playable audio stream.

    Text search strategy
    ─────────────────────
    0. Direct URL      — supported as an explicit user request only.
    1. YouTube Music   — song-focused catalog search and Music URL extraction.
    2. YouTube         — standard ``ytsearch20`` fallback.
    3. SoundCloud      — ``scsearch20`` independent provider search.
    4. Spotify         — optional metadata search → public stream search.
    5. Anghami         — public metadata search → public stream search.

    YouTube Music and standard YouTube are deliberately sequential so the
    primary source always wins when it can provide a playable stream. The
    remaining secondary searches run concurrently only after both YouTube
    stages fail. yt-dlp never receives a cookie file.

    Raises RuntimeError with an Arabic message when every stage fails.
    """
    # ── Stage 0: direct URL ───────────────────────────────────────────────────
    if _is_url(query):
        url = query if query.startswith(("http://", "https://")) else f"https://{query}"

        # Raw audio file (.mp3 / .opus / etc.) — pipe directly, skip yt-dlp.
        if _is_direct_audio_url(url):
            filename = url.split("/")[-1].split("?")[0] or url
            logger.info("Direct audio file — skipping extraction: %r", filename)
            return TrackInfo(
                url=url, title=filename, duration="0:00",
                thumbnail="", source=SOURCE_YOUTUBE,
            )

        # Determine source tag from the URL before extracting.
        if "soundcloud.com" in url:
            tag = SOURCE_SOUNDCLOUD
        elif "anghami.com" in url:
            tag = SOURCE_ANGHAMI
        elif "spotify.com" in url:
            tag = SOURCE_SPOTIFY
        elif "music.youtube.com" in url:
            tag = SOURCE_YOUTUBE_MUSIC
        else:
            tag = SOURCE_YOUTUBE

        info = _extract_info_with_fallback(url)
        if info is not None:
            track = _extract_track(info, url, tag)
            if track:
                return track

        raise RuntimeError(
            "❌ تعذّر استخراج الصوت من هذا الرابط.\n"
            "تأكد من صحة الرابط أو أرسل رابط يوتيوب مباشرًا."
        )

    # ── YouTube Music is the primary text search and stream source ─────────────
    # Keep this before all other providers. A playable Music result must not be
    # displaced by a higher-scoring result from standard YouTube or SoundCloud.
    youtube_music_track = search_youtube_music(query)
    if youtube_music_track:
        return youtube_music_track

    # ── Standard YouTube is the first fallback ─────────────────────────────────
    youtube_track = search_youtube(query)
    if youtube_track:
        return youtube_track

    # ── Remaining provider fallbacks ───────────────────────────────────────────
    candidates = _search_secondary_platforms(query)
    if candidates:
        # Deduplicate equivalent CDN/page resolutions while retaining the
        # highest-quality title match.
        unique: dict[str, TrackInfo] = {}
        for candidate in candidates:
            current = unique.get(candidate["url"])
            if current is None or _match_score(query, candidate) > _match_score(query, current):
                unique[candidate["url"]] = candidate
        return max(unique.values(), key=lambda item: _match_score(query, item))

    raise RuntimeError(
        "❌ لم يتم العثور على الأغنية في أي مصدر.\n"
        "جرب اسمًا آخر أو اكتب اسم الفنان مع الأغنية."
    )


# ─── Voice chat lifecycle ─────────────────────────────────────────────────────

# Error substrings that mean there is no active Telegram Voice Chat in the
# chat (as opposed to auth / network failures).
# py-tgcalls 0.9.x surfaces these when JoinGroupCall is sent to a chat that
# has no running call.  Telegram may return GROUPCALL_FORBIDDEN (supergroups
# where call was never started), GROUPCALL_INVALID (call ended / never existed),
# or a generic "no active" message depending on version and chat type.
_NO_CALL_KEYWORDS = (
    "no active",
    "not active",
    "groupcall_forbidden",
    "groupcall_invalid",
    "groupcall_notfound",
    "group call",
    "call not found",
    "call_id invalid",
    "video_chat_id",        # some pyrogram versions surface this
    "voice_chat",           # "voice_chat_empty" / "voice_chat_not_started"
)

# Error substrings that mean the assistant is not a member of the chat at all.
# pytgcalls surfaces these as generic exceptions whose message contains these
# fragments when the Pyrogram client doesn't know the peer yet.
_NOT_MEMBER_KEYWORDS = (
    "peer id invalid",
    "peer_id_invalid",
    "not in a group",
    "not in the group",
    "userbot",          # "the userbot there isn't in a group call"
    "user not participant",
    "chat not found",
    "not found",        # covers several Telegram API 400 variants
)

# Optional reference to the PTB Application bot, injected from commands.py
# so the player can ask the main bot to invite the assistant into private chats.
_ptb_bot = None


def set_bot(bot) -> None:
    """
    Store a reference to the python-telegram-bot Bot instance so that
    _ensure_assistant_in_chat() can use it to invite the assistant into
    private groups where self-join is not allowed.

    Call this once from music/commands.py after the Application is built.
    """
    global _ptb_bot
    _ptb_bot = bot


async def _ensure_assistant_in_chat(chat_id: int) -> bool:
    """
    Guarantee @HelpQaed is a member of *chat_id* AND that its peer is
    resolved in Pyrogram's internal MTProto cache.

    Why Pyrogram peer caching matters
    -----------------------------------
    Pyrogram needs an ``InputPeer`` cached locally before any MTProto call
    for a chat succeeds.  ``resolve_peer()`` is the canonical check.  A fresh
    session that hasn't seen a chat yet fails even ``get_chat(chat_id)`` with
    "Peer id invalid".

    Why ``add_chat_member`` is NOT used
    -------------------------------------
    The Bot API blocks bots from adding users who haven't started the bot
    (USER_NOT_MUTUAL_CONTACT / USER_PRIVACY_RESTRICTED).  The userbot must
    join the group *itself* using the group's @username or invite link.

    Flow
    ----
    1. Fast path     — ``resolve_peer()`` already cached → return True.
    2. PTB check     — ``get_chat_member(ASSISTANT_USER_ID)`` to learn current
                       membership status without touching Pyrogram's cache.
    3. Self-join     — Skipped when already a member.  Collects @username and
                       invite link from the PTB bot (exports a fresh link if
                       needed), then tries each identifier in order:
                         a. @username  — works for public groups/supergroups.
                         b. invite link — works for private groups.
                       ``USER_ALREADY_PARTICIPANT`` from ``join_chat`` is
                       treated as "already a member, cache just cold" and
                       skips to the poll immediately.
    4. Cache poll    — ``resolve_peer()`` retried every second for up to 10 s.
                       Covers the MTProto round-trip delay between a successful
                       ``join_chat`` and Pyrogram receiving the update.

    Returns True when ready; False when every strategy failed.
    """
    if assistant is None:
        return False

    import config as _config

    # ── 1. Fast path: peer already cached ────────────────────────────────────
    try:
        await assistant.resolve_peer(chat_id)
        logger.debug(
            "_ensure_assistant_in_chat(%s): peer already cached ✓", chat_id,
        )
        return True
    except Exception:
        pass

    logger.info(
        "_ensure_assistant_in_chat(%s): peer not cached — checking membership",
        chat_id,
    )

    # ── 2. PTB membership check (no Pyrogram cache required) ─────────────────
    already_member = False
    if _ptb_bot is not None:
        try:
            cm     = await _ptb_bot.get_chat_member(chat_id, _config.ASSISTANT_USER_ID)
            status = str(cm.status).lower()
            if not any(s in status for s in ("left", "kicked", "banned")):
                already_member = True
                logger.info(
                    "_ensure_assistant_in_chat(%s): @%s already a member "
                    "(status=%s) — cache cold, polling to warm it",
                    chat_id, _config.ASSISTANT_USERNAME, cm.status,
                )
        except Exception as exc:
            logger.debug(
                "_ensure_assistant_in_chat(%s): PTB membership check failed: %s",
                chat_id, exc,
            )

    # ── 3. Self-join (skipped when already a member) ──────────────────────────
    if not already_member:
        # Collect identifiers that let the userbot join without a pre-cached peer.
        group_username: str | None = None
        invite_link:    str | None = None

        if _ptb_bot is not None:
            try:
                ptb_chat       = await _ptb_bot.get_chat(chat_id)
                group_username = getattr(ptb_chat, "username",    None)
                invite_link    = getattr(ptb_chat, "invite_link", None)
            except Exception as exc:
                logger.debug("PTB get_chat(%s) failed: %s", chat_id, exc)

            # Generate a fresh invite link for private groups where none exists yet.
            if not group_username and not invite_link:
                try:
                    invite_link = await _ptb_bot.export_chat_invite_link(chat_id)
                    logger.debug(
                        "_ensure_assistant_in_chat(%s): exported fresh invite link",
                        chat_id,
                    )
                except Exception as exc:
                    logger.debug(
                        "_ensure_assistant_in_chat(%s): export_chat_invite_link failed: %s",
                        chat_id, exc,
                    )

        # Build the ordered list of identifiers to try.
        identifiers = []
        if group_username:
            identifiers.append(f"@{group_username.lstrip('@')}")
        if invite_link:
            identifiers.append(invite_link)

        if not identifiers:
            # Private group with no accessible invite link — cannot auto-join.
            logger.warning(
                "_ensure_assistant_in_chat(%s): no @username or invite link — "
                "@%s cannot auto-join. Add it to the group manually.",
                chat_id, _config.ASSISTANT_USERNAME,
            )
            return False

        logger.info(
            "_ensure_assistant_in_chat(%s): @%s not a member — "
            "trying %d identifier(s): %s",
            chat_id, _config.ASSISTANT_USERNAME,
            len(identifiers), identifiers,
        )

        for identifier in identifiers:
            try:
                await assistant.join_chat(identifier)
                logger.info(
                    "@%s joined chat %s via %r",
                    _config.ASSISTANT_USERNAME, chat_id, identifier,
                )
                await asyncio.sleep(0.5)
                try:
                    await assistant.resolve_peer(chat_id)
                    return True          # joined + cached immediately
                except Exception:
                    break               # joined but cache not warm yet → poll
            except Exception as exc:
                exc_str = str(exc).lower()
                if "already" in exc_str or "participant" in exc_str:
                    # USER_ALREADY_PARTICIPANT: the userbot IS in the group but
                    # this Pyrogram session hasn't cached that peer yet.
                    logger.info(
                        "@%s is already a participant of chat %s "
                        "(caught from join_chat) — warming cache",
                        _config.ASSISTANT_USERNAME, chat_id,
                    )
                    already_member = True   # skip remaining identifiers
                    break
                logger.debug(
                    "join_chat(%r) for chat %s failed: %s",
                    identifier, chat_id, exc,
                )
                # Try the next identifier (invite link) if username failed.

    # ── 4. Cache-warm poll ────────────────────────────────────────────────────
    # Pyrogram receives the membership update asynchronously; poll until
    # resolve_peer() succeeds or we time out (4 s at 0.5 s intervals).
    for attempt in range(8):
        await asyncio.sleep(0.5)
        try:
            await assistant.resolve_peer(chat_id)
            logger.info(
                "Peer %s resolved for @%s (attempt %d/8)",
                chat_id, _config.ASSISTANT_USERNAME, attempt + 1,
            )
            return True
        except Exception:
            pass

    logger.warning(
        "_ensure_assistant_in_chat(%s): @%s peer still unresolvable after 4 s — "
        "the userbot may have joined but Pyrogram hasn't processed the update yet.",
        chat_id, _config.ASSISTANT_USERNAME,
    )
    return False


async def _create_voice_chat(chat_id: int) -> None:
    """
    Start a new Telegram Voice Chat in *chat_id* via phone.CreateGroupCall.

    Called only when the supported join/play method has already failed because
    no call is
    active — never proactively.  Requires the userbot to have the
    "Manage Voice Chats" (manage_call) admin permission.

    Error handling
    --------------
    GROUPCALL_ALREADY_STARTED  — silently ignored (race: call started between
                                 join attempt and this create attempt).
    CHAT_ADMIN_REQUIRED        — raised as a clear RuntimeError with an Arabic
                                 message explaining exactly which admin permission
                                 is needed and how to grant it.
    Anything else              — re-raised unchanged for the caller to surface.
    """
    if assistant is None:
        raise RuntimeError(
            "Pyrogram assistant is not configured — cannot create a voice chat. "
            "Set API_ID, API_HASH, and ASSISTANT_SESSION_STRING."
        )

    import random
    from pyrogram.raw.functions.phone import CreateGroupCall

    try:
        peer = await assistant.resolve_peer(chat_id)
        await assistant.invoke(
            CreateGroupCall(
                peer=peer,
                random_id=random.randint(1, 2 ** 31 - 1),
            )
        )
        logger.info(
            "Voice chat created in chat %s — waiting 2 s for Telegram to activate it.",
            chat_id,
        )
        await asyncio.sleep(1.0)
    except Exception as exc:
        err = str(exc).lower()
        if "already_started" in err or "already started" in err:
            # Another client started the call between our join attempt and now.
            logger.debug("_create_voice_chat: call already active in chat %s", chat_id)
        elif "chat_admin_required" in err or "admin_required" in err:
            # The userbot is a plain member, not an admin with manage_call right.
            # Surface an actionable Arabic error instead of a raw Telegram error.
            raise RuntimeError(
                "⚠️ لا يمكن بدء المكالمة الصوتية تلقائيًا — "
                "@HelpQaed لا يملك صلاحية «إدارة المكالمات الصوتية».\n\n"
                "الحلول المتاحة (اختر أحدها):\n"
                "١. افتح إعدادات المجموعة ← المسؤولون ← @HelpQaed "
                "← فعّل «إدارة المكالمات الصوتية» فقط.\n"
                "٢. أو ابدأ المكالمة الصوتية يدويًا من أي تطبيق تيليغرام، "
                "ثم أعد إرسال أمر /play."
            ) from exc
        else:
            logger.warning("_create_voice_chat failed for chat %s: %s", chat_id, exc)
            raise


async def _join_group_call_with_autocreate(chat_id: int, stream) -> None:
    """
    Robustly join the voice chat for *chat_id*, auto-creating one only when
    needed.  This is the pattern used by all standard Telegram music bots.

    Step 1 — Membership + peer cache
        ``_ensure_assistant_in_chat`` auto-joins the group via @username or
        invite link when the userbot is not a member.  Raises RuntimeError if
        every strategy fails.

    Step 2 — play/join_call (reactive, not proactive)
        Try to join the call directly.  If a call is already running this
        succeeds immediately — no admin rights needed.  Creation is attempted
        *only* when join fails because no call is active:

        • "already in call" / "already joined"
              The userbot is already streaming — switch via change_stream().
        • Peer / membership keyword (attempt 0)
              Re-ensure membership, retry once.
        • No-active-call keyword (attempt 0)
              Call _create_voice_chat() [requires "Manage Voice Chats" admin],
              then retry the join method once.
        • Anything else / second-attempt failure
              Re-raise unchanged.

    Why not proactive creation?
        phone.CreateGroupCall requires the "Manage Voice Chats" (manage_call)
        admin permission.  Calling it unconditionally — even when a call is
        already running — produces CHAT_ADMIN_REQUIRED for plain members.
        The reactive approach avoids touching the API entirely when a call is
        already active, matching how music bots like YukkiMusicBot operate.
    """
    # ── Step 1: guarantee membership + peer cache ─────────────────────────────
    ready = await _ensure_assistant_in_chat(chat_id)
    if not ready:
        raise RuntimeError(
            "⚠️ تعذّر على الحساب المساعد @HelpQaed الانضمام إلى المجموعة.\n"
            "• أضف @HelpQaed إلى المجموعة يدويًا.\n"
            "• ثم أعد المحاولة بأمر /play."
        )

    # ── Step 2: join the call reactively ─────────────────────────────────────
    for attempt in range(2):
        try:
            await _play_stream(chat_id, stream)
            logger.info(
                "Joined voice call in chat %s (attempt %d)", chat_id, attempt + 1,
            )
            return
        except Exception as exc:
            err = str(exc).lower()

            # ── Already streaming in this call — swap the stream ──────────────
            if "already" in err or "joined" in err:
                logger.info(
                    "Already in voice call for chat %s — switching stream",
                    chat_id,
                )
                await _change_stream(chat_id, stream)
                return

            if attempt == 0:
                # ── No active voice chat — create one, then retry ─────────────
                # _create_voice_chat raises a clear RuntimeError when
                # CHAT_ADMIN_REQUIRED is returned (userbot lacks manage_call).
                if any(kw in err for kw in _NO_CALL_KEYWORDS):
                    logger.info(
                        "No active voice chat in chat %s (%s) — creating one",
                        chat_id, exc,
                    )
                    await _create_voice_chat(chat_id)
                    continue  # retry the join method

                # ── Peer / membership issue — re-ensure once and retry ─────────
                if any(kw in err for kw in _NOT_MEMBER_KEYWORDS):
                    logger.info(
                        "voice-call join method(%s) peer/membership error (%s) "
                        "— re-ensuring and retrying",
                        chat_id, exc,
                    )
                    await _ensure_assistant_in_chat(chat_id)
                    continue  # retry join_group_call

            # Unrelated error, or second attempt also failed — surface it.
            raise


# ─── PyTgCalls API compatibility ──────────────────────────────────────────────

async def _call_is_active(chat_id: int) -> bool | None:
    """
    Return whether PyTgCalls has registered an active call for *chat_id*.

    PyTgCalls 2.x keeps this state in its ntgcalls binding. Older APIs do not
    expose a stable public equivalent, so ``None`` means that the join method's
    successful return is the only available confirmation for that version.
    """
    if call_py is None:
        return False

    binding = getattr(call_py, "_binding", None)
    calls_method = getattr(binding, "calls", None)
    if not callable(calls_method):
        return None

    calls = calls_method()
    if inspect.isawaitable(calls):
        calls = await calls
    return chat_id in calls


async def _wait_for_active_call(chat_id: int, timeout: float = 8.0) -> None:
    """
    Confirm that a successful join method actually registered the call.

    The Telegram/ntgcalls connection completes asynchronously after the
    method returns. A short poll avoids reporting "Started Streaming" while
    the assistant has already disconnected or never registered the call.
    """
    deadline = asyncio.get_running_loop().time() + timeout
    observed_binding = False

    while asyncio.get_running_loop().time() < deadline:
        active = await _call_is_active(chat_id)
        if active is None:
            logger.info(
                "PyTgCalls join method returned for chat %s; "
                "active-call introspection is unavailable on this API.",
                chat_id,
            )
            return
        observed_binding = True
        if active:
            logger.info("PyTgCalls reports an active call in chat %s.", chat_id)
            return
        await asyncio.sleep(0.25)

    if observed_binding:
        raise RuntimeError(
            f"PyTgCalls join method returned, but no active call was registered "
            f"for chat {chat_id} within {timeout:.0f}s.",
        )


async def _invoke_call_method(method, method_name: str, chat_id: int, stream) -> None:
    """Invoke and await one PyTgCalls join method with explicit diagnostics."""
    logger.info(
        "Calling PyTgCalls.%s(chat_id=%s) to join/start the voice stream.",
        method_name, chat_id,
    )
    try:
        result = method(chat_id, stream)
        if inspect.isawaitable(result):
            await result
        else:
            logger.warning(
                "PyTgCalls.%s returned a non-awaitable result; "
                "the installed API may be incompatible.",
                method_name,
            )
    except Exception:
        logger.exception(
            "PyTgCalls.%s failed while joining chat %s.",
            method_name, chat_id,
        )
        raise


async def _play_stream(chat_id: int, stream) -> None:
    """
    Start or join a stream using the installed PyTgCalls API.

    py-tgcalls 2.x uses ``play`` for both joining a call and replacing the
    current stream. Some intermediate releases exposed ``join_call`` and
    older releases exposed ``join_group_call`` instead.
    """
    if call_py is None:
        raise RuntimeError("PyTgCalls is not initialized.")

    for method_name in ("play", "join_call", "join_group_call"):
        method = getattr(call_py, method_name, None)
        if callable(method):
            await _invoke_call_method(method, method_name, chat_id, stream)
            await _wait_for_active_call(chat_id)
            return

    raise RuntimeError(
        "Installed PyTgCalls has no supported stream-start method "
        "(expected play, join_call, or join_group_call).",
    )


async def _change_stream(chat_id: int, stream) -> None:
    """Replace the current stream across PyTgCalls API generations."""
    if call_py is None:
        raise RuntimeError("PyTgCalls is not initialized.")
    method = getattr(call_py, "play", None)
    if callable(method):
        # In py-tgcalls 2.x, play() detects an existing call and swaps its
        # stream sources internally.
        await _invoke_call_method(method, "play", chat_id, stream)
        await _wait_for_active_call(chat_id)
        return
    legacy_method = getattr(call_py, "change_stream", None)
    if callable(legacy_method):
        await _invoke_call_method(legacy_method, "change_stream", chat_id, stream)
        await _wait_for_active_call(chat_id)
        return
    join_call = getattr(call_py, "join_call", None)
    if callable(join_call):
        await _invoke_call_method(join_call, "join_call", chat_id, stream)
        await _wait_for_active_call(chat_id)
        return
    raise RuntimeError(
        "Installed PyTgCalls has no supported stream-change method "
        "(expected play, change_stream, or join_call).",
    )


async def _leave_stream(chat_id: int) -> None:
    """Leave a voice call across PyTgCalls API generations."""
    if call_py is None:
        return
    method = getattr(call_py, "leave_call", None) or getattr(call_py, "leave_group_call", None)
    if method is None:
        raise RuntimeError("Installed PyTgCalls has no supported leave method.")
    await method(chat_id)


async def _pause_stream(chat_id: int) -> None:
    """Pause a voice call across PyTgCalls API generations."""
    if call_py is None:
        return
    method = getattr(call_py, "pause", None) or getattr(call_py, "pause_stream", None)
    if method is None:
        raise RuntimeError("Installed PyTgCalls has no supported pause method.")
    await method(chat_id)


async def _resume_stream(chat_id: int) -> None:
    """Resume a voice call across PyTgCalls API generations."""
    if call_py is None:
        return
    method = getattr(call_py, "resume", None) or getattr(call_py, "resume_stream", None)
    if method is None:
        raise RuntimeError("Installed PyTgCalls has no supported resume method.")
    await method(chat_id)


# ─── Playback control ─────────────────────────────────────────────────────────

async def warm_assistant(chat_id: int) -> None:
    """
    Pre-warm the Pyrogram peer cache for *chat_id* in the background.

    Called concurrently with yt-dlp audio resolution so that by the time
    the track URL is ready the assistant is already a member and its peer
    is resolved — eliminating the membership/cache-warm delay from the
    critical play path.

    This function is intentionally non-fatal: any error is logged at DEBUG
    level and play() will retry _ensure_assistant_in_chat() itself.
    """
    if assistant is None or call_py is None:
        return
    try:
        await _ensure_assistant_in_chat(chat_id)
    except Exception as exc:
        logger.debug("warm_assistant(%s) background error: %s", chat_id, exc)


async def play(chat_id: int, track: TrackInfo) -> None:
    """
    Start streaming *track* in the group voice chat immediately.

    Behaviour mirrors professional Telegram music bots (X-Music, etc.):
      • Nothing playing  → join the call and start the track.
      • Already playing  → replace the current stream instantly via
                           change_stream(); the queue is cleared so the new
                           track takes full control.

    Use ``enqueue()`` to add a track to the queue without interrupting.
    Raises RuntimeError if the assistant or PyTgCalls is not available.
    """
    if call_py is None:
        raise RuntimeError("Music assistant is not configured.")

    await ensure_pytgcalls_started()

    state = _get_state(chat_id)

    if state["playing"]:
        # Ensure membership + warm peer cache before change_stream.
        await _ensure_assistant_in_chat(chat_id)
        state["queue"].clear()
        state["current"] = track
        state["paused"] = False
        await _change_stream(chat_id, _make_stream(track["url"]))
        logger.info("Replaced stream with: %s in chat %s", track["title"], chat_id)
        return

    stream = _make_stream(track["url"])
    await _join_group_call_with_autocreate(chat_id, stream)
    state["current"] = track
    state["playing"] = True
    state["paused"] = False
    logger.info("Started streaming: %s in chat %s", track["title"], chat_id)


async def enqueue(chat_id: int, track: TrackInfo) -> bool:
    """
    Add *track* to the end of the queue without interrupting playback.

    Returns True if the track was queued (something was already playing),
    or False if nothing was playing and the track started immediately instead.
    """
    if call_py is None:
        raise RuntimeError("Music assistant is not configured.")

    state = _get_state(chat_id)

    if state["playing"]:
        state["queue"].append(track)
        logger.info("Queued: %s in chat %s", track["title"], chat_id)
        return True

    # Nothing is playing — start immediately.
    await play(chat_id, track)
    return False


async def stop(chat_id: int) -> None:
    """Stop playback, clear the queue, and leave the voice chat."""
    state = _get_state(chat_id)
    state["playing"] = False
    state["paused"] = False
    state["queue"].clear()
    state["current"] = None
    if call_py is not None:
        try:
            await _leave_stream(chat_id)
        except Exception as exc:
            logger.warning("stop: leave call raised %s", exc)


async def pause(chat_id: int) -> None:
    """Pause the current stream."""
    state = _get_state(chat_id)
    if call_py is not None and state["playing"] and not state["paused"]:
        await _pause_stream(chat_id)
        state["paused"] = True


async def resume(chat_id: int) -> None:
    """Resume a paused stream."""
    state = _get_state(chat_id)
    if call_py is not None and state["playing"] and state["paused"]:
        await _resume_stream(chat_id)
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
        await _change_stream(
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
#
# py-tgcalls 2.x (ntgcalls-based rewrite) removed the old @call_py.on_stream_end()
# decorator. All voice-chat events now flow through a single @call_py.on_update()
# handler, and you filter for the event type you care about with isinstance().

if call_py is not None:
    @call_py.on_update()
    async def _on_pytgcalls_update(client, update) -> None:  # type: ignore[misc]
        """
        Called by pytgcalls on every voice-chat event (join, left, kicked,
        stream ended, etc.). We only act when the stream finished.

        Matched by class *name* (not isinstance) because the exact import
        path for the "stream ended" event type differs across py-tgcalls
        versions/builds (StreamAudioEnded vs StreamEnded, different
        sub-modules, etc.) — matching the name avoids an ImportError that
        would crash the whole module at startup if the wrong path is picked.
        """
        if type(update).__name__ not in ("StreamAudioEnded", "StreamEnded", "StreamVideoEnded"):
            return

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
                await _change_stream(
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
                await _change_stream(
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
            await _leave_stream(chat_id)
        except Exception as exc:
            logger.warning("on_stream_end leave: %s", exc)

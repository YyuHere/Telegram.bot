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


def _make_stream(url: str):
    """
    Build the pytgcalls stream object for *url* and return it.
    Delegates to _stream_builder which is resolved once at module load.
    """
    return _stream_builder(url)


def _resolve_stream_builder():
    """
    Return the correct ``url → stream_object`` callable for the installed
    py-tgcalls version.

    Detection order (most-specific first to avoid noisy ImportErrors):

      1. AudioPiped / HighQualityAudio  — py-tgcalls 0.9.x (pinned in
         requirements.txt).  This is the correct API for this project.
         Pattern: AudioPiped(url, HighQualityAudio())

      2. MediaStream  — py-tgcalls ≥ 3.x / pytgcalls fork.  Tried second so
         an accidental upgrade is handled gracefully.
         Pattern: MediaStream(url)

    If neither import succeeds the engine logs a clear error and returns a
    no-op lambda; callers guard on call_py being None.
    """
    # ── py-tgcalls 0.9.x  ─────────────────────────────────────────────────────
    try:
        from pytgcalls.types.input_stream import AudioPiped          # noqa: PLC0415
        from pytgcalls.types.input_stream.quality import HighQualityAudio  # noqa: PLC0415
        logger.info("pytgcalls stream API: AudioPiped / HighQualityAudio (py-tgcalls 0.9.x)")
        return lambda url: AudioPiped(url, HighQualityAudio())
    except ImportError:
        pass

    # ── py-tgcalls ≥ 3.x / newer forks  ──────────────────────────────────────
    try:
        from pytgcalls.types import MediaStream                       # noqa: PLC0415
        logger.info("pytgcalls stream API: MediaStream (py-tgcalls ≥ 3.x)")
        return lambda url: MediaStream(url)
    except ImportError:
        pass

    logger.error(
        "pytgcalls stream API: neither AudioPiped nor MediaStream could be "
        "imported.  Ensure py-tgcalls==0.9.7 (or compatible) and tgcrypto "
        "are installed correctly."
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
    Build yt-dlp options for SoundCloud search and generic URL extraction.

    YouTube is intentionally excluded — SoundCloud has no bot-detection gate
    and carries a strong Arabic/regional catalogue that covers the vast majority
    of user requests.  Generic ``http(s)://`` URLs (direct audio files, other
    yt-dlp-supported platforms) are also handled.
    """
    import os

    opts: dict = {
        "format": "bestaudio/best",
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        # Generic browser User-Agent — works for SoundCloud and most other
        # yt-dlp extractors without requiring platform-specific client IDs.
        "http_headers": {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0.0.0 Safari/537.36"
            ),
        },
    }

    # Optional cookie file for any platform that requires authentication.
    cookie_file = os.environ.get("YTDLP_COOKIES_FILE", "").strip()
    if cookie_file and os.path.isfile(cookie_file):
        opts["cookiefile"] = cookie_file
        logger.debug("yt-dlp: using cookie file %s", cookie_file)

    return opts


def _fmt_duration(secs: int) -> str:
    """Convert seconds to m:ss string."""
    m, s = divmod(max(0, int(secs)), 60)
    if m >= 60:
        h, m = divmod(m, 60)
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


import re as _re

# ─── Arabic text helpers ──────────────────────────────────────────────────────

# Unicode ranges for Arabic diacritical marks (tashkeel / harakat).
# These are present in Quranic/formal text but absent from SoundCloud titles,
# so stripping them dramatically improves search hit-rates.
_TASHKEEL_RE = _re.compile(r"[\u0610-\u061A\u064B-\u065F\u0670\u06D6-\u06DC\u06DF-\u06E4\u06E7\u06E8\u06EA-\u06ED]")


def _strip_tashkeel(text: str) -> str:
    """Remove Arabic diacritical marks from *text* (no-op for non-Arabic)."""
    return _TASHKEEL_RE.sub("", text).strip()


# Hostnames that unambiguously identify a platform URL even without a scheme.
# YouTube is intentionally absent — it is no longer supported.
_PLATFORM_HOSTS = (
    "soundcloud.com", "www.soundcloud.com", "on.soundcloud.com",
    "spotify.com", "open.spotify.com",
    "mixcloud.com", "www.mixcloud.com",
    "bandcamp.com",
    "deezer.com", "www.deezer.com",
)


def _is_url(text: str) -> bool:
    """
    Return True if *text* is a URL rather than a search query.

    Accepts:
      • Any string starting with ``http://``, ``https://``, or ``www.``
      • Bare platform hostnames (soundcloud.com/…, etc.)
        so users don't have to type the scheme.
    """
    if text.startswith(("http://", "https://", "www.")):
        return True
    # Check for bare platform hostnames (no scheme prefix)
    lower = text.lower()
    return any(lower.startswith(host + "/") or lower == host for host in _PLATFORM_HOSTS)


# Common audio-file extensions whose URLs can be piped directly into pytgcalls
# without going through yt-dlp's extraction step.
_DIRECT_AUDIO_EXTS = (".mp3", ".m4a", ".ogg", ".opus", ".wav", ".flac", ".aac", ".webm")


def _is_direct_audio_url(url: str) -> bool:
    """Return True when the URL path ends with a known audio extension."""
    path = url.split("?")[0].lower()
    return any(path.endswith(ext) for ext in _DIRECT_AUDIO_EXTS)


# yt-dlp error substrings that indicate an extraction / access failure.
_EXTRACTION_ERROR_PHRASES = (
    "sign in to confirm",
    "confirm you're not a bot",
    "confirm you are not a bot",
    "bot detection",
    "this video is not available",
    "video unavailable",
    "private track",
    "content not available",
    "geo restriction",
)


def _search_variants(query: str) -> list[tuple[str, str]]:
    """
    Return a prioritized list of ``(yt-dlp-prefix, query_string)`` pairs that
    ``get_audio_info`` will try in order until one succeeds.

    YouTube is excluded entirely.  SoundCloud is the primary search platform:
    it has no bot-detection gate, is fully supported by yt-dlp, and carries a
    large Arabic/regional catalogue.

    Pipeline:
      1. SoundCloud — original query, 5 candidates
      2. SoundCloud — tashkeel-stripped query (only added when it differs)
      3. SoundCloud — first 4 words of original (only for long queries)

    Duplicates are suppressed so the same string is never tried twice.
    """
    seen: set[tuple[str, str]] = set()
    variants: list[tuple[str, str]] = []

    def _add(prefix: str, q: str) -> None:
        key = (prefix, q.strip())
        if key not in seen:
            seen.add(key)
            variants.append(key)

    normalized = _strip_tashkeel(query)
    words = query.split()
    short = " ".join(words[:4]) if len(words) > 4 else ""

    _add("scsearch5", query)

    if normalized != query:
        _add("scsearch5", normalized)

    if short and short != query:
        _add("scsearch5", short)

    return variants


def _extract_first_valid(info: dict, query: str) -> "TrackInfo | None":
    """
    Given a yt-dlp result dict, return the first entry that has a usable URL.
    Returns None so the caller can transparently try the next fallback stage.
    """
    if "entries" in info:
        entries = [e for e in (info["entries"] or []) if e]
        if not entries:
            return None
        info = entries[0]

    url: str = info.get("url") or info.get("webpage_url", "")
    if not url:
        return None

    return TrackInfo(
        url=url,
        title=info.get("title", query),
        duration=_fmt_duration(info.get("duration") or 0),
        thumbnail=info.get("thumbnail", ""),
    )


def get_audio_info(query: str) -> TrackInfo:
    """
    Resolve *query* (song name or direct URL) via yt-dlp.

    Text queries are searched on SoundCloud (no bot-detection, strong Arabic
    catalogue).  YouTube is intentionally excluded.

    Direct URLs (http/https/www or bare platform hostnames) bypass search:
      • Raw audio files (.mp3, .opus, etc.) are piped directly to pytgcalls.
      • All other URLs go through yt-dlp to obtain a real CDN streaming URL.

    For text queries the function walks a staged fallback pipeline
    (see ``_search_variants``) that handles:
      • Arabic diacritics (tashkeel) — stripped before retrying.
      • Long multi-word titles — retried with the first 4 words.

    Returns a TrackInfo dict: url, title, duration (m:ss), thumbnail URL.
    Raises RuntimeError with an Arabic-language message if all stages fail.
    """
    opts = _ydl_opts()

    # ── Direct URL ────────────────────────────────────────────────────────────
    if _is_url(query):
        # Normalise bare hostnames → add https:// so yt-dlp can handle them.
        url = query if query.startswith(("http://", "https://")) else f"https://{query}"

        # Raw audio file (.mp3 / .opus / etc.) — skip yt-dlp entirely and pipe
        # the URL directly to pytgcalls/ffmpeg which handles them natively.
        if _is_direct_audio_url(url):
            filename = url.split("/")[-1].split("?")[0] or url
            logger.info("yt-dlp: direct audio file URL — skipping extraction: %r", filename)
            return TrackInfo(url=url, title=filename, duration="0:00", thumbnail="")

        # Platform URL (YouTube, SoundCloud, etc.) — must go through yt-dlp to
        # obtain a real CDN streaming URL.  Passing the page URL directly to
        # ffmpeg/pytgcalls causes [Errno 2] No such file or directory because
        # ffmpeg cannot open an HTML page as audio.
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
            track = _extract_first_valid(info, url)
            if track:
                return track
            raise RuntimeError("لم يُعثر على رابط تدفق صالح في هذا الرابط.")
        except RuntimeError:
            raise
        except Exception as exc:
            err_lower = str(exc).lower()
            if any(p in err_lower for p in _EXTRACTION_ERROR_PHRASES):
                raise RuntimeError(
                    "❌ تعذّر الوصول إلى هذا الرابط (محتوى خاص أو مقيّد).\n"
                    "جرّب رابط SoundCloud مباشرًا بدلاً من ذلك."
                ) from exc
            raise RuntimeError(
                f"❌ فشل استخراج الصوت من الرابط:\n{exc}"
            ) from exc

    # ── Staged text search (SoundCloud only) ──────────────────────────────────
    for prefix, variant in _search_variants(query):
        search_str = f"{prefix}:{variant}"
        logger.debug("yt-dlp: trying %r", search_str)
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(search_str, download=False)
            track = _extract_first_valid(info, query)
            if track:
                logger.info(
                    "yt-dlp: found %r via %r (variant=%r)",
                    track["title"], prefix, variant,
                )
                return track
        except Exception as exc:
            logger.warning("yt-dlp: %r failed: %s", search_str, exc)

    raise RuntimeError(
        f"❌ لم يتم العثور على نتائج في SoundCloud لـ: {query!r}\n"
        "جرّب اسمًا آخر للأغنية، أو أرسل رابط SoundCloud مباشرًا."
    )


# ─── Voice chat lifecycle ─────────────────────────────────────────────────────

# Error substrings that mean there is no active Telegram Voice Chat in the
# chat (as opposed to auth / network failures).
_NO_CALL_KEYWORDS = (
    "no active",
    "groupcall_forbidden",
    "groupcall_invalid",
    "group call",
    "call not found",
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
                await asyncio.sleep(2.0)
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
    # resolve_peer() succeeds or we time out (10 s).
    for attempt in range(10):
        await asyncio.sleep(1.0)
        try:
            await assistant.resolve_peer(chat_id)
            logger.info(
                "Peer %s resolved for @%s (attempt %d/10)",
                chat_id, _config.ASSISTANT_USERNAME, attempt + 1,
            )
            return True
        except Exception:
            pass

    logger.warning(
        "_ensure_assistant_in_chat(%s): @%s peer still unresolvable after 10 s — "
        "the userbot may have joined but Pyrogram hasn't processed the update yet.",
        chat_id, _config.ASSISTANT_USERNAME,
    )
    return False


async def _create_voice_chat(chat_id: int) -> None:
    """
    Start a new Telegram Voice Chat in *chat_id* via the Pyrogram assistant's
    raw MTProto API (phone.CreateGroupCall).

    py-tgcalls cannot join a call that does not yet exist, so the assistant
    must create it first.  GROUPCALL_ALREADY_STARTED is silently ignored.
    The assistant is guaranteed to be a chat member before this is called
    (enforced by _join_group_call_with_autocreate).
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
            "Voice chat created in chat %s — waiting 1.5 s for Telegram to activate it.",
            chat_id,
        )
        await asyncio.sleep(1.5)
    except Exception as exc:
        err = str(exc).lower()
        if "already_started" in err or "already started" in err:
            logger.debug("_create_voice_chat: call already active in chat %s", chat_id)
        else:
            logger.warning("_create_voice_chat failed for chat %s: %s", chat_id, exc)
            raise


async def _join_group_call_with_autocreate(chat_id: int, stream) -> None:
    """
    Robustly join the voice chat for *chat_id*, with full auto-join and
    auto-create behaviour:

    Step 1 — Membership + peer cache
        ``_ensure_assistant_in_chat`` auto-joins the group via @username or
        invite link when the userbot isn't a member yet.  Raises RuntimeError
        if every strategy fails (caller surfaces the Arabic error).

    Step 2 — Proactive voice-chat creation
        Always call ``_create_voice_chat`` before joining.  If a call is
        already active, ``GROUPCALL_ALREADY_STARTED`` is silently swallowed
        inside that helper — no harm done.  This removes the need to detect
        "no active call" errors reactively and makes the startup path explicit.

    Step 3 — join_group_call with a single retry
        * "already in call" / "already joined" → the assistant is already
          streaming; switch to ``change_stream`` instead of joining again.
        * Peer / membership keyword             → re-ensure membership once,
          then retry ``join_group_call``.
        * Anything else                         → re-raise unchanged.
    """
    # ── Step 1: guarantee membership + peer cache ─────────────────────────────
    ready = await _ensure_assistant_in_chat(chat_id)
    if not ready:
        raise RuntimeError(
            "⚠️ تعذّر على الحساب المساعد @HelpQaed الانضمام إلى المجموعة.\n"
            "• أضف @HelpQaed إلى المجموعة يدويًا، أو تأكد أن البوت مسؤول "
            "وله صلاحية دعوة الأعضاء.\n"
            "• ثم أعد المحاولة بأمر /play."
        )

    # ── Step 2: ensure a voice chat exists (proactive, idempotent) ───────────
    # _create_voice_chat silently ignores GROUPCALL_ALREADY_STARTED, so it is
    # safe to call even when a call is already running.
    await _create_voice_chat(chat_id)

    # ── Step 3: join the call (with one membership-error retry) ───────────────
    for attempt in range(2):
        try:
            await call_py.join_group_call(chat_id, stream)
            logger.info(
                "Joined voice call in chat %s (attempt %d)", chat_id, attempt + 1,
            )
            return
        except Exception as exc:
            err = str(exc).lower()

            # The userbot is already streaming in this call — swap the stream.
            if "already" in err or "joined" in err:
                logger.info(
                    "Already in voice call for chat %s — switching stream via change_stream",
                    chat_id,
                )
                await call_py.change_stream(chat_id, stream)
                return

            # Peer / membership error on first attempt — re-ensure and retry.
            if attempt == 0 and any(kw in err for kw in _NOT_MEMBER_KEYWORDS):
                logger.info(
                    "join_group_call(%s) peer/membership error (%s) "
                    "— re-ensuring membership and retrying",
                    chat_id, exc,
                )
                await _ensure_assistant_in_chat(chat_id)
                continue  # second iteration of the loop

            # Unrelated error, or second attempt also failed — surface it.
            raise


# ─── Playback control ─────────────────────────────────────────────────────────

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
        await call_py.change_stream(chat_id, _make_stream(track["url"]))
        logger.info("Replaced stream with: %s in chat %s", track["title"], chat_id)
        return

    state["current"] = track
    state["playing"] = True
    state["paused"] = False

    await _join_group_call_with_autocreate(chat_id, _make_stream(track["url"]))
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

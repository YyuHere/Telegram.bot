# Telegram Bot + Assistant Userbot

A production-ready Telegram group management bot with a companion Pyrogram userbot.

## Stack
- **python-telegram-bot v21** — main bot (async polling)
- **Pyrogram** — assistant userbot for full member enumeration
- **pytgcalls + yt-dlp + ytmusicapi** — voice chat music streaming
- **SQLite** — local member and auto-reply store

## Project Structure

```
.
├── main.py                  # Entry point
├── config.py                # Env var loader & validator
├── db.py                    # SQLite helpers (members + replies)
├── generate_session.py      # One-time: generate Pyrogram session string
├── requirements.txt
├── Dockerfile               # Docker / Railway deployment
├── Procfile                 # Railway worker process
│
├── assistant/
│   └── userbot.py           # Pyrogram assistant client
│
├── handlers/
│   ├── general.py           # /start, /help, /ping
│   ├── ludo.py              # Ludo Club code extractor
│   ├── moderation.py        # /ban /unban /kick /mute /unmute + Arabic triggers
│   └── replies.py           # Auto-reply system (اضافه رد)
│
└── music/
    ├── commands.py          # /play /queue /stop /skip + Arabic aliases
    └── player.py            # pytgcalls audio engine + per-chat queue
```

## How to Run

The `Telegram Bot` workflow runs `python main.py` from the root directory.

Required secrets (add via Replit Secrets):
- `BOT_TOKEN` — from @BotFather

Optional secrets (enable full member sync + voice chat streaming via Pyrogram assistant):
- `API_ID` — from my.telegram.org/apps
- `API_HASH` — from my.telegram.org/apps
- `ASSISTANT_SESSION_STRING` — generate once with `python generate_session.py`

The assistant account used for voice chat streaming is fixed:
- **User ID**: `7769827870`
- **Username**: `@HelpQaed`
- Configured in `config.py` as `ASSISTANT_USER_ID` / `ASSISTANT_USERNAME` (no env var needed)
- When `/play` is triggered, the bot automatically checks if `@HelpQaed` is in the group and invites it if not (requires the main bot to be admin with "Add Members" permission)

Optional:
- `BOT_ADMIN_ID` — Telegram user ID allowed to add global auto-replies via DM
- `SPOTIFY_CLIENT_ID` / `SPOTIFY_CLIENT_SECRET` — optional Spotify text metadata search credentials; playback does not use Spotify cookies or a Spotify stream

Music search works without cookies by default. Song-name requests first search
the YouTube Music catalog and extract a `music.youtube.com` stream. Standard
YouTube is the next fallback, followed by SoundCloud and optional Spotify and
Anghami metadata-guided searches. No browser cookies or restricted single-site
URL is required unless an optional cookie jar is provided.

An optional local Netscape-format `cookies.txt` can be used to help yt-dlp
avoid YouTube 403 responses. The file is Git-ignored and excluded from Docker
images; for deployment, provide an equivalent secure mounted path through
`YTDLP_COOKIE_FILE` rather than copying credentials into the image. Treat
browser cookies like passwords and rotate them if they are ever exposed.
Authenticated extraction uses that file only when it exists and is non-empty.
`YTDLP_SOCKET_TIMEOUT` defaults to 8 seconds, and
`YTDLP_BLOCK_COOLDOWN` defaults to 60 seconds after a YouTube rate-limit or
bot-challenge response.

The resolver keeps provider searches metadata-only, resolves only the selected
song, uses the mobile `android` YouTube client by default, and fails fast on
429, bot-challenge, and authentication-block responses instead of cycling
through restricted browser clients. `android_vr` and `mweb` are supported as
explicit tested alternatives.

All yt-dlp operations use the shared Android + web player-client configuration
and Android Chrome User-Agent in `config.py`. The request path is intentionally
centralized so searches and direct stream resolution receive the same
extractor arguments. `YTDLP_STREAM_TIMEOUT` bounds FFmpeg's direct CDN
transport read. FFmpeg reconnects only for transient network/5xx/429 failures
and does not reconnect at EOF, so completed songs advance normally.

Playback requests are isolated by Telegram chat ID. `/play`, `/queue`,
تشغيل, and قائمة share that chat's local queue; the next queued song starts
automatically when the current stream ends. `/stop` and
اسكت stop playback and clear the chat queue, while `/skip` and تخطى advance to
the next queued song.

## Deployment

Deployed on Railway using the `Procfile` (`worker: python main.py`) or `Dockerfile`.
All secrets are injected as environment variables at runtime.

## User Preferences
- Keep the bot code flat at root (no nested subdirectory for the bot).
- All Telegram bot files live directly at root alongside `main.py`.

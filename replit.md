# Telegram Bot + Assistant Userbot

A production-ready Telegram group management bot with a companion Pyrogram userbot.

## Stack
- **python-telegram-bot v21** — main bot (async polling)
- **Pyrogram** — assistant userbot for full member enumeration
- **pytgcalls + yt-dlp** — voice chat music streaming (placeholder, coming soon)
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
    ├── commands.py          # /play /stop /pause /resume /skip (placeholder)
    └── player.py            # pytgcalls audio engine + queue
```

## How to Run

The `Telegram Bot` workflow runs `python main.py` from the root directory.

Required secrets (add via Replit Secrets):
- `BOT_TOKEN` — from @BotFather

Optional secrets (enable full member sync via Pyrogram assistant):
- `API_ID` — from my.telegram.org/apps
- `API_HASH` — from my.telegram.org/apps
- `ASSISTANT_SESSION_STRING` — generate once with `python generate_session.py`

Optional:
- `BOT_ADMIN_ID` — Telegram user ID allowed to add global auto-replies via DM

## Deployment

Deployed on Railway using the `Procfile` (`worker: python main.py`) or `Dockerfile`.
All secrets are injected as environment variables at runtime.

## User Preferences
- Keep the bot code flat at root (no nested subdirectory for the bot).
- All Telegram bot files live directly at root alongside `main.py`.

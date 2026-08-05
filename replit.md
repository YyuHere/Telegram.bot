# Telegram Bot

A Python Telegram bot with group moderation, Ludo Club referral code extraction, and music/voice chat features.

## Stack

- **Runtime:** Python 3.11+
- **Framework:** python-telegram-bot v21 (async polling)
- **Database:** SQLite (`telegram-bot/members.db`)
- **Optional userbot:** Pyrogram + pytgcalls for voice chat

## Project Structure

```
telegram-bot/
├── main.py                  # Entry point
├── config.py                # Environment variable loader & validator
├── generate_session.py      # One-time helper: generate Assistant session string
├── requirements.txt
├── handlers/
│   ├── general.py           # /start, /help, /ping
│   ├── moderation.py        # /ban /unban /kick /mute /unmute + Arabic triggers
│   └── ludo.py              # Ludo Club code extractor
├── music/
│   ├── player.py            # pytgcalls audio engine + queue
│   └── commands.py          # /play, /stop, /pause, /resume, /skip
└── assistant/
    └── userbot.py           # Pyrogram userbot client + group-join helper
```

## Running the Bot

The bot requires a `BOT_TOKEN` secret (set via Replit Secrets).

```bash
cd telegram-bot && python main.py
```

## Required Secrets

| Secret | Description | Required |
|---|---|---|
| `BOT_TOKEN` | Bot token from @BotFather | Yes |
| `API_ID` | From my.telegram.org/apps | Only for music/voice |
| `API_HASH` | From my.telegram.org/apps | Only for music/voice |
| `ASSISTANT_SESSION_STRING` | Pyrogram session (run `generate_session.py` once) | Only for music/voice |

## User Preferences

<!-- Add any agent preferences here -->

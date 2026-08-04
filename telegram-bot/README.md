# Telegram Bot + Assistant Userbot

A complete, production-ready Telegram Bot with a companion Userbot for:
- 🎮 **Ludo Club code extraction** — auto-extracts referral codes from group messages.
- 🎵 **Music voice chat** — plays audio from YouTube / any yt-dlp-supported source.
- 🛠 **Group management** — `/start`, `/help`, `/ping`.

---

## Project Structure

```
telegram-bot/
├── main.py                  # Entry point
├── config.py                # Environment variable loader & validator
├── generate_session.py      # One-time helper: generate Assistant session string
├── requirements.txt
├── Procfile                 # Railway / Heroku worker process
├── Dockerfile               # Docker deployment
├── .env.example             # Template for secrets
├── .gitignore
│
├── handlers/
│   ├── general.py           # /start, /help, /ping
│   └── ludo.py              # Ludo Club code extractor
│
├── music/
│   ├── player.py            # pytgcalls audio engine + queue
│   └── commands.py          # /play, /stop, /pause, /resume, /skip
│
└── assistant/
    └── userbot.py           # Pyrogram userbot client + group-join helper
```

---

## Quick Start

### 1. Prerequisites

- Python 3.11+
- A Telegram account (for the Assistant userbot)
- A bot created via [@BotFather](https://t.me/BotFather)
- API credentials from [my.telegram.org/apps](https://my.telegram.org/apps)

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Configure environment variables

```bash
cp .env.example .env
# Edit .env with your real values
```

### 4. Generate the Assistant session string

Run this **once** on your local machine (interactive — requires your phone number):

```bash
python generate_session.py
```

Copy the printed string into `.env` as `ASSISTANT_SESSION_STRING`.

### 5. Run locally

```bash
python main.py
```

---

## Deploy to Railway

1. Push this folder to a GitHub repository.
2. Create a new Railway project → **Deploy from GitHub repo**.
3. Add all environment variables in the Railway dashboard:
   - `API_ID`
   - `API_HASH`
   - `BOT_TOKEN`
   - `ASSISTANT_SESSION_STRING`
4. Railway detects the `Procfile` and runs `python main.py` as a **worker** (no port needed).

> **Tip:** You can also deploy via Docker — Railway auto-detects the `Dockerfile`.

---

## Command Reference

| Command | Scope | Description |
|---|---|---|
| `/start` | Private | Welcome message |
| `/help` | Anywhere | Command list |
| `/ping` | Anywhere | Response latency |
| `/play <song/URL>` | Groups | Play audio in voice chat |
| `/pause` | Groups | Pause playback |
| `/resume` | Groups | Resume playback |
| `/skip` | Groups | Skip current track |
| `/stop` | Groups | Stop and leave voice chat |

---

## How the Ludo Club Extractor Works

The bot listens for any group message containing:
- A Ludo Club invite URL: `https://ludoclub.com/invite.html?code=WAKL189`
- Plain text: `enter my Code: WAKL189` / `use code WAKL189`

It replies with just the code as inline monospace text so users can **copy it with one tap**.

---

## Notes

- The Assistant userbot must be a **real Telegram user account** (not a bot).
- Voice chat streaming requires the group to have an **active voice chat** (start one via the group info).
- `ffmpeg` must be available in the runtime environment — the `Dockerfile` installs it automatically.
- The `ASSISTANT_SESSION_STRING` grants full account access — treat it like a password.

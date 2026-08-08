# ─────────────────────────────────────────────────────────────────────────────
# Dockerfile — Production-ready container for Railway / Docker deployment.
# ─────────────────────────────────────────────────────────────────────────────

FROM python:3.11-slim

# System dependencies required by pytgcalls / tgcalls native bindings
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    libffi-dev \
    libssl-dev \
    ffmpeg \
    python3-pip \
    nodejs \
    npm \
    && rm -rf /var/lib/apt/lists/*

# Working directory
WORKDIR /app

# Install Python dependencies first (layer-cached unless requirements.txt changes)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ── Force-refresh yt-dlp on every build ─────────────────────────────────────
# YouTube changes its player/signature logic often enough that yt-dlp and its
# yt-dlp-ejs challenge-solver component need near-weekly updates to keep
# working. requirements.txt pins yt-dlp with ">=", so once the layer above is
# cached, Railway keeps reusing that (now stale) install on every redeploy —
# even though the version constraint technically allows something newer.
#
# Bump YTDLP_CACHE_BUST (to today's date, or any different value) whenever
# you want this layer to reinstall the latest yt-dlp[default] on the next
# deploy, without invalidating the apt-get or base pip layers above.
ARG YTDLP_CACHE_BUST=2026-08-08
RUN pip install --no-cache-dir --upgrade "yt-dlp[default]"

# Copy application source
COPY . .

# Railway injects environment variables at runtime; no .env file needed in prod.
# The container runs as a background worker — no port exposed.
CMD ["python", "main.py"]

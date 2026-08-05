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

# Copy application source
COPY . .

# Railway injects environment variables at runtime; no .env file needed in prod.
# The container runs as a background worker — no port exposed.
CMD ["python", "main.py"]

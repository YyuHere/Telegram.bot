FROM python:3.11-slim

# Install system dependencies & curl
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    libffi-dev \
    libssl-dev \
    ffmpeg \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install modern Node.js (Required for yt-dlp signature solving)
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN pip install --no-cache-dir -U yt-dlp

COPY . .

CMD ["python", "main.py"]

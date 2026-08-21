#!/bin/sh
set -eu

# Keep cloud instances on the newest yt-dlp extractor implementation. This is
# intentionally performed at startup as well as during image/package builds,
# because YouTube frequently changes its player and challenge behavior.
python -m pip install --no-cache-dir --upgrade yt-dlp
exec python main.py
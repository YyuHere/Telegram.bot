---
name: yt-dlp cookie handling
description: Safe handling rules for YouTube authentication cookies used by yt-dlp
---

Never store browser session cookies in tracked project files or deployment artifacts. Support private local files for development, but prefer secure environment injection for production.

**Why:** Browser cookies are live authentication credentials and can grant access to the associated account if exposed.

**How to apply:** Keep cookie paths/content optional, ignore local cookie files in Git and deployment configuration, and never reproduce cookie values in source, logs, or responses.
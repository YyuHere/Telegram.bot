---
name: YouTube extractor client choice
description: Runtime behavior of yt-dlp YouTube player clients in this environment
---

Use a mobile-only YouTube player-client policy for voice-chat extraction:
android is the default, while android_vr and mweb are valid explicit
alternatives. Never rotate into web, tv, ios, or browser-cookie clients.

**Why:** A live resolver check showed android_vr returning a direct media URL
cleanly while tv produced DRM/signature warnings and no playable formats.
Mobile-only extraction also avoids multiplying requests across restricted
clients when the deployment cannot use a proxy.

**How to apply:** When adjusting yt-dlp client fallback order, keep exactly one
mobile client active and require a direct media URL, not merely a non-empty info
result.
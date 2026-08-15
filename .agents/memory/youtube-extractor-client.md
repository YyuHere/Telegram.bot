---
name: YouTube extractor client choice
description: Runtime behavior of yt-dlp YouTube player clients in this environment
---

Prefer the android_vr YouTube player client before tv for voice-chat extraction. The tv client can return a non-empty result made up of DRM-protected entries, producing signature and n-challenge warnings while yielding no usable media. Keep independent provider fallbacks available.

**Why:** A live resolver check showed android_vr returning a direct media URL cleanly while tv produced DRM/signature warnings and no playable formats.

**How to apply:** When adjusting yt-dlp client fallback order, test a real video and require a direct media URL, not merely a non-empty info result.
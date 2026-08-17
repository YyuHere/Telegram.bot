---
name: FFmpeg stream lifecycle
description: Reconnect policy for direct CDN audio streams consumed by PyTgCalls
---

Direct audio streams should reconnect only for transient network and selected HTTP transport failures. EOF must remain terminal so PyTgCalls can emit its stream-ended event and advance the queue.

**Why:** Reconnecting at EOF can reopen a completed media URL indefinitely, which looks like playback hanging or cycling instead of moving to the next track.

**How to apply:** Keep FFmpeg reconnect-at-EOF disabled in the raw audio pipeline; use a bounded read timeout and limited network/5xx/429 reconnects.
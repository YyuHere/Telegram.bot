---
name: PyTgCalls 2.x API
description: Compatibility expectations for the installed PyTgCalls voice-chat API
---

The installed PyTgCalls 2.x API uses `play(chat_id, stream)` to join or replace a group-call stream, with `leave_call`, `pause`, and `resume` for controls. Legacy method names differ.

**Why:** The project’s previous calls to `join_group_call`, `change_stream`, `leave_group_call`, `pause_stream`, and `resume_stream` raised attribute errors against the installed package.

**How to apply:** Route voice operations through a compatibility layer that prefers the 2.x names and only falls back to legacy names for older installations.
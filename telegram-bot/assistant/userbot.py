"""
assistant/userbot.py — Pyrogram userbot (Assistant) client.

The Assistant is a real Telegram user account (not a bot) that:
  1. Can be invited to groups by the main bot.
  2. Joins a group's Voice Chat and streams audio via pytgcalls.

The session string is generated once with Pyrogram's StringSession
and stored in ASSISTANT_SESSION_STRING env var.
"""

import logging
from pyrogram import Client
from pyrogram.types import Message
from config import API_ID, API_HASH, ASSISTANT_SESSION_STRING

logger = logging.getLogger(__name__)

# Singleton: one Assistant client shared across the whole app
assistant: Client = Client(
    name="assistant",
    api_id=API_ID,
    api_hash=API_HASH,
    session_string=ASSISTANT_SESSION_STRING,
)


async def ensure_in_group(bot: Client, chat_id: int) -> bool:
    """
    Make sure the Assistant user is a member of *chat_id*.

    Strategy:
      1. Try to get the assistant's membership status.
      2. If not a member, the main bot invites the assistant to the chat.

    Returns True if the assistant is (now) in the group, False on error.
    """
    try:
        # Check if the assistant can see the chat (i.e., is already a member)
        await assistant.get_chat(chat_id)
        logger.info("Assistant already in chat %s", chat_id)
        return True
    except Exception:
        # Assistant cannot see the chat — invite it
        pass

    try:
        assistant_me = await assistant.get_me()
        await bot.add_chat_members(chat_id, assistant_me.id)
        logger.info("Invited assistant to chat %s", chat_id)
        return True
    except Exception as exc:
        logger.error("Failed to add assistant to chat %s: %s", chat_id, exc)
        return False

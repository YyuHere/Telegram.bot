"""
generate_session.py — One-time helper to generate the Assistant session string.

Run this script ONCE on your local machine (not on Railway):

    python generate_session.py

Then copy the printed string into your .env as ASSISTANT_SESSION_STRING.

⚠️  Never share or commit the session string — it grants full access to your account.
"""

from pyrogram import Client
from pyrogram.types import User
import asyncio


async def generate() -> None:
    print("=== Pyrogram StringSession Generator ===\n")
    api_id = int(input("Enter your API_ID: ").strip())
    api_hash = input("Enter your API_HASH: ").strip()

    async with Client(
        name="session_gen",
        api_id=api_id,
        api_hash=api_hash,
        in_memory=True,
    ) as app:
        me: User = await app.get_me()
        session_string: str = await app.export_session_string()

    print(f"\n✅ Logged in as: {me.first_name} (@{me.username})")
    print("\n📋 Your ASSISTANT_SESSION_STRING (copy this into .env):\n")
    print(session_string)
    print()


if __name__ == "__main__":
    asyncio.run(generate())

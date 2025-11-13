# context_cache.py
"""
Efficient Discord message caching and context building for chatbot.py
"""

import os
import json
import time
import redis.asyncio as redis
from dotenv import load_dotenv

load_dotenv()

# Redis client (shared)
redis_client = redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"))

# In-memory cache (fallback)
_memory_cache = {}
CACHE_TTL = 120  # seconds


# ──────────────────────────────────────────────
# Fetch + Cache Recent Messages
# ──────────────────────────────────────────────

async def get_recent_context(channel, limit: int = 500):
    """
    Get up to `limit` most recent messages from a Discord channel.
    Uses layered caching: in-memory → Redis → Discord API.

    Returns:
        list[str]: formatted messages like "User(ID): message"
    """
    now = time.time()
    mem_entry = _memory_cache.get(channel.id)

    # 1. In-memory cache hit
    if mem_entry and now - mem_entry["timestamp"] < CACHE_TTL:
        return mem_entry["data"]

    # 2. Redis cache hit
    redis_key = f"context:{channel.id}"
    try:
        cached_data = await redis_client.get(redis_key)
        if cached_data:
            data = json.loads(cached_data)
            _memory_cache[channel.id] = {"data": data, "timestamp": now}
            return data
    except Exception:
        pass  # redis optional

    # 3. Fetch from Discord API
    messages = [
        m async for m in channel.history(limit=limit)
        if not m.author.bot and m.content.strip()
    ]
    messages.reverse()  # chronological order (oldest → newest)

    formatted = [
        f"{m.author.display_name}({m.author.id}): {m.clean_content}"
        for m in messages
    ]

    # Cache results
    _memory_cache[channel.id] = {"data": formatted, "timestamp": now}
    try:
        await redis_client.set(redis_key, json.dumps(formatted), ex=CACHE_TTL)
    except Exception:
        pass

    return formatted


# ──────────────────────────────────────────────
# Context Builder
# ──────────────────────────────────────────────

async def build_context_prompt(message, raw_prompt: str, limit: int = 500):
    """
    Build a model-ready text prompt with up to `limit` recent messages.
    """
    user_label = f"{message.author.display_name}({message.author.id})"
    context_lines = await get_recent_context(message.channel, limit=limit)

    prompt = (
        "Here is the recent Discord conversation:\n"
        + "\n".join(context_lines)
        + f"\n\nNow, respond to the following message by {user_label}:\n"
        + raw_prompt
    )
    return prompt


# ──────────────────────────────────────────────
# Cache Updates for Edits / Deletes
# ──────────────────────────────────────────────

async def update_message_in_cache(before, after):
    """
    Update a message in both caches if it’s edited.
    """
    redis_key = f"context:{before.channel.id}"
    data = _memory_cache.get(before.channel.id)
    if not data:
        return

    updated = []
    replaced = False
    old_line = f"{before.author.display_name}({before.author.id}): {before.clean_content}"
    new_line = f"{after.author.display_name}({after.author.id}): {after.clean_content}"

    for line in data["data"]:
        if line == old_line:
            updated.append(new_line)
            replaced = True
        else:
            updated.append(line)

    if replaced:
        _memory_cache[before.channel.id] = {"data": updated, "timestamp": time.time()}
        try:
            await redis_client.set(redis_key, json.dumps(updated), ex=CACHE_TTL)
        except Exception:
            pass


async def delete_message_from_cache(message):
    """
    Remove a deleted message from both caches.
    """
    redis_key = f"context:{message.channel.id}"
    data = _memory_cache.get(message.channel.id)
    if not data:
        return

    target_line = f"{message.author.display_name}({message.author.id}): {message.clean_content}"
    new_data = [line for line in data["data"] if line != target_line]

    _memory_cache[message.channel.id] = {"data": new_data, "timestamp": time.time()}
    try:
        await redis_client.set(redis_key, json.dumps(new_data), ex=CACHE_TTL)
    except Exception:
        pass

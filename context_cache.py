# context_cache.py
"""
Efficient Discord message caching and context building for chatbot.py
"""

import os
import json
import time
import logging
import redis.asyncio as redis
from typing import List, Optional
from dotenv import load_dotenv

load_dotenv()

# Logger
logger = logging.getLogger(__name__)

# Redis client (shared, with connection pooling)
_redis_client: Optional[redis.Redis] = None
_redis_enabled = os.getenv("USE_REDIS", "false").lower() == "true"

def get_redis_client():
    """Get or create Redis client singleton."""
    global _redis_client
    if _redis_client is None and _redis_enabled:
        try:
            _redis_client = redis.from_url(
                os.getenv("REDIS_URL", "redis://localhost:6379/0"),
                decode_responses=False,  # We handle JSON ourselves
                socket_connect_timeout=2,
                socket_timeout=2,
            )
        except Exception as e:
            logger.warning(f"Redis connection failed: {e}, using in-memory cache only")
            _redis_client = None
    return _redis_client

# In-memory cache (fallback)
_memory_cache = {}
CACHE_TTL = int(os.getenv("CACHE_TTL", "120"))  # seconds


# ──────────────────────────────────────────────
# Fetch + Cache Recent Messages
# ──────────────────────────────────────────────

async def get_recent_context(channel, limit: int = 500) -> List[str]:
    """
    Get up to `limit` most recent messages from a Discord channel.
    Uses layered caching: in-memory → Redis → Discord API.

    Args:
        channel: Discord channel object
        limit: Maximum number of messages to fetch

    Returns:
        list[str]: formatted messages like "User(ID): message"
    """
    now = time.time()
    channel_id = channel.id
    mem_entry = _memory_cache.get(channel_id)

    # 1. In-memory cache hit
    if mem_entry and now - mem_entry["timestamp"] < CACHE_TTL:
        return mem_entry["data"].copy()  # Return copy to prevent mutation

    # 2. Redis cache hit
    redis_key = f"context:{channel_id}"
    redis_client = get_redis_client()
    if redis_client:
        try:
            cached_data = await redis_client.get(redis_key)
            if cached_data:
                data = json.loads(cached_data)
                _memory_cache[channel_id] = {"data": data, "timestamp": now}
                return data.copy()
        except (json.JSONDecodeError, Exception) as e:
            logger.debug(f"Redis cache miss or error for {channel_id}: {e}")

    # 3. Fetch from Discord API
    try:
        messages = []
        async for m in channel.history(limit=limit):
            if not m.author.bot and m.content.strip():
                messages.append(m)
        messages.reverse()  # chronological order (oldest → newest)

        formatted = [
            f"{m.author.display_name}({m.author.id}): {m.clean_content}"
            for m in messages
        ]

        # Cache results
        _memory_cache[channel_id] = {"data": formatted, "timestamp": now}
        if redis_client:
            try:
                await redis_client.set(
                    redis_key, 
                    json.dumps(formatted), 
                    ex=CACHE_TTL
                )
            except Exception as e:
                logger.debug(f"Failed to cache in Redis: {e}")

        return formatted
    except Exception as e:
        logger.error(f"Failed to fetch messages from channel {channel_id}: {e}")
        # Return cached data if available, even if expired
        if mem_entry:
            return mem_entry["data"].copy()
        return []


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

async def append_message_to_cache(message):
    """
    Append a new message to the cache if it exists.
    This is more efficient than refetching all messages.
    """
    channel_id = message.channel.id
    if message.author.bot or not message.content.strip():
        return
    
    mem_entry = _memory_cache.get(channel_id)
    if not mem_entry:
        return  # Cache doesn't exist, will be built on next fetch
    
    new_line = f"{message.author.display_name}({message.author.id}): {message.clean_content}"
    updated_data = mem_entry["data"] + [new_line]
    
    # Keep only last N messages to prevent unbounded growth
    max_messages = 500
    if len(updated_data) > max_messages:
        updated_data = updated_data[-max_messages:]
    
    _memory_cache[channel_id] = {"data": updated_data, "timestamp": time.time()}
    
    redis_key = f"context:{channel_id}"
    redis_client = get_redis_client()
    if redis_client:
        try:
            await redis_client.set(redis_key, json.dumps(updated_data), ex=CACHE_TTL)
        except Exception as e:
            logger.debug(f"Failed to update Redis cache: {e}")


async def update_message_in_cache(before, after):
    """
    Update a message in both caches if it's edited.
    """
    channel_id = before.channel.id
    mem_entry = _memory_cache.get(channel_id)
    if not mem_entry:
        return

    updated = []
    replaced = False
    old_line = f"{before.author.display_name}({before.author.id}): {before.clean_content}"
    new_line = f"{after.author.display_name}({after.author.id}): {after.clean_content}"

    for line in mem_entry["data"]:
        if line == old_line:
            updated.append(new_line)
            replaced = True
        else:
            updated.append(line)

    if replaced:
        _memory_cache[channel_id] = {"data": updated, "timestamp": time.time()}
        redis_key = f"context:{channel_id}"
        redis_client = get_redis_client()
        if redis_client:
            try:
                await redis_client.set(redis_key, json.dumps(updated), ex=CACHE_TTL)
            except Exception as e:
                logger.debug(f"Failed to update Redis cache: {e}")


async def delete_message_from_cache(message):
    """
    Remove a deleted message from both caches.
    """
    channel_id = message.channel.id
    mem_entry = _memory_cache.get(channel_id)
    if not mem_entry:
        return

    target_line = f"{message.author.display_name}({message.author.id}): {message.clean_content}"
    new_data = [line for line in mem_entry["data"] if line != target_line]

    _memory_cache[channel_id] = {"data": new_data, "timestamp": time.time()}
    redis_key = f"context:{channel_id}"
    redis_client = get_redis_client()
    if redis_client:
        try:
            await redis_client.set(redis_key, json.dumps(new_data), ex=CACHE_TTL)
        except Exception as e:
            logger.debug(f"Failed to update Redis cache: {e}")


async def invalidate_cache(channel_id: int):
    """
    Invalidate cache for a specific channel.
    Useful when you want to force a refresh.
    """
    if channel_id in _memory_cache:
        del _memory_cache[channel_id]
    
    redis_key = f"context:{channel_id}"
    redis_client = get_redis_client()
    if redis_client:
        try:
            await redis_client.delete(redis_key)
        except Exception as e:
            logger.debug(f"Failed to delete Redis cache: {e}")

# context_cache.py
"""
Efficient Discord message caching and context building for chatbot.py
"""

import os
import json
import time
import logging
from datetime import datetime, timezone, timedelta
from typing import List, Optional
import redis.asyncio as redis
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

# Timezone configuration (Discord uses UTC, but we can display in a specific timezone)
# Default to Asia/Kolkata (IST - Indian Standard Time), but can be configured via DISCORD_TIMEZONE env var
try:
    import pytz
    _timezone_str = os.getenv("DISCORD_TIMEZONE", "Asia/Kolkata")
    _timezone = pytz.timezone(_timezone_str)
    _has_pytz = True
except ImportError:
    # Fallback: IST is UTC+5:30, but without pytz we can't do proper timezone conversion
    _timezone_str = "UTC"
    _timezone = timezone.utc
    _has_pytz = False
    logger.warning("pytz not installed, using UTC. Install pytz for timezone support (required for IST).")


def format_message_timestamp(message_created_at, current_time: datetime) -> str:
    """
    Format message timestamp with relative time indication.
    Returns formatted string like "[2h ago]" or "[Dec 15, 14:30]" for older messages.
    """
    if not message_created_at:
        return ""
    
    # Ensure both are timezone-aware
    if message_created_at.tzinfo is None:
        message_created_at = message_created_at.replace(tzinfo=timezone.utc)
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=timezone.utc)
    
    # Convert to configured timezone if pytz is available
    if _has_pytz and _timezone != timezone.utc:
        try:
            message_created_at = message_created_at.astimezone(_timezone)
            current_time = current_time.astimezone(_timezone)
        except Exception:
            pass  # Fallback to UTC
    
    time_diff = current_time - message_created_at
    
    # Relative time formatting
    if time_diff < timedelta(minutes=1):
        return "[just now]"
    elif time_diff < timedelta(hours=1):
        minutes = int(time_diff.total_seconds() / 60)
        return f"[{minutes}m ago]"
    elif time_diff < timedelta(days=1):
        hours = int(time_diff.total_seconds() / 3600)
        return f"[{hours}h ago]"
    elif time_diff < timedelta(days=7):
        days = time_diff.days
        return f"[{days}d ago]"
    else:
        # For older messages, show date and time
        return f"[{message_created_at.strftime('%b %d, %H:%M')}]"


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
        current_time = datetime.now(timezone.utc)
        async for m in channel.history(limit=limit):
            if not m.author.bot and m.content.strip():
                messages.append(m)
        messages.reverse()  # chronological order (oldest → newest)

        formatted = []
        for m in messages:
            timestamp_str = format_message_timestamp(m.created_at, current_time)
            formatted.append(
                f"{timestamp_str} {m.author.display_name}({m.author.id}): {m.clean_content}"
            )

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
    Includes current date/time for temporal awareness.
    """
    user_label = f"{message.author.display_name}({message.author.id})"
    context_lines = await get_recent_context(message.channel, limit=limit)
    
    # Get current date/time with timezone
    now = datetime.now(timezone.utc)
    if _has_pytz and _timezone != timezone.utc:
        try:
            now = now.astimezone(_timezone)
        except Exception:
            pass
    
    # Format current time with proper timezone display
    if _has_pytz:
        # Get timezone abbreviation (IST, etc.)
        try:
            tz_abbr = now.strftime("%Z") or _timezone_str
        except Exception:
            tz_abbr = "IST" if "Kolkata" in _timezone_str else _timezone_str
        current_time_str = now.strftime("%Y-%m-%d %H:%M:%S %Z")
        timezone_name = f"{tz_abbr} (Asia/Kolkata, UTC+5:30)" if "Kolkata" in _timezone_str else _timezone_str
    else:
        current_time_str = now.strftime("%Y-%m-%d %H:%M:%S UTC")
        timezone_name = "UTC (pytz not installed - install pytz for IST support)"
    
    # Format message timestamp
    message_timestamp = format_message_timestamp(message.created_at, now)
    if not message_timestamp:
        message_timestamp = "[now]"

    prompt = (
        f"Current Date/Time: {current_time_str} ({timezone_name})\n"
        f"Timezone: Indian Standard Time (IST) - Asia/Kolkata (UTC+5:30)\n"
        f"All message timestamps are relative to this current time and displayed in IST.\n\n"
        f"Here is the recent Discord conversation (messages are in chronological order, oldest to newest):\n"
        + "\n".join(context_lines)
        + f"\n\n{message_timestamp} {user_label} says: {raw_prompt}\n\n"
        f"IMPORTANT: The message above is the CURRENT message you need to respond to. "
        f"All previous messages in the conversation are from the PAST. "
        f"Pay attention to timestamps to understand when things happened relative to now. "
        f"All times are in IST (Indian Standard Time, UTC+5:30)."
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
    
    # Format with timestamp
    current_time = datetime.now(timezone.utc)
    timestamp_str = format_message_timestamp(message.created_at, current_time)
    new_line = f"{timestamp_str} {message.author.display_name}({message.author.id}): {message.clean_content}"
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
    current_time = datetime.now(timezone.utc)
    timestamp_str = format_message_timestamp(before.created_at, current_time)
    
    # Match with or without timestamp (for backward compatibility)
    old_line_no_ts = f"{before.author.display_name}({before.author.id}): {before.clean_content}"
    old_line_with_ts = f"{timestamp_str} {old_line_no_ts}"
    new_line = f"{timestamp_str} {after.author.display_name}({after.author.id}): {after.clean_content}"

    for line in mem_entry["data"]:
        # Check both with and without timestamp for backward compatibility
        if line == old_line_with_ts or line == old_line_no_ts or line.endswith(before.clean_content):
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

    # Match with or without timestamp
    target_line_no_ts = f"{message.author.display_name}({message.author.id}): {message.clean_content}"
    current_time = datetime.now(timezone.utc)
    timestamp_str = format_message_timestamp(message.created_at, current_time)
    target_line_with_ts = f"{timestamp_str} {target_line_no_ts}"
    
    # Remove matching lines (with or without timestamp)
    new_data = [
        line for line in mem_entry["data"] 
        if line != target_line_no_ts and line != target_line_with_ts and not line.endswith(message.clean_content)
    ]

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

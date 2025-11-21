# context_cache.py
"""
Efficient Discord message caching and context building for chatbot.py
Optimized for low latency using local memory (deque).
"""

import os
import time
import logging
from datetime import datetime, timezone, timedelta
from typing import List, Optional, Dict, Deque
from collections import deque
from dotenv import load_dotenv

load_dotenv()

# Logger
logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────
# Configuration & In-Memory Cache
# ──────────────────────────────────────────────

# Cache configuration
CACHE_TTL = int(os.getenv("CACHE_TTL", "120"))  # seconds
MAX_MESSAGES_IN_CACHE = int(os.getenv("MAX_MESSAGES_IN_CACHE", "2000"))

# In-memory cache structure:
# { channel_id: {"data": deque(maxlen=MAX_MESSAGES_IN_CACHE), "timestamp": float} }
_memory_cache: Dict[int, Dict] = {}

# Timezone configuration
try:
    import pytz
    _timezone_str = os.getenv("DISCORD_TIMEZONE", "Asia/Kolkata")
    _timezone = pytz.timezone(_timezone_str)
    _has_pytz = True
except ImportError:
    _timezone_str = "UTC"
    _timezone = timezone.utc
    _has_pytz = False
    logger.warning("pytz not installed, using UTC. Install pytz for timezone support.")


def format_message_timestamp(message_created_at, current_time: datetime) -> str:
    """
    Format message timestamp with relative time indication.
    """
    if not message_created_at:
        return ""
    
    if message_created_at.tzinfo is None:
        message_created_at = message_created_at.replace(tzinfo=timezone.utc)
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=timezone.utc)
    
    if _has_pytz and _timezone != timezone.utc:
        try:
            message_created_at = message_created_at.astimezone(_timezone)
            current_time = current_time.astimezone(_timezone)
        except Exception:
            pass
    
    time_diff = current_time - message_created_at
    
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
        return f"[{message_created_at.strftime('%b %d, %H:%M')}]"


# ──────────────────────────────────────────────
# Fetch + Cache Recent Messages
# ──────────────────────────────────────────────

async def get_recent_context(channel, limit: int = 500, before_message=None) -> List[str]:
    """
    Get recent messages from cache or Discord API.
    """
    now = time.time()
    channel_id = channel.id
    mem_entry = _memory_cache.get(channel_id)

    # 1. Cache Hit
    if mem_entry and now - mem_entry["timestamp"] < CACHE_TTL and before_message is None:
        # Convert deque to list for slicing
        cached_data = list(mem_entry["data"])
        
        # Only return if we have enough data or if the cache is likely full (heuristic)
        # If we requested 2000 but have 100, we should fetch more.
        if len(cached_data) >= limit:
            return cached_data[-limit:]
        
        # If we have fewer messages than limit, we might need to fetch more.
        # However, if the channel simply has few messages, we don't want to spam API.
        # But for now, let's assume if we want more, we fetch more.
        logger.info(f"[get_recent_context] Cache has {len(cached_data)} messages, requested {limit}. Fetching more.")

    # 2. Fetch from Discord API
    logger.info(f"[get_recent_context] Fetching messages for channel {channel_id}")
    
    try:
        messages = []
        current_time = datetime.now(timezone.utc)
        
        # Fetch 50% more to account for filtering
        fetch_limit = int(limit * 1.5)
        
        if before_message:
            async for m in channel.history(limit=fetch_limit, before=before_message):
                if m.content and m.content.strip():
                    messages.append(m)
                    if len(messages) >= limit:
                        break
        else:
            async for m in channel.history(limit=fetch_limit):
                if m.content and m.content.strip():
                    messages.append(m)
                    if len(messages) >= limit:
                        break
        
        messages.reverse()  # chronological order

        formatted = deque(maxlen=MAX_MESSAGES_IN_CACHE)
        for m in messages:
            timestamp_str = format_message_timestamp(m.created_at, current_time)
            formatted.append(
                f"{timestamp_str} {m.author.display_name}({m.author.id}): {m.clean_content}"
            )

        # Update Cache
        _memory_cache[channel_id] = {"data": formatted, "timestamp": now}
        
        return list(formatted)
        
    except Exception as e:
        logger.error(f"[get_recent_context] Error: {e}", exc_info=True)
        if mem_entry:
            return list(mem_entry["data"])
        return []


# ──────────────────────────────────────────────
# Context Builder
# ──────────────────────────────────────────────

async def build_context_prompt(message, raw_prompt: str, limit: int = None, reply_to_message=None):
    """
    Build a model-ready text prompt.
    """
    if limit is None:
        limit = MAX_MESSAGES_IN_CACHE

    user_label = f"{message.author.display_name}({message.author.id})"
    context_lines = await get_recent_context(message.channel, limit=limit, before_message=message)

    # Trim if needed
    if len(context_lines) > limit:
        context_lines = context_lines[-limit:]

    # Metadata
    try:
        channel_name = getattr(message.channel, "name", "DM")
        guild_name = getattr(message.guild, "name", "DM")
    except Exception:
        channel_name = "unknown"
        guild_name = "DM"

    channel_meta = (
        f"Channel ID: {message.channel.id}\n"
        f"Channel: {channel_name}\n"
        f"Guild: {guild_name}\n"
        "----\n"
    )

    # Time
    now = datetime.now(timezone.utc)
    if _has_pytz and _timezone != timezone.utc:
        try:
            now = now.astimezone(_timezone)
        except Exception:
            pass
            
    current_time_str = now.strftime("%Y-%m-%d %H:%M:%S %Z")
    message_timestamp = format_message_timestamp(message.created_at, now) or "[now]"

    # Format Reply Context if present
    reply_context_str = ""
    if reply_to_message:
        reply_ts = format_message_timestamp(reply_to_message.created_at, now)
        reply_author = f"{reply_to_message.author.display_name}({reply_to_message.author.id})"
        reply_content = reply_to_message.clean_content
        reply_context_str = (
            f"\n[REPLY CONTEXT]\n"
            f"The user is replying to:\n"
            f"{reply_ts} {reply_author}: {reply_content}\n"
            f"----------------\n"
        )

    prompt = (
        f"{channel_meta}"
        f"Current Time: {current_time_str}\n"
        f"Timestamps are relative to this time.\n\n"
        f"Conversation History:\n"
        + "\n".join(context_lines)
        + f"\n{reply_context_str}"
        + f"\n{message_timestamp} {user_label} says: {raw_prompt}\n\n"
        f"IMPORTANT: The message above is the CURRENT message that you need to respond to."
    )
    return prompt


# ──────────────────────────────────────────────
# Cache Updates
# ──────────────────────────────────────────────

async def append_message_to_cache(message):
    """
    Append a new message to the in-memory deque.
    """
    if not message.content.strip():
        return

    channel_id = message.channel.id
    current_time = datetime.now(timezone.utc)
    timestamp_str = format_message_timestamp(message.created_at, current_time)
    new_line = f"{timestamp_str} {message.author.display_name}({message.author.id}): {message.clean_content}"
    
    mem_entry = _memory_cache.get(channel_id)
    
    if mem_entry:
        mem_entry["data"].append(new_line)
        mem_entry["timestamp"] = time.time()
    else:
        # Initialize new cache entry
        d = deque(maxlen=MAX_MESSAGES_IN_CACHE)
        d.append(new_line)
        _memory_cache[channel_id] = {"data": d, "timestamp": time.time()}


async def update_message_in_cache(before, after):
    """
    Update a message in the cache.
    """
    channel_id = before.channel.id
    mem_entry = _memory_cache.get(channel_id)
    if not mem_entry:
        return

    current_time = datetime.now(timezone.utc)
    timestamp_str = format_message_timestamp(before.created_at, current_time)
    
    # We need to reconstruct the deque to update an item in the middle
    # This is O(N) but N is small (MAX_MESSAGES_IN_CACHE)
    old_data = mem_entry["data"]
    new_data = deque(maxlen=MAX_MESSAGES_IN_CACHE)
    
    new_line = f"{timestamp_str} {after.author.display_name}({after.author.id}): {after.clean_content}"
    
    for line in old_data:
        if line.endswith(before.clean_content):
            new_data.append(new_line)
        else:
            new_data.append(line)
            
    mem_entry["data"] = new_data
    mem_entry["timestamp"] = time.time()


async def delete_message_from_cache(message):
    """
    Remove a message from the cache.
    """
    channel_id = message.channel.id
    mem_entry = _memory_cache.get(channel_id)
    if not mem_entry:
        return

    old_data = mem_entry["data"]
    new_data = deque(maxlen=MAX_MESSAGES_IN_CACHE)
    
    for line in old_data:
        if not line.endswith(message.clean_content):
            new_data.append(line)
            
    mem_entry["data"] = new_data
    mem_entry["timestamp"] = time.time()


async def invalidate_cache(channel_id: int):
    if channel_id in _memory_cache:
        del _memory_cache[channel_id]

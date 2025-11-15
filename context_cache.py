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

# Redis size limits (to prevent "max request size exceeded" errors)
# Default: 10MB per command (10 * 1024 * 1024 bytes) - safe limit for most Redis instances
# We'll chunk data across multiple commands if it exceeds this
MAX_REDIS_CHUNK_SIZE = int(os.getenv("MAX_REDIS_CHUNK_SIZE", str(10 * 1024 * 1024)))  # bytes per chunk
MAX_MESSAGES_IN_CACHE = int(os.getenv("MAX_MESSAGES_IN_CACHE", "500"))  # Can be higher now with chunking

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
# Redis Chunking Management
# ──────────────────────────────────────────────

def _chunk_data(data: List[str], max_chunk_size_bytes: int) -> List[List[str]]:
    """
    Split data into chunks where each chunk's JSON representation fits within max_chunk_size_bytes.
    Returns a list of chunks.
    """
    if not data:
        return []
    
    chunks = []
    current_chunk = []
    
    for item in data:
        # Test if adding this item would exceed the limit
        test_chunk = current_chunk + [item]
        test_json = json.dumps(test_chunk)
        test_size = len(test_json.encode('utf-8'))
        
        # Check if adding this item would exceed the limit
        if current_chunk and test_size > max_chunk_size_bytes:
            # Current chunk is full, save it and start a new one
            chunks.append(current_chunk)
            current_chunk = [item]
        else:
            # Add to current chunk
            current_chunk.append(item)
    
    # Add the last chunk if it has data
    if current_chunk:
        chunks.append(current_chunk)
    
    return chunks


async def _chunked_redis_set(redis_client, base_key: str, data: List[str], ttl: int):
    """
    Store data in Redis using chunking if needed. Data is split across multiple keys
    if it exceeds MAX_REDIS_CHUNK_SIZE.
    
    Keys format:
    - Single chunk: `base_key` (for backward compatibility)
    - Multiple chunks: `base_key:chunk:0`, `base_key:chunk:1`, etc.
    - Metadata: `base_key:meta` (stores chunk count)
    """
    if not data:
        # Delete all chunks if data is empty
        await _chunked_redis_delete(redis_client, base_key)
        return True
    
    # Check if data fits in a single chunk
    json_str = json.dumps(data)
    size_bytes = len(json_str.encode('utf-8'))
    
    if size_bytes <= MAX_REDIS_CHUNK_SIZE:
        # Single chunk - use the base key for backward compatibility
        try:
            # Clean up any old chunked data
            await _chunked_redis_delete(redis_client, base_key, keep_base=False)
            await redis_client.set(base_key, json_str, ex=ttl)
            return True
        except Exception as e:
            error_msg = str(e).lower()
            if "max request size" in error_msg or "command too large" in error_msg:
                # Fall through to chunking
                logger.debug(f"Single chunk too large, falling back to chunking: {e}")
            else:
                logger.debug(f"Failed to set Redis key {base_key}: {e}")
                return False
    
    # Need to chunk the data
    chunks = _chunk_data(data, MAX_REDIS_CHUNK_SIZE)
    
    if not chunks:
        return False
    
    try:
        # Delete old chunks first
        await _chunked_redis_delete(redis_client, base_key, keep_base=True)
        
        # Store each chunk
        for i, chunk in enumerate(chunks):
            chunk_key = f"{base_key}:chunk:{i}"
            chunk_json = json.dumps(chunk)
            await redis_client.set(chunk_key, chunk_json, ex=ttl)
        
        # Store metadata (chunk count)
        meta_key = f"{base_key}:meta"
        meta_data = {"chunks": len(chunks), "total_messages": len(data)}
        await redis_client.set(meta_key, json.dumps(meta_data), ex=ttl)
        
        logger.debug(f"Stored {len(data)} messages in {len(chunks)} chunks for key {base_key}")
        return True
    except Exception as e:
        error_msg = str(e).lower()
        if "max request size" in error_msg or "command too large" in error_msg:
            logger.error(
                f"Redis chunk still too large for key {base_key}: {e}. "
                f"Consider reducing MAX_REDIS_CHUNK_SIZE."
            )
        else:
            logger.debug(f"Failed to set chunked Redis data for key {base_key}: {e}")
        return False


async def _chunked_redis_get(redis_client, base_key: str) -> Optional[List[str]]:
    """
    Retrieve data from Redis, handling both single-key and chunked storage.
    Returns None if data doesn't exist or can't be retrieved.
    """
    try:
        # First, try to get as single key (backward compatibility)
        single_data = await redis_client.get(base_key)
        if single_data:
            return json.loads(single_data)
        
        # Check for chunked data
        meta_key = f"{base_key}:meta"
        meta_data = await redis_client.get(meta_key)
        
        if not meta_data:
            return None
        
        meta = json.loads(meta_data)
        num_chunks = meta.get("chunks", 0)
        
        if num_chunks == 0:
            return None
        
        # Fetch all chunks
        chunks = []
        for i in range(num_chunks):
            chunk_key = f"{base_key}:chunk:{i}"
            chunk_data = await redis_client.get(chunk_key)
            if chunk_data:
                chunks.append(json.loads(chunk_data))
            else:
                logger.warning(f"Missing chunk {i} for key {base_key}")
                return None  # Incomplete data
        
        # Combine all chunks
        combined = []
        for chunk in chunks:
            combined.extend(chunk)
        
        return combined
    except (json.JSONDecodeError, Exception) as e:
        logger.debug(f"Failed to get chunked Redis data for key {base_key}: {e}")
        return None


async def _chunked_redis_delete(redis_client, base_key: str, keep_base: bool = False):
    """
    Delete all chunks and metadata for a key. If keep_base is True, don't delete the base key.
    """
    try:
        # Delete base key if not keeping it
        if not keep_base:
            await redis_client.delete(base_key)
        
        # Delete metadata to get chunk count
        meta_key = f"{base_key}:meta"
        meta_data = await redis_client.get(meta_key)
        
        if meta_data:
            meta = json.loads(meta_data)
            num_chunks = meta.get("chunks", 0)
            
            # Delete all chunks
            chunk_keys = [f"{base_key}:chunk:{i}" for i in range(num_chunks)]
            if chunk_keys:
                await redis_client.delete(*chunk_keys)
            
            # Delete metadata
            await redis_client.delete(meta_key)
    except Exception as e:
        logger.debug(f"Failed to delete chunked Redis data for key {base_key}: {e}")


# ──────────────────────────────────────────────
# Fetch + Cache Recent Messages
# ──────────────────────────────────────────────

async def get_recent_context(channel, limit: int = 500) -> List[str]:
    """
    Get up to `limit` most recent messages from a Discord channel.
    Uses layered caching: in-memory → Redis → Discord API.

    Args:
        channel: Discord channel object
        limit: Maximum number of messages to fetch (will be capped at MAX_MESSAGES_IN_CACHE)

    Returns:
        list[str]: formatted messages like "User(ID): message"
    """
    # Cap the limit to prevent Redis size issues
    effective_limit = min(limit, MAX_MESSAGES_IN_CACHE)
    
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
            data = await _chunked_redis_get(redis_client, redis_key)
            if data:
                # Cap the cached data to MAX_MESSAGES_IN_CACHE
                if len(data) > MAX_MESSAGES_IN_CACHE:
                    data = data[-MAX_MESSAGES_IN_CACHE:]
                _memory_cache[channel_id] = {"data": data, "timestamp": now}
                return data.copy()
        except Exception as e:
            logger.debug(f"Redis cache miss or error for {channel_id}: {e}")

    # 3. Fetch from Discord API
    try:
        messages = []
        current_time = datetime.now(timezone.utc)
        async for m in channel.history(limit=effective_limit):
            # Include all messages (both user and bot) for full context
            if m.content.strip():
                messages.append(m)
        messages.reverse()  # chronological order (oldest → newest)

        formatted = []
        for m in messages:
            timestamp_str = format_message_timestamp(m.created_at, current_time)
            formatted.append(
                f"{timestamp_str} {m.author.display_name}({m.author.id}): {m.clean_content}"
            )

        # Cache results (in-memory always, Redis with chunking if needed)
        _memory_cache[channel_id] = {"data": formatted, "timestamp": now}
        if redis_client:
            await _chunked_redis_set(redis_client, redis_key, formatted, CACHE_TTL)

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

async def build_context_prompt(message, raw_prompt: str, limit: int = None):
    """
    Build a model-ready text prompt with up to `limit` recent messages.
    Includes current date/time for temporal awareness.
    """
    # Use MAX_MESSAGES_IN_CACHE as default if limit not specified
    if limit is None:
        limit = MAX_MESSAGES_IN_CACHE
    
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
    Includes both user and bot messages for full conversation context.
    """
    channel_id = message.channel.id
    if not message.content.strip():
        return
    
    # Format with timestamp
    current_time = datetime.now(timezone.utc)
    timestamp_str = format_message_timestamp(message.created_at, current_time)
    new_line = f"{timestamp_str} {message.author.display_name}({message.author.id}): {message.clean_content}"
    
    mem_entry = _memory_cache.get(channel_id)
    redis_key = f"context:{channel_id}"
    redis_client = get_redis_client()
    
    # Get existing data from memory or Redis
    existing_data = None
    if mem_entry:
        existing_data = mem_entry["data"]
    elif redis_client:
        # Try to load from Redis if memory cache doesn't exist
        try:
            existing_data = await _chunked_redis_get(redis_client, redis_key)
        except Exception as e:
            logger.debug(f"Failed to load from Redis for append: {e}")
    
    # If no cache exists, just create a new one with this message
    if existing_data is None:
        updated_data = [new_line]
    else:
        updated_data = existing_data + [new_line]
    
    # Keep only last N messages to prevent unbounded growth
    if len(updated_data) > MAX_MESSAGES_IN_CACHE:
        updated_data = updated_data[-MAX_MESSAGES_IN_CACHE:]
    
    # Update both caches
    _memory_cache[channel_id] = {"data": updated_data, "timestamp": time.time()}
    
    if redis_client:
        await _chunked_redis_set(redis_client, redis_key, updated_data, CACHE_TTL)


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
        # Cap to MAX_MESSAGES_IN_CACHE
        if len(updated) > MAX_MESSAGES_IN_CACHE:
            updated = updated[-MAX_MESSAGES_IN_CACHE:]
        
        _memory_cache[channel_id] = {"data": updated, "timestamp": time.time()}
        redis_key = f"context:{channel_id}"
        redis_client = get_redis_client()
        if redis_client:
            await _chunked_redis_set(redis_client, redis_key, updated, CACHE_TTL)


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

    # Cap to MAX_MESSAGES_IN_CACHE
    if len(new_data) > MAX_MESSAGES_IN_CACHE:
        new_data = new_data[-MAX_MESSAGES_IN_CACHE:]
    
    _memory_cache[channel_id] = {"data": new_data, "timestamp": time.time()}
    redis_key = f"context:{channel_id}"
    redis_client = get_redis_client()
    if redis_client:
        await _chunked_redis_set(redis_client, redis_key, new_data, CACHE_TTL)


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
            await _chunked_redis_delete(redis_client, redis_key, keep_base=False)
        except Exception as e:
            logger.debug(f"Failed to delete Redis cache: {e}")

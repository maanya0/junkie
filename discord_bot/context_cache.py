# context_cache.py
"""
Efficient Discord message caching and context building for chatbot.py
Optimized for persistence using PostgreSQL.
"""

import os
import time
import logging
from datetime import datetime, timezone, timedelta
from typing import List, Optional, Dict
from dotenv import load_dotenv
from core.database import store_message, get_messages, get_message_count, is_channel_fully_backfilled, mark_channel_fully_backfilled
import discord

load_dotenv()

# Logger
logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────
# Configuration
# ──────────────────────────────────────────────

# Cache configuration
CACHE_TTL = int(os.getenv("CACHE_TTL", "120"))  # seconds
from core.config import CONTEXT_AGENT_MAX_MESSAGES
MAX_MESSAGES_IN_CACHE = CONTEXT_AGENT_MAX_MESSAGES

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
    Get recent messages from DB or Discord API.
    Implements loop prevention to avoid infinite recursion.
    """
    channel_id = channel.id
    
    # 1. Try DB first
    db_messages = await get_messages(channel_id, limit)
    
    # If we have enough messages, return them
    # Note: We ignore 'before_message' for DB fetch simplicity for now, 
    # assuming we want the *latest* context. If strict pagination is needed, 
    # get_messages needs updating. For chatbot context, latest is usually what we want.
    if len(db_messages) >= limit and before_message is None:
        formatted = []
        current_time = datetime.now(timezone.utc)
        for m in db_messages:
            # Calculate relative time dynamically
            rel_time = format_message_timestamp(m['created_at'], current_time)
            formatted.append(f"{rel_time} {m['author_name']}({m['author_id']}): {m['content']}")
        return formatted

    # 2. If DB has insufficient data, we might rely on backfill or fetch fresh
    # For "instant" retrieval, we prefer DB. But if it's empty, we must fetch.
    if len(db_messages) == 0:
        logger.info(f"[get_recent_context] DB empty for {channel_id}, fetching from API.")
        return await fetch_and_cache_from_api(channel, limit, before_message)
    
    # If we have some data but not enough, check if we can fetch more
    if len(db_messages) < limit:
        # Check if channel is fully backfilled (meaning no more history exists)
        is_full = await is_channel_fully_backfilled(channel_id)
        
        if not is_full:
            needed = limit - len(db_messages)
            logger.info(f"[get_recent_context] DB has {len(db_messages)} messages, need {needed} more. Fetching from API.")
            
            # Oldest message in DB is the first one in the list (chronological)
            oldest_msg_id = db_messages[0]['message_id']
            
            try:
                before_obj = discord.Object(id=oldest_msg_id)
                more_messages = await fetch_and_cache_from_api(channel, limit=needed, before_message=before_obj)
                
                if not more_messages:
                    # If API returns nothing, we are likely fully backfilled
                    await mark_channel_fully_backfilled(channel_id, True)
            except Exception as e:
                logger.error(f"[get_recent_context] Error fetching more history: {e}")

    # FIXED: Don't re-fetch in a loop. Return what we have after one attempt.
    logger.info(f"[get_recent_context] Returning {len(db_messages)} messages from DB (requested {limit}).")
    
    # Format messages with current time (calculated once)
    current_time = datetime.now(timezone.utc)
    formatted = []
    
    # Re-query DB one final time to include any newly cached messages
    final_db_messages = await get_messages(channel_id, limit)
    
    for m in final_db_messages:
        rel_time = format_message_timestamp(m['created_at'], current_time)
        formatted.append(f"{rel_time} {m['author_name']}({m['author_id']}): {m['content']}")
    return formatted

async def fetch_and_cache_from_api(channel, limit, before_message=None, after_message=None):
    """Helper to fetch from API and cache to DB."""
    try:
        channel_name = getattr(channel, "name", "DM")
        logger.info(f"[fetch_and_cache] Fetching up to {limit} messages for channel {channel_name} ({channel.id})")
        messages = []
        current_time = datetime.now(timezone.utc)
        
        # Cap fetch_limit to prevent overwhelming the API (Discord max is 100 per request)
        # Reasonable cap: 1000 messages (10 API requests with proper pagination)
        BACKFILL_MAX_FETCH_LIMIT = int(os.getenv("BACKFILL_MAX_FETCH_LIMIT", "1000"))
        fetch_limit = min(int(limit * 1.2), BACKFILL_MAX_FETCH_LIMIT)  # 20% buffer, capped
        
        
        if after_message:
            # When using 'after', Discord returns oldest -> newest
            async for m in channel.history(limit=fetch_limit, after=after_message):
                # Include messages with content, attachments, or embeds
                if m.content or m.attachments or m.embeds:
                    messages.append(m)
                    if len(messages) >= limit:
                        break
        elif before_message:
            async for m in channel.history(limit=fetch_limit, before=before_message):
                # Include messages with content, attachments, or embeds
                if m.content or m.attachments or m.embeds:
                    messages.append(m)
                    if len(messages) >= limit:
                        break
        else:
            async for m in channel.history(limit=fetch_limit):
                # Include messages with content, attachments, or embeds
                if m.content or m.attachments or m.embeds:
                    messages.append(m)
                    if len(messages) >= limit:
                        break
        
        # If NOT using 'after', the default history order is newest -> oldest, 
        # so we reverse to get chronological.
        # If using 'after', it's already chronological (oldest -> newest).
        if not after_message:
            messages.reverse() # Chronological
        
        formatted = []
        stored_count = 0
        for m in messages:
            # Store absolute timestamp for hygiene, but use dynamic relative time for return
            timestamp_str = m.created_at.strftime("%Y-%m-%d %H:%M:%S")
            rel_time = format_message_timestamp(m.created_at, current_time)
            
            # Store in DB
            await store_message(
                message_id=m.id,
                channel_id=channel.id,
                author_id=m.author.id,
                author_name=m.author.display_name,
                content=m.content or "[Image/Embed]",  # Handle messages with no text content
                created_at=m.created_at,
                timestamp_str=timestamp_str
            )
            stored_count += 1
            
            formatted.append(
                f"{rel_time} {m.author.display_name}({m.author.id}): {m.clean_content}"
            )
        
        logger.info(f"[fetch_and_cache] Successfully stored {stored_count} messages for channel {channel.id}")
        return formatted
    except discord.errors.Forbidden:
        logger.warning(f"[fetch_and_cache] Missing access to channel {channel.id}. Skipping.")
        return []
    except Exception as e:
        logger.error(f"[fetch_and_cache] Error: {e}", exc_info=True)
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
    Append a new message to the DB.
    """
    if not message.content.strip():
        return

    current_time = datetime.now(timezone.utc)
    timestamp_str = message.created_at.strftime("%Y-%m-%d %H:%M:%S")
    
    await store_message(
        message_id=message.id,
        channel_id=message.channel.id,
        author_id=message.author.id,
        author_name=message.author.display_name,
        content=message.clean_content,
        created_at=message.created_at,
        timestamp_str=timestamp_str
    )


async def update_message_in_cache(before, after):
    """
    Update a message in the DB.
    """
    # store_message handles upsert/update
    await append_message_to_cache(after)


async def delete_message_from_cache(message):
    """
    Remove a message from the DB? 
    Actually, for history we might want to keep it or mark deleted. 
    But for now, let's do nothing or implement delete in DB if needed.
    """
    pass # TODO: Implement delete if strict history accuracy is needed


async def invalidate_cache(channel_id: int):
    pass # No-op for DB

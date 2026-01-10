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
    

    # 2. If DB has insufficient data, fetch from API
    if len(db_messages) < limit:
        # Check if channel is fully backfilled
        is_full = await is_channel_fully_backfilled(channel_id)
        
        # If DB is empty, we fetch regardless of backfill status to be safe (migration case)
        if not is_full or len(db_messages) == 0:
            needed = limit - len(db_messages)
            if len(db_messages) == 0:
                logger.info(f"[get_recent_context] DB empty for {channel_id}, fetching from API.")
                await fetch_and_cache_from_api(channel, limit, before_message)
            else:
                logger.info(f"[get_recent_context] DB has {len(db_messages)} messages, fetching more.")
                oldest_msg_id = db_messages[0]['message_id']
                try:
                    before_obj = discord.Object(id=oldest_msg_id)
                    await fetch_and_cache_from_api(channel, limit=needed, before_message=before_obj)
                except Exception as e:
                     logger.error(f"[get_recent_context] Error fetching more: {e}")

    # Re-fetch from DB to get everything including newly fetched
    final_messages = await get_messages(channel_id, limit)
    return final_messages

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
        messages_to_store = []  # Collect for batch insert
        
        for m in messages:
            # Store absolute timestamp for hygiene, but use dynamic relative time for return
            timestamp_str = m.created_at.strftime("%Y-%m-%d %H:%M:%S")
            rel_time = format_message_timestamp(m.created_at, current_time)
            
            # Build content with attachments and embeds
            content_parts = []
            if m.content:
                content_parts.append(m.content)
            if m.attachments:
                for att in m.attachments:
                    content_parts.append(f"[Attachment: {att.url}]")
            if m.embeds and not m.attachments:  # Only add embeds if no attachments (avoid duplication)
                content_parts.append(f"[Embed: {len(m.embeds)} embed(s)]")
            
            content = " ".join(content_parts) if content_parts else "[Empty message]"
            
            # Prepare reply info if present
            reply_to_id = None
            reply_to_author_id = None
            reply_to_author_name = None
            reply_to_content = None
            
            if m.reference and m.reference.resolved:
                if isinstance(m.reference.resolved, discord.Message):
                    ref = m.reference.resolved
                    reply_to_id = ref.id
                    reply_to_author_id = ref.author.id
                    reply_to_author_name = ref.author.display_name
                    reply_to_content = ref.clean_content

            content_hash = str(hash(content))
            
            # Collect message data for batch insert
            messages_to_store.append({
                'message_id': m.id,
                'channel_id': channel.id,
                'author_id': m.author.id,
                'author_name': m.author.display_name,
                'content': content,
                'created_at': m.created_at,
                'timestamp_str': timestamp_str,
                'content_hash': content_hash,
                'reply_to_message_id': reply_to_id,
                'reply_to_author_id': reply_to_author_id,
                'reply_to_author_name': reply_to_author_name,
                'reply_to_content': reply_to_content
            })
            
            # Build formatted string for legacy return value
            formatted.append(
                f"{rel_time} {m.author.display_name}({m.author.id}): {m.clean_content}"
            )
        
        # Batch store all messages at once (single DB round-trip instead of N)
        if messages_to_store:
            from core.database import batch_store_messages_with_replies
            stored_count = await batch_store_messages_with_replies(messages_to_store)
            logger.info(f"[fetch_and_cache] Batch stored {stored_count} messages for channel {channel.id}")
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
    Build a model-ready TOON-formatted prompt with structured history.
    """
    import json
    try:
        from toon_format import encode as toon_encode
        HAS_TOON = True
    except ImportError:
        HAS_TOON = False
    
    if limit is None:
        limit = MAX_MESSAGES_IN_CACHE

    # Use unified structured history
    structured_history = await get_recent_context(message.channel, limit=limit, before_message=message)

    # Trim if needed
    if len(structured_history) > limit:
        structured_history = structured_history[-limit:]

    # Metadata
    try:
        channel_name = getattr(message.channel, "name", "DM")
        guild_name = getattr(message.guild, "name", "DM")
    except Exception:
        channel_name = "unknown"
        guild_name = "DM"

    # Time
    now = datetime.now(timezone.utc)
    if _has_pytz and _timezone != timezone.utc:
        try:
            now = now.astimezone(_timezone)
        except Exception:
            pass
            
    current_time_str = now.strftime("%Y-%m-%d %H:%M:%S %Z")
    message_timestamp = format_message_timestamp(message.created_at, now) or "[now]"

    # Process history for TOON
    history_data = []
    for m in structured_history:
        # Format timestamps
        ts = format_message_timestamp(m['created_at'], now)
        
        entry = {
            "id": str(m['message_id']),
            "author": f"{m['author_name']}",
            "content": m['content'],
            "time": ts
        }
        
        # Add reply info if present
        if m.get('reply_to_message_id'):
            entry["reply_to"] = {
                "id": str(m['reply_to_message_id']),
                "author": m.get('reply_to_author_name', 'Unknown'),
                "snippet": (m.get('reply_to_content') or "")[:50] + "..."
            }
        
        history_data.append(entry)

    # Build current message with attachments
    current_attachments = []
    if message.attachments:
        for att in message.attachments:
            current_attachments.append({
                "filename": att.filename,
                "url": att.url
            })

    # Build reply context for current message
    reply_context = None
    if reply_to_message:
        reply_ts = format_message_timestamp(reply_to_message.created_at, now)
        reply_context = {
            "id": str(reply_to_message.id),
            "author": reply_to_message.author.display_name,
            "content": reply_to_message.clean_content,
            "time": reply_ts
        }

    # Build the structured context structure
    context_data = {
        "meta": {
            "channel": f"{channel_name} ({message.channel.id})",
            "guild": guild_name,
            "time": current_time_str
        },
        "history": history_data,
        "current": {
            "id": str(message.id),
            "author": message.author.display_name,
            "time": message_timestamp,
            "content": raw_prompt,
            "attachments": current_attachments, 
            "reply_to": reply_context
        }
    }

    # Format using TOON or JSON fallback
    if HAS_TOON:
        formatted_context = toon_encode(context_data)
        format_name = "TOON"
    else:
        formatted_context = json.dumps(context_data, indent=2, ensure_ascii=False)
        format_name = "JSON"

    prompt = f"""## Context ({format_name})
```{format_name.lower()}
{formatted_context}
```

IMPORTANT: Responding to "current" message.
- "history" holds recent conversation.
- "reply_to" shows message hierarchy.
- "attachments" contains URLs for tools."""

    return prompt


# ──────────────────────────────────────────────
# Cache Updates
# ──────────────────────────────────────────────

async def append_message_to_cache(message):
    """
    Append a new message to the DB.
    """
    # Build content with attachments and embeds (consistent with fetch_and_cache_from_api)
    content_parts = []
    if message.content:
        content_parts.append(message.content)
    if message.attachments:
        for att in message.attachments:
            content_parts.append(f"[Attachment: {att.url}]")
    if message.embeds and not message.attachments:
        content_parts.append(f"[Embed: {len(message.embeds)} embed(s)]")
    
    content = " ".join(content_parts) if content_parts else ""
    if not content.strip():
        return  # Skip empty messages

    timestamp_str = message.created_at.strftime("%Y-%m-%d %H:%M:%S")
    
    # Prepare reply info if present
    reply_to_id = None
    reply_to_author_id = None
    reply_to_author_name = None
    reply_to_content = None
    
    if message.reference and message.reference.resolved:
        if isinstance(message.reference.resolved, discord.Message):
            ref = message.reference.resolved
            reply_to_id = ref.id
            reply_to_author_id = ref.author.id
            reply_to_author_name = ref.author.display_name
            reply_to_content = ref.clean_content

    content_hash = str(hash(content))
    
    # Store in DB (unified schema)
    await store_message(
        message_id=message.id,
        channel_id=message.channel.id,
        author_id=message.author.id,
        author_name=message.author.display_name,
        content=content,
        created_at=message.created_at,
        timestamp_str=timestamp_str,
        content_hash=content_hash,
        reply_to_message_id=reply_to_id,
        reply_to_author_id=reply_to_author_id,
        reply_to_author_name=reply_to_author_name,
        reply_to_content=reply_to_content
    )
    
    # Queue for live knowledge ingestion (ingest messages falling off context window)
    try:
        from core.discord_knowledge_ingestion import on_new_message
        channel_name = getattr(message.channel, "name", "DM")
        await on_new_message(message.channel.id, channel_name)
    except Exception as e:
        logger.debug(f"[KnowledgeIngestion] Overflow check failed (non-critical): {e}")


async def update_message_in_cache(before, after):
    """
    Update a message in the DB when it's edited.
    """
    from core.database import store_message
    from datetime import datetime, timezone
    
    # Build updated content with attachments
    content_parts = []
    if after.content:
        content_parts.append(after.content)
    if after.attachments:
        for att in after.attachments:
            content_parts.append(f"[Attachment: {att.url}]")
    if after.embeds and not after.attachments:
        content_parts.append(f"[Embed: {len(after.embeds)} embed(s)]")
    
    content = " ".join(content_parts) if content_parts else "[Empty message]"
    timestamp_str = after.created_at.strftime("%Y-%m-%d %H:%M:%S")
    
    # Update in database (store_message handles upsert)
    await store_message(
        message_id=after.id,
        channel_id=after.channel.id,
        author_id=after.author.id,
        author_name=after.author.display_name,
        content=content,
        created_at=after.created_at,
        timestamp_str=timestamp_str
    )


async def delete_message_from_cache(message):
    """
    Remove a message from the DB when it's deleted.
    """
    from core.database import delete_message
    await delete_message(message.id)


async def invalidate_cache(channel_id: int):
    pass # No-op for DB

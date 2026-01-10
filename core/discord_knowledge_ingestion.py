"""
Discord message ingestion to Agno Knowledge base.

Strategy:
- Bulk ingest after backfill (skip last N messages in context window)
- Live ingest as messages "fall off" the context window
- Uses Agno SemanticChunking for intelligent topic-based splitting
"""
import asyncio
import logging
import hashlib
import re
from datetime import datetime, timezone
from typing import List, Dict, Optional

from core.knowledge import get_knowledge_base
from core.database import get_messages, get_pool
from core.config import TEAM_LEADER_CONTEXT_LIMIT

logger = logging.getLogger(__name__)

# Configuration
CONTEXT_WINDOW_SIZE = TEAM_LEADER_CONTEXT_LIMIT  # Messages in context (skip these)


# ──────────────────────────────────────────────
# Format Messages for Embedding
# ──────────────────────────────────────────────

def format_messages_for_embedding(
    messages: List[Dict],
    channel_name: str = "unknown"
) -> tuple[str, Dict]:
    """
    Format messages into efficient embedding text format.
    SemanticChunking will split this at topic boundaries.
    
    Returns:
        tuple: (formatted_text, metadata_dict)
    """
    if not messages:
        return "", {}
    
    # Extract participants
    participants = list(set(m.get('author_name', 'Unknown') for m in messages))
    
    # Time range
    times = [m.get('created_at') for m in messages if m.get('created_at')]
    if times:
        start_time = min(times)
        end_time = max(times)
        time_range = f"{start_time.strftime('%Y-%m-%d %H:%M')}-{end_time.strftime('%H:%M')}"
    else:
        time_range = "unknown"
        start_time = end_time = None
    
    # Build header (context for the whole batch)
    header = f"#{channel_name} | {time_range} | {', '.join(participants[:5])}"
    if len(participants) > 5:
        header += f" +{len(participants) - 5} more"
    
    # Build message lines
    lines = [header, ""]
    
    for m in messages:
        author = m.get('author_name', 'Unknown')
        content = m.get('content', '')
        
        # Handle attachments - convert to emoji format with URL
        if '[Attachment:' in content:
            content = re.sub(
                r'\[Attachment: (https?://[^\]]+)\]',
                lambda match: f"[📎 file]({match.group(1)})",
                content
            )
        
        # Handle replies
        reply_suffix = ""
        if m.get('reply_to_author_name'):
            reply_suffix = f" [→{m['reply_to_author_name']}]"
        
        lines.append(f"{author}: {content}{reply_suffix}")
    
    formatted_text = "\n".join(lines)
    
    # Build metadata
    metadata = {
        "source": "discord_messages",
        "channel_name": channel_name,
        "channel_id": str(messages[0].get('channel_id', '')),
        "participants": ",".join(participants),
        "message_count": len(messages),
        "has_attachments": any('[📎' in l for l in lines),
    }
    
    if start_time:
        metadata["start_ts"] = start_time.isoformat()
    if end_time:
        metadata["end_ts"] = end_time.isoformat()
    
    return formatted_text, metadata


# ──────────────────────────────────────────────
# Core Ingestion with Semantic Chunking
# ──────────────────────────────────────────────

async def ingest_messages_semantic(
    messages: List[Dict],
    channel_id: int,
    channel_name: str = None
) -> bool:
    """
    Ingest messages using Agno's SemanticChunking.
    Messages are formatted as text, then semantically chunked by topic.
    """
    kb = get_knowledge_base()
    if kb is None:
        logger.debug("[KnowledgeIngestion] Knowledge base not configured, skipping")
        return False
    
    if not messages:
        return True
    
    try:
        channel_name = channel_name or f"channel-{channel_id}"
        formatted_text, metadata = format_messages_for_embedding(messages, channel_name)
        
        if not formatted_text.strip():
            return True
        
        # Content hash for deduplication
        content_hash = hashlib.sha256(formatted_text.encode()).hexdigest()[:16]
        
        # Use SemanticChunking via TextReader
        # Get the embedder from the knowledge base (already configured for LiteLLM)
        try:
            from agno.knowledge.chunking.semantic import SemanticChunking
            from agno.knowledge.reader.text_reader import TextReader
            
            # Reuse the embedder from the vector DB (configured for LiteLLM proxy)
            embedder = kb.vector_db.embedder
            
            reader = TextReader(
                chunking_strategy=SemanticChunking(
                    embedder=embedder,
                    similarity_threshold=0.5,  # Split when topic changes
                    chunk_size=500,  # Target ~500 chars per chunk
                )
            )
            
            await kb.add_content_async(
                name=f"discord_{channel_id}_{content_hash}",
                text_content=formatted_text,
                metadata=metadata,
                reader=reader,
                upsert=True,
                skip_if_exists=True,
            )
        except ImportError as e:
            # Fallback if SemanticChunking not available
            logger.warning(f"[KnowledgeIngestion] SemanticChunking unavailable, using default: {e}")
            await kb.add_content_async(
                name=f"discord_{channel_id}_{content_hash}",
                text_content=formatted_text,
                metadata=metadata,
                upsert=True,
                skip_if_exists=True,
            )
        
        return True
        
    except Exception as e:
        logger.error(f"[KnowledgeIngestion] Failed to ingest: {e}", exc_info=True)
        return False


# ──────────────────────────────────────────────
# Bulk Ingestion (after backfill)
# ──────────────────────────────────────────────

async def bulk_ingest_channel(
    channel_id: int,
    channel_name: str = None,
    skip_recent: int = CONTEXT_WINDOW_SIZE
) -> bool:
    """
    Bulk ingest messages from a channel, SKIPPING the most recent N (context window).
    Uses semantic chunking to split at natural topic boundaries.
    Tracks completion in DB for resume support.
    """
    from core.database import is_channel_knowledge_ingested, mark_channel_knowledge_ingested
    
    # Check if already ingested (for resume support)
    if await is_channel_knowledge_ingested(channel_id):
        logger.debug(f"[KnowledgeIngestion] Channel {channel_id} already ingested, skipping")
        return True
    
    all_messages = await get_messages(channel_id, limit=100000)
    
    if not all_messages:
        logger.info(f"[KnowledgeIngestion] No messages for channel {channel_id}")
        return False
    
    if len(all_messages) <= skip_recent:
        logger.info(f"[KnowledgeIngestion] Channel {channel_id}: {len(all_messages)} msgs, all in context window")
        # Mark as ingested even if nothing to ingest (all in context)
        await mark_channel_knowledge_ingested(channel_id, 0)
        return True
    
    # Skip last N messages (they're in context window)
    messages_to_ingest = all_messages[:-skip_recent]
    
    logger.info(f"[KnowledgeIngestion] Ingesting {len(messages_to_ingest)} messages (skipping {skip_recent} recent)")
    
    # Ingest all at once - SemanticChunking handles the splitting
    success = await ingest_messages_semantic(messages_to_ingest, channel_id, channel_name)
    
    if success:
        await mark_channel_knowledge_ingested(channel_id, len(messages_to_ingest))
        logger.info(f"[KnowledgeIngestion] ✓ Ingested channel {channel_id}")
    
    return success


async def bulk_ingest_all_channels() -> int:
    """
    Bulk ingest channels that haven't been ingested yet.
    Uses persistent tracking for resume support.
    """
    from core.database import get_channels_needing_ingestion
    
    # Only get channels that need ingestion
    channel_ids = await get_channels_needing_ingestion()
    
    if not channel_ids:
        logger.info("[KnowledgeIngestion] All channels already ingested")
        return 0
    
    logger.info(f"[KnowledgeIngestion] {len(channel_ids)} channels need ingestion")
    
    success_count = 0
    for i, channel_id in enumerate(channel_ids):
        if await bulk_ingest_channel(channel_id):
            success_count += 1
        
        # Rate limit delay: 1 seconds between channels to avoid API throttling
        if i < len(channel_ids) - 1:
            await asyncio.sleep(1.0)
    
    logger.info(f"[KnowledgeIngestion] Bulk ingested {success_count}/{len(channel_ids)} channels")
    return success_count


# ──────────────────────────────────────────────
# Live Ingestion (as messages fall off context)
# ──────────────────────────────────────────────

_ingested_message_ids: set = set()
_ingestion_lock = asyncio.Lock()


async def ingest_overflow_messages(channel_id: int, channel_name: str = None) -> bool:
    """
    Ingest messages that have "fallen off" the context window.
    """
    global _ingested_message_ids
    
    async with _ingestion_lock:
        all_messages = await get_messages(channel_id, limit=100000)
        
        if len(all_messages) <= CONTEXT_WINDOW_SIZE:
            return False  # All still in context
        
        # Messages outside context window
        overflow_messages = all_messages[:-CONTEXT_WINDOW_SIZE]
        
        # Filter already ingested
        new_messages = [
            m for m in overflow_messages 
            if m['message_id'] not in _ingested_message_ids
        ]
        
        if not new_messages:
            return False
        
        # Ingest with semantic chunking
        success = await ingest_messages_semantic(new_messages, channel_id, channel_name)
        
        if success:
            for m in new_messages:
                _ingested_message_ids.add(m['message_id'])
            logger.info(f"[KnowledgeIngestion] Live ingested {len(new_messages)} overflow messages")
        
        return success


async def on_new_message(channel_id: int, channel_name: str = None):
    """
    Call when a new message arrives.
    Non-blocking trigger for overflow ingestion.
    """
    asyncio.create_task(ingest_overflow_messages(channel_id, channel_name))

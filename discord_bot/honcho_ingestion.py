# discord_bot/honcho_ingestion.py
"""
Honcho Ingestion - Sync messages from PostgreSQL database to Honcho on cold start.

This ensures Honcho has access to historical conversation data for:
- Building user representations
- Providing cross-session memory
- Enabling Dialectic API queries

The ingestion runs after database initialization and syncs messages
that exist in the DB but haven't been sent to Honcho yet.
"""

import logging
import asyncio
import os
from typing import List, Dict, Optional
from datetime import datetime

from core.database import pool
from core.honcho_service import honcho_service

logger = logging.getLogger(__name__)

# Configuration
HONCHO_INGESTION_BATCH_SIZE = int(os.getenv("HONCHO_INGESTION_BATCH_SIZE", "100"))
HONCHO_INGESTION_CONCURRENCY = int(os.getenv("HONCHO_INGESTION_CONCURRENCY", "2"))


async def get_honcho_sync_status(channel_id: int) -> Optional[Dict]:
    """Get the Honcho sync status for a channel."""
    if not pool:
        return None
    
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow("""
                SELECT channel_id, last_synced_message_id, messages_synced, last_sync_at
                FROM honcho_sync_status
                WHERE channel_id = $1
            """, channel_id)
            return dict(row) if row else None
    except Exception as e:
        logger.error(f"[Honcho Ingestion] Failed to get sync status for {channel_id}: {e}")
        return None


async def update_honcho_sync_status(
    channel_id: int,
    last_synced_message_id: int,
    messages_synced: int
):
    """Update the Honcho sync status for a channel."""
    if not pool:
        return
    
    try:
        async with pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO honcho_sync_status (channel_id, last_synced_message_id, messages_synced, last_sync_at)
                VALUES ($1, $2, $3, CURRENT_TIMESTAMP)
                ON CONFLICT (channel_id) DO UPDATE SET
                    last_synced_message_id = GREATEST(honcho_sync_status.last_synced_message_id, EXCLUDED.last_synced_message_id),
                    messages_synced = honcho_sync_status.messages_synced + EXCLUDED.messages_synced,
                    last_sync_at = EXCLUDED.last_sync_at;
            """, channel_id, last_synced_message_id, messages_synced)
    except Exception as e:
        logger.error(f"[Honcho Ingestion] Failed to update sync status for {channel_id}: {e}")


async def get_unsynced_messages(channel_id: int, last_synced_id: Optional[int] = None, limit: int = 1000) -> List[Dict]:
    """
    Get messages from DB that haven't been synced to Honcho yet.
    
    Returns messages in chronological order (oldest first) for proper Honcho ingestion.
    """
    if not pool:
        return []
    
    try:
        async with pool.acquire() as conn:
            if last_synced_id:
                # Get messages newer than the last synced message
                rows = await conn.fetch("""
                    SELECT message_id, channel_id, author_id, author_name, content, created_at
                    FROM messages
                    WHERE channel_id = $1 AND message_id > $2
                    ORDER BY created_at ASC
                    LIMIT $3
                """, channel_id, last_synced_id, limit)
            else:
                # Get all messages (first sync)
                rows = await conn.fetch("""
                    SELECT message_id, channel_id, author_id, author_name, content, created_at
                    FROM messages
                    WHERE channel_id = $1
                    ORDER BY created_at ASC
                    LIMIT $2
                """, channel_id, limit)
            
            return [dict(row) for row in rows]
    except Exception as e:
        logger.error(f"[Honcho Ingestion] Failed to get unsynced messages for {channel_id}: {e}")
        return []


async def ingest_channel_to_honcho(channel_id: int) -> int:
    """
    Ingest messages from a single channel to Honcho.
    
    Returns the number of messages synced.
    """
    if not honcho_service.is_enabled:
        logger.debug("[Honcho Ingestion] Honcho service not enabled, skipping ingestion")
        return 0
    
    try:
        # Get current sync status
        sync_status = await get_honcho_sync_status(channel_id)
        last_synced_id = sync_status["last_synced_message_id"] if sync_status else None
        
        # Get unsynced messages
        messages = await get_unsynced_messages(channel_id, last_synced_id)
        
        if not messages:
            logger.debug(f"[Honcho Ingestion] No new messages to sync for channel {channel_id}")
            return 0
        
        logger.info(f"[Honcho Ingestion] Syncing {len(messages)} messages for channel {channel_id}")
        
        # Get or create Honcho session for this channel
        session = honcho_service.get_session(str(channel_id))
        if not session:
            logger.warning(f"[Honcho Ingestion] Could not create session for channel {channel_id}")
            return 0
        
        # Build peer cache for this batch
        peer_cache = {}
        synced_count = 0
        last_message_id = None
        
        # Process messages in batches
        for i in range(0, len(messages), HONCHO_INGESTION_BATCH_SIZE):
            batch = messages[i:i + HONCHO_INGESTION_BATCH_SIZE]
            honcho_messages = []
            
            for msg in batch:
                author_id = str(msg["author_id"])
                author_name = msg["author_name"]
                content = msg["content"]
                
                # Skip empty messages
                if not content or not content.strip():
                    continue
                
                # Get or create peer for this author
                if author_id not in peer_cache:
                    peer_cache[author_id] = honcho_service.get_peer(
                        user_id=author_id,
                        username=author_name,
                    )
                
                peer = peer_cache[author_id]
                if peer:
                    honcho_messages.append(peer.message(content))
                
                last_message_id = msg["message_id"]
            
            # Add all peers to session and store messages
            if honcho_messages:
                try:
                    peers_to_add = list(peer_cache.values())
                    if honcho_service.assistant:
                        peers_to_add.append(honcho_service.assistant)
                    session.add_peers(peers_to_add)
                    session.add_messages(honcho_messages)
                    synced_count += len(honcho_messages)
                except Exception as e:
                    logger.error(f"[Honcho Ingestion] Error adding messages to Honcho: {e}")
            
            # Small delay between batches to avoid rate limits
            if i + HONCHO_INGESTION_BATCH_SIZE < len(messages):
                await asyncio.sleep(0.1)
        
        # Update sync status
        if last_message_id:
            await update_honcho_sync_status(channel_id, last_message_id, synced_count)
        
        logger.info(f"[Honcho Ingestion] ✓ Synced {synced_count} messages for channel {channel_id}")
        return synced_count
        
    except Exception as e:
        logger.error(f"[Honcho Ingestion] Error ingesting channel {channel_id}: {e}", exc_info=True)
        return 0


async def ingest_all_channels_to_honcho(channel_ids: List[int]) -> Dict[str, int]:
    """
    Ingest messages from multiple channels to Honcho with concurrency control.
    
    Returns a summary of synced messages per channel.
    """
    if not honcho_service.is_enabled:
        logger.warning("[Honcho Ingestion] Honcho service not enabled, skipping all ingestion")
        return {}
    
    logger.info(f"[Honcho Ingestion] Starting ingestion for {len(channel_ids)} channels")
    
    sem = asyncio.Semaphore(HONCHO_INGESTION_CONCURRENCY)
    results = {}
    
    async def bounded_ingest(channel_id: int):
        async with sem:
            count = await ingest_channel_to_honcho(channel_id)
            results[str(channel_id)] = count
            await asyncio.sleep(0.5)  # Rate limit protection
    
    # Run all ingestions with concurrency control
    tasks = [bounded_ingest(cid) for cid in channel_ids]
    await asyncio.gather(*tasks, return_exceptions=True)
    
    # Summary
    total_synced = sum(results.values())
    channels_with_data = sum(1 for v in results.values() if v > 0)
    
    logger.info(f"[Honcho Ingestion] ═══════════════════════════════════════")
    logger.info(f"[Honcho Ingestion] Summary: {total_synced} messages synced from {channels_with_data}/{len(channel_ids)} channels")
    logger.info(f"[Honcho Ingestion] ═══════════════════════════════════════")
    
    return results


async def start_honcho_ingestion_task(channels) -> None:
    """
    Start Honcho ingestion as a background task.
    
    This should be called after backfill completes to ensure
    the database has the historical messages to sync.
    
    Args:
        channels: List of Discord channel objects
    """
    if not honcho_service.is_enabled:
        logger.info("[Honcho Ingestion] Honcho not configured, skipping ingestion task")
        return
    
    channel_ids = [c.id for c in channels]
    await ingest_all_channels_to_honcho(channel_ids)

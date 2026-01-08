# discord_bot/cold_start.py
"""
Production-ready cold start system for Honcho ingestion.

Features:
- Quick context sync (blocking): Last N messages from active channels
- Deep background sync (non-blocking): Full history
- Deriver polling: Wait for Honcho to process messages
- Deduplication: Track sync state per channel
- Temporal ordering: Use created_at for message timeline
"""

import logging
import asyncio
import os
from typing import List, Optional
from datetime import datetime, timezone, timedelta

from core.database import pool
from core.honcho_service import honcho_service

logger = logging.getLogger(__name__)

# Configuration
QUICK_SYNC_MESSAGE_LIMIT = int(os.getenv("HONCHO_QUICK_SYNC_MESSAGES", "50"))
QUICK_SYNC_MAX_CHANNELS = int(os.getenv("HONCHO_QUICK_SYNC_CHANNELS", "20"))
QUICK_SYNC_ACTIVE_HOURS = int(os.getenv("HONCHO_QUICK_SYNC_HOURS", "24"))
DERIVER_POLL_TIMEOUT = int(os.getenv("HONCHO_DERIVER_TIMEOUT", "30"))
DEEP_SYNC_ENABLED = os.getenv("HONCHO_DEEP_SYNC", "true").lower() == "true"


async def get_active_channels(channels, hours: int = 24) -> List:
    """Filter channels by recent activity."""
    if not pool:
        return channels[:QUICK_SYNC_MAX_CHANNELS]
    
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    active = []
    
    for channel in channels:
        try:
            async with pool.acquire() as conn:
                # Check for recent messages in DB
                recent = await conn.fetchval("""
                    SELECT EXISTS(
                        SELECT 1 FROM messages 
                        WHERE channel_id = $1 AND created_at > $2
                        LIMIT 1
                    )
                """, channel.id, cutoff)
                if recent:
                    active.append(channel)
        except Exception as e:
            logger.debug(f"[ColdStart] Error checking channel activity: {e}")
            active.append(channel)  # Include if we can't check
    
    return active[:QUICK_SYNC_MAX_CHANNELS]


async def get_sync_status(channel_id: int) -> Optional[dict]:
    """Get Honcho sync status for a channel."""
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
        logger.error(f"[ColdStart] Failed to get sync status: {e}")
        return None


async def update_sync_status(channel_id: int, last_message_id: int, count: int):
    """Update Honcho sync status for a channel."""
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
                    last_sync_at = EXCLUDED.last_sync_at
            """, channel_id, last_message_id, count)
    except Exception as e:
        logger.error(f"[ColdStart] Failed to update sync status: {e}")


async def get_unsynced_messages(channel_id: int, last_synced_id: Optional[int], limit: int) -> List[dict]:
    """Get messages that haven't been synced to Honcho yet."""
    if not pool:
        return []
    
    try:
        async with pool.acquire() as conn:
            if last_synced_id:
                rows = await conn.fetch("""
                    SELECT message_id, channel_id, author_id, author_name, content, created_at
                    FROM messages
                    WHERE channel_id = $1 AND message_id > $2
                    ORDER BY created_at ASC
                    LIMIT $3
                """, channel_id, last_synced_id, limit)
            else:
                rows = await conn.fetch("""
                    SELECT message_id, channel_id, author_id, author_name, content, created_at
                    FROM messages
                    WHERE channel_id = $1
                    ORDER BY created_at ASC
                    LIMIT $2
                """, channel_id, limit)
            return [dict(row) for row in rows]
    except Exception as e:
        logger.error(f"[ColdStart] Failed to get unsynced messages: {e}")
        return []


def _ingest_messages_sync(channel_id: int, messages: List[dict]) -> int:
    """Synchronous implementation of ingestion logic to run in executor."""
    try:
        session = honcho_service.get_session(str(channel_id))
        if not session:
            return 0
        
        peer_cache = {}
        honcho_messages = []
        
        for msg in messages:
            author_id = str(msg["author_id"])
            content = msg["content"]
            created_at = msg["created_at"]
            
            if not content or not content.strip():
                continue
            
            # Get or create peer
            if author_id not in peer_cache:
                peer_cache[author_id] = honcho_service.get_peer(
                    user_id=author_id,
                    username=msg["author_name"],
                )
            
            peer = peer_cache[author_id]
            if peer:
                # Use created_at for proper temporal ordering
                timestamp = created_at.isoformat() if hasattr(created_at, 'isoformat') else str(created_at)
                honcho_messages.append(peer.message(content, created_at=timestamp))
        
        if honcho_messages:
            peers_to_add = list(peer_cache.values())
            if honcho_service.assistant:
                peers_to_add.append(honcho_service.assistant)
            session.add_peers(peers_to_add)
            session.add_messages(honcho_messages)
        
        return len(honcho_messages)
        
    except Exception as e:
        logger.error(f"[ColdStart] Error ingesting to Honcho: {e}")
        return 0


async def ingest_messages_to_honcho(channel_id: int, messages: List[dict]) -> int:
    """Ingest messages to Honcho with proper timestamps (Non-blocking wrapper)."""
    if not honcho_service.is_enabled or not messages:
        return 0
    
    # Run synchronous Honcho calls in thread pool to avoid blocking event loop
    return await asyncio.to_thread(_ingest_messages_sync, channel_id, messages)


async def poll_deriver_until_ready(timeout: int = DERIVER_POLL_TIMEOUT) -> bool:
    """Poll Honcho Deriver until processing is complete or timeout."""
    if not honcho_service.is_enabled:
        return True
    
    try:
        start = datetime.now()
        while (datetime.now() - start).seconds < timeout:
            status = honcho_service.client.get_deriver_status()
            pending = getattr(status, 'pending_work_units', 0)
            in_progress = getattr(status, 'in_progress_work_units', 0)
            
            if pending == 0 and in_progress == 0:
                logger.info("[ColdStart] Deriver processing complete")
                return True
            
            logger.debug(f"[ColdStart] Deriver: {pending} pending, {in_progress} in progress")
            await asyncio.sleep(1)
        
        logger.warning(f"[ColdStart] Deriver poll timeout after {timeout}s")
        return False
        
    except Exception as e:
        logger.error(f"[ColdStart] Error polling Deriver: {e}")
        return False


async def quick_honcho_sync(channels) -> int:
    """
    Phase 1: Quick sync of recent messages from active channels.
    Blocking operation to ensure context is available for first message.
    Runs concurrently for faster startup.
    """
    if not honcho_service.is_enabled:
        logger.info("[ColdStart] Honcho not enabled, skipping quick sync")
        return 0
    
    # Get concurrency from env (default 5)
    concurrency = int(os.getenv("HONCHO_INGESTION_CONCURRENCY", "5"))
    sem = asyncio.Semaphore(concurrency)
    
    logger.info(f"[ColdStart] Starting quick sync for active channels with concurrency {concurrency}...")
    
    # Get active channels only
    active_channels = await get_active_channels(channels, hours=QUICK_SYNC_ACTIVE_HOURS)
    logger.info(f"[ColdStart] Found {len(active_channels)} active channels")
    
    total_synced = 0
    synced_channels = 0
    
    async def ingest_channel_safe(channel):
        nonlocal total_synced, synced_channels
        async with sem:
            try:
                channel_id = channel.id
                
                # Get sync status to avoid duplicates
                status = await get_sync_status(channel_id)
                last_synced_id = status["last_synced_message_id"] if status else None
                
                # Get unsynced messages
                messages = await get_unsynced_messages(channel_id, last_synced_id, QUICK_SYNC_MESSAGE_LIMIT)
                
                if messages:
                    count = await ingest_messages_to_honcho(channel_id, messages)
                    # Always update status to avoid re-processing same batch even if count is 0
                    await update_sync_status(channel_id, messages[-1]["message_id"], count)
                    total_synced += count
                    logger.debug(f"[ColdStart] Synced {count} messages from channel {channel_id}")
                    return True
            except Exception as e:
                logger.error(f"[ColdStart] Error syncing channel {channel.id}: {e}")
            return False

    # Create tasks for all channels
    tasks = [ingest_channel_safe(c) for c in active_channels]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    synced_channels = sum(1 for r in results if r is True)
    
    logger.info(f"[ColdStart] Quick sync complete: {total_synced} messages from {synced_channels}/{len(active_channels)} channels")
    
    # Poll Deriver to ensure messages are processed
    if total_synced > 0:
        logger.info("[ColdStart] Waiting for Deriver to process messages...")
        await poll_deriver_until_ready()
    
    return total_synced


async def deep_honcho_sync(channels) -> int:
    """
    Phase 2: Deep sync of all historical messages.
    Non-blocking background operation.
    Runs concurrently but responsibly.
    """
    if not honcho_service.is_enabled or not DEEP_SYNC_ENABLED:
        return 0
    
    # Lower concurrency for background sync to avoid impacting bot performance
    concurrency = int(os.getenv("HONCHO_DEEP_SYNC_CONCURRENCY", "3"))
    sem = asyncio.Semaphore(concurrency)
    
    logger.info(f"[ColdStart] Starting deep background sync for {len(channels)} channels with concurrency {concurrency}...")
    
    total_synced = 0
    
    async def deep_sync_channel(channel):
        nonlocal total_synced
        async with sem:
            try:
                channel_id = channel.id
                channel_synced = 0
                
                # Get all unsynced messages (no limit for deep sync)
                status = await get_sync_status(channel_id)
                last_synced_id = status["last_synced_message_id"] if status else None
                
                # Fetch in batches of 1000
                batch_size = 1000
                while True:
                    messages = await get_unsynced_messages(channel_id, last_synced_id, batch_size)
                    if not messages:
                        break
                    
                    count = await ingest_messages_to_honcho(channel_id, messages)
                    # Always update status to avoid infinite loop on empty content batches
                    last_synced_id = messages[-1]["message_id"]
                    await update_sync_status(channel_id, last_synced_id, count)
                    channel_synced += count
                    total_synced += count
                    
                    # Small delay to avoid overwhelming Honcho
                    await asyncio.sleep(0.5)
                
                if channel_synced > 0:
                    logger.info(f"[ColdStart] Deep synced {channel_synced} messages for channel {channel_id}")
                    
            except Exception as e:
                logger.error(f"[ColdStart] Error deep syncing channel {channel.id}: {e}")

    # Create tasks for all channels
    tasks = [deep_sync_channel(c) for c in channels]
    await asyncio.gather(*tasks, return_exceptions=True)
    
    logger.info(f"[ColdStart] Deep sync complete: {total_synced} total messages")
    return total_synced


async def run_cold_start_sync(channels):
    """
    Main cold start function - runs quick sync then schedules deep sync.
    """
    if not honcho_service.is_enabled:
        logger.info("[ColdStart] Honcho not configured, skipping cold start sync")
        return
    
    # Phase 1: Quick sync (blocking)
    await quick_honcho_sync(channels)
    
    # Phase 2: Deep sync (background)
    if DEEP_SYNC_ENABLED:
        asyncio.create_task(deep_honcho_sync(channels))
        logger.info("[ColdStart] Deep sync started in background")

"""
Message synchronization to detect edits/deletes that happened while bot was offline.
"""
import logging
from typing import Set
import discord
from core.database import get_messages, delete_message
from discord_bot.context_cache import fetch_and_cache_from_api

logger = logging.getLogger(__name__)

async def sync_recent_messages(channel, sync_limit: int = 200):
    """
    Sync the most recent messages to detect edits/deletes that happened offline.
    
    Args:
        channel: Discord channel object
        sync_limit: Number of recent messages to sync (default: 200)
    """
    try:
        channel_id = channel.id
        channel_name = getattr(channel, 'name', 'DM')
        
        logger.info(f"[Sync] Syncing last {sync_limit} messages for {channel_name} ({channel_id})")
        
        # 1. Fetch recent messages from Discord FIRST (to determine time range)
        discord_messages = []
        async for msg in channel.history(limit=sync_limit):
            discord_messages.append(msg)
        
        if not discord_messages:
            logger.info(f"[Sync] No messages from Discord for {channel_name}, skipping sync")
            return
        
        discord_message_ids = {msg.id for msg in discord_messages}
        
        # 2. Determine the time range of Discord messages (oldest message = cutoff)
        oldest_discord_msg = min(discord_messages, key=lambda m: m.created_at)
        oldest_timestamp = oldest_discord_msg.created_at
        
        # Make oldest_timestamp timezone-naive for comparison with DB (which stores naive timestamps)
        if oldest_timestamp.tzinfo is not None:
            oldest_timestamp = oldest_timestamp.replace(tzinfo=None)
        
        logger.debug(f"[Sync] Discord time range cutoff: {oldest_timestamp}")
        
        # 3. Get existing message IDs from database ONLY within this time range
        # This prevents false deletion detection for messages older than what Discord returned
        db_messages = await get_messages(channel_id, limit=sync_limit)
        
        # Filter to only messages within the Discord fetch time range
        # Make DB timestamps timezone-naive too for comparison
        db_message_ids = set()
        for msg in db_messages:
            db_ts = msg['created_at']
            if db_ts.tzinfo is not None:
                db_ts = db_ts.replace(tzinfo=None)
            if db_ts >= oldest_timestamp:
                db_message_ids.add(msg['message_id'])
        
        logger.debug(f"[Sync] DB messages in range: {len(db_message_ids)}, Discord messages: {len(discord_message_ids)}")
        
        # 4. Update/insert messages from Discord (handles edits automatically via upsert)
        await fetch_and_cache_from_api(channel, limit=sync_limit)
        
        # 5. Find messages deleted from Discord (only within the fetched time range)
        deleted_ids = db_message_ids - discord_message_ids
        
        if deleted_ids:
            logger.info(f"[Sync] Found {len(deleted_ids)} deleted messages in {channel_name}")
            for msg_id in deleted_ids:
                await delete_message(msg_id)
                logger.debug(f"[Sync] Deleted message {msg_id} from DB")
        
        # 6. Log sync summary
        updated_count = len(discord_message_ids & db_message_ids)
        new_count = len(discord_message_ids - db_message_ids)
        
        logger.info(
            f"[Sync] ✓ Synced {channel_name}: "
            f"{updated_count} updated, {new_count} new, {len(deleted_ids)} deleted"
        )
        
    except discord.errors.Forbidden:
        logger.warning(f"[Sync] Missing access to channel {channel_id}. Skipping sync.")
    except Exception as e:
        logger.error(f"[Sync] Error syncing channel {channel_id}: {e}", exc_info=True)


async def sync_all_channels(channels, sync_limit: int = 200):
    """
    Sync recent messages for all channels after backfill.
    Runs concurrently for better performance.
    
    Args:
        channels: List of Discord channel objects
        sync_limit: Number of recent messages to sync per channel
    """
    import os
    import asyncio
    
    # Get concurrency from env (default 5 to match backfill efficiency without easy rate limits)
    concurrency = int(os.getenv("SYNC_CONCURRENCY", "5"))
    sem = asyncio.Semaphore(concurrency)
    
    logger.info(f"[Sync] Starting message sync for {len(channels)} channels with concurrency {concurrency} (last {sync_limit} messages each)")
    
    async def bound_sync(channel):
        async with sem:
            try:
                await sync_recent_messages(channel, sync_limit=sync_limit)
                return True
            except Exception as e:
                logger.error(f"[Sync] Failed to sync channel {channel.id}: {e}")
                return False
    
    # Create tasks for all channels
    tasks = [bound_sync(c) for c in channels]
    
    # Execute concurrently
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    # Calculate stats
    successes = sum(1 for r in results if r is True)
    failures = len(results) - successes
    
    logger.info(f"[Sync] ═══════════════════════════════════════")
    logger.info(f"[Sync] Sync complete: {successes}/{len(channels)} channels synced, {failures} failed")
    logger.info(f"[Sync] ═══════════════════════════════════════")

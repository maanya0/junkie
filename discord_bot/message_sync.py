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
        
        # 1. Get existing message IDs from database (most recent)
        db_messages = await get_messages(channel_id, limit=sync_limit)
        db_message_ids = {msg['message_id'] for msg in db_messages}
        
        if not db_message_ids:
            logger.info(f"[Sync] No messages in DB for {channel_name}, skipping sync")
            return
        
        # 2. Fetch recent messages from Discord
        discord_messages = []
        async for msg in channel.history(limit=sync_limit):
            discord_messages.append(msg)
        
        discord_message_ids = {msg.id for msg in discord_messages}
        
        # 3. Update/insert messages from Discord (handles edits automatically via upsert)
        await fetch_and_cache_from_api(channel, limit=sync_limit)
        
        # 4. Find messages deleted from Discord
        deleted_ids = db_message_ids - discord_message_ids
        
        if deleted_ids:
            logger.info(f"[Sync] Found {len(deleted_ids)} deleted messages in {channel_name}")
            for msg_id in deleted_ids:
                await delete_message(msg_id)
                logger.debug(f"[Sync] Deleted message {msg_id} from DB")
        
        # 5. Log sync summary
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
    
    Args:
        channels: List of Discord channel objects
        sync_limit: Number of recent messages to sync per channel
    """
    logger.info(f"[Sync] Starting post-backfill sync for {len(channels)} channels (last {sync_limit} messages each)")
    
    synced = 0
    failed = 0
    
    for channel in channels:
        try:
            await sync_recent_messages(channel, sync_limit=sync_limit)
            synced += 1
        except Exception as e:
            logger.error(f"[Sync] Failed to sync channel {channel.id}: {e}")
            failed += 1
    
    logger.info(f"[Sync] ═══════════════════════════════════════")
    logger.info(f"[Sync] Sync complete: {synced}/{len(channels)} channels synced, {failed} failed")
    logger.info(f"[Sync] ═══════════════════════════════════════")

"""
Smart Sync - Intelligent message synchronization for Discord bots.

Features:
- Parallel channel processing with semaphore control
- Hash-based change detection to skip unchanged messages
- Delta sync using last_synced_at timestamp
- Priority-based channel ordering (recently active first)
- Batch database operations for efficiency
"""

import asyncio
import hashlib
import logging
import os
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Optional, Set
import discord

from core.database import (
    get_message_hashes,
    batch_store_messages,
    batch_delete_messages,
    get_channel_last_sync,
    update_channel_sync_status,
    get_channels_by_activity,
    get_db_message_ids,
)

logger = logging.getLogger(__name__)

# Configuration
SMART_SYNC_CONCURRENCY = int(os.getenv("SMART_SYNC_CONCURRENCY", "8"))
SMART_SYNC_LIMIT = int(os.getenv("SMART_SYNC_LIMIT", "200"))
SMART_SYNC_DELTA_HOURS = int(os.getenv("SMART_SYNC_DELTA_HOURS", "24"))


def compute_content_hash(content: str) -> str:
    """Compute a hash of message content for change detection."""
    return hashlib.md5(content.encode('utf-8')).hexdigest()[:16]


def build_message_content(msg: discord.Message) -> str:
    """Build full message content including attachments and embeds."""
    content_parts = []
    if msg.content:
        content_parts.append(msg.content)
    if msg.attachments:
        for att in msg.attachments:
            content_parts.append(f"[Attachment: {att.url}]")
    if msg.embeds and not msg.attachments:
        content_parts.append(f"[Embed: {len(msg.embeds)} embed(s)]")
    return " ".join(content_parts) if content_parts else "[Empty message]"


async def smart_sync_channel(
    channel,
    sync_limit: int = SMART_SYNC_LIMIT,
    force_full: bool = False
) -> Dict:
    """
    Intelligently sync a single channel using delta sync and hash-based change detection.
    
    Args:
        channel: Discord channel object
        sync_limit: Number of messages to sync
        force_full: If True, ignore delta sync and fetch all messages
    
    Returns:
        Dict with sync statistics
    """
    channel_id = channel.id
    channel_name = getattr(channel, 'name', 'DM')
    
    stats = {
        'channel_id': channel_id,
        'channel_name': channel_name,
        'fetched': 0,
        'new': 0,
        'updated': 0,
        'unchanged': 0,
        'deleted': 0,
        'skipped': False,
        'error': None
    }
    
    try:
        sync_start = datetime.now(timezone.utc)
        
        # Get last sync time for delta sync
        last_sync = await get_channel_last_sync(channel_id) if not force_full else None
        
        # Determine fetch parameters
        after_obj = None
        delta_mode = False
        
        if last_sync and not force_full:
            # Delta sync: only fetch messages since last sync
            # Add a small buffer (1 hour) to handle timezone issues
            delta_cutoff = last_sync - timedelta(hours=1)
            after_obj = discord.Object(id=int((delta_cutoff.timestamp() - 1420070400) * 1000) << 22)
            delta_mode = True
            logger.debug(f"[SmartSync] {channel_name}: Delta sync from {last_sync}")
        
        # Fetch messages from Discord
        discord_messages: List[discord.Message] = []
        try:
            if after_obj:
                async for msg in channel.history(limit=sync_limit, after=after_obj):
                    if msg.content or msg.attachments or msg.embeds:
                        discord_messages.append(msg)
            else:
                async for msg in channel.history(limit=sync_limit):
                    if msg.content or msg.attachments or msg.embeds:
                        discord_messages.append(msg)
        except discord.errors.Forbidden:
            logger.warning(f"[SmartSync] Missing access to channel {channel_name}")
            stats['error'] = 'forbidden'
            return stats
        
        stats['fetched'] = len(discord_messages)
        
        if not discord_messages:
            if not delta_mode:
                logger.info(f"[SmartSync] {channel_name}: No messages found")
            else:
                logger.debug(f"[SmartSync] {channel_name}: No new messages since last sync")
                stats['skipped'] = True
            await update_channel_sync_status(channel_id, sync_start)
            return stats
        
        # Compute hashes for fetched messages
        discord_msg_ids = [msg.id for msg in discord_messages]
        discord_hashes: Dict[int, str] = {}
        prepared_messages: List[Dict] = []
        latest_msg_time = None
        
        for msg in discord_messages:
            content = build_message_content(msg)
            content_hash = compute_content_hash(content)
            discord_hashes[msg.id] = content_hash
            
            # Track latest message time for activity ordering
            if latest_msg_time is None or msg.created_at > latest_msg_time:
                latest_msg_time = msg.created_at
            
            prepared_messages.append({
                'message_id': msg.id,
                'channel_id': channel_id,
                'author_id': msg.author.id,
                'author_name': msg.author.display_name,
                'content': content,
                'content_hash': content_hash,
                'created_at': msg.created_at,
                'timestamp_str': msg.created_at.strftime("%Y-%m-%d %H:%M:%S")
            })
        
        # Get existing hashes from DB for comparison
        db_hashes = await get_message_hashes(channel_id, discord_msg_ids)
        
        # Determine which messages need to be stored
        messages_to_store = []
        for msg_data in prepared_messages:
            msg_id = msg_data['message_id']
            new_hash = msg_data['content_hash']
            old_hash = db_hashes.get(msg_id)
            
            if old_hash is None:
                # New message
                messages_to_store.append(msg_data)
                stats['new'] += 1
            elif old_hash != new_hash:
                # Changed message
                messages_to_store.append(msg_data)
                stats['updated'] += 1
            else:
                # Unchanged
                stats['unchanged'] += 1
        
        # Batch store changed messages
        if messages_to_store:
            stored = await batch_store_messages(messages_to_store)
            logger.info(
                f"[SmartSync] {channel_name}: Batch stored {stored} messages "
                f"({stats['new']} new, {stats['updated']} updated, {stats['unchanged']} unchanged)"
            )
        else:
            logger.debug(f"[SmartSync] {channel_name}: All {stats['unchanged']} messages unchanged")
        
        # Detect deleted messages (only for full sync, not delta)
        if not delta_mode:
            db_msg_ids = await get_db_message_ids(channel_id, limit=sync_limit)
            discord_msg_id_set = set(discord_msg_ids)
            deleted_ids = list(db_msg_ids - discord_msg_id_set)
            
            if deleted_ids:
                deleted_count = await batch_delete_messages(deleted_ids)
                stats['deleted'] = deleted_count
                logger.info(f"[SmartSync] {channel_name}: Batch deleted {deleted_count} messages")
        
        # Update sync status
        await update_channel_sync_status(channel_id, sync_start, latest_msg_time)
        
        logger.info(
            f"[SmartSync] ✓ {channel_name}: "
            f"fetched={stats['fetched']}, new={stats['new']}, "
            f"updated={stats['updated']}, unchanged={stats['unchanged']}, "
            f"deleted={stats['deleted']}"
        )
        
    except Exception as e:
        logger.error(f"[SmartSync] Error syncing {channel_name}: {e}", exc_info=True)
        stats['error'] = str(e)
    
    return stats


async def smart_sync_all_channels(
    channels: List,
    concurrency: int = SMART_SYNC_CONCURRENCY,
    sync_limit: int = SMART_SYNC_LIMIT,
    force_full: bool = False,
    priority_sort: bool = True
) -> Dict:
    """
    Sync all channels intelligently with parallel processing.
    
    Args:
        channels: List of Discord channel objects
        concurrency: Number of channels to sync in parallel
        sync_limit: Number of messages to sync per channel
        force_full: If True, ignore delta sync
        priority_sort: If True, sync recently active channels first
    
    Returns:
        Dict with overall sync statistics
    """
    if not channels:
        return {'total': 0, 'success': 0, 'failed': 0, 'stats': []}
    
    logger.info(f"[SmartSync] ═══════════════════════════════════════")
    logger.info(
        f"[SmartSync] Starting smart sync for {len(channels)} channels "
        f"(concurrency={concurrency}, limit={sync_limit})"
    )
    
    # Priority sort: recently active channels first
    if priority_sort:
        try:
            activity_data = await get_channels_by_activity()
            activity_order = {data['channel_id']: idx for idx, data in enumerate(activity_data)}
            
            # Sort channels by activity (known active first, then unknown)
            channels = sorted(
                channels,
                key=lambda c: activity_order.get(c.id, len(activity_order))
            )
            logger.debug(f"[SmartSync] Channels sorted by activity")
        except Exception as e:
            logger.warning(f"[SmartSync] Could not sort by activity: {e}")
    
    # Create semaphore for concurrency control
    sem = asyncio.Semaphore(concurrency)
    all_stats = []
    
    async def sync_with_semaphore(channel):
        async with sem:
            stats = await smart_sync_channel(channel, sync_limit, force_full)
            all_stats.append(stats)
            # Small delay to be nice to Discord API
            await asyncio.sleep(0.1)
    
    # Create tasks for all channels
    tasks = [sync_with_semaphore(c) for c in channels]
    
    # Execute with parallel batches
    start_time = datetime.now(timezone.utc)
    await asyncio.gather(*tasks, return_exceptions=True)
    elapsed = (datetime.now(timezone.utc) - start_time).total_seconds()
    
    # Compute summary
    success = sum(1 for s in all_stats if s.get('error') is None)
    failed = len(all_stats) - success
    total_fetched = sum(s.get('fetched', 0) for s in all_stats)
    total_new = sum(s.get('new', 0) for s in all_stats)
    total_updated = sum(s.get('updated', 0) for s in all_stats)
    total_unchanged = sum(s.get('unchanged', 0) for s in all_stats)
    total_deleted = sum(s.get('deleted', 0) for s in all_stats)
    total_skipped = sum(1 for s in all_stats if s.get('skipped'))
    
    logger.info(f"[SmartSync] ═══════════════════════════════════════")
    logger.info(
        f"[SmartSync] ✓ Sync complete in {elapsed:.1f}s: "
        f"{success}/{len(channels)} channels"
    )
    logger.info(
        f"[SmartSync]   Messages: {total_fetched} fetched, "
        f"{total_new} new, {total_updated} updated, "
        f"{total_unchanged} unchanged, {total_deleted} deleted"
    )
    if total_skipped:
        logger.info(f"[SmartSync]   Skipped {total_skipped} channels (no changes)")
    if failed:
        logger.warning(f"[SmartSync]   Failed: {failed} channels")
    logger.info(f"[SmartSync] ═══════════════════════════════════════")
    
    return {
        'total': len(channels),
        'success': success,
        'failed': failed,
        'skipped': total_skipped,
        'elapsed_seconds': elapsed,
        'messages': {
            'fetched': total_fetched,
            'new': total_new,
            'updated': total_updated,
            'unchanged': total_unchanged,
            'deleted': total_deleted
        },
        'stats': all_stats
    }


# Convenience alias for backward compatibility
sync_all_channels = smart_sync_all_channels
sync_recent_messages = smart_sync_channel

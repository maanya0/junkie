"""
Message synchronization to detect edits/deletes that happened while bot was offline.

This module wraps the new smart_sync module for backward compatibility.
The smart sync provides:
- Parallel channel processing
- Hash-based change detection
- Delta sync using last_synced_at timestamp
- Batch database operations
"""
import logging
from discord_bot.smart_sync import (
    smart_sync_channel,
    smart_sync_all_channels,
    SMART_SYNC_LIMIT,
    SMART_SYNC_CONCURRENCY
)

logger = logging.getLogger(__name__)


async def sync_recent_messages(channel, sync_limit: int = 200, force_full: bool = False):
    """
    Sync the most recent messages to detect edits/deletes that happened offline.
    
    This is a wrapper around smart_sync_channel for backward compatibility.
    
    Args:
        channel: Discord channel object
        sync_limit: Number of recent messages to sync (default: 200)
        force_full: If True, ignore delta sync and fetch all messages
    """
    return await smart_sync_channel(channel, sync_limit=sync_limit, force_full=force_full)


async def sync_all_channels(channels, sync_limit: int = 200, force_full: bool = False):
    """
    Sync recent messages for all channels after backfill.
    
    This is a wrapper around smart_sync_all_channels for backward compatibility.
    Uses parallel processing and smart change detection.
    
    Args:
        channels: List of Discord channel objects
        sync_limit: Number of recent messages to sync per channel
        force_full: If True, ignore delta sync and fetch all messages
    """
    return await smart_sync_all_channels(
        channels,
        sync_limit=sync_limit,
        force_full=force_full,
        concurrency=SMART_SYNC_CONCURRENCY,
        priority_sort=True
    )

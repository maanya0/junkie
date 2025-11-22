import logging
import asyncio
from core.database import get_message_count
from discord_bot.context_cache import fetch_and_cache_from_api
from core.config import CONTEXT_AGENT_MAX_MESSAGES

logger = logging.getLogger(__name__)

async def backfill_channel(channel, target_limit: int = CONTEXT_AGENT_MAX_MESSAGES):
    """
    Backfill message history for a channel if DB count is low.
    """
    try:
        channel_id = channel.id
        current_count = await get_message_count(channel_id)
        
        # If we have enough messages (e.g. > 90% of target), skip backfill
        if current_count >= target_limit * 0.9:
            logger.info(f"[Backfill] Channel {channel.name} ({channel_id}) has {current_count} messages. Skipping backfill.")
            return

        logger.info(f"[Backfill] Starting backfill for {channel.name} ({channel_id}). Current: {current_count}, Target: {target_limit}")
        
        # Fetch missing messages
        # Note: fetch_and_cache_from_api fetches 'limit * 1.5' to be safe.
        # We might want to fetch in chunks if limit is huge (80k), but for now let's rely on the helper.
        # WARNING: Fetching 80k messages will take a long time and many requests.
        
        await fetch_and_cache_from_api(channel, limit=target_limit)
        
        new_count = await get_message_count(channel_id)
        logger.info(f"[Backfill] Completed backfill for {channel.name} ({channel_id}). New count: {new_count}")
        
    except Exception as e:
        logger.error(f"[Backfill] Error backfilling channel {channel.id}: {e}")

async def start_backfill_task(channels):
    """
    Start background backfill for a list of channels.
    """
    logger.info(f"[Backfill] Starting background backfill for {len(channels)} channels.")
    for channel in channels:
        # Run sequentially to avoid flooding Discord API too hard concurrently
        # Or use a semaphore if we want some concurrency.
        # Given 80k limit, sequential is safer for rate limits.
        await backfill_channel(channel)
        # Small sleep between channels
        await asyncio.sleep(5)

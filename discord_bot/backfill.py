import logging
import asyncio
from core.database import get_message_count, get_latest_message_id, get_oldest_message_id
from discord_bot.context_cache import fetch_and_cache_from_api
from core.config import CONTEXT_AGENT_MAX_MESSAGES
import discord

logger = logging.getLogger(__name__)

async def backfill_channel(channel, target_limit: int = CONTEXT_AGENT_MAX_MESSAGES):
    """
    Backfill message history for a channel if DB count is low.
    """
    try:
        channel_id = channel.id
        current_count = await get_message_count(channel_id)
        
        channel_name = getattr(channel, "name", "DM")
        
        # If we have enough messages (e.g. > 90% of target), skip backfill
        if current_count >= target_limit * 0.9:
            logger.info(f"[Backfill] Channel {channel_name} ({channel_id}) has {current_count} messages (Target: {target_limit}). Skipping backfill.")
            return

        logger.info(f"[Backfill] Starting backfill for {channel_name} ({channel_id}). Current: {current_count}, Target: {target_limit}")
        
        # Check for existing data boundaries
        latest_id = await get_latest_message_id(channel_id)
        oldest_id = await get_oldest_message_id(channel_id)
        
        fetched_count = 0
        
        if latest_id:
            # 1. Catch Up: Fetch messages newer than the latest stored message
            logger.info(f"[Backfill] Catching up new messages for {channel_name} after ID {latest_id}")
            try:
                # Create a dummy object for 'after' since discord.py expects a Snowflake-like object
                after_obj = discord.Object(id=latest_id)
                new_messages = await fetch_and_cache_from_api(channel, limit=target_limit, after_message=after_obj)
                fetched_count += len(new_messages)
                logger.info(f"[Backfill] Caught up {len(new_messages)} new messages.")
            except Exception as e:
                logger.error(f"[Backfill] Error catching up: {e}")

            # Re-check count
            current_count = await get_message_count(channel_id)
            
            # 2. Deepen: If still below target, fetch older messages
            if current_count < target_limit:
                needed = target_limit - current_count
                logger.info(f"[Backfill] Still need {needed} messages. Deepening history before ID {oldest_id}")
                try:
                    before_obj = discord.Object(id=oldest_id)
                    old_messages = await fetch_and_cache_from_api(channel, limit=needed, before_message=before_obj)
                    fetched_count += len(old_messages)
                except Exception as e:
                    logger.error(f"[Backfill] Error deepening history: {e}")
        else:
            # No data, full fetch
            logger.info(f"[Backfill] No existing data. Performing full fetch.")
            fetched_count = len(await fetch_and_cache_from_api(channel, limit=target_limit))
        
        new_count = await get_message_count(channel_id)
        logger.info(f"[Backfill] Completed backfill for {channel_name} ({channel_id}). Fetched: {fetched_count}, New Total: {new_count}")
        
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

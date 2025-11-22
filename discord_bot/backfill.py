import logging
import asyncio
import os
from core.database import get_message_count, get_latest_message_id, get_oldest_message_id, is_channel_fully_backfilled, mark_channel_fully_backfilled
from discord_bot.context_cache import fetch_and_cache_from_api
from core.config import CONTEXT_AGENT_MAX_MESSAGES
import discord

logger = logging.getLogger(__name__)

# Per-channel locks to prevent race conditions during concurrent backfill
_backfill_locks = {}

async def backfill_channel(channel, target_limit: int = CONTEXT_AGENT_MAX_MESSAGES):
    """
    Backfill message history for a channel if DB count is low.
    Thread-safe with per-channel locking.
    """
    channel_id = channel.id
    
    # Acquire per-channel lock to prevent race conditions
    if channel_id not in _backfill_locks:
        _backfill_locks[channel_id] = asyncio.Lock()
    
    async with _backfill_locks[channel_id]:
        try:
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
                    after_obj = discord.Object(id=latest_id)
                    new_messages = await fetch_and_cache_from_api(channel, limit=target_limit, after_message=after_obj)
                    fetched_count += len(new_messages)
                    logger.info(f"[Backfill] Caught up {len(new_messages)} new messages.")
                except Exception as e:
                    logger.error(f"[Backfill] Error catching up: {e}")

                # Re-check count after catch-up
                current_count = await get_message_count(channel_id)
                oldest_id = await get_oldest_message_id(channel_id)  # Update oldest_id
            else:
                # No data, full fetch
                logger.info(f"[Backfill] No existing data. Performing full fetch.")
                fetched_count = len(await fetch_and_cache_from_api(channel, limit=target_limit))
                current_count = await get_message_count(channel_id)
                
                # If we did a full fetch and got less than limit, we are fully backfilled
                if fetched_count < target_limit:
                    await mark_channel_fully_backfilled(channel_id, True)
            
            # 2. Deepen: If still below target, fetch older messages (FIXED INDENTATION)
            if current_count < target_limit:
                # Check if we already fully backfilled this channel
                if await is_channel_fully_backfilled(channel_id):
                    logger.info(f"[Backfill] Channel {channel_name} is marked as fully backfilled. Skipping deepen.")
                elif oldest_id:  # Only deepen if we have an oldest_id
                    needed = target_limit - current_count
                    logger.info(f"[Backfill] Still need {needed} messages. Deepening history before ID {oldest_id}")
                    try:
                        before_obj = discord.Object(id=oldest_id)
                        old_messages = await fetch_and_cache_from_api(channel, limit=needed, before_message=before_obj)
                        fetched_count += len(old_messages)
                        
                        # If we fetched 0 messages, we've reached the beginning
                        if len(old_messages) == 0:
                            logger.info(f"[Backfill] No older messages found for {channel_name}. Marking as fully backfilled.")
                            await mark_channel_fully_backfilled(channel_id, True)
                    except Exception as e:
                        logger.error(f"[Backfill] Error deepening history: {e}")
            
            new_count = await get_message_count(channel_id)
            logger.info(f"[Backfill] Completed backfill for {channel_name} ({channel_id}). Fetched: {fetched_count}, New Total: {new_count}")
            
        except Exception as e:
            logger.error(f"[Backfill] Error backfilling channel {channel_id}: {e}", exc_info=True)

async def start_backfill_task(channels):
    """
    Start background backfill for a list of channels with concurrency control.
    Handles failures gracefully without cancelling other tasks.
    """
    # Default to 2 concurrent channels to be safe with rate limits
    concurrency = int(os.getenv("BACKFILL_CONCURRENCY", "2"))
    sem = asyncio.Semaphore(concurrency)
    
    logger.info(f"[Backfill] Starting background backfill for {len(channels)} channels with concurrency {concurrency}.")
    
    async def bound_backfill(channel):
        async with sem:
            try:
                await backfill_channel(channel)
            except Exception as e:
                channel_name = getattr(channel, "name", "DM")
                logger.error(f"[Backfill] Failed for channel {channel_name} ({channel.id}): {e}", exc_info=True)
            finally:
                # Small sleep to be nice to API even with semaphore
                await asyncio.sleep(1)

    # Create tasks for all channels
    tasks = [bound_backfill(c) for c in channels]
    
    # Use return_exceptions=True to prevent one failure from cancelling all others
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    # Log summary
    errors = [r for r in results if isinstance(r, Exception)]
    successes = len(results) - len(errors)
    logger.info(f"[Backfill] Completed: {successes}/{len(channels)} channels successful, {len(errors)} failed.")

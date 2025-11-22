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
                logger.info(f"[Backfill] ✓ Channel {channel_name}: {current_count}/{target_limit} messages (≥90%). Skipping backfill.")
                return

            logger.info(f"[Backfill] ▶ Starting backfill for {channel_name}: {current_count}/{target_limit} messages")
            
            # Check for existing data boundaries
            latest_id = await get_latest_message_id(channel_id)
            oldest_id = await get_oldest_message_id(channel_id)
            
            fetched_count = 0
            
            if latest_id:
                # 1. Catch Up: Fetch messages newer than the latest stored message
                logger.info(f"[Backfill] ↑ Catching up {channel_name} (after ID {latest_id})...")
                try:
                    after_obj = discord.Object(id=latest_id)
                    new_messages = await fetch_and_cache_from_api(channel, limit=target_limit, after_message=after_obj)
                    fetched_count += len(new_messages)
                    logger.info(f"[Backfill] ✓ Caught up {len(new_messages)} new messages. Total: {current_count + len(new_messages)}/{target_limit}")
                except Exception as e:
                    logger.error(f"[Backfill] Error catching up: {e}")

                # Re-check count after catch-up
                current_count = await get_message_count(channel_id)
                oldest_id = await get_oldest_message_id(channel_id)  # Update oldest_id
            else:
                # No data, full fetch
                logger.info(f"[Backfill] ⚡ No existing data for {channel_name}. Performing initial fetch...")
                fetched_count = len(await fetch_and_cache_from_api(channel, limit=target_limit))
                current_count = await get_message_count(channel_id)
                oldest_id = await get_oldest_message_id(channel_id)  # Update oldest_id after fetch
                oldest_id = await get_oldest_message_id(channel_id)  # Update oldest_id after fetch
                
                # Only mark as fully backfilled if we fetched ZERO messages (reached end of history)
                # Don't mark just because fetched_count < target_limit (channel might have fewer than target)
                if fetched_count == 0:
                    logger.info(f"[Backfill] No messages fetched for {channel_name}. Marking as fully backfilled.")
                    await mark_channel_fully_backfilled(channel_id, True)
            
            # 2. Deepen: If still below target, fetch older messages iteratively
            # Loop until we reach target or can't fetch more (important for cold start resume)
            max_deepen_iterations = int(os.getenv("BACKFILL_MAX_ITERATIONS", "10"))
            deepen_iteration = 0
            
            while current_count < target_limit and deepen_iteration < max_deepen_iterations:
                # Check if we already fully backfilled this channel
                if await is_channel_fully_backfilled(channel_id):
                    logger.info(f"[Backfill] Channel {channel_name} is marked as fully backfilled. Stopping deepen.")
                    break
                
                if not oldest_id:
                    # Update oldest_id in case it wasn't set
                    oldest_id = await get_oldest_message_id(channel_id)
                    if not oldest_id:
                        logger.warning(f"[Backfill] No oldest_id found for {channel_name}, cannot deepen further.")
                        break
                
                needed = target_limit - current_count
                logger.info(f"[Backfill] ↓ {channel_name} iteration {deepen_iteration + 1}: {current_count}/{target_limit} (need {needed} more)")
                
                try:
                    before_obj = discord.Object(id=oldest_id)
                    old_messages = await fetch_and_cache_from_api(channel, limit=min(needed, 1000), before_message=before_obj)
                    fetched_count += len(old_messages)
                    logger.info(f"[Backfill]   → Fetched {len(old_messages)} older messages")
                    
                    # If we fetched 0 messages, we've reached the beginning
                    if len(old_messages) == 0:
                        logger.info(f"[Backfill] No older messages found for {channel_name}. Marking as fully backfilled.")
                        await mark_channel_fully_backfilled(channel_id, True)
                        break
                    
                    # Update counters for next iteration
                    prev_count = current_count
                    current_count = await get_message_count(channel_id)
                    oldest_id = await get_oldest_message_id(channel_id)
                    deepen_iteration += 1
                    
                    progress_pct = int((current_count / target_limit) * 100)
                    logger.info(f"[Backfill]   ✓ Progress: {current_count}/{target_limit} ({progress_pct}%)")
                    
                    # Small delay to avoid hammering the API
                    if deepen_iteration < max_deepen_iterations and current_count < target_limit:
                        await asyncio.sleep(0.5)
                        
                except Exception as e:
                    logger.error(f"[Backfill] Error deepening history (iteration {deepen_iteration + 1}): {e}")
                    break
            
            new_count = await get_message_count(channel_id)
            completion_pct = int((new_count / target_limit) * 100) if target_limit > 0 else 100
            logger.info(f"[Backfill] ✓ Completed {channel_name}: {new_count}/{target_limit} ({completion_pct}%) - Fetched {fetched_count} messages this run")
            
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
    logger.info(f"[Backfill] ═══════════════════════════════════════")
    logger.info(f"[Backfill] Summary: {successes}/{len(channels)} channels successful, {len(errors)} failed")
    logger.info(f"[Backfill] ═══════════════════════════════════════")

# chat_handler.py
import logging
import sys
import time
from discord_bot.discord_utils import resolve_mentions, restore_mentions, correct_mentions
from agno.media import Image
# NOTE: updated imports to use team factory functions
from agent.agent_factory import get_or_create_team, create_team_for_user
from core.honcho_service import honcho_service
from tools.tools_factory import setup_mcp, get_mcp_tools, MultiMCPTools
from discord_bot.context_cache import (
    build_context_prompt,
    update_message_in_cache,
    delete_message_from_cache,
    append_message_to_cache,
)
from core.config import TEAM_LEADER_CONTEXT_LIMIT
from core.execution_context import set_current_channel_id, set_current_channel
from core.database import init_db, close_db
from discord_bot.backfill import start_backfill_task
import asyncio
import discord

logger = logging.getLogger(__name__)

async def async_ask_junkie(user_text: str, user_id: str, session_id: str, images: list = None, client=None) -> str:
    """
    Run the user's Team with improved error handling and response validation.
    """
    try:
        # Get or create team
        team = await get_or_create_team(user_id, client=client)
        
        # Execute the team
        result = await team.arun(
            input=user_text, user_id=user_id, session_id=session_id, images=images
        )
        
        # Handle the result
        content = result.content if result and hasattr(result, 'content') else ""
        
        if not content or not content.strip():
            return "I apologize, but I couldn't generate a valid response. Please try rephrasing your question."
        
        return content
    except Exception as e:
        logger.error(f"Team error for user {user_id}: {e}", exc_info=True)
        raise

def setup_chat(bot):
    @bot.event
    async def on_ready():
        logger.info("[on_ready] Bot ready event triggered!")
        # Ensure MCP tools are connected/initialized
        await setup_mcp()
        
        # Initialize Database
        logger.info("[on_ready] Initializing database...")
        await init_db()
        logger.info("[on_ready] Database initialized")
        
        # Log Honcho status
        if honcho_service.is_enabled:
            logger.info("[on_ready] Honcho service is ready for memory management")
        else:
            logger.warning("[on_ready] Honcho service not configured - memory features disabled")
        
        # Start Backfill Task
        # Filter for TextChannels, DMs, and GroupChats where the bot has read permissions
        text_channels = [
            c for c in bot.bot.get_all_channels() 
            if isinstance(c, (discord.TextChannel, discord.DMChannel, discord.GroupChannel))
        ]
        # Also check private_channels as get_all_channels might miss some DMs depending on cache state
        for c in bot.bot.private_channels:
            if c not in text_channels:
                text_channels.append(c)
        
        logger.info(f"[on_ready] Found {len(text_channels)} total channels")
        
        # Start backfill task with error handling, post-sync, and Honcho cold start
        async def run_backfill_and_sync():
            try:
                import os
                from datetime import datetime, timezone, timedelta
                from core.database import pool
                
                # Filter for active channels (activity in last N days)
                active_days = int(os.getenv("BACKFILL_ACTIVE_DAYS", "7"))
                cutoff = datetime.now(timezone.utc) - timedelta(days=active_days)
                
                active_channels = []
                inactive_channels = []
                
                for channel in text_channels:
                    try:
                        # Check if channel has recent messages in DB
                        if pool:
                            async with pool.acquire() as conn:
                                has_recent = await conn.fetchval("""
                                    SELECT EXISTS(
                                        SELECT 1 FROM messages 
                                        WHERE channel_id = $1 AND created_at > $2
                                        LIMIT 1
                                    )
                                """, channel.id, cutoff)
                                if has_recent:
                                    active_channels.append(channel)
                                else:
                                    inactive_channels.append(channel)
                        else:
                            active_channels.append(channel)  # Include if we can't check
                    except Exception as e:
                        logger.debug(f"[on_ready] Error checking channel activity: {e}")
                        active_channels.append(channel)
                
                logger.info(f"[on_ready] Channel priority: {len(active_channels)} active (last {active_days} days), {len(inactive_channels)} inactive")
                
                # Only backfill active channels
                logger.info(f"[on_ready] Starting backfill for {len(active_channels)} active channels...")
                await start_backfill_task(active_channels)
                logger.info("[on_ready] Backfill task completed")
                
                # Sync recent messages only for active channels
                from discord_bot.message_sync import sync_all_channels
                sync_limit = int(os.getenv("MESSAGE_SYNC_LIMIT", "100"))
                logger.info(f"[on_ready] Starting message sync for {len(active_channels)} active channels (last {sync_limit} messages)...")
                await sync_all_channels(active_channels, sync_limit=sync_limit)
                logger.info("[on_ready] Message sync completed")
                
                # Production cold start: Quick sync to Honcho + background deep sync
                if honcho_service.is_enabled:
                    from discord_bot.cold_start import run_cold_start_sync
                    logger.info("[on_ready] Starting Honcho cold start sync...")
                    await run_cold_start_sync(active_channels)
                    logger.info("[on_ready] Honcho cold start sync completed")
                
            except Exception as e:
                logger.error(f"[on_ready] Backfill/sync/cold_start task failed: {e}", exc_info=True)
        
        logger.info(f"[on_ready] Creating backfill+sync background task...")
        asyncio.create_task(run_backfill_and_sync())
        logger.info("[on_ready] Backfill+sync task created - running in background")
    
    @bot.event
    async def on_disconnect():
        """Handle disconnection events - note: this fires for temporary gateway disconnects too."""
        # Note: Do NOT close the database pool here!
        # on_disconnect fires during temporary gateway reconnects, not just shutdown.
        # The pool should remain open for background tasks.
        # Cleanup happens when the bot process actually terminates.
        logger.info("[on_disconnect] Bot disconnected (gateway may reconnect)")

    @bot.event
    async def on_message(message):
        # Update cache with new message (both user and bot messages for full context)
        await append_message_to_cache(message)
        
        # Allow normal bot commands to be handled by discord.py
        if message.content.startswith(bot.prefix):
            await bot.bot.process_commands(message)
            return

        # Chatbot prefix (!) — handle via Team
        chatbot_prefix = "!"
        if message.content.startswith(chatbot_prefix):
            # Step 1: replace mentions with readable form for context
            processed_content = resolve_mentions(message)
            
            # Extract the prompt after the prefix
            raw_prompt = processed_content[len(chatbot_prefix):].strip()
            if not raw_prompt:
                return

            # Step 2: build context-aware prompt
            logger.info(f"[chatbot] Building context for channel {message.channel.id}, user {message.author.id}")
            
            # Try to find reply context if present
            reply_to_message = None
            if message.reference and message.reference.resolved:
                if isinstance(message.reference.resolved, type(message)):
                    reply_to_message = message.reference.resolved
                    logger.info(f"[chatbot] Found reply context: {reply_to_message.id}")
            elif message.reference and message.reference.message_id:
                try:
                    reply_to_message = await message.channel.fetch_message(message.reference.message_id)
                    logger.info(f"[chatbot] Fetched reply context: {reply_to_message.id}")
                except Exception as e:
                    logger.warning(f"[chatbot] Failed to fetch reply context: {e}")

            prompt = await build_context_prompt(message, raw_prompt, limit=TEAM_LEADER_CONTEXT_LIMIT, reply_to_message=reply_to_message)
            logger.info(f"[chatbot] Context prompt built, length: {len(prompt)} characters")

            # Extract images from current message and reply
            images = []
            
            # 1. Current message attachments
            if message.attachments:
                for attachment in message.attachments:
                    if attachment.content_type and attachment.content_type.startswith('image/'):
                        images.append(Image(url=attachment.url))
                        logger.info(f"[chatbot] Found image attachment: {attachment.url}")

            # 2. Reply message attachments (if any)
            if reply_to_message and reply_to_message.attachments:
                for attachment in reply_to_message.attachments:
                    if attachment.content_type and attachment.content_type.startswith('image/'):
                        images.append(Image(url=attachment.url))
                        logger.info(f"[chatbot] Found reply image attachment: {attachment.url}")

            # Step 3: run the Team (shared session per channel)
            async with message.channel.typing():
                user_id = str(message.author.id)
                session_id = str(message.channel.id)
                
                # Log invocation
                channel_name = getattr(message.channel, "name", "DM")
                logger.info(f"[chatbot] Agent invoked in channel {channel_name} ({message.channel.id}) by user {message.author.name} ({user_id})")
                
                # Set the execution context for tools
                set_current_channel_id(message.channel.id)
                set_current_channel(message.channel)
                
                start_time = time.time()
                try:
                    reply = await async_ask_junkie(
                        prompt, user_id=user_id, session_id=session_id, images=images, client=bot.bot
                    )
                except Exception as e:
                    # Surface a truncated error to the user; keep details in logs
                    logger.exception(f"[chatbot] Failed to generate reply for user {user_id}")
                    await message.channel.send(
                        f"**Error:** Failed to process request: {str(e)[:500]}"
                    )
                    return
                
                end_time = time.time()
                time_taken = end_time - start_time
                
            # Step 4: restore mentions in the reply
            final_reply = restore_mentions(reply, message.guild)
            # Remove any agent-supplied prefix artifacts
            final_reply = final_reply.replace("**🗿 hero:**", "")
            # Replace any leftover plain @name with actual mentions
            final_reply = correct_mentions(prompt, final_reply)
            
            # Append time taken
            final_reply += f"\n\n*(Time taken: {time_taken:.2f}s)*"
            
            # Step 5: send reply, chunking long outputs (Discord limit is ~2000 chars)
            chunk_size = 1900
            for chunk in [final_reply[i:i+chunk_size] for i in range(0, len(final_reply), chunk_size)]:
                await message.channel.send(f"**🗿 hero:**\n{chunk}")
            
            # Step 6: Store messages in Honcho for persistent memory
            if honcho_service.is_enabled:
                try:
                    # Get/create Honcho session for this channel
                    honcho_session = honcho_service.get_session(
                        channel_id=session_id,
                        metadata={
                            "channel_name": getattr(message.channel, "name", "DM"),
                            "channel_type": str(type(message.channel).__name__),
                        }
                    )
                    
                    # Get/create peer for the user with Discord metadata for nickname resolution
                    author = message.author
                    user_peer = honcho_service.get_peer(
                        user_id=user_id,
                        username=author.name,
                        display_name=author.display_name,
                        nickname=getattr(author, "nick", None),  # Server nickname if in guild
                    )
                    
                    # Store both user message and bot response with timestamps
                    from datetime import datetime, timezone
                    honcho_service.store_messages(
                        session=honcho_session,
                        user_message=raw_prompt,
                        bot_response=reply,
                        user_peer=user_peer,
                        user_metadata={
                            "discord_message_id": str(message.id),
                            "has_images": len(images) > 0,
                        },
                        user_created_at=message.created_at.isoformat(),
                        bot_created_at=datetime.now(timezone.utc).isoformat(),
                    )
                    logger.debug(f"[chatbot] Stored messages in Honcho for user {user_id}")
                except Exception as e:
                    logger.error(f"[chatbot] Failed to store messages in Honcho: {e}")

    @bot.event
    async def on_message_edit(before, after):
        """Handle message edits to update cache."""
        await update_message_in_cache(before, after)

    @bot.event
    async def on_message_delete(message):
        """Handle message deletions to update cache."""
        await delete_message_from_cache(message)


async def main_cli():
    """
    CLI entrypoint — create a per-user Team and run its CLI app if available.
    """
    await setup_mcp()
    try:
        if sys.stdin and sys.stdin.isatty():
            # Create a team for a default CLI user and run interactive CLI if Team exposes it
            _, cli_team = create_team_for_user("cli_user")
            # many agent/team implementations expose acli_app similar to agents;
            # fall back gracefully if not present
            if hasattr(cli_team, "acli_app"):
                await cli_team.acli_app()
            else:
                print("CLI app not available for Team object.")
        else:
            print("Non-interactive environment detected; skipping CLI app.")
    finally:
        # Attempt to gracefully close MCP tools if available
        mcp = get_mcp_tools()
        if mcp:
            try:
                # If close is async, await it; otherwise, call it
                close_call = getattr(mcp, "close", None)
                if close_call:
                    if hasattr(close_call, "__await__"):
                        await close_call()
                    else:
                        close_call()
            except Exception:
                logger.exception("Error closing MCP tools, ignoring.")

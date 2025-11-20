# chat_handler.py
import logging
import sys
from discord_bot.discord_utils import resolve_mentions, restore_mentions, correct_mentions
# NOTE: updated imports to use team factory functions
from agent.agent_factory import get_or_create_team, create_team_for_user
from tools.tools_factory import setup_mcp, get_mcp_tools, MultiMCPTools
from discord_bot.context_cache import (
    build_context_prompt,
    update_message_in_cache,
    delete_message_from_cache,
    append_message_to_cache,
)

logger = logging.getLogger(__name__)

async def async_ask_junkie(user_text: str, user_id: str, session_id: str) -> str:
    """
    Run the user's Team with improved error handling and response validation.
    """
    # get_or_create_team returns a Team instance (or equivalent orchestrator)
    team = get_or_create_team(user_id)
    try:
        # Teams should implement async arun similar to Agents
        result = await team.arun(
            input=user_text, user_id=user_id, session_id=session_id
        )
        
        # Basic response validation
        content = result.content if result and hasattr(result, 'content') else ""
        
        # Ensure we have a valid response
        if not content or not content.strip():
            return "I apologize, but I couldn't generate a valid response. Please try rephrasing your question."
        
        return content
    except Exception as e:
        # Log the error for debugging
        logger.error(f"Team error for user {user_id}: {e}", exc_info=True)
        raise  # Re-raise to be handled by caller


def setup_chat(bot):
    @bot.event
    async def on_ready():
        # Ensure MCP tools are connected/initialized
        await setup_mcp()

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

            prompt = await build_context_prompt(message, raw_prompt, limit=500, reply_to_message=reply_to_message)
            logger.info(f"[chatbot] Context prompt built, length: {len(prompt)} characters")

            # Step 3: run the Team (shared session per channel)
            async with message.channel.typing():
                user_id = str(message.author.id)
                session_id = str(message.channel.id)
                try:
                    reply = await async_ask_junkie(
                        prompt, user_id=user_id, session_id=session_id
                    )
                except Exception as e:
                    # Surface a truncated error to the user; keep details in logs
                    logger.exception(f"[chatbot] Failed to generate reply for user {user_id}")
                    await message.channel.send(
                        f"**Error:** Failed to process request: {str(e)[:500]}"
                    )
                    return

            # Step 4: restore mentions in the reply
            final_reply = restore_mentions(reply, message.guild)
            # Remove any agent-supplied prefix artifacts
            final_reply = final_reply.replace("**🗿 hero:**", "")
            # Replace any leftover plain @name with actual mentions
            final_reply = correct_mentions(prompt, final_reply)
            
            # Step 5: send reply, chunking long outputs (Discord limit is ~2000 chars)
            chunk_size = 1900
            for chunk in [final_reply[i:i+chunk_size] for i in range(0, len(final_reply), chunk_size)]:
                await message.channel.send(f"**🗿 hero:**\n{chunk}")

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

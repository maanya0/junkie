import logging
import sys
from discord_bot.discord_utils import resolve_mentions, restore_mentions, correct_mentions
from agent.agent_factory import get_or_create_agent, create_model_and_team
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
    Run the agent with improved error handling and response validation.
    """
    agent = get_or_create_team(user_id)
    try:
        result = await agent.arun(
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
        logger.error(f"Agent error for user {user_id}: {e}", exc_info=True)
        raise  # Re-raise to be handled by caller

def setup_chat(bot):
    @bot.event
    async def on_ready():
        await setup_mcp()

    @bot.event
    async def on_message(message):
        # Update cache with new message (both user and bot messages for full context)
        await append_message_to_cache(message)
        
        # Check for command prefix (.) - let commands be processed by bot
        if message.content.startswith(bot.prefix):
            await bot.bot.process_commands(message)
            return

        # Check for chatbot prefix (!) - process as chatbot message
        chatbot_prefix = "!"
        if message.content.startswith(chatbot_prefix):
            # Step 1: replace mentions with readable form
            processed_content = resolve_mentions(message)
            
            # Extract the prompt after the prefix
            raw_prompt = processed_content[len(chatbot_prefix):].strip()
            if not raw_prompt:
                return

            # Step 2: build context-aware prompt
            # Request 500 messages (current message will be excluded and added separately)
            logger.info(f"[chatbot] Building context for channel {message.channel.id}, user {message.author.id}")
            
            # Check for reply reference
            reply_to_message = None
            if message.reference and message.reference.resolved:
                if isinstance(message.reference.resolved, type(message)):
                    reply_to_message = message.reference.resolved
                    logger.info(f"[chatbot] Found reply context: {reply_to_message.id}")
            elif message.reference and message.reference.message_id:
                # Try to fetch if not resolved (e.g. not in cache)
                try:
                    reply_to_message = await message.channel.fetch_message(message.reference.message_id)
                    logger.info(f"[chatbot] Fetched reply context: {reply_to_message.id}")
                except Exception as e:
                    logger.warning(f"[chatbot] Failed to fetch reply context: {e}")

            prompt = await build_context_prompt(message, raw_prompt, limit=500, reply_to_message=reply_to_message)
            logger.info(f"[chatbot] Context prompt built, length: {len(prompt)} characters")

            # Step 3: run the agent (shared session per channel)
            async with message.channel.typing():
                user_id = str(message.author.id)
                session_id = str(message.channel.id)
                try:
                    reply = await async_ask_junkie(
                        prompt, user_id=user_id, session_id=session_id
                    )
                except Exception as e:
                    await message.channel.send(
                        f"**Error:** Failed to process request: {str(e)[:500]}"
                    )
                    return

            # Step 4: convert '@Name(id)' → actual mentions
            final_reply = restore_mentions(reply, message.guild)
            #replace **🗿 hero:** if the agent provides it iin its response
            final_reply = final_reply.replace("**🗿 hero:**", "")
            #replace only @name with mentions
            final_reply = correct_mentions(prompt, final_reply)
            

            # Step 5: send reply, splitting long ones (Discord limit is 2000 chars)
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
    await setup_mcp()
    try:
        if sys.stdin and sys.stdin.isatty():
            # For CLI, use a default user_id
            _, cli_agent = create_model_and_team("cli_user")
            await cli_agent.acli_app()
        else:
            print("Non-interactive environment detected; skipping CLI app.")
    finally:
        mcp = get_mcp_tools()
        if mcp and isinstance(mcp, MultiMCPTools) and _mcp_connected:
            try:
                await mcp.close()
            except Exception:
                pass

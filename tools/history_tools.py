from agno.tools import Toolkit
from discord_bot.context_cache import get_recent_context
from core.execution_context import get_current_channel_id, get_current_channel
from core.database import get_messages
from core.tool_cache import get_cached_tool_result, cache_tool_result
import logging
import asyncio

logger = logging.getLogger(__name__)

class HistoryTools(Toolkit):
    def __init__(self):
        super().__init__(name="history_tools")
        self.register(self.read_chat_history)

    async def read_chat_history(self, limit: int = 2000) -> str:
        """
        Reads the chat history for the current channel from the cache.
        
        Args:
            limit (int): The maximum number of messages to return. Defaults to 2000.
            
        Returns:
            str: The chat history as a string, or a message indicating no history found.
        """
        # Get channel object from execution context
        channel = get_current_channel()
        
        if not channel:
            # Fallback to ID if object missing (e.g. testing), but we can't fetch new history
            channel_id = get_current_channel_id()
            if not channel_id:
                 return "Error: No execution context found. Cannot determine channel."
            
            logger.warning(f"[HistoryTools] Channel object missing, falling back to DB-only for ID {channel_id}")
            
            # Check cache first
            cache_key = ("read_chat_history_db", channel_id, limit)
            cached_result = get_cached_tool_result("read_chat_history", (channel_id, limit), {})
            if cached_result:
                return cached_result
                
            # Use DB directly
            db_messages = await get_messages(channel_id, limit)
            if not db_messages:
                result = "No history found in database."
            else:
                formatted = []
                for m in db_messages:
                    formatted.append(f"{m['timestamp_str']} {m['author_name']}({m['author_id']}): {m['content']}")
                result = "\n".join(formatted)
                
            # Cache the result
            cache_tool_result("read_chat_history", (channel_id, limit), {}, result, ttl=60)  # Cache for 1 minute
            return result

        logger.info(f"[HistoryTools] Fetching history for channel {channel.id} with limit {limit}")
        
        try:
            # Check cache first
            cached_result = get_cached_tool_result("read_chat_history", (channel.id, limit), {})
            if cached_result:
                logger.debug(f"[HistoryTools] Returning cached history for channel {channel.id}")
                return cached_result
                
            # Use get_recent_context which handles fetching if cache is insufficient
            history_lines = await get_recent_context(channel, limit=limit)
            result = "\n".join(history_lines)
            
            # Cache the result
            cache_tool_result("read_chat_history", (channel.id, limit), {}, result, ttl=60)  # Cache for 1 minute
            return result
        except Exception as e:
            logger.error(f"[HistoryTools] Error fetching history: {e}", exc_info=True)
            return f"Error fetching history: {str(e)}"

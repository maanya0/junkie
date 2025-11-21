from agno.tools import Toolkit
from discord_bot.context_cache import _memory_cache, get_recent_context
from core.execution_context import get_current_channel_id, get_current_channel
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
            
            logger.warning(f"[HistoryTools] Channel object missing, falling back to cache-only for ID {channel_id}")
            mem_entry = _memory_cache.get(channel_id)
            if not mem_entry:
                return "No history found in cache."
            cached_data = list(mem_entry["data"])
            if len(cached_data) > limit:
                cached_data = cached_data[-limit:]
            return "\n".join(cached_data)

        logger.info(f"[HistoryTools] Fetching history for channel {channel.id} with limit {limit}")
        
        try:
            # Use get_recent_context which handles fetching if cache is insufficient
            history_lines = await get_recent_context(channel, limit=limit)
            return "\n".join(history_lines)
        except Exception as e:
            logger.error(f"[HistoryTools] Error fetching history: {e}", exc_info=True)
            return f"Error fetching history: {str(e)}"

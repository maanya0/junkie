from agno.tools import Toolkit
from discord_bot.context_cache import _memory_cache
from core.execution_context import get_current_channel_id
import logging

logger = logging.getLogger(__name__)

class HistoryTools(Toolkit):
    def __init__(self):
        super().__init__(name="history_tools")
        self.register(self.read_chat_history)

    def read_chat_history(self, limit: int = 2000) -> str:
        """
        Reads the chat history for the current channel from the cache.
        
        Args:
            limit (int): The maximum number of messages to return. Defaults to 2000.
            
        Returns:
            str: The chat history as a string, or a message indicating no history found.
        """
        # Get channel ID from execution context
        channel_id = get_current_channel_id()
            
        if channel_id is None:
            return "Error: No execution context found. Cannot determine channel ID."

        logger.info(f"[HistoryTools] Fetching history for channel {channel_id} with limit {limit}")
        
        mem_entry = _memory_cache.get(channel_id)
        if not mem_entry:
            return "No history found for this channel."
            
        cached_data = list(mem_entry["data"])
        
        # Apply limit
        if len(cached_data) > limit:
            cached_data = cached_data[-limit:]
            
        return "\n".join(cached_data)

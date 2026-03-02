from agno.tools import Toolkit
from discord_bot.context_cache import get_recent_context
from core.execution_context import get_current_channel_id, get_current_channel
from core.database import get_messages
import logging
import asyncio

logger = logging.getLogger(__name__)

class HistoryTools(Toolkit):
    def __init__(self):
        super().__init__(name="history_tools")
        self.register(self.read_chat_history)

    # Validation constants
    MIN_LIMIT = 1
    MAX_LIMIT = 10000
    
    async def read_chat_history(self, limit: int = 2000) -> str:
        """
        Reads the chat history for the current channel from the cache.
        
        Args:
            limit (int): The maximum number of messages to return. 
                         Must be between 1 and 10000. Defaults to 2000.
            
        Returns:
            str: The chat history as a string, or a message indicating no history found.
        """
        # Validate and clamp limit to safe range
        if not isinstance(limit, int) or limit < self.MIN_LIMIT:
            logger.warning(f"[HistoryTools] Invalid limit {limit}, using minimum {self.MIN_LIMIT}")
            limit = self.MIN_LIMIT
        elif limit > self.MAX_LIMIT:
            logger.warning(f"[HistoryTools] Limit {limit} exceeds maximum, clamping to {self.MAX_LIMIT}")
            limit = self.MAX_LIMIT
        
        # Get channel object from execution context
        channel = get_current_channel()
        
        if not channel:
            # Fallback to ID if object missing (e.g. testing), but we can't fetch new history
            channel_id = get_current_channel_id()
            if not channel_id:
                 return "Error: No execution context found. Cannot determine channel."
            
            logger.warning(f"[HistoryTools] Channel object missing, falling back to DB-only for ID {channel_id}")
            
            # Use DB directly with error handling
            try:
                db_messages = await get_messages(channel_id, limit)
                if not db_messages:
                    return "No history found in database."
                    
                formatted = []
                for m in db_messages:
                    formatted.append(f"{m['timestamp_str']} {m['author_name']}({m['author_id']}): {m['content']}")
                return "\n".join(formatted)
            except Exception as e:
                logger.error(f"[HistoryTools] Database error for channel {channel_id}: {e}", exc_info=True)
                return f"Error fetching history from database: {str(e)}"

        logger.info(f"[HistoryTools] Fetching history for channel {channel.id} with limit {limit}")
        
        try:
            # Use get_recent_context which handles fetching if cache is insufficient
            history_lines = await get_recent_context(channel, limit=limit)
            return "\n".join(history_lines)
        except Exception as e:
            logger.error(f"[HistoryTools] Error fetching history: {e}", exc_info=True)
            return f"Error fetching history: {str(e)}"

from agno.tools import Toolkit
from discord_bot.context_cache import get_recent_context

from core.database import get_messages
import logging
import asyncio

logger = logging.getLogger(__name__)

class HistoryTools(Toolkit):
    def __init__(self, client=None):
        super().__init__(name="history_tools")
        self.client = client
        self.register(self.read_chat_history)

    async def read_chat_history(self, agent, limit: int = 2000) -> str:
        """
        Reads the chat history for the current channel from the cache.
        
        Args:
            agent (Agent): The agent instance.
            limit (int): The maximum number of messages to return. Defaults to 2000.
            
        Returns:
            str: The chat history as a string, or a message indicating no history found.
        """
        # Get channel ID from session state
        channel_id = agent.session_state.get("channel_id")
        
        if not channel_id:
            return "Error: No channel context found in session state."

        # Try to get channel object using injected client
        channel = None
        if self.client:
            channel = self.client.get_channel(int(channel_id))
            if not channel:
                try:
                    channel = await self.client.fetch_channel(int(channel_id))
                except Exception as e:
                    logger.warning(f"[HistoryTools] Failed to fetch channel {channel_id}: {e}")

        if not channel:
            logger.warning(f"[HistoryTools] Channel object missing, falling back to DB-only for ID {channel_id}")
            
            # Use DB directly
            db_messages = await get_messages(channel_id, limit)
            if not db_messages:
                return "No history found in database."
                
            formatted = []
            for m in db_messages:
                formatted.append(f"{m['timestamp_str']} {m['author_name']}({m['author_id']}): {m['content']}")
            return "\n".join(formatted)

        logger.info(f"[HistoryTools] Fetching history for channel {channel.id} with limit {limit}")
        
        try:
            # Use get_recent_context which handles fetching if cache is insufficient
            history_lines = await get_recent_context(channel, limit=limit)
            return "\n".join(history_lines)
        except Exception as e:
            logger.error(f"[HistoryTools] Error fetching history: {e}", exc_info=True)
            return f"Error fetching history: {str(e)}"

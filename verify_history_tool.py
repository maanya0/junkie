import sys
from unittest.mock import MagicMock

# Mock dependencies
sys.modules["redis"] = MagicMock()
sys.modules["agno.db.redis"] = MagicMock()
sys.modules["agno.db.redis.redis"] = MagicMock()
sys.modules["openai"] = MagicMock()
sys.modules["mcp"] = MagicMock()
sys.modules["exa_py"] = MagicMock()
sys.modules["wikipedia"] = MagicMock()
sys.modules["youtube_transcript_api"] = MagicMock()

# Mock agent factory
mock_agent_factory = MagicMock()
sys.modules["agent"] = MagicMock()
sys.modules["agent.agent_factory"] = mock_agent_factory

import asyncio
from unittest.mock import patch
from discord_bot.context_cache import _memory_cache
from tools.history_tools import HistoryTools

def test_history_tool():
    print("Testing HistoryTools...")
    
    # Setup mock cache
    channel_id = 12345
    _memory_cache[channel_id] = {
        "data": ["msg1", "msg2", "msg3", "msg4", "msg5"],
        "timestamp": 1000
    }
    
    tool = HistoryTools()
    
    # Test fetching all history
    history = tool.read_chat_history(channel_id)
    print(f"Full History:\n{history}")
    assert "msg1" in history
    assert "msg5" in history
    
    # Test limiting history
    limited_history = tool.read_chat_history(channel_id, limit=2)
    print(f"Limited History (2):\n{limited_history}")
    assert "msg4" in limited_history
    assert "msg5" in limited_history
    assert "msg1" not in limited_history
    
    print("SUCCESS: HistoryTools works as expected.")

if __name__ == "__main__":
    test_history_tool()

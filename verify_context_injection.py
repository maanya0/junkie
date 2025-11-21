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
from discord_bot.context_cache import _memory_cache
from tools.history_tools import HistoryTools
from core.execution_context import set_current_channel_id

def test_context_injection():
    print("Testing Context Injection...")
    
    # Setup mock cache
    channel_id = 99999
    _memory_cache[channel_id] = {
        "data": ["context_msg_1", "context_msg_2"],
        "timestamp": 1000
    }
    
    tool = HistoryTools()
    
    async def run_tests():
        # 1. Test without context (should fail)
        print("Test 1: No Context")
        result = await tool.read_chat_history()
        print(f"Result: {result}")
        assert "Error" in result
        
        # 2. Test with context injection (ID only fallback)
        print("\nTest 2: With Context Injection (ID only)")
        set_current_channel_id(channel_id)
        # Note: We are NOT setting the channel object, so it should fallback to cache
        result = await tool.read_chat_history()
        print(f"Result: {result}")
        assert "context_msg_1" in result
        assert "context_msg_2" in result
        
        print("\nSUCCESS: Context injection works as expected.")

    asyncio.run(run_tests())

if __name__ == "__main__":
    test_context_injection()

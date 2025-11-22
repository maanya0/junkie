import sys
from unittest.mock import MagicMock, AsyncMock

# Mock dependencies
sys.modules["asyncpg"] = MagicMock()
sys.modules["discord"] = MagicMock()

import asyncio
import core.database
from discord_bot.backfill import backfill_channel
from discord_bot.context_cache import fetch_and_cache_from_api

# Mock DB pool and connection
mock_pool = MagicMock() # Pool itself isn't async, acquire returns context
mock_conn = AsyncMock()

# Create a proper AsyncContextManager mock
class AsyncContextManager:
    async def __aenter__(self):
        return mock_conn
    async def __aexit__(self, exc_type, exc, tb):
        pass

mock_pool.acquire.return_value = AsyncContextManager()
core.database.pool = mock_pool

async def test_db_operations():
    print("Testing DB Operations...")
    
    # Mock get_messages return
    mock_conn.fetch.return_value = [
        {"message_id": 1, "channel_id": 123, "author_id": 1, "author_name": "User", "content": "Hello", "created_at": "now", "timestamp_str": "[now]"}
    ]
    
    messages = await core.database.get_messages(123, limit=10)
    print(f"get_messages result: {len(messages)} messages")
    assert len(messages) == 1
    assert messages[0]["content"] == "Hello"
    
    # Mock get_message_count
    mock_conn.fetchval.return_value = 50
    count = await core.database.get_message_count(123)
    print(f"get_message_count result: {count}")
    assert count == 50

async def test_backfill():
    print("\nTesting Backfill Logic...")
    
    # Mock channel
    mock_channel = MagicMock()
    mock_channel.id = 999
    mock_channel.name = "test-channel"
    
    # Mock history iterator
    mock_msg = MagicMock()
    mock_msg.id = 1001
    mock_msg.content = "Backfilled Message"
    mock_msg.clean_content = "Backfilled Message"
    mock_msg.author.id = 1
    mock_msg.author.display_name = "User"
    from datetime import datetime, timezone
    mock_msg.created_at = datetime.now(timezone.utc)
    
    async def history_gen(*args, **kwargs):
        yield mock_msg
        
    mock_channel.history = MagicMock(side_effect=history_gen)
    
    # 1. Test skip backfill if count is high
    mock_conn.fetchval.return_value = 80000  # Full
    await backfill_channel(mock_channel, target_limit=80000)
    # Should NOT call history
    mock_channel.history.assert_not_called()
    print("Backfill skipped correctly when full.")
    
    # 2. Test trigger backfill if count is low
    mock_conn.fetchval.return_value = 0  # Empty
    # Reset mocks
    mock_channel.history = MagicMock(return_value=history_gen()) # Async iterator mock is tricky, let's use the helper directly
    
    # We'll test fetch_and_cache_from_api directly since backfill_channel calls it
    print("Testing fetch_and_cache_from_api...")
    
    class AsyncIterator:
        def __init__(self, items):
            self.items = items
        def __aiter__(self):
            return self
        async def __anext__(self):
            if not self.items:
                raise StopAsyncIteration
            print("AsyncIterator yielding item")
            return self.items.pop(0)
            
    mock_channel.history.return_value = AsyncIterator([mock_msg])
    
    # Re-mock store_message in the module where it's used
    import discord_bot.context_cache
    discord_bot.context_cache.store_message = AsyncMock()
    
    result = await fetch_and_cache_from_api(mock_channel, limit=10)
    print(f"fetch_and_cache_from_api result: {result}")
    
    discord_bot.context_cache.store_message.assert_called()
    print("fetch_and_cache_from_api stored message in DB.")

if __name__ == "__main__":
    asyncio.run(test_db_operations())
    asyncio.run(test_backfill())

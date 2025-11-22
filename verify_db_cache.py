import sys
from unittest.mock import MagicMock, AsyncMock
from datetime import datetime, timezone
import asyncio
import os
from dotenv import load_dotenv

# Load env vars for POSTGRES_URL
load_dotenv()

# Mock discord only
sys.modules["discord"] = MagicMock()
import discord

# Import real database module
import core.database
from core.database import init_db, close_db, get_messages, get_message_count, store_message, get_latest_message_id
from discord_bot.backfill import backfill_channel
from discord_bot.context_cache import fetch_and_cache_from_api

# Test Constants
TEST_CHANNEL_ID = 999999999
TEST_AUTHOR_ID = 12345

async def setup_test_db():
    print("Connecting to Real Database...")
    await init_db()
    
    # Clean up previous test data
    if core.database.pool:
        async with core.database.pool.acquire() as conn:
            await conn.execute("DELETE FROM messages WHERE channel_id = $1", TEST_CHANNEL_ID)
            await conn.execute("DELETE FROM channel_status WHERE channel_id = $1", TEST_CHANNEL_ID)
    print("Test DB connected and cleaned.")

async def teardown_test_db():
    print("Cleaning up and closing DB...")
    if core.database.pool:
        async with core.database.pool.acquire() as conn:
            await conn.execute("DELETE FROM messages WHERE channel_id = $1", TEST_CHANNEL_ID)
            await conn.execute("DELETE FROM channel_status WHERE channel_id = $1", TEST_CHANNEL_ID)
    await close_db()

async def test_db_operations():
    print("\nTesting Real DB Operations...")
    
    # 1. Store a message
    created_at = datetime.now(timezone.utc)
    await store_message(
        message_id=1001,
        channel_id=TEST_CHANNEL_ID,
        author_id=TEST_AUTHOR_ID,
        author_name="TestUser",
        content="Hello World",
        created_at=created_at,
        timestamp_str=created_at.strftime("%Y-%m-%d %H:%M:%S")
    )
    
    # 2. Get messages
    messages = await get_messages(TEST_CHANNEL_ID, limit=10)
    print(f"get_messages result: {len(messages)} messages")
    assert len(messages) == 1
    assert messages[0]["content"] == "Hello World"
    
    # 3. Get count
    count = await get_message_count(TEST_CHANNEL_ID)
    print(f"get_message_count result: {count}")
    assert count == 1
    
    # 4. Get latest ID
    latest = await get_latest_message_id(TEST_CHANNEL_ID)
    print(f"get_latest_message_id result: {latest}")
    assert latest == 1001

async def test_backfill_logic():
    print("\nTesting Backfill Logic with Real DB...")
    
    # Mock channel
    mock_channel = MagicMock()
    mock_channel.id = TEST_CHANNEL_ID
    mock_channel.name = "test-channel"
    
    # Mock history iterator helper
    class AsyncIterator:
        def __init__(self, items):
            self.items = items
        def __aiter__(self):
            return self
        async def __anext__(self):
            if not self.items:
                raise StopAsyncIteration
            return self.items.pop(0)

    # Create a mock message to be "fetched" from API
    mock_msg = MagicMock()
    mock_msg.id = 2002
    mock_msg.content = "New API Message"
    mock_msg.clean_content = "New API Message"
    mock_msg.author.id = TEST_AUTHOR_ID
    mock_msg.author.display_name = "TestUser"
    mock_msg.created_at = datetime.now(timezone.utc)
    
    # Mock channel.history to return this message
    mock_channel.history.return_value = AsyncIterator([mock_msg])
    
    # Run fetch_and_cache_from_api (which writes to real DB)
    print("Running fetch_and_cache_from_api...")
    result = await fetch_and_cache_from_api(mock_channel, limit=10)
    print(f"Result: {result}")
    
    # Verify it's in DB
    count = await get_message_count(TEST_CHANNEL_ID)
    print(f"New DB count: {count}")
    assert count == 2  # 1 initial + 1 fetched

async def main():
    try:
        await setup_test_db()
        await test_db_operations()
        await test_backfill_logic()
    finally:
        await teardown_test_db()

if __name__ == "__main__":
    asyncio.run(main())

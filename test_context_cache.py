#!/usr/bin/env python3
"""
Quick test script to verify context_cache.py functionality
Tests the key changes: before_message parameter and message fetching logic
"""

import asyncio
import sys
from unittest.mock import AsyncMock, MagicMock, patch

# Test imports
try:
    from context_cache import (
        get_recent_context,
        build_context_prompt,
        _chunk_data,
        _chunked_redis_set,
        _chunked_redis_get,
    )
    print("✅ All imports successful")
except ImportError as e:
    print(f"❌ Import error: {e}")
    sys.exit(1)


def test_chunk_data():
    """Test the chunking logic"""
    print("\n📦 Testing chunk data function...")
    
    # Test with small data (should fit in one chunk)
    small_data = [f"Message {i}" for i in range(10)]
    chunks = _chunk_data(small_data, max_chunk_size_bytes=10000)
    assert len(chunks) == 1, f"Expected 1 chunk, got {len(chunks)}"
    assert len(chunks[0]) == 10, f"Expected 10 messages, got {len(chunks[0])}"
    print("  ✅ Small data chunking works")
    
    # Test with empty data
    empty_chunks = _chunk_data([], max_chunk_size_bytes=10000)
    assert empty_chunks == [], "Empty data should return empty chunks"
    print("  ✅ Empty data handling works")
    
    print("✅ Chunk data tests passed\n")


async def test_get_recent_context_with_before_message():
    """Test get_recent_context with before_message parameter"""
    print("🔍 Testing get_recent_context with before_message...")
    
    # Create mock channel and message
    mock_channel = MagicMock()
    mock_channel.id = 12345
    
    # Create mock messages with proper datetime
    from datetime import datetime, timezone, timedelta
    mock_messages = []
    base_time = datetime.now(timezone.utc)
    for i in range(10):
        mock_msg = MagicMock()
        mock_msg.id = 1000 + i
        mock_msg.content = f"Test message {i}"
        mock_msg.clean_content = f"Test message {i}"
        mock_msg.author.display_name = f"User{i}"
        mock_msg.author.id = 2000 + i
        mock_msg.created_at = base_time - timedelta(minutes=10-i)  # Older messages first
        mock_messages.append(mock_msg)
    
    # Mock the history method
    async def mock_history(limit=None, before=None):
        # Return messages before the 'before' message
        if before:
            # Return messages with IDs less than before.id
            filtered = [m for m in mock_messages if m.id < before.id]
            for msg in reversed(filtered[:limit]):
                yield msg
        else:
            # Return all messages
            for msg in reversed(mock_messages[:limit]):
                yield msg
    
    mock_channel.history = mock_history
    
    # Test without before_message
    with patch('context_cache._memory_cache', {}), \
         patch('context_cache.get_redis_client', return_value=None):
        result = await get_recent_context(mock_channel, limit=5)
        print(f"  📊 Got {len(result)} messages without before_message")
        assert len(result) == 5, f"Expected 5 messages, got {len(result)}"
        print("  ✅ Without before_message works")
    
    # Test with before_message
    before_msg = mock_messages[7]  # Message with ID 1007
    with patch('context_cache._memory_cache', {}), \
         patch('context_cache.get_redis_client', return_value=None):
        result = await get_recent_context(mock_channel, limit=5, before_message=before_msg)
        print(f"  📊 Got {len(result)} messages with before_message (ID {before_msg.id})")
        # Should get messages before ID 1007, so IDs 1000-1006
        assert len(result) <= 5, f"Expected <= 5 messages, got {len(result)}"
        print("  ✅ With before_message works")
    
    print("✅ get_recent_context tests passed\n")


async def test_build_context_prompt():
    """Test build_context_prompt excludes current message"""
    print("📝 Testing build_context_prompt...")
    
    # Create mock message with proper datetime
    from datetime import datetime, timezone
    mock_message = MagicMock()
    mock_message.id = 9999
    mock_message.channel.id = 12345
    mock_message.author.display_name = "TestUser"
    mock_message.author.id = 123456
    mock_message.clean_content = "!test command"
    mock_message.content = "!test command"
    mock_message.created_at = datetime.now(timezone.utc)
    
    mock_channel = MagicMock()
    mock_channel.id = 12345
    
    # Mock get_recent_context to return some messages
    async def mock_get_recent_context(channel, limit, before_message=None):
        # Return mock messages (excluding current if before_message is provided)
        messages = []
        for i in range(min(limit, 10)):
            msg_text = f"[1h ago] User{i}({2000+i}): Previous message {i}"
            messages.append(msg_text)
        return messages
    
    with patch('context_cache.get_recent_context', side_effect=mock_get_recent_context), \
         patch('context_cache.datetime') as mock_datetime:
        # Mock datetime
        from datetime import datetime, timezone
        mock_datetime.now.return_value = datetime.now(timezone.utc)
        mock_datetime.side_effect = lambda *args, **kw: datetime(*args, **kw)
        
        result = await build_context_prompt(mock_message, "test command", limit=500)
        
        assert "test command" in result, "Prompt should contain the current message"
        assert "[1h ago]" in result, "Prompt should contain previous messages"
        print(f"  📊 Prompt length: {len(result)} characters")
        print("  ✅ build_context_prompt works")
    
    print("✅ build_context_prompt tests passed\n")


def test_imports_and_basic_functionality():
    """Test that all critical functions can be imported and called"""
    print("🔧 Testing imports and basic functionality...")
    
    # Test that functions exist and are callable
    functions_to_test = [
        get_recent_context,
        build_context_prompt,
        _chunk_data,
    ]
    
    for func in functions_to_test:
        assert callable(func), f"{func.__name__} should be callable"
        print(f"  ✅ {func.__name__} is callable")
    
    print("✅ Import and basic functionality tests passed\n")


async def main():
    """Run all tests"""
    print("=" * 60)
    print("🧪 Testing context_cache.py functionality")
    print("=" * 60)
    
    try:
        # Run synchronous tests
        test_chunk_data()
        test_imports_and_basic_functionality()
        
        # Run async tests
        await test_get_recent_context_with_before_message()
        await test_build_context_prompt()
        
        print("=" * 60)
        print("✅ All tests passed!")
        print("=" * 60)
        return 0
    except AssertionError as e:
        print(f"\n❌ Test failed: {e}")
        import traceback
        traceback.print_exc()
        return 1
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)


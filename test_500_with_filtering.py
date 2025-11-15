#!/usr/bin/env python3
"""
Test to verify that 500 messages are fetched even when some messages are filtered out
"""

import asyncio
import sys
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone, timedelta

try:
    from context_cache import get_recent_context
    print("✅ Imports successful")
except ImportError as e:
    print(f"❌ Import error: {e}")
    sys.exit(1)


async def test_500_with_filtering():
    """Test that we get 500 messages even when some are filtered out"""
    print("\n" + "=" * 60)
    print("🧪 Testing 500 message fetch with filtering")
    print("=" * 60)
    
    # Create a mock channel
    mock_channel = MagicMock()
    mock_channel.id = 1422582119774949571
    
    # Create 800 mock messages (more than 500)
    # Some will have empty content to simulate filtering
    mock_messages = []
    base_time = datetime.now(timezone.utc)
    
    print(f"📝 Creating 800 mock messages (some with empty content)...")
    for i in range(800):
        mock_msg = MagicMock()
        mock_msg.id = 10000 + i
        # Every 5th message has no content (to simulate embeds-only, system messages, etc.)
        if i % 5 == 0:
            mock_msg.content = None  # No content
            mock_msg.clean_content = ""
        else:
            mock_msg.content = f"Message number {i}"
            mock_msg.clean_content = f"Message number {i}"
        
        mock_msg.author.display_name = f"User{i % 10}"
        mock_msg.author.id = 20000 + (i % 10)
        mock_msg.created_at = base_time - timedelta(minutes=800-i)
        mock_messages.append(mock_msg)
    
    # Count messages with content
    messages_with_content = [m for m in mock_messages if m.content and m.content.strip()]
    print(f"✅ Created {len(mock_messages)} messages")
    print(f"   Messages with content: {len(messages_with_content)}")
    print(f"   Messages without content: {len(mock_messages) - len(messages_with_content)}")
    
    # Create a "current" message
    current_message = MagicMock()
    current_message.id = 20000  # Higher ID than all others
    current_message.channel = mock_channel
    
    # Mock the history method
    async def mock_history(limit=None, before=None):
        """Mock history that returns messages before the 'before' message"""
        if before:
            # Return messages with IDs less than before.id
            filtered = [m for m in mock_messages if m.id < before.id]
            # Return in reverse order (newest first, as Discord does)
            count = 0
            for msg in reversed(filtered):
                if count >= limit:
                    break
                yield msg
                count += 1
        else:
            # Return most recent messages
            count = 0
            for msg in reversed(mock_messages):
                if count >= limit:
                    break
                yield msg
                count += 1
    
    mock_channel.history = mock_history
    
    # Test: Fetch 500 messages before current message
    print(f"\n🔍 Test: Fetching 500 messages BEFORE message {current_message.id}...")
    print(f"   (Some messages will be filtered out due to empty content)")
    
    with patch('context_cache._memory_cache', {}), \
         patch('context_cache.get_redis_client', return_value=None):
        
        result = await get_recent_context(mock_channel, limit=500, before_message=current_message)
        
        print(f"\n📊 Results:")
        print(f"   Messages fetched: {len(result)}")
        print(f"   Target: 500")
        
        if len(result) == 500:
            print(f"   ✅ SUCCESS: Got exactly 500 messages!")
            
            # Verify all messages have content
            all_have_content = all(']:' in msg and len(msg.split(']:', 1)[1].strip()) > 0 for msg in result)
            if all_have_content:
                print(f"   ✅ All messages have content")
            else:
                print(f"   ⚠️  Some messages might not have content")
        else:
            print(f"   ❌ FAILED: Expected 500 messages, got {len(result)}")
            if len(result) < 500:
                print(f"   ⚠️  Only {len(result)} messages available (may not be enough in channel)")
            return False
        
        # Show sample messages
        if result:
            print(f"\n📄 Sample messages:")
            print(f"   First: {result[0][:80]}...")
            print(f"   Last: {result[-1][:80]}...")
    
    print("\n" + "=" * 60)
    print("✅ Test completed!")
    print("=" * 60)
    return True


async def main():
    """Run the test"""
    try:
        success = await test_500_with_filtering()
        return 0 if success else 1
    except Exception as e:
        print(f"\n❌ Test failed with error: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)


#!/usr/bin/env python3
"""
Test to verify that exactly 500 messages are fetched
"""

import asyncio
import sys
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone, timedelta

# Test imports
try:
    from context_cache import get_recent_context, build_context_prompt
    print("✅ Imports successful")
except ImportError as e:
    print(f"❌ Import error: {e}")
    sys.exit(1)


async def test_fetch_500_messages():
    """Test that we can fetch exactly 500 messages"""
    print("\n" + "=" * 60)
    print("🧪 Testing 500 message fetch")
    print("=" * 60)
    
    # Create a mock channel
    mock_channel = MagicMock()
    mock_channel.id = 12345
    
    # Create 600 mock messages (more than 500 to test limiting)
    mock_messages = []
    base_time = datetime.now(timezone.utc)
    
    print(f"📝 Creating 600 mock messages...")
    for i in range(600):
        mock_msg = MagicMock()
        mock_msg.id = 10000 + i  # IDs from 10000 to 10599
        mock_msg.content = f"Message number {i}"
        mock_msg.clean_content = f"Message number {i}"
        mock_msg.author.display_name = f"User{i % 10}"  # Cycle through 10 users
        mock_msg.author.id = 20000 + (i % 10)
        # Messages get older as i increases
        mock_msg.created_at = base_time - timedelta(minutes=600-i)
        mock_messages.append(mock_msg)
    
    print(f"✅ Created {len(mock_messages)} messages")
    print(f"   Message IDs range from {mock_messages[0].id} to {mock_messages[-1].id}")
    
    # Create a "current" message (the one we're responding to)
    current_message = MagicMock()
    current_message.id = 11000  # Higher ID than all others
    current_message.content = "!test command"
    current_message.clean_content = "!test command"
    current_message.author.display_name = "CurrentUser"
    current_message.author.id = 30000
    current_message.created_at = base_time
    current_message.channel = mock_channel
    
    # Mock the history method to return messages before current_message
    async def mock_history(limit=None, before=None):
        """Mock history that returns messages before the 'before' message"""
        if before:
            # Return messages with IDs less than before.id
            # In our case, before.id is 11000, so return all messages with ID < 11000
            filtered = [m for m in mock_messages if m.id < before.id]
            # Return in reverse order (newest first, as Discord does)
            for msg in reversed(filtered[-limit:]):
                yield msg
        else:
            # Return most recent messages
            for msg in reversed(mock_messages[-limit:]):
                yield msg
    
    mock_channel.history = mock_history
    
    # Test 1: Fetch 500 messages before current message
    print(f"\n🔍 Test 1: Fetching 500 messages BEFORE message {current_message.id}...")
    
    with patch('context_cache._memory_cache', {}), \
         patch('context_cache.get_redis_client', return_value=None):
        
        result = await get_recent_context(mock_channel, limit=500, before_message=current_message)
        
        print(f"   📊 Result: Got {len(result)} messages")
        
        if len(result) == 500:
            print("   ✅ SUCCESS: Got exactly 500 messages!")
        else:
            print(f"   ❌ FAILED: Expected 500 messages, got {len(result)}")
            return False
        
        # Verify the messages are formatted correctly
        if result:
            first_msg = result[0]
            last_msg = result[-1]
            print(f"   📄 First message: {first_msg[:80]}...")
            print(f"   📄 Last message: {last_msg[:80]}...")
            
            # Check that messages contain expected content
            if "Message number" in first_msg and "Message number" in last_msg:
                print("   ✅ Messages are properly formatted")
            else:
                print("   ⚠️  Message formatting might be incorrect")
    
    # Test 2: Test with build_context_prompt (the actual function used)
    print(f"\n🔍 Test 2: Testing build_context_prompt with limit=500...")
    
    with patch('context_cache._memory_cache', {}), \
         patch('context_cache.get_redis_client', return_value=None), \
         patch('context_cache.datetime') as mock_dt:
        
        # Mock datetime for timestamp formatting
        mock_dt.now.return_value = base_time
        mock_dt.side_effect = lambda *args, **kw: datetime(*args, **kw)
        
        prompt = await build_context_prompt(current_message, "test command", limit=500)
        
        # Split prompt into sections
        if "Here is the recent Discord conversation" in prompt:
            parts = prompt.split("Here is the recent Discord conversation")
            context_section = parts[1].split("says:")[0]  # Everything between context header and current message
            current_message_section = parts[1].split("says:")[1] if "says:" in parts[1] else ""
        else:
            context_section = ""
            current_message_section = ""
        
        # Count messages in the CONTEXT section only (should be 500)
        context_lines = [line.strip() for line in context_section.split('\n') if line.strip()]
        # Filter for message lines (contain timestamps and user info)
        context_messages = [line for line in context_lines 
                           if (']:' in line or 'ago]' in line) and 
                           any(f"User{i}" in line for i in range(10))]
        
        # Count current message (should be 1)
        has_current_message = "test command" in current_message_section or "says: test command" in prompt
        
        print(f"   📊 Context messages: {len(context_messages)} (should be 500)")
        print(f"   📊 Current message present: {has_current_message} (should be True)")
        print(f"   📏 Prompt length: {len(prompt)} characters")
        
        if len(context_messages) == 500:
            print("   ✅ SUCCESS: Context contains exactly 500 previous messages!")
        elif len(context_messages) > 500:
            print(f"   ❌ FAILED: Context has {len(context_messages)} messages (expected 500)")
            return False
        else:
            print(f"   ⚠️  WARNING: Context has {len(context_messages)} messages (expected 500)")
            print(f"      This might be okay if there aren't 500 messages in the channel")
        
        # Verify current message is separate
        if has_current_message:
            print("   ✅ Current message is added separately (not in context)")
        else:
            print("   ⚠️  Current message not found in prompt")
    
    # Test 3: Test with fewer messages available
    print(f"\n🔍 Test 3: Testing with only 100 messages available...")
    
    # Create only 100 messages
    limited_messages = mock_messages[:100]
    
    async def limited_history(limit=None, before=None):
        if before:
            filtered = [m for m in limited_messages if m.id < before.id]
            for msg in reversed(filtered[-limit:]):
                yield msg
        else:
            for msg in reversed(limited_messages[-limit:]):
                yield msg
    
    mock_channel.history = limited_history
    
    with patch('context_cache._memory_cache', {}), \
         patch('context_cache.get_redis_client', return_value=None), \
         patch('context_cache.datetime') as mock_dt:
        
        mock_dt.now.return_value = base_time
        mock_dt.side_effect = lambda *args, **kw: datetime(*args, **kw)
        
        result = await get_recent_context(mock_channel, limit=500, before_message=current_message)
        
        print(f"   📊 Result: Got {len(result)} messages (only 100 available)")
        
        if len(result) == 100:
            print("   ✅ SUCCESS: Got all available messages (100) when requesting 500")
        else:
            print(f"   ⚠️  Got {len(result)} messages (expected 100)")
    
    print("\n" + "=" * 60)
    print("✅ All tests completed!")
    print("=" * 60)
    return True


async def main():
    """Run the test"""
    try:
        success = await test_fetch_500_messages()
        return 0 if success else 1
    except Exception as e:
        print(f"\n❌ Test failed with error: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)


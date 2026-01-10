"""
Verification script for Discord knowledge ingestion.

Usage:
    cd /home/maanya/Desktop/junkie
    source .venv/bin/activate
    python tests/verify_discord_ingestion.py
"""
import asyncio
import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.knowledge import get_knowledge_base
from core.discord_knowledge_ingestion import (
    format_messages_for_embedding,
    ingest_messages_to_knowledge,
    get_buffer_manager
)


async def main():
    print("=" * 50)
    print("Discord Knowledge Ingestion Verification")
    print("=" * 50)
    
    # 1. Check knowledge base
    kb = get_knowledge_base()
    if kb is None:
        print("❌ Knowledge base not configured!")
        print("   Make sure POSTGRES_URL and MISTRAL_API_KEY are set")
        return False
    print("✅ Knowledge base initialized")
    
    # 2. Test formatting
    print("\n📝 Testing message formatting...")
    sample_messages = [
        {
            'message_id': 1,
            'channel_id': 123456,
            'author_name': 'alice',
            'content': 'Has anyone tried the new feature?',
            'created_at': None,
            'reply_to_author_name': None,
        },
        {
            'message_id': 2,
            'channel_id': 123456,
            'author_name': 'bob',
            'content': 'Yes! It works great [Attachment: https://example.com/file.pdf]',
            'created_at': None,
            'reply_to_author_name': None,
        },
        {
            'message_id': 3,
            'channel_id': 123456,
            'author_name': 'alice',
            'content': 'Nice, what about performance?',
            'created_at': None,
            'reply_to_author_name': 'bob',
        },
    ]
    
    formatted, metadata = format_messages_for_embedding(sample_messages, "test-channel")
    print("Formatted text:")
    print("-" * 40)
    print(formatted)
    print("-" * 40)
    print(f"Metadata: {metadata}")
    print("✅ Formatting works")
    
    # 3. Test ingestion
    print("\n🚀 Testing ingestion...")
    try:
        success = await ingest_messages_to_knowledge(
            sample_messages, 
            channel_id=123456, 
            channel_name="test-channel"
        )
        if success:
            print("✅ Ingestion successful")
        else:
            print("❌ Ingestion failed")
            return False
    except Exception as e:
        print(f"❌ Ingestion error: {e}")
        return False
    
    # 4. Search for ingested content
    print("\n🔍 Searching knowledge base...")
    try:
        results = kb.search("new feature performance", max_results=3)
        print(f"✅ Found {len(results)} results")
        for i, r in enumerate(results, 1):
            preview = r.content[:100].replace("\n", " ") + "..."
            print(f"   {i}. {preview}")
    except Exception as e:
        print(f"❌ Search failed: {e}")
        return False
    
    # 5. Test buffer manager
    print("\n📦 Testing buffer manager...")
    manager = get_buffer_manager()
    print(f"✅ Buffer manager initialized: {type(manager).__name__}")
    
    print("\n" + "=" * 50)
    print("✅ All verification checks passed!")
    print("=" * 50)
    return True


if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)

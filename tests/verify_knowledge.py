"""
Verification script for the knowledge base.

Usage:
    cd /home/maanya/Desktop/junkie
    source .venv/bin/activate
    python tests/verify_knowledge.py
"""
import asyncio
import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.knowledge import get_knowledge_base


async def main():
    print("=" * 50)
    print("Knowledge Base Verification")
    print("=" * 50)
    
    # Get knowledge base
    kb = get_knowledge_base()
    if kb is None:
        print("❌ Knowledge base not configured!")
        print("   Make sure POSTGRES_URL and MISTRAL_API_KEY are set")
        return False
    
    print("✅ Knowledge base initialized")
    print(f"   Name: {kb.name}")
    print(f"   Max results: {kb.max_results}")
    
    # Add test content
    print("\n📝 Adding test content...")
    try:
        await kb.add_content_async(
            text_content="""
            Agno is a Python framework for building multi-agent systems.
            Key features include: Teams, Knowledge (RAG), Memory, Skills, and Reasoning.
            The bot uses MistralEmbedder for embeddings and PgVector for hybrid search.
            This is test content added by verify_knowledge.py.
            """,
            metadata={"topic": "agno", "type": "test", "source": "verification"}
        )
        print("✅ Test content added")
    except Exception as e:
        print(f"❌ Failed to add content: {e}")
        return False
    
    # Search
    print("\n🔍 Searching knowledge base...")
    try:
        results = kb.search("What is Agno?", max_results=3)
        print(f"✅ Found {len(results)} results:")
        for i, r in enumerate(results, 1):
            content_preview = r.content[:80].replace("\n", " ") + "..."
            print(f"   {i}. {content_preview}")
    except Exception as e:
        print(f"❌ Search failed: {e}")
        return False
    
    print("\n" + "=" * 50)
    print("✅ Knowledge base is working!")
    print("=" * 50)
    return True


if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)

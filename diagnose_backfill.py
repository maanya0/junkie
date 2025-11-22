#!/usr/bin/env python3
"""
Diagnostic script to check backfill state and Discord channel message counts.
"""
import asyncio
import asyncpg
import os
import discord
from dotenv import load_dotenv

load_dotenv()

async def diagnose_backfill():
    """Check database state and compare with Discord."""
    
    # Connect to database
    conn = await asyncpg.connect(os.getenv("POSTGRES_URL"))
    
    try:
        # Get channels with their message counts
        rows = await conn.fetch("""
            SELECT 
                channel_id,
                COUNT(*) as db_count,
                MIN(created_at) as oldest_msg_time,
                MAX(created_at) as newest_msg_time,
                MIN(message_id) as oldest_msg_id,
                MAX(message_id) as newest_msg_id
            FROM messages
            GROUP BY channel_id
            ORDER BY db_count DESC
            LIMIT 10
        """)
        
        print("\n" + "="*80)
        print("Top 10 Channels by Message Count in Database:")
        print("="*80)
        print(f"{'Channel ID':<20} {'Count':<10} {'Oldest Time':<25} {'Newest Time':<25}")
        print("-"*80)
        
        for row in rows:
            print(f"{row['channel_id']:<20} {row['db_count']:<10} {str(row['oldest_msg_time']):<25} {str(row['newest_msg_time']):<25}")
        
        # Check backfill status
        status_rows = await conn.fetch("""
            SELECT cs.channel_id, cs.is_fully_backfilled, COUNT(m.message_id) as msg_count
            FROM channel_status cs
            LEFT JOIN messages m ON cs.channel_id = m.channel_id
            WHERE cs.is_fully_backfilled = TRUE
            GROUP BY cs.channel_id, cs.is_fully_backfilled
            HAVING COUNT(m.message_id) < 1000
            ORDER BY msg_count DESC
        """)
        
        if status_rows:
            print("\n" + "="*80)
            print("⚠️  Channels marked 'fully backfilled' with < 1000 messages:")
            print("="*80)
            for row in status_rows:
                print(f"Channel {row['channel_id']}: {row['msg_count']} messages (marked as complete)")
        
        print("\n" + "="*80)
        print("✓ Database diagnostic complete")
        print("="*80)
        
    finally:
        await conn.close()

async def test_discord_fetch():
    """Test fetching from Discord to see if API is working."""
    intents = discord.Intents.default()
    intents.message_content = True
    client = discord.Client(intents=intents)
    
    @client.event
    async def on_ready():
        print(f"\n✓ Connected to Discord as {client.user}")
        
        # Try to fetch a problematic channel
        # Replace with actual channel ID from logs (e.g., "Normal people": 1387826699760308244)
        test_channel_id = 1387826699760308244
        
        try:
            channel = client.get_channel(test_channel_id)
            if not channel:
                print(f"❌ Cannot access channel {test_channel_id}")
                await client.close()
                return
            
            print(f"\n✓ Found channel: {channel.name}")
            
            # Try to fetch latest messages
            print("\nFetching latest 10 messages...")
            messages = []
            async for m in channel.history(limit=10):
                messages.append(m)
            
            print(f"✓ Fetched {len(messages)} latest messages")
            if messages:
                print(f"  Newest: ID {messages[0].id} at {messages[0].created_at}")
                print(f"  Oldest: ID {messages[-1].id} at {messages[-1].created_at}")
                
                # Now try fetching BEFORE the oldest
                print(f"\nFetching 10 messages BEFORE ID {messages[-1].id}...")
                older_msgs = []
                async for m in channel.history(limit=10, before=discord.Object(id=messages[-1].id)):
                    older_msgs.append(m)
                
                print(f"✓ Fetched {len(older_msgs)} older messages")
                if older_msgs:
                    print(f"  These messages are from: {older_msgs[0].created_at} to {older_msgs[-1].created_at}")
                else:
                    print(f"  ⚠️  No messages found before ID {messages[-1].id}")
                    print(f"  This suggests the channel only has {len(messages)} discoverable messages")
        
        except Exception as e:
            print(f"❌ Error: {e}")
        
        await client.close()
    
    await client.start(os.getenv("DISCORD_TOKEN"))

if __name__ == "__main__":
    print("Running database diagnostic...")
    asyncio.run(diagnose_backfill())
    
    print("\n\nTesting Discord API fetch...")
    print("(This will connect to Discord - press Ctrl+C to skip)")
    try:
        asyncio.run(test_discord_fetch())
    except KeyboardInterrupt:
        print("\nSkipped Discord test")

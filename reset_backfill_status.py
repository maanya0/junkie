#!/usr/bin/env python3
"""
Reset backfill status for channels that were incorrectly marked as fully backfilled.
Run this script to fix channels that have few messages but are marked as complete.
"""
import asyncio
import asyncpg
import os
from dotenv import load_dotenv

load_dotenv()

async def reset_backfill_status():
    """Reset fully_backfilled status for channels that clearly aren't full."""
    conn = await asyncpg.connect(os.getenv("POSTGRES_URL"))
    
    try:
        # Reset channels that are marked as fully backfilled but have < 45K messages (90% of 50K target)
        result = await conn.execute("""
            UPDATE channel_status
            SET is_fully_backfilled = FALSE, 
                last_updated = CURRENT_TIMESTAMP
            WHERE is_fully_backfilled = TRUE
            AND channel_id IN (
                SELECT channel_id 
                FROM messages 
                GROUP BY channel_id 
                HAVING COUNT(*) < 45000
            )
        """)
        print(f"✓ Reset backfill status: {result}")
        
        # Show current status
        rows = await conn.fetch("""
            SELECT 
                cs.channel_id, 
                cs.is_fully_backfilled, 
                COALESCE(COUNT(m.message_id), 0) as message_count
            FROM channel_status cs
            LEFT JOIN messages m ON cs.channel_id = m.channel_id
            GROUP BY cs.channel_id, cs.is_fully_backfilled
            ORDER BY message_count DESC
            LIMIT 20
        """)
        
        print("\nTop 20 channels by message count:")
        print(f"{'Channel ID':<20} {'Fully Backfilled':<20} {'Message Count':<15}")
        print("=" * 60)
        for row in rows:
            print(f"{row['channel_id']:<20} {str(row['is_fully_backfilled']):<20} {row['message_count']:<15}")
        
        print(f"\n✓ Complete! Restart your bot to resume backfilling.")
        
    finally:
        await conn.close()

if __name__ == "__main__":
    asyncio.run(reset_backfill_status())

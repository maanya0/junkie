
import asyncio
import os
import logging
from core.database import init_db, close_db, pool
from core.config import POSTGRES_URL

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def run_migration():
    logger.info("Starting schema migration...")
    try:
        await init_db()
        
        async with pool.acquire() as conn:
            # Check if reply_to_message_id exists in messages table
            exists = await conn.fetchval("""
                SELECT EXISTS (
                    SELECT FROM information_schema.columns 
                    WHERE table_name = 'messages' AND column_name = 'reply_to_message_id'
                );
            """)
            
            if exists:
                logger.info("✅ 'messages' table has 'reply_to_message_id' column.")
            else:
                logger.error("❌ 'messages' table MISSING 'reply_to_message_id' column!")
                
            # Check count
            count = await conn.fetchval("SELECT COUNT(*) FROM messages")
            logger.info(f"📊 messages row count: {count}")
                
    except Exception as e:
        logger.error(f"Migration failed: {e}")
    finally:
        await close_db()

if __name__ == "__main__":
    asyncio.run(run_migration())

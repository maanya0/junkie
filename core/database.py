import asyncpg
import logging
from typing import List, Optional, Dict
from datetime import datetime
from core.config import POSTGRES_URL

logger = logging.getLogger(__name__)

pool: Optional[asyncpg.Pool] = None

async def init_db():
    """Initialize the database connection pool."""
    global pool
    try:
        pool = await asyncpg.create_pool(POSTGRES_URL)
        logger.info("Database connection pool created.")
        await create_schema()
    except Exception as e:
        logger.error(f"Failed to initialize database: {e}")
        raise

async def close_db():
    """Close the database connection pool."""
    global pool
    if pool:
        await pool.close()
        logger.info("Database connection pool closed.")

async def create_schema():
    """Create the necessary database schema."""
    if not pool:
        return
    
    async with pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                message_id BIGINT PRIMARY KEY,
                channel_id BIGINT NOT NULL,
                author_id BIGINT NOT NULL,
                author_name TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TIMESTAMP WITH TIME ZONE NOT NULL,
                timestamp_str TEXT NOT NULL
            );
            
            CREATE INDEX IF NOT EXISTS idx_messages_channel_created 
            ON messages (channel_id, created_at DESC);
        """)
        logger.info("Database schema initialized.")

async def store_message(
    message_id: int,
    channel_id: int,
    author_id: int,
    author_name: str,
    content: str,
    created_at: datetime,
    timestamp_str: str
):
    """Store or update a message in the database."""
    if not pool:
        return

    try:
        async with pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO messages (message_id, channel_id, author_id, author_name, content, created_at, timestamp_str)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                ON CONFLICT (message_id) DO UPDATE SET
                    content = EXCLUDED.content,
                    timestamp_str = EXCLUDED.timestamp_str;
            """, message_id, channel_id, author_id, author_name, content, created_at, timestamp_str)
    except Exception as e:
        logger.error(f"Failed to store message {message_id}: {e}")

async def get_messages(channel_id: int, limit: int = 2000) -> List[Dict]:
    """Retrieve recent messages for a channel."""
    if not pool:
        return []

    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT * FROM messages 
                WHERE channel_id = $1 
                ORDER BY created_at DESC 
                LIMIT $2
            """, channel_id, limit)
            
            # Return reversed (chronological)
            return list(reversed([dict(row) for row in rows]))
    except Exception as e:
        logger.error(f"Failed to get messages for channel {channel_id}: {e}")
        return []

async def get_message_count(channel_id: int) -> int:
    """Get the number of messages stored for a channel."""
    if not pool:
        return 0

    try:
        async with pool.acquire() as conn:
            return await conn.fetchval("""
                SELECT COUNT(*) FROM messages WHERE channel_id = $1
            """, channel_id)
    except Exception as e:
        logger.error(f"Failed to count messages for channel {channel_id}: {e}")
        return 0

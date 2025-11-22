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
            
            -- Optimized index for fetching recent messages (DESC order)
            CREATE INDEX IF NOT EXISTS idx_messages_channel_created
            ON messages (channel_id, created_at DESC);
            
            -- Index for message_id lookups (upserts)
            CREATE INDEX IF NOT EXISTS idx_messages_message_id
            ON messages (message_id);
            
            -- Drop old ASC index if it exists
            DROP INDEX IF EXISTS idx_messages_channel_created_asc;

            CREATE TABLE IF NOT EXISTS channel_status (
                channel_id BIGINT PRIMARY KEY,
                is_fully_backfilled BOOLEAN DEFAULT FALSE,
                last_updated TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            );
        """)
        logger.info("Database schema initialized with optimized indexes.")

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
        raise  # Propagate error to caller instead of silently swallowing

async def delete_message(message_id: int):
    """Delete a message from the database."""
    if not pool:
        return

    try:
        async with pool.acquire() as conn:
            await conn.execute("""
                DELETE FROM messages WHERE message_id = $1
            """, message_id)
            logger.debug(f"Deleted message {message_id} from database")
    except Exception as e:
        logger.error(f"Failed to delete message {message_id}: {e}")

async def get_messages(channel_id: int, limit: int = 2000) -> List[Dict]:
    """Retrieve the most recent messages for a channel in chronological order."""
    if not pool:
        return []

    try:
        async with pool.acquire() as conn:
            # ORDER BY DESC to get NEWEST messages first, then reverse to chronological
            rows = await conn.fetch("""
                SELECT message_id, channel_id, author_id, author_name, content, created_at
                FROM messages 
                WHERE channel_id = $1 
                ORDER BY created_at DESC 
                LIMIT $2
            """, channel_id, limit)
            
            # Reverse to chronological order (oldest to newest) for display
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

async def get_latest_message_id(channel_id: int) -> Optional[int]:
    """Get the ID of the newest message stored for a channel."""
    if not pool:
        return None

    try:
        async with pool.acquire() as conn:
            return await conn.fetchval("""
                SELECT message_id FROM messages 
                WHERE channel_id = $1 
                ORDER BY created_at DESC 
                LIMIT 1
            """, channel_id)
    except Exception as e:
        logger.error(f"Failed to get latest message ID for channel {channel_id}: {e}")
        return None

async def get_oldest_message_id(channel_id: int) -> Optional[int]:
    """Get the ID of the oldest message stored for a channel."""
    if not pool:
        return None

    try:
        async with pool.acquire() as conn:
            return await conn.fetchval("""
                SELECT message_id FROM messages 
                WHERE channel_id = $1 
                ORDER BY created_at ASC 
                LIMIT 1
            """, channel_id)
    except Exception as e:
        logger.error(f"Failed to get oldest message ID for channel {channel_id}: {e}")
        return None

async def is_channel_fully_backfilled(channel_id: int) -> bool:
    """Check if a channel is marked as fully backfilled."""
    if not pool:
        return False
    try:
        async with pool.acquire() as conn:
            return await conn.fetchval("""
                SELECT is_fully_backfilled FROM channel_status WHERE channel_id = $1
            """, channel_id) or False
    except Exception as e:
        logger.error(f"Failed to check backfill status for {channel_id}: {e}")
        return False

async def mark_channel_fully_backfilled(channel_id: int, status: bool = True):
    """Mark a channel as fully backfilled."""
    if not pool:
        return
    try:
        async with pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO channel_status (channel_id, is_fully_backfilled, last_updated)
                VALUES ($1, $2, CURRENT_TIMESTAMP)
                ON CONFLICT (channel_id) DO UPDATE SET
                    is_fully_backfilled = EXCLUDED.is_fully_backfilled,
                    last_updated = EXCLUDED.last_updated;
            """, channel_id, status)
    except Exception as e:
        logger.error(f"Failed to mark backfill status for {channel_id}: {e}")

import asyncpg
import logging
from typing import List, Optional, Dict
from datetime import datetime
from core.config import POSTGRES_URL

logger = logging.getLogger(__name__)

pool: Optional[asyncpg.Pool] = None

async def init_db():
    """Initialize the database connection pool and trigger infrastructure."""
    global pool
    try:
        pool = await asyncpg.create_pool(POSTGRES_URL)
        logger.info("Database connection pool created.")
        await create_schema()
        
        # Initialize trigger infrastructure
        from core.triggers.store import init_trigger_store
        from core.triggers.service import init_trigger_service
        
        trigger_store = init_trigger_store(pool)
        init_trigger_service(trigger_store)
        logger.info("Trigger infrastructure initialized.")
        
    except Exception as e:
        logger.error(f"Failed to initialize database: {e}")
        raise


def get_pool():
    """Get the database connection pool."""
    return pool

async def close_db():
    """Close the database connection pool."""
    global pool
    if pool:
        await pool.close()
        pool = None
        logger.info("Database connection pool closed.")

async def create_schema():
    """Create the necessary database schema."""
    if not pool:
        return
    
    async with pool.acquire() as conn:
        # Create tables if they don't exist (base schema)
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
            
            CREATE TABLE IF NOT EXISTS channel_status (
                channel_id BIGINT PRIMARY KEY,
                is_fully_backfilled BOOLEAN DEFAULT FALSE,
                last_updated TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            );
            
            -- Triggers table for scheduled tasks and reminders
            CREATE TABLE IF NOT EXISTS triggers (
                id SERIAL PRIMARY KEY,
                user_id TEXT NOT NULL,
                channel_id TEXT NOT NULL,
                agent_id TEXT NOT NULL,
                payload TEXT NOT NULL,
                start_time TIMESTAMP WITH TIME ZONE,
                next_trigger TIMESTAMP WITH TIME ZONE,
                recurrence_rule TEXT,
                timezone TEXT NOT NULL DEFAULT 'UTC',
                status TEXT NOT NULL DEFAULT 'active',
                last_error TEXT,
                created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
            );
        """)
        
        # Add new columns to existing tables (migration for smart sync + reply support)
        # These use IF NOT EXISTS pattern for safe migration
        migration_queries = [
            # Add content_hash column to messages
            "ALTER TABLE messages ADD COLUMN IF NOT EXISTS content_hash TEXT",
            # Add reply tracking columns to messages
            "ALTER TABLE messages ADD COLUMN IF NOT EXISTS reply_to_message_id BIGINT",
            "ALTER TABLE messages ADD COLUMN IF NOT EXISTS reply_to_author_id BIGINT",
            "ALTER TABLE messages ADD COLUMN IF NOT EXISTS reply_to_author_name TEXT",
            "ALTER TABLE messages ADD COLUMN IF NOT EXISTS reply_to_content TEXT",
            # Add sync tracking columns to channel_status
            "ALTER TABLE channel_status ADD COLUMN IF NOT EXISTS last_synced_at TIMESTAMP WITH TIME ZONE",
            "ALTER TABLE channel_status ADD COLUMN IF NOT EXISTS last_message_at TIMESTAMP WITH TIME ZONE",
        ]
        
        for query in migration_queries:
            try:
                await conn.execute(query)
            except Exception as e:
                # Column might already exist or other non-critical error
                logger.debug(f"Migration query note: {e}")
        
        
        # Create indexes INDIVIDUALLY (each can reference new columns safely now)
        index_queries = [
            # Basic indexes
            "CREATE INDEX IF NOT EXISTS idx_messages_channel_created ON messages (channel_id, created_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_messages_message_id ON messages (message_id)",
            # Hash-based change detection index (depends on content_hash column)
            "CREATE INDEX IF NOT EXISTS idx_messages_channel_hash ON messages (channel_id, message_id, content_hash)",
            # Reply hierarchy index
            "CREATE INDEX IF NOT EXISTS idx_messages_reply_to on messages (reply_to_message_id) WHERE reply_to_message_id IS NOT NULL",
            # Drop old ASC index
            "DROP INDEX IF EXISTS idx_messages_channel_created_asc",
            # Activity ordering index
            "CREATE INDEX IF NOT EXISTS idx_channel_status_activity ON channel_status (last_message_at DESC NULLS LAST)",
            # Trigger indexes
            "CREATE INDEX IF NOT EXISTS idx_triggers_user_status ON triggers (user_id, status)",
        ]
        
        for query in index_queries:
            try:
                await conn.execute(query)
            except Exception as e:
                logger.debug(f"Index creation note: {e}")
        
        # Partial index needs special handling
        try:
            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_triggers_next_trigger 
                ON triggers (next_trigger) 
                WHERE status = 'active' AND next_trigger IS NOT NULL
            """)
        except Exception as e:
            logger.debug(f"Partial index creation note: {e}")
        
        logger.info("Database schema initialized with smart sync and reply support.")

async def store_message(
    message_id: int,
    channel_id: int,
    author_id: int,
    author_name: str,
    content: str,
    created_at: datetime,
    timestamp_str: str,
    content_hash: str = None,
    reply_to_message_id: int = None,
    reply_to_author_id: int = None,
    reply_to_author_name: str = None,
    reply_to_content: str = None
):
    """Store or update a message in the database."""
    if not pool:
        return

    try:
        async with pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO messages (
                    message_id, channel_id, author_id, author_name, content, 
                    created_at, timestamp_str, content_hash,
                    reply_to_message_id, reply_to_author_id, reply_to_author_name, reply_to_content
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
                ON CONFLICT (message_id) DO UPDATE SET
                    content = EXCLUDED.content,
                    timestamp_str = EXCLUDED.timestamp_str,
                    content_hash = EXCLUDED.content_hash;
            """, message_id, channel_id, author_id, author_name, content, 
                created_at, timestamp_str, content_hash,
                reply_to_message_id, reply_to_author_id, reply_to_author_name, reply_to_content)
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
                SELECT 
                    message_id, channel_id, author_id, author_name, content, 
                    created_at, reply_to_message_id, reply_to_author_id, 
                    reply_to_author_name, reply_to_content
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


# ──────────────────────────────────────────────
# Smart Sync Support Functions
# ──────────────────────────────────────────────

async def get_message_hashes(channel_id: int, message_ids: List[int]) -> Dict[int, str]:
    """Get content hashes for specific messages in a channel."""
    if not pool or not message_ids:
        return {}
    
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT message_id, content_hash FROM messages 
                WHERE channel_id = $1 AND message_id = ANY($2)
            """, channel_id, message_ids)
            return {row['message_id']: row['content_hash'] for row in rows if row['content_hash']}
    except Exception as e:
        logger.error(f"Failed to get message hashes for channel {channel_id}: {e}")
        return {}


async def batch_store_messages(messages: List[Dict]) -> int:
    """
    Batch store/update multiple messages efficiently.
    
    Each message dict should have:
        message_id, channel_id, author_id, author_name, content, 
        content_hash, created_at, timestamp_str
    
    Returns the number of messages stored.
    """
    if not pool or not messages:
        return 0
    
    try:
        async with pool.acquire() as conn:
            # Prepare data for batch insert
            values = [
                (
                    m['message_id'], m['channel_id'], m['author_id'], 
                    m['author_name'], m['content'], m.get('content_hash'),
                    m['created_at'], m['timestamp_str']
                )
                for m in messages
            ]
            
            # Use executemany for batch operation with upsert
            await conn.executemany("""
                INSERT INTO messages (message_id, channel_id, author_id, author_name, content, content_hash, created_at, timestamp_str)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                ON CONFLICT (message_id) DO UPDATE SET
                    content = EXCLUDED.content,
                    content_hash = EXCLUDED.content_hash,
                    timestamp_str = EXCLUDED.timestamp_str;
            """, values)
            
            return len(values)
    except Exception as e:
        logger.error(f"Failed to batch store messages: {e}")
        return 0


async def batch_store_messages_with_replies(messages: List[Dict]) -> int:
    """
    Batch store messages with full reply tracking (optimized for fetch_and_cache_from_api).
    
    Each message dict should have:
        message_id, channel_id, author_id, author_name, content, 
        content_hash, created_at, timestamp_str,
        reply_to_message_id, reply_to_author_id, reply_to_author_name, reply_to_content
    
    Returns the number of messages stored.
    """
    if not pool or not messages:
        return 0
    
    try:
        async with pool.acquire() as conn:
            # Prepare data for batch insert with all columns including replies
            values = [
                (
                    m['message_id'], m['channel_id'], m['author_id'], 
                    m['author_name'], m['content'], m.get('content_hash'),
                    m['created_at'], m['timestamp_str'],
                    m.get('reply_to_message_id'), m.get('reply_to_author_id'),
                    m.get('reply_to_author_name'), m.get('reply_to_content')
                )
                for m in messages
            ]
            
            # Use executemany for batch operation with upsert
            await conn.executemany("""
                INSERT INTO messages (
                    message_id, channel_id, author_id, author_name, content, content_hash,
                    created_at, timestamp_str,
                    reply_to_message_id, reply_to_author_id, reply_to_author_name, reply_to_content
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
                ON CONFLICT (message_id) DO UPDATE SET
                    content = EXCLUDED.content,
                    content_hash = EXCLUDED.content_hash,
                    timestamp_str = EXCLUDED.timestamp_str;
            """, values)
            
            logger.debug(f"Batch stored {len(values)} messages with replies")
            return len(values)
    except Exception as e:
        logger.error(f"Failed to batch store messages with replies: {e}")
        return 0


async def batch_delete_messages(message_ids: List[int]) -> int:
    """Batch delete multiple messages efficiently."""
    if not pool or not message_ids:
        return 0
    
    try:
        async with pool.acquire() as conn:
            result = await conn.execute("""
                DELETE FROM messages WHERE message_id = ANY($1)
            """, message_ids)
            # Extract count from result string like "DELETE 5"
            count = int(result.split()[-1]) if result else 0
            logger.debug(f"Batch deleted {count} messages")
            return count
    except Exception as e:
        logger.error(f"Failed to batch delete messages: {e}")
        return 0


async def get_channel_last_sync(channel_id: int) -> Optional[datetime]:
    """Get the last sync timestamp for a channel."""
    if not pool:
        return None
    
    try:
        async with pool.acquire() as conn:
            return await conn.fetchval("""
                SELECT last_synced_at FROM channel_status WHERE channel_id = $1
            """, channel_id)
    except Exception as e:
        logger.error(f"Failed to get last sync for channel {channel_id}: {e}")
        return None


async def update_channel_sync_status(channel_id: int, last_synced_at: datetime, last_message_at: datetime = None):
    """Update channel sync status after a sync operation."""
    if not pool:
        return
    
    try:
        async with pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO channel_status (channel_id, last_synced_at, last_message_at, last_updated)
                VALUES ($1, $2, $3, CURRENT_TIMESTAMP)
                ON CONFLICT (channel_id) DO UPDATE SET
                    last_synced_at = EXCLUDED.last_synced_at,
                    last_message_at = COALESCE(EXCLUDED.last_message_at, channel_status.last_message_at),
                    last_updated = EXCLUDED.last_updated;
            """, channel_id, last_synced_at, last_message_at)
    except Exception as e:
        logger.error(f"Failed to update sync status for channel {channel_id}: {e}")


async def get_channels_by_activity() -> List[Dict]:
    """Get all channels ordered by recent activity (most active first)."""
    if not pool:
        return []
    
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT channel_id, last_synced_at, last_message_at, is_fully_backfilled
                FROM channel_status 
                ORDER BY last_message_at DESC NULLS LAST
            """)
            return [dict(row) for row in rows]
    except Exception as e:
        logger.error(f"Failed to get channels by activity: {e}")
        return []


async def get_db_message_ids(channel_id: int, limit: int = 500) -> set:
    """Get message IDs from database for a channel (for deletion detection)."""
    if not pool:
        return set()
    
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT message_id FROM messages 
                WHERE channel_id = $1 
                ORDER BY created_at DESC 
                LIMIT $2
            """, channel_id, limit)
            return {row['message_id'] for row in rows}
    except Exception as e:
        logger.error(f"Failed to get message IDs for channel {channel_id}: {e}")
        return set()


# ──────────────────────────────────────────────
# messages_v2 Functions (Reply Hierarchy Support)
# ──────────────────────────────────────────────

async def store_message_v2(
    message_id: int,
    channel_id: int,
    author_id: int,
    author_name: str,
    content: str,
    created_at: datetime,
    timestamp_str: str,
    content_hash: str = None,
    reply_to_message_id: int = None,
    reply_to_author_id: int = None,
    reply_to_author_name: str = None,
    reply_to_content: str = None
):
    """Store or update a message in messages_v2 with reply tracking."""
    if not pool:
        return

    try:
        async with pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO messages_v2 (
                    message_id, channel_id, author_id, author_name, content, 
                    created_at, timestamp_str, content_hash,
                    reply_to_message_id, reply_to_author_id, reply_to_author_name, reply_to_content
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
                ON CONFLICT (message_id) DO UPDATE SET
                    content = EXCLUDED.content,
                    timestamp_str = EXCLUDED.timestamp_str,
                    content_hash = EXCLUDED.content_hash;
            """, message_id, channel_id, author_id, author_name, content, 
                created_at, timestamp_str, content_hash,
                reply_to_message_id, reply_to_author_id, reply_to_author_name, reply_to_content)
    except Exception as e:
        logger.error(f"Failed to store message_v2 {message_id}: {e}")
        raise


async def get_messages_v2(channel_id: int, limit: int = 100) -> List[Dict]:
    """
    Retrieve messages with reply info for a channel in chronological order.
    Returns structured data for JSON context building.
    """
    if not pool:
        return []

    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT 
                    message_id, channel_id, author_id, author_name, content, 
                    created_at, reply_to_message_id, reply_to_author_id, 
                    reply_to_author_name, reply_to_content
                FROM messages_v2 
                WHERE channel_id = $1 
                ORDER BY created_at DESC 
                LIMIT $2
            """, channel_id, limit)
            
            # Reverse to chronological order
            return list(reversed([dict(row) for row in rows]))
    except Exception as e:
        logger.error(f"Failed to get messages_v2 for channel {channel_id}: {e}")
        return []


async def delete_message_v2(message_id: int):
    """Delete a message from messages_v2."""
    if not pool:
        return

    try:
        async with pool.acquire() as conn:
            await conn.execute("DELETE FROM messages_v2 WHERE message_id = $1", message_id)
    except Exception as e:
        logger.error(f"Failed to delete message_v2 {message_id}: {e}")



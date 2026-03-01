import asyncpg
import logging
from typing import List, Optional, Dict, Set
from datetime import datetime
from core.config import POSTGRES_URL

logger = logging.getLogger(__name__)

pool: Optional[asyncpg.Pool] = None


async def init_db():
    """
    Initialize the module-level database connection pool and ensure the required schema exists.
    
    Sets the module-level `pool` variable to a new asyncpg connection pool and creates any missing tables/indexes by invoking schema creation.
    
    Raises:
        Exception: If creating the connection pool or initializing the schema fails; the original exception is propagated.
    """
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
        pool = None
        logger.info("Database connection pool closed.")


async def create_schema():
    """
    Ensure required database tables and indexes exist.
    
    Creates the following tables if missing: `messages`, `channel_status`, `access_control_settings`, `access_control_users`, and `bot_admins`, and creates optimized indexes for querying recent messages and message_id lookups. No action is taken if the module-level database pool is not initialized.
    """
    if not pool:
        return

    async with pool.acquire() as conn:
        await conn.execute(
            """
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

            CREATE TABLE IF NOT EXISTS access_control_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS access_control_users (
                user_id BIGINT PRIMARY KEY,
                added_by BIGINT,
                note TEXT,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS bot_admins (
                user_id BIGINT PRIMARY KEY,
                added_by BIGINT,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        logger.info("Database schema initialized with optimized indexes.")


async def store_message(
    message_id: int,
    channel_id: int,
    author_id: int,
    author_name: str,
    content: str,
    created_at: datetime,
    timestamp_str: str,
):
    """
    Insert a new message record or update an existing message's content and timestamp.
    
    Upserts a message identified by `message_id` into the messages table; if a row with the same `message_id` already exists, its `content` and `timestamp_str` are replaced.
    
    Parameters:
        message_id (int): Unique identifier for the message.
        channel_id (int): Identifier of the channel the message belongs to.
        author_id (int): Identifier of the message author.
        author_name (str): Display name of the message author.
        content (str): Message content to store.
        created_at (datetime): Timestamp when the message was created.
        timestamp_str (str): Original or formatted timestamp string to store.
    """
    if not pool:
        return

    try:
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO messages (message_id, channel_id, author_id, author_name, content, created_at, timestamp_str)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                ON CONFLICT (message_id) DO UPDATE SET
                    content = EXCLUDED.content,
                    timestamp_str = EXCLUDED.timestamp_str;
                """,
                message_id,
                channel_id,
                author_id,
                author_name,
                content,
                created_at,
                timestamp_str,
            )
    except Exception as e:
        logger.error(f"Failed to store message {message_id}: {e}")
        raise


async def delete_message(message_id: int):
    """Delete a message from the database."""
    if not pool:
        return

    try:
        async with pool.acquire() as conn:
            await conn.execute(
                """
                DELETE FROM messages WHERE message_id = $1
                """,
                message_id,
            )
            logger.debug(f"Deleted message {message_id} from database")
    except Exception as e:
        logger.error(f"Failed to delete message {message_id}: {e}")


async def get_messages(channel_id: int, limit: int = 2000) -> List[Dict]:
    """Retrieve the most recent messages for a channel in chronological order."""
    if not pool:
        return []

    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT message_id, channel_id, author_id, author_name, content, created_at
                FROM messages
                WHERE channel_id = $1
                ORDER BY created_at DESC
                LIMIT $2
                """,
                channel_id,
                limit,
            )
            return list(reversed([dict(row) for row in rows]))
    except Exception as e:
        logger.error(f"Failed to get messages for channel {channel_id}: {e}")
        return []


async def get_message_count(channel_id: int) -> int:
    """
    Return the number of messages stored for a channel.
    
    Returns:
        count (int): Number of messages for the given channel_id. Returns 0 if the database pool is not initialized or if an error occurs while querying.
    """
    if not pool:
        return 0

    try:
        async with pool.acquire() as conn:
            return await conn.fetchval(
                """
                SELECT COUNT(*) FROM messages WHERE channel_id = $1
                """,
                channel_id,
            )
    except Exception as e:
        logger.error(f"Failed to count messages for channel {channel_id}: {e}")
        return 0


async def get_latest_message_id(channel_id: int) -> Optional[int]:
    """
    Return the newest stored message ID for the given channel.
    
    Parameters:
        channel_id (int): Channel identifier to query.
    
    Returns:
        Optional[int]: The latest `message_id` for the channel, or `None` if no message is found.
    """
    if not pool:
        return None

    try:
        async with pool.acquire() as conn:
            return await conn.fetchval(
                """
                SELECT message_id FROM messages
                WHERE channel_id = $1
                ORDER BY created_at DESC
                LIMIT 1
                """,
                channel_id,
            )
    except Exception as e:
        logger.error(f"Failed to get latest message ID for channel {channel_id}: {e}")
        return None


async def get_oldest_message_id(channel_id: int) -> Optional[int]:
    """
    Get the oldest stored message ID for the given channel.
    
    @returns The oldest message_id for the channel as an int, or `None` if no message exists, the database pool is uninitialized, or an error occurs.
    """
    if not pool:
        return None

    try:
        async with pool.acquire() as conn:
            return await conn.fetchval(
                """
                SELECT message_id FROM messages
                WHERE channel_id = $1
                ORDER BY created_at ASC
                LIMIT 1
                """,
                channel_id,
            )
    except Exception as e:
        logger.error(f"Failed to get oldest message ID for channel {channel_id}: {e}")
        return None


async def is_channel_fully_backfilled(channel_id: int) -> bool:
    """
    Determine whether the specified channel is recorded as fully backfilled.
    
    If the database pool is not initialized or an error occurs while querying, the function returns `False`.
    
    Returns:
        `true` if the channel's `is_fully_backfilled` flag is set, `false` otherwise.
    """
    if not pool:
        return False
    try:
        async with pool.acquire() as conn:
            return (
                await conn.fetchval(
                    """
                    SELECT is_fully_backfilled FROM channel_status WHERE channel_id = $1
                    """,
                    channel_id,
                )
                or False
            )
    except Exception as e:
        logger.error(f"Failed to check backfill status for {channel_id}: {e}")
        return False


async def mark_channel_fully_backfilled(channel_id: int, status: bool = True):
    """
    Set the backfilled state for a channel.
    
    Parameters:
        channel_id (int): The identifier of the channel to update.
        status (bool): `True` to mark the channel as fully backfilled, `False` to mark it as not fully backfilled.
    """
    if not pool:
        return
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO channel_status (channel_id, is_fully_backfilled, last_updated)
                VALUES ($1, $2, CURRENT_TIMESTAMP)
                ON CONFLICT (channel_id) DO UPDATE SET
                    is_fully_backfilled = EXCLUDED.is_fully_backfilled,
                    last_updated = EXCLUDED.last_updated;
                """,
                channel_id,
                status,
            )
    except Exception as e:
        logger.error(f"Failed to mark backfill status for {channel_id}: {e}")


async def get_access_control_mode(default_mode: str = "whitelist") -> str:
    """
    Get the active access control mode from the database, or return the provided default if the stored value is missing or invalid.
    
    Returns:
        `'whitelist'` or `'blacklist'` — the persisted mode when valid, otherwise `default_mode`.
    """
    if not pool:
        return default_mode
    try:
        async with pool.acquire() as conn:
            mode = await conn.fetchval(
                """
                SELECT value FROM access_control_settings WHERE key = 'mode'
                """
            )
            if mode in {"whitelist", "blacklist"}:
                return mode
            return default_mode
    except Exception as e:
        logger.error(f"Failed to get access control mode: {e}")
        return default_mode


async def set_access_control_mode(mode: str):
    """
    Set the access control mode persisted in the database.
    
    Parameters:
        mode (str): Access control mode to store; must be "whitelist" or "blacklist".
        
    Raises:
        ValueError: If `mode` is not "whitelist" or "blacklist".
    
    Notes:
        If the database pool is not initialized, the function performs no action.
    """
    if not pool:
        return
    if mode not in {"whitelist", "blacklist"}:
        raise ValueError("mode must be 'whitelist' or 'blacklist'")
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO access_control_settings (key, value, updated_at)
            VALUES ('mode', $1, CURRENT_TIMESTAMP)
            ON CONFLICT (key) DO UPDATE SET
                value = EXCLUDED.value,
                updated_at = EXCLUDED.updated_at;
            """,
            mode,
        )


async def get_access_control_users() -> Set[str]:
    """
    Retrieve the configured access-control user IDs from the database as strings.
    
    If the database pool is not initialized or an error occurs while querying, an empty set is returned.
    
    Returns:
        users (Set[str]): A set of user IDs converted to strings; empty if unavailable or on error.
    """
    if not pool:
        return set()
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch("SELECT user_id FROM access_control_users")
            return {str(row["user_id"]) for row in rows}
    except Exception as e:
        logger.error(f"Failed to get access control users: {e}")
        return set()


async def add_access_control_user(user_id: int, added_by: Optional[int] = None, note: Optional[str] = None):
    """Add or update a user in the access-control set."""
    if not pool:
        return
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO access_control_users (user_id, added_by, note, created_at)
            VALUES ($1, $2, $3, CURRENT_TIMESTAMP)
            ON CONFLICT (user_id) DO UPDATE SET
                added_by = EXCLUDED.added_by,
                note = EXCLUDED.note;
            """,
            user_id,
            added_by,
            note,
        )


async def remove_access_control_user(user_id: int):
    """Remove a user from the access-control set."""
    if not pool:
        return
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM access_control_users WHERE user_id = $1", user_id)


async def get_admin_users() -> Set[str]:
    """
    Retrieve admin user IDs stored in the database.
    
    Returns:
        Set[str]: A set of admin user IDs as strings. Returns an empty set if the database pool is not initialized or if an error occurs.
    """
    if not pool:
        return set()
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch("SELECT user_id FROM bot_admins")
            return {str(row["user_id"]) for row in rows}
    except Exception as e:
        logger.error(f"Failed to get admin users: {e}")
        return set()


async def add_admin_user(user_id: int, added_by: Optional[int] = None):
    """
    Add or update an admin user's record.
    
    If the database pool is not initialized, the call is a no-op. The function records who granted admin and updates that information if the user already exists.
    
    Parameters:
        user_id (int): ID of the user to grant admin privileges.
        added_by (Optional[int]): ID of the user who granted admin privileges; stored or updated as the granter.
    """
    if not pool:
        return
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO bot_admins (user_id, added_by, created_at)
            VALUES ($1, $2, CURRENT_TIMESTAMP)
            ON CONFLICT (user_id) DO UPDATE SET
                added_by = EXCLUDED.added_by;
            """,
            user_id,
            added_by,
        )


async def remove_admin_user(user_id: int):
    """Revoke admin permissions from a user."""
    if not pool:
        return
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM bot_admins WHERE user_id = $1", user_id)

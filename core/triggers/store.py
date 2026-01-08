"""PostgreSQL-backed trigger storage."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from .models import TriggerRecord
from .utils import to_storage_timestamp, utc_now

logger = logging.getLogger(__name__)


class TriggerStore:
    """Low-level persistence for triggers backed by PostgreSQL."""
    
    def __init__(self, pool):
        """Initialize with an asyncpg connection pool."""
        self._pool = pool
    
    async def ensure_schema(self) -> None:
        """Create the triggers table if it doesn't exist."""
        if not self._pool:
            logger.warning("No database pool available for trigger schema")
            return
        
        async with self._pool.acquire() as conn:
            await conn.execute("""
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
                
                CREATE INDEX IF NOT EXISTS idx_triggers_user_status 
                ON triggers (user_id, status);
                
                CREATE INDEX IF NOT EXISTS idx_triggers_next_trigger 
                ON triggers (next_trigger) 
                WHERE status = 'active' AND next_trigger IS NOT NULL;
            """)
            logger.info("Trigger schema initialized")
    
    async def insert(self, data: Dict[str, Any]) -> int:
        """Insert a new trigger and return its ID."""
        if not self._pool:
            raise RuntimeError("No database pool available")
        
        async with self._pool.acquire() as conn:
            trigger_id = await conn.fetchval("""
                INSERT INTO triggers (
                    user_id, channel_id, agent_id, payload, 
                    start_time, next_trigger, recurrence_rule, 
                    timezone, status, last_error, created_at, updated_at
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
                RETURNING id
            """,
                data.get("user_id"),
                data.get("channel_id"),
                data.get("agent_id"),
                data.get("payload"),
                data.get("start_time"),
                data.get("next_trigger"),
                data.get("recurrence_rule"),
                data.get("timezone", "UTC"),
                data.get("status", "active"),
                data.get("last_error"),
                data.get("created_at", utc_now()),
                data.get("updated_at", utc_now()),
            )
            return trigger_id
    
    async def fetch_one(self, trigger_id: int, user_id: Optional[str] = None) -> Optional[TriggerRecord]:
        """Fetch a single trigger by ID, optionally filtered by user."""
        if not self._pool:
            return None
        
        async with self._pool.acquire() as conn:
            if user_id:
                row = await conn.fetchrow(
                    "SELECT * FROM triggers WHERE id = $1 AND user_id = $2",
                    trigger_id, user_id
                )
            else:
                row = await conn.fetchrow(
                    "SELECT * FROM triggers WHERE id = $1",
                    trigger_id
                )
        
        return self._row_to_record(row) if row else None
    
    async def update(self, trigger_id: int, fields: Dict[str, Any], user_id: Optional[str] = None) -> bool:
        """Update trigger fields."""
        if not fields or not self._pool:
            return False
        
        # Add updated_at
        fields["updated_at"] = utc_now()
        
        # Build dynamic SET clause
        set_parts = []
        values = []
        for i, (key, value) in enumerate(fields.items(), start=1):
            set_parts.append(f"{key} = ${i}")
            values.append(value)
        
        set_clause = ", ".join(set_parts)
        
        # Add WHERE clause parameters
        param_idx = len(values) + 1
        where_clause = f"id = ${param_idx}"
        values.append(trigger_id)
        
        if user_id:
            param_idx += 1
            where_clause += f" AND user_id = ${param_idx}"
            values.append(user_id)
        
        sql = f"UPDATE triggers SET {set_clause} WHERE {where_clause}"
        
        async with self._pool.acquire() as conn:
            result = await conn.execute(sql, *values)
            return "UPDATE 1" in result
    
    async def list_for_user(self, user_id: str) -> List[TriggerRecord]:
        """List all triggers for a user."""
        if not self._pool:
            return []
        
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """SELECT * FROM triggers 
                   WHERE user_id = $1 
                   ORDER BY next_trigger IS NULL, next_trigger""",
                user_id
            )
        
        return [self._row_to_record(row) for row in rows]
    
    async def fetch_due(self, before: datetime) -> List[TriggerRecord]:
        """Fetch all active triggers that are due for execution."""
        if not self._pool:
            return []
        
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """SELECT * FROM triggers 
                   WHERE status = 'active' 
                   AND next_trigger IS NOT NULL 
                   AND next_trigger <= $1
                   ORDER BY next_trigger, id""",
                before
            )
        
        return [self._row_to_record(row) for row in rows]
    
    async def delete(self, trigger_id: int, user_id: Optional[str] = None) -> bool:
        """Delete a trigger."""
        if not self._pool:
            return False
        
        async with self._pool.acquire() as conn:
            if user_id:
                result = await conn.execute(
                    "DELETE FROM triggers WHERE id = $1 AND user_id = $2",
                    trigger_id, user_id
                )
            else:
                result = await conn.execute(
                    "DELETE FROM triggers WHERE id = $1",
                    trigger_id
                )
            return "DELETE 1" in result
    
    def _row_to_record(self, row) -> TriggerRecord:
        """Convert a database row to a TriggerRecord."""
        return TriggerRecord(
            id=row["id"],
            user_id=row["user_id"],
            channel_id=row["channel_id"],
            agent_id=row["agent_id"],
            payload=row["payload"],
            start_time=row["start_time"],
            next_trigger=row["next_trigger"],
            recurrence_rule=row["recurrence_rule"],
            timezone=row["timezone"],
            status=row["status"],
            last_error=row["last_error"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


# Singleton instance (initialized when pool is available)
_trigger_store: Optional[TriggerStore] = None


def get_trigger_store() -> Optional[TriggerStore]:
    """Get the singleton trigger store (may be None if not initialized)."""
    return _trigger_store


def init_trigger_store(pool) -> TriggerStore:
    """Initialize the trigger store with a database pool."""
    global _trigger_store
    _trigger_store = TriggerStore(pool)
    return _trigger_store


__all__ = ["TriggerStore", "get_trigger_store", "init_trigger_store"]

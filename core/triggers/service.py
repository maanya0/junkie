"""High-level trigger management with recurrence awareness."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from zoneinfo import ZoneInfo

from .models import TriggerRecord, TriggerCreate
from .store import TriggerStore, get_trigger_store
from .utils import (
    build_recurrence,
    coerce_start_datetime,
    load_rrule,
    normalize_status,
    parse_iso,
    resolve_timezone,
    to_storage_timestamp,
    utc_now,
)

logger = logging.getLogger(__name__)


# Grace period for missed triggers - if a trigger is missed by more than this,
# we reschedule instead of firing immediately
MISSED_TRIGGER_GRACE_PERIOD = timedelta(minutes=5)


class TriggerService:
    """High-level trigger management with recurrence awareness."""
    
    def __init__(self, store: TriggerStore):
        self._store = store
    
    async def create_trigger(
        self,
        *,
        user_id: str,
        channel_id: str,
        payload: str,
        agent_id: str = "default",
        recurrence_rule: Optional[str] = None,
        start_time: Optional[str] = None,
        timezone_name: Optional[str] = None,
        status: Optional[str] = None,
    ) -> TriggerRecord:
        """Create a new trigger with schedule calculation."""
        tz = resolve_timezone(timezone_name)
        now = utc_now()
        start_dt_local = coerce_start_datetime(start_time, tz, now)
        stored_recurrence = build_recurrence(recurrence_rule, start_dt_local, tz) if recurrence_rule else None
        
        next_fire = self._compute_next_fire(
            stored_recurrence=stored_recurrence,
            start_dt_local=start_dt_local,
            tz=tz,
            now=now,
        )
        
        record: Dict[str, Any] = {
            "user_id": user_id,
            "channel_id": channel_id,
            "agent_id": agent_id,
            "payload": payload,
            "start_time": start_dt_local,
            "next_trigger": next_fire,
            "recurrence_rule": stored_recurrence,
            "timezone": getattr(tz, "key", "UTC"),
            "status": normalize_status(status),
            "last_error": None,
            "created_at": now,
            "updated_at": now,
        }
        
        trigger_id = await self._store.insert(record)
        created = await self._store.fetch_one(trigger_id)
        
        if not created:
            raise RuntimeError("Failed to load trigger after insert")
        
        logger.info(f"Created trigger {trigger_id} for user {user_id}, next fire: {next_fire}")
        return created
    
    async def update_trigger(
        self,
        trigger_id: int,
        user_id: str,
        *,
        payload: Optional[str] = None,
        recurrence_rule: Optional[str] = None,
        start_time: Optional[str] = None,
        timezone_name: Optional[str] = None,
        status: Optional[str] = None,
        last_error: Optional[str] = None,
        clear_error: bool = False,
    ) -> Optional[TriggerRecord]:
        """Update an existing trigger."""
        existing = await self._store.fetch_one(trigger_id, user_id)
        if existing is None:
            return None
        
        tz = resolve_timezone(timezone_name or existing.timezone)
        start_reference = existing.start_time if existing.start_time else utc_now()
        start_dt_local = coerce_start_datetime(start_time, tz, start_reference)
        
        fields: Dict[str, Any] = {}
        
        if payload is not None:
            fields["payload"] = payload
        
        normalized_status = None
        status_changed_to_active = False
        if status is not None:
            normalized_status = normalize_status(status)
            fields["status"] = normalized_status
            status_changed_to_active = (
                normalized_status == "active" and existing.status != "active"
            )
        else:
            normalized_status = existing.status
        
        if start_time is not None:
            fields["start_time"] = start_dt_local
        if timezone_name is not None:
            fields["timezone"] = getattr(tz, "key", "UTC")
        
        # Check if schedule inputs changed
        schedule_inputs_changed = any(
            value is not None for value in (recurrence_rule, start_time, timezone_name)
        )
        
        recurrence_source = (
            recurrence_rule if recurrence_rule is not None else existing.recurrence_rule
        )
        if schedule_inputs_changed and recurrence_source:
            stored_recurrence = build_recurrence(recurrence_source, start_dt_local, tz)
        else:
            stored_recurrence = recurrence_source
        
        # Determine if we need to recompute the schedule
        should_recompute_schedule = schedule_inputs_changed
        
        if status_changed_to_active:
            if existing.next_trigger is None:
                should_recompute_schedule = True
            else:
                missed_duration = utc_now() - existing.next_trigger
                if missed_duration > MISSED_TRIGGER_GRACE_PERIOD:
                    should_recompute_schedule = True
        
        if should_recompute_schedule:
            now = utc_now()
            next_fire = self._compute_next_fire(
                stored_recurrence=stored_recurrence,
                start_dt_local=start_dt_local,
                tz=tz,
                now=now,
            )
            fields["next_trigger"] = next_fire
            if schedule_inputs_changed:
                fields["recurrence_rule"] = stored_recurrence
        elif schedule_inputs_changed:
            fields["recurrence_rule"] = stored_recurrence
        
        if clear_error:
            fields["last_error"] = None
        elif last_error is not None:
            fields["last_error"] = last_error
        
        if not fields:
            return existing
        
        await self._store.update(trigger_id, fields, user_id)
        return await self._store.fetch_one(trigger_id, user_id)
    
    async def list_triggers(self, user_id: str) -> List[TriggerRecord]:
        """List all triggers for a user."""
        return await self._store.list_for_user(user_id)
    
    async def get_due_triggers(self, before: datetime) -> List[TriggerRecord]:
        """Get all triggers due for execution."""
        return await self._store.fetch_due(before)
    
    async def mark_as_completed(self, trigger_id: int, user_id: Optional[str] = None) -> None:
        """Mark a trigger as completed (for one-time triggers)."""
        await self._store.update(
            trigger_id,
            {
                "status": "completed",
                "next_trigger": None,
                "last_error": None,
            },
            user_id
        )
    
    async def schedule_next_occurrence(
        self,
        trigger: TriggerRecord,
        *,
        fired_at: datetime,
    ) -> Optional[TriggerRecord]:
        """Schedule the next occurrence of a recurring trigger."""
        if not trigger.recurrence_rule:
            await self.mark_as_completed(trigger.id)
            return await self._store.fetch_one(trigger.id)
        
        tz = resolve_timezone(trigger.timezone)
        next_fire = self._compute_next_after(trigger.recurrence_rule, fired_at, tz)
        
        fields: Dict[str, Any] = {
            "next_trigger": next_fire,
            "last_error": None,
        }
        if next_fire is None:
            fields["status"] = "completed"
        
        await self._store.update(trigger.id, fields)
        return await self._store.fetch_one(trigger.id)
    
    async def record_failure(self, trigger: TriggerRecord, error: str) -> None:
        """Record a trigger execution failure."""
        await self._store.update(
            trigger.id,
            {"last_error": error}
        )
    
    async def delete_trigger(self, trigger_id: int, user_id: str) -> bool:
        """Delete a trigger."""
        return await self._store.delete(trigger_id, user_id)
    
    def _compute_next_fire(
        self,
        *,
        stored_recurrence: Optional[str],
        start_dt_local: datetime,
        tz: ZoneInfo,
        now: datetime,
    ) -> Optional[datetime]:
        """Compute the next fire time for a trigger."""
        if stored_recurrence:
            try:
                rule = load_rrule(stored_recurrence)
                next_occurrence = rule.after(now.astimezone(tz), inc=True)
                if next_occurrence is None:
                    return None
                if next_occurrence.tzinfo is None:
                    next_occurrence = next_occurrence.replace(tzinfo=tz)
                return next_occurrence.astimezone(tz)
            except Exception as e:
                logger.warning(f"Failed to parse recurrence rule: {e}")
                return start_dt_local
        
        # Non-recurring: just use start time
        if start_dt_local < now.astimezone(tz):
            logger.warning(f"start_time in the past; trigger will fire immediately: {start_dt_local}")
        return start_dt_local
    
    def _compute_next_after(
        self,
        stored_recurrence: str,
        fired_at: datetime,
        tz: ZoneInfo,
    ) -> Optional[datetime]:
        """Compute the next occurrence after a given time."""
        try:
            rule = load_rrule(stored_recurrence)
            next_occurrence = rule.after(fired_at.astimezone(tz), inc=False)
            if next_occurrence is None:
                return None
            if next_occurrence.tzinfo is None:
                next_occurrence = next_occurrence.replace(tzinfo=tz)
            return next_occurrence.astimezone(tz)
        except Exception as e:
            logger.warning(f"Failed to compute next occurrence: {e}")
            return None


# Singleton instance
_trigger_service: Optional[TriggerService] = None


def get_trigger_service() -> Optional[TriggerService]:
    """Get the singleton trigger service."""
    return _trigger_service


def init_trigger_service(store: TriggerStore) -> TriggerService:
    """Initialize the trigger service with a store."""
    global _trigger_service
    _trigger_service = TriggerService(store)
    return _trigger_service


__all__ = ["TriggerService", "get_trigger_service", "init_trigger_service", "MISSED_TRIGGER_GRACE_PERIOD"]

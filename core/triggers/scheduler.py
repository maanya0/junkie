"""Background scheduler that watches trigger definitions and executes them."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional, Set

from .models import TriggerRecord
from .service import TriggerService, get_trigger_service

logger = logging.getLogger(__name__)


UTC = timezone.utc


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _isoformat(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


class TriggerScheduler:
    """Polls stored triggers and launches execution when due."""
    
    def __init__(self, poll_interval_seconds: float = 30.0) -> None:
        self._poll_interval = poll_interval_seconds
        self._task: Optional[asyncio.Task[None]] = None
        self._running = False
        self._in_flight: Set[int] = set()  # Track currently executing trigger IDs
        self._lock = asyncio.Lock()
        self._discord_client = None
    
    def set_discord_client(self, client) -> None:
        """Set the Discord client for sending messages."""
        self._discord_client = client
    
    async def start(self) -> None:
        """Start the scheduler loop."""
        async with self._lock:
            if self._task and not self._task.done():
                return
            
            self._running = True
            self._task = asyncio.create_task(self._run(), name="trigger-scheduler")
            logger.info(f"Trigger scheduler started (poll interval: {self._poll_interval}s)")
    
    async def stop(self) -> None:
        """Gracefully stop the scheduler."""
        async with self._lock:
            self._running = False
            if self._task:
                self._task.cancel()
                try:
                    await self._task
                except asyncio.CancelledError:
                    pass
                self._task = None
                logger.info("Trigger scheduler stopped")
    
    async def _run(self) -> None:
        """Main scheduler loop."""
        try:
            while self._running:
                await self._poll_once()
                await asyncio.sleep(self._poll_interval)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception(f"Trigger scheduler loop crashed: {exc}")
    
    async def _poll_once(self) -> None:
        """Check for due triggers and dispatch them."""
        service = get_trigger_service()
        if not service:
            return
        
        now = _utc_now()
        try:
            due_triggers = await service.get_due_triggers(before=now)
        except Exception as e:
            logger.error(f"Failed to fetch due triggers: {e}")
            return
        
        if not due_triggers:
            return
        
        logger.debug(f"Found {len(due_triggers)} due triggers")
        
        for trigger in due_triggers:
            # Skip if already executing
            if trigger.id in self._in_flight:
                continue
            
            self._in_flight.add(trigger.id)
            asyncio.create_task(
                self._execute_trigger(trigger),
                name=f"trigger-{trigger.id}"
            )
    
    async def _execute_trigger(self, trigger: TriggerRecord) -> None:
        """Execute a single trigger."""
        service = get_trigger_service()
        if not service:
            self._in_flight.discard(trigger.id)
            return
        
        fired_at = _utc_now()
        
        try:
            logger.info(
                f"Executing trigger {trigger.id} for user {trigger.user_id}: {trigger.payload[:100]}"
            )
            
            # Execute the trigger payload
            response = await self._run_trigger_payload(trigger)
            
            # Send response to Discord channel
            await self._send_response(trigger, response, success=True)
            
            # Schedule next occurrence (or mark completed)
            await service.schedule_next_occurrence(trigger, fired_at=fired_at)
            
            logger.info(f"Trigger {trigger.id} completed successfully")
            
        except Exception as exc:
            error_msg = str(exc)
            logger.exception(f"Trigger {trigger.id} execution failed: {error_msg}")
            
            # Record failure
            await service.record_failure(trigger, error_msg)
            
            # Send error notification
            await self._send_response(
                trigger,
                f"⚠️ Scheduled task failed: {error_msg[:200]}",
                success=False
            )
            
            # Still reschedule recurring triggers
            if trigger.recurrence_rule:
                await service.schedule_next_occurrence(trigger, fired_at=fired_at)
        
        finally:
            self._in_flight.discard(trigger.id)
    
    async def _run_trigger_payload(self, trigger: TriggerRecord) -> str:
        """Run the trigger payload using an execution agent."""
        # Import here to avoid circular imports
        from agent.execution_agents import execute_trigger_task
        
        response = await execute_trigger_task(
            user_id=trigger.user_id,
            agent_id=trigger.agent_id,
            instructions=self._format_instructions(trigger),
            channel_id=trigger.channel_id,  # Pass channel for context
        )
        return response
    
    def _format_instructions(self, trigger: TriggerRecord) -> str:
        """Format trigger payload into instructions for the execution agent."""
        fired_at = _utc_now()
        
        lines = [
            f"⏰ **Scheduled Task Triggered**",
            f"",
            f"**Time:** {_isoformat(fired_at)}",
            f"**Task:** {trigger.payload}",
        ]
        
        if trigger.recurrence_rule:
            lines.append(f"**Recurrence:** This is a recurring task")
        
        lines.append("")
        lines.append("Please complete this scheduled task and provide a response.")
        
        return "\n".join(lines)
    
    async def _send_response(self, trigger: TriggerRecord, message: str, success: bool) -> None:
        """Send the trigger response to the Discord channel."""
        if not self._discord_client:
            logger.warning("No Discord client available to send trigger response")
            return
        
        try:
            channel = self._discord_client.get_channel(int(trigger.channel_id))
            if not channel:
                channel = await self._discord_client.fetch_channel(int(trigger.channel_id))
            
            if channel:
                prefix = "🔔" if success else "⚠️"
                await channel.send(f"{prefix} **Reminder for <@{trigger.user_id}>:**\n{message}")
            else:
                logger.warning(f"Could not find channel {trigger.channel_id} for trigger {trigger.id}")
        
        except Exception as e:
            logger.error(f"Failed to send trigger response to channel: {e}")


# Singleton instance
_scheduler_instance: Optional[TriggerScheduler] = None


def get_trigger_scheduler() -> TriggerScheduler:
    """Get the singleton scheduler instance."""
    global _scheduler_instance
    if _scheduler_instance is None:
        _scheduler_instance = TriggerScheduler()
    return _scheduler_instance


__all__ = ["TriggerScheduler", "get_trigger_scheduler"]

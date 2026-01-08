"""Trigger management tools for the Agno Team."""

from __future__ import annotations

import logging
from typing import Optional
from agno.tools import Toolkit
from agno.agent import Agent

logger = logging.getLogger(__name__)


class TriggerTools(Toolkit):
    """Tools for creating and managing scheduled triggers/reminders."""
    
    def __init__(self):
        super().__init__(name="trigger_tools")
        
        # Register tools
        self.register(self.create_reminder)
        self.register(self.schedule_task)
        self.register(self.list_reminders)
        self.register(self.cancel_reminder)
    
    async def create_reminder(
        self,
        agent: Agent,
        message: str,
        when: str,
        recurring: Optional[str] = None,
    ) -> str:
        """Create a scheduled reminder that will be sent at the specified time.
        
        Args:
            agent: The agent executing the tool (injected automatically).
            message: The reminder message to send (what to remind about)
            when: When to send the reminder. Can be:
                - Natural language: "in 10 minutes", "tomorrow at 9am", "next Monday at 3pm"
                - ISO datetime: "2024-01-15T14:30:00"
            recurring: Optional recurrence pattern:
                - "daily" - Every day at the same time
                - "weekly" - Every week on the same day
                - "weekdays" - Every weekday (Mon-Fri)
                - "monthly" - Every month on the same date
                - Or RRULE format for complex patterns
        
        Returns:
            Confirmation message with trigger ID
        """
        from core.triggers.service import get_trigger_service
        
        user_id = agent.user_id
        # For now, we assume channel_id is passed in session_state or we rely on the implementation 
        # to handle channel routing. 
        # Ideally, session_state would have channel_id if set contextually.
        # Let's check session_state for channel_id
        channel_id = agent.session_state.get("channel_id")
        
        if not user_id or not channel_id:
            return "Error: User or channel context not available. Cannot create reminder."
        
        service = get_trigger_service()
        if not service:
            return "Error: Trigger service not available. The scheduling system may not be initialized."
        
        try:
            trigger = await service.create_trigger(
                user_id=user_id,
                channel_id=str(channel_id),
                payload=message,
                agent_id="reminder",
                start_time=when,
                recurrence_rule=recurring,
                timezone_name="Asia/Kolkata",
            )
            
            # Format response
            time_str = trigger.next_trigger.strftime("%Y-%m-%d %H:%M") if trigger.next_trigger else "soon"
            
            response = f"✅ Reminder created! (ID: {trigger.id})\n"
            response += f"📝 **Message:** {message}\n"
            response += f"⏰ **Scheduled for:** {time_str}"
            
            if recurring:
                response += f"\n🔄 **Recurring:** {recurring}"
            
            return response
            
        except Exception as e:
            logger.exception(f"Failed to create reminder: {e}")
            return f"Error: Failed to create reminder - {str(e)}"
    
    async def schedule_task(
        self,
        agent: Agent,
        task_description: str,
        when: str,
        task_type: str = "task",
        recurring: Optional[str] = None,
    ) -> str:
        """Schedule a complex task to run at a specific time using the full AI Team.
        
        Unlike simple reminders, scheduled tasks use the entire Agno Team with all
        agents (research, summarization, code, etc.) to complete complex work.
        
        Args:
            agent: The agent executing the tool (injected automatically).
            task_description: What the task should do, e.g.:
                - "Summarize this channel's activity"
                - "Research the latest news on AI"
                - "Generate a daily standup summary"
            when: When to run the task. Can be:
                - Natural language: "today at 6pm", "tomorrow at 9am"
                - ISO datetime: "2024-01-15T18:00:00"
            task_type: Type of task execution:
                - "summarize" - For channel/conversation summarization
                - "research" - For research tasks using Perplexity
                - "task" - General task (default)
            recurring: Optional recurrence pattern:
                - "daily" - Every day at the same time
                - "weekly" - Every week on the same day
                - "weekdays" - Every weekday (Mon-Fri)
        
        Returns:
            Confirmation message with task ID
        
        Examples:
            - schedule_task("Summarize today's channel activity", "today at 6pm", "summarize")
            - schedule_task("Research latest Python 3.13 features", "tomorrow at 10am", "research")
            - schedule_task("Generate daily standup", "9am", "summarize", "weekdays")
        """
        from core.triggers.service import get_trigger_service
        
        user_id = agent.user_id
        channel_id = agent.session_state.get("channel_id")
        
        if not user_id or not channel_id:
            return "Error: User or channel context not available. Cannot schedule task."
        
        service = get_trigger_service()
        if not service:
            return "Error: Trigger service not available. The scheduling system may not be initialized."
        
        # Validate task type
        valid_types = ("summarize", "research", "task")
        if task_type not in valid_types:
            return f"Error: Invalid task type '{task_type}'. Must be one of: {', '.join(valid_types)}"
        
        try:
            trigger = await service.create_trigger(
                user_id=user_id,
                channel_id=str(channel_id),
                payload=task_description,
                agent_id=task_type,  # This determines how execution happens
                start_time=when,
                recurrence_rule=recurring,
                timezone_name="Asia/Kolkata",
            )
            
            # Format response
            time_str = trigger.next_trigger.strftime("%Y-%m-%d %H:%M") if trigger.next_trigger else "soon"
            
            type_emoji = {"summarize": "📊", "research": "🔍", "task": "📋"}.get(task_type, "📋")
            
            response = f"✅ Scheduled task created! (ID: {trigger.id})\n"
            response += f"{type_emoji} **Type:** {task_type.title()}\n"
            response += f"📝 **Task:** {task_description}\n"
            response += f"⏰ **Scheduled for:** {time_str}"
            
            if recurring:
                response += f"\n🔄 **Recurring:** {recurring}"
            
            return response
            
        except Exception as e:
            logger.exception(f"Failed to schedule task: {e}")
            return f"Error: Failed to schedule task - {str(e)}"
    
    async def list_reminders(self, agent: Agent) -> str:
        """List all active reminders for the current user.
        
        Args:
           agent: The agent executing the tool (injected automatically).
        
        Returns:
            Formatted list of reminders with their IDs, messages, and schedules
        """
        from core.triggers.service import get_trigger_service
        
        user_id = agent.user_id
        
        if not user_id:
            return "Error: User context not available. Cannot list reminders."
        
        service = get_trigger_service()
        if not service:
            return "Error: Trigger service not available."
        
        try:
            triggers = await service.list_triggers(user_id)
            
            if not triggers:
                return "You have no active reminders."
            
            lines = ["📋 **Your Reminders:**\n"]
            
            for t in triggers:
                status_emoji = "✅" if t.status == "active" else "⏸️" if t.status == "paused" else "✓"
                time_str = t.next_trigger.strftime("%Y-%m-%d %H:%M") if t.next_trigger else "N/A"
                
                payload_preview = t.payload[:50] + "..." if len(t.payload) > 50 else t.payload
                line = f"{status_emoji} **ID {t.id}**: {payload_preview}"
                line += f"\n   ⏰ Next: {time_str}"
                
                if t.recurrence_rule:
                    line += " (recurring)"
                
                if t.last_error:
                    line += f"\n   ⚠️ Last error: {t.last_error[:50]}"
                
                lines.append(line)
            
            return "\n\n".join(lines)
            
        except Exception as e:
            logger.exception(f"Failed to list reminders: {e}")
            return f"Error: Failed to list reminders - {str(e)}"
    
    async def cancel_reminder(self, agent: Agent, reminder_id: int) -> str:
        """Cancel and delete a reminder.
        
        Args:
            agent: The agent executing the tool (injected automatically).
            reminder_id: The ID of the reminder to cancel (shown in list_reminders)
        
        Returns:
            Confirmation message
        """
        from core.triggers.service import get_trigger_service
        
        user_id = agent.user_id
        
        if not user_id:
            return "Error: User context not available. Cannot cancel reminder."
        
        service = get_trigger_service()
        if not service:
            return "Error: Trigger service not available."
        
        try:
            deleted = await service.delete_trigger(reminder_id, user_id)
            
            if deleted:
                return f"✅ Reminder {reminder_id} has been cancelled."
            else:
                return f"❌ Reminder {reminder_id} not found or you don't have permission to delete it."
            
        except Exception as e:
            logger.exception(f"Failed to cancel reminder: {e}")
            return f"Error: Failed to cancel reminder - {str(e)}"


__all__ = ["TriggerTools"]

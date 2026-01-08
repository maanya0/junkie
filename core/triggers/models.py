"""Trigger models for the scheduling system."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict


class TriggerRecord(BaseModel):
    """Serialized trigger representation."""
    
    model_config = ConfigDict(from_attributes=True)
    
    id: int
    user_id: str  # Discord user ID
    channel_id: str  # Discord channel ID for response delivery
    agent_id: str  # Execution agent identifier
    payload: str  # Instructions for the agent
    start_time: Optional[datetime] = None
    next_trigger: Optional[datetime] = None
    recurrence_rule: Optional[str] = None  # RRULE format
    timezone: str = "UTC"
    status: str = "active"  # active, paused, completed
    last_error: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class TriggerCreate(BaseModel):
    """Input model for creating a trigger."""
    
    user_id: str
    channel_id: str
    agent_id: str
    payload: str
    start_time: Optional[str] = None  # ISO format or natural language
    recurrence_rule: Optional[str] = None
    timezone: str = "Asia/Kolkata"


class TriggerUpdate(BaseModel):
    """Input model for updating a trigger."""
    
    payload: Optional[str] = None
    start_time: Optional[str] = None
    recurrence_rule: Optional[str] = None
    timezone: Optional[str] = None
    status: Optional[str] = None


__all__ = ["TriggerRecord", "TriggerCreate", "TriggerUpdate"]

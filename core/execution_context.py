import contextvars
from typing import Optional

# Context variable to store the current channel ID
_current_channel_id: contextvars.ContextVar[Optional[int]] = contextvars.ContextVar("current_channel_id", default=None)
# Context variable to store the current Discord channel object
_current_channel: contextvars.ContextVar[Optional[object]] = contextvars.ContextVar("current_channel", default=None)

def set_current_channel_id(channel_id: int):
    """Sets the current channel ID in the execution context."""
    _current_channel_id.set(channel_id)

def get_current_channel_id() -> Optional[int]:
    """Gets the current channel ID from the execution context."""
    return _current_channel_id.get()

def set_current_channel(channel: object):
    """Sets the current Discord channel object in the execution context."""
    _current_channel.set(channel)

def get_current_channel() -> Optional[object]:
    """Gets the current Discord channel object from the execution context."""
    return _current_channel.get()

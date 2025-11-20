import os
import logging

_cached_system_prompt = None

def get_system_prompt():
    """
    Efficiently retrieve the system prompt.
    Uses in-memory caching to avoid disk I/O on every request.
    """
    global _cached_system_prompt
    if _cached_system_prompt is None:
        try:
            # Assuming system_prompt.md is in the same directory as this file
            prompt_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "system_prompt.md")
            with open(prompt_path, "r", encoding="utf-8") as f:
                _cached_system_prompt = f.read()
            logger = logging.getLogger(__name__)
            logger.info(f"Loaded system prompt from {prompt_path}")
        except Exception as e:
            logger = logging.getLogger(__name__)
            logger.error(f"Failed to load system prompt: {e}")
            # Fallback minimal prompt if file read fails
            return "You are a helpful AI assistant."
            
    return _cached_system_prompt

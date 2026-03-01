import hashlib
import json
import time
from typing import Any, Optional, Dict
import logging

logger = logging.getLogger(__name__)

# Default TTL for cached tool results (in seconds)
DEFAULT_TOOL_CACHE_TTL = 300  # 5 minutes

# In-memory cache for tool results
_tool_cache: Dict[str, Dict] = {}


def _generate_tool_cache_key(tool_name: str, args: Any, kwargs: Any) -> str:
    """Generate a unique cache key for a tool call with specific arguments."""
    # Create a serialization-safe representation of arguments
    key_data = {
        "tool_name": tool_name,
        "args": args,
        "kwargs": kwargs,
    }
    # Serialize to JSON and hash for a unique key
    serialized = json.dumps(key_data, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(serialized).hexdigest()


def get_cached_tool_result(tool_name: str, args: Any, kwargs: Any) -> Optional[Any]:
    """Get cached result for a tool call if it exists and hasn't expired."""
    cache_key = _generate_tool_cache_key(tool_name, args, kwargs)
    if cache_key in _tool_cache:
        cache_entry = _tool_cache[cache_key]
        if time.time() - cache_entry["timestamp"] < cache_entry["ttl"]:
            logger.debug(f"[ToolCache] Hit for {tool_name} with key {cache_key}")
            return cache_entry["result"]
        else:
            logger.debug(f"[ToolCache] Expired for {tool_name} with key {cache_key}")
            del _tool_cache[cache_key]  # Clean up expired entry
    else:
        logger.debug(f"[ToolCache] Miss for {tool_name} with key {cache_key}")
    return None


def cache_tool_result(
    tool_name: str, args: Any, kwargs: Any, result: Any, ttl: int = DEFAULT_TOOL_CACHE_TTL
):
    """Cache a tool result with optional TTL."""
    cache_key = _generate_tool_cache_key(tool_name, args, kwargs)
    _tool_cache[cache_key] = {
        "timestamp": time.time(),
        "ttl": ttl,
        "result": result,
    }
    logger.debug(f"[ToolCache] Cached result for {tool_name} with key {cache_key}")


def clear_tool_cache() -> None:
    """Clear all tool cache entries."""
    global _tool_cache
    _tool_cache = {}
    logger.debug("[ToolCache] Cleared all cache entries")


def get_tool_cache_stats() -> Dict[str, int]:
    """Get cache statistics (hit, miss, size)."""
    # For simplicity, we can track hits/misses with a wrapper, but since we don't have that yet,
    # just return the current cache size
    return {
        "cache_size": len(_tool_cache),
    }

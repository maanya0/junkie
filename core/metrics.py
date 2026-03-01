import time
from typing import Dict, Any, Optional
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class LLMCallMetrics:
    """Dataclass to store LLM call metrics."""
    model: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    duration_ms: float
    timestamp: float
    prompt_cost: Optional[float] = None
    completion_cost: Optional[float] = None
    total_cost: Optional[float] = None


# In-memory storage for metrics (could be extended to use a database)
_llm_metrics: list[LLMCallMetrics] = []


def track_llm_call(
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    duration_ms: float,
    prompt_cost: Optional[float] = None,
    completion_cost: Optional[float] = None,
) -> LLMCallMetrics:
    """Track an LLM call with metrics."""
    total_tokens = prompt_tokens + completion_tokens
    total_cost = None
    if prompt_cost is not None and completion_cost is not None:
        total_cost = prompt_cost + completion_cost

    metrics = LLMCallMetrics(
        model=model,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        duration_ms=duration_ms,
        timestamp=time.time(),
        prompt_cost=prompt_cost,
        completion_cost=completion_cost,
        total_cost=total_cost,
    )

    _llm_metrics.append(metrics)
    logger.info(
        f"[LLMMetrics] Model: {model}, "
        f"Tokens: {prompt_tokens}/{completion_tokens}/{total_tokens}, "
        f"Duration: {duration_ms:.2f}ms, "
        f"Cost: {total_cost:.6f}$" if total_cost else ""
    )

    return metrics


def get_llm_metrics() -> list[LLMCallMetrics]:
    """Get all tracked LLM metrics."""
    return _llm_metrics.copy()


def get_llm_metrics_summary() -> Dict[str, Any]:
    """Get a summary of LLM metrics."""
    if not _llm_metrics:
        return {
            "total_calls": 0,
            "total_prompt_tokens": 0,
            "total_completion_tokens": 0,
            "total_tokens": 0,
            "avg_duration_ms": 0,
            "total_cost": 0,
        }

    total_calls = len(_llm_metrics)
    total_prompt_tokens = sum(m.prompt_tokens for m in _llm_metrics)
    total_completion_tokens = sum(m.completion_tokens for m in _llm_metrics)
    total_tokens = sum(m.total_tokens for m in _llm_metrics)
    total_duration_ms = sum(m.duration_ms for m in _llm_metrics)
    total_cost = sum(m.total_cost or 0 for m in _llm_metrics)

    return {
        "total_calls": total_calls,
        "total_prompt_tokens": total_prompt_tokens,
        "total_completion_tokens": total_completion_tokens,
        "total_tokens": total_tokens,
        "avg_duration_ms": total_duration_ms / total_calls,
        "total_cost": total_cost,
    }


def clear_llm_metrics() -> None:
    """Clear all tracked LLM metrics."""
    global _llm_metrics
    _llm_metrics = []
    logger.debug("[LLMMetrics] Cleared all metrics")


# Context management for memory optimization
def optimize_memory_usage() -> None:
    """Optimize memory usage by clearing old metrics (if needed)."""
    global _llm_metrics
    # Keep only the last 1000 metrics to prevent memory bloat
    if len(_llm_metrics) > 1000:
        _llm_metrics = _llm_metrics[-1000:]
        logger.debug("[LLMMetrics] Cleared old metrics to optimize memory")

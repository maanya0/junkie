import os
import logging
from core.config import TRACING_ENABLED, PHOENIX_API_KEY, PHOENIX_ENDPOINT, PHOENIX_PROJECT_NAME
from core.metrics import get_llm_metrics_summary, get_llm_metrics
from core.tool_cache import get_tool_cache_stats

_phoenix_tracer = None
logger = logging.getLogger(__name__)

def setup_phoenix_tracing():
    """Lazy initialization of Phoenix tracing with optimizations."""
    global _phoenix_tracer
    
    if _phoenix_tracer is not None:
        return _phoenix_tracer  # Already initialized
    
    if not TRACING_ENABLED:
        return None
    
    try:
        # Import phoenix lazily to avoid importing heavy/optional deps when tracing is off
        from phoenix.otel import register
        
        if not PHOENIX_API_KEY:
            logger.warning("PHOENIX_API_KEY not set, skipping Phoenix tracing")
            _phoenix_tracer = False  # Mark as attempted but failed
            return None
        
        # Set environment variables for Arize Phoenix (only if not already set)
        if "PHOENIX_CLIENT_HEADERS" not in os.environ:
            os.environ["PHOENIX_CLIENT_HEADERS"] = f"api_key={PHOENIX_API_KEY}"
        if "PHOENIX_COLLECTOR_ENDPOINT" not in os.environ:
            os.environ["PHOENIX_COLLECTOR_ENDPOINT"] = "https://app.phoenix.arize.com"
        
        # Configure the Phoenix tracer with optimizations
        tracer_provider = register(
            project_name=PHOENIX_PROJECT_NAME,
            endpoint=PHOENIX_ENDPOINT,
            auto_instrument=True,
            batch=True,  # Batch traces for better performance (reduces overhead)
        )
        
        _phoenix_tracer = tracer_provider
        logger.info(f"Phoenix tracing enabled (project: {PHOENIX_PROJECT_NAME})")
        
        return tracer_provider
    except ImportError:
        logger.warning("Phoenix tracing requested but 'phoenix' package not installed")
        _phoenix_tracer = False
        return None
    except Exception as e:
        logger.error(f"Failed to initialize Phoenix tracing: {e}", exc_info=True)
        _phoenix_tracer = False
        return None


def log_system_metrics():
    """Log system metrics for observability."""
    # Log LLM metrics summary
    llm_summary = get_llm_metrics_summary()
    logger.info(
        f"[SystemMetrics] LLM Calls: {llm_summary['total_calls']}, "
        f"Total Tokens: {llm_summary['total_tokens']} "
        f"({llm_summary['total_prompt_tokens']}/{llm_summary['total_completion_tokens']}), "
        f"Avg Duration: {llm_summary['avg_duration_ms']:.2f}ms, "
        f"Total Cost: {llm_summary['total_cost']:.6f}$"
    )
    
    # Log tool cache stats
    cache_stats = get_tool_cache_stats()
    logger.info(f"[SystemMetrics] Tool Cache: {cache_stats['cache_size']} entries")


def export_metrics_to_json() -> str:
    """Export metrics to JSON string for debugging or reporting."""
    import json
    
    llm_summary = get_llm_metrics_summary()
    cache_stats = get_tool_cache_stats()
    
    metrics_data = {
        "llm": llm_summary,
        "tool_cache": cache_stats,
    }
    
    return json.dumps(metrics_data, indent=2)


import os
import logging
from core.config import (
    TRACING_ENABLED, PHOENIX_API_KEY, PHOENIX_ENDPOINT, PHOENIX_PROJECT_NAME,
    LANGDB_TRACING_ENABLED, LANGDB_API_KEY, LANGDB_PROJECT_ID
)

_phoenix_tracer = None

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
            logger = logging.getLogger(__name__)
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
        logger = logging.getLogger(__name__)
        logger.info(f"Phoenix tracing enabled (project: {PHOENIX_PROJECT_NAME})")
        
        return tracer_provider
    except ImportError:
        logger = logging.getLogger(__name__)
        logger.warning("Phoenix tracing requested but 'phoenix' package not installed")
        _phoenix_tracer = False
        return None
    except Exception as e:
        logger = logging.getLogger(__name__)
        logger.error(f"Failed to initialize Phoenix tracing: {e}", exc_info=True)
        _phoenix_tracer = False
        return None

def setup_langdb_tracing():
    """Initialize LangDB tracing if enabled."""
    if not LANGDB_TRACING_ENABLED:
        return

    if not LANGDB_API_KEY or not LANGDB_PROJECT_ID:
        logger = logging.getLogger(__name__)
        logger.warning("LangDB tracing enabled but API key or Project ID missing")
        return

    try:
        from pylangdb.agno import init
        init()
        logger = logging.getLogger(__name__)
        logger.info("LangDB tracing enabled")
    except ImportError:
        logger = logging.getLogger(__name__)
        logger.warning("LangDB tracing requested but 'pylangdb' package not installed")
    except Exception as e:
        logger = logging.getLogger(__name__)
        logger.error(f"Failed to initialize LangDB tracing: {e}", exc_info=True)

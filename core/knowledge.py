"""Knowledge base setup with embeddings via LiteLLM proxy + PgVector hybrid search."""
import logging
from agno.knowledge.knowledge import Knowledge
from agno.vectordb.pgvector import PgVector, SearchType
from agno.knowledge.embedder.openai import OpenAIEmbedder
from agno.knowledge.embedder.mistral import MistralEmbedder
from agno.db.postgres import PostgresDb
from core.config import (
    POSTGRES_URL, 
    MISTRAL_API_KEY,
    EMBEDDER_BASE_URL,
    EMBEDDER_MODEL,
    EMBEDDER_API_KEY,
)

logger = logging.getLogger(__name__)
_knowledge_base: Knowledge = None


def _to_psycopg_url(url: str) -> str:
    """Convert to psycopg format for Knowledge components."""
    if "+asyncpg" in url:
        return url.replace("+asyncpg", "+psycopg")
    if url.startswith("postgresql://") and "+psycopg" not in url:
        return url.replace("postgresql://", "postgresql+psycopg://")
    return url


def _create_embedder():
    """Create embedder - use LiteLLM proxy if configured, else direct Mistral."""
    
    # Option 1: LiteLLM proxy (supports load balancing, multiple deployments)
    if EMBEDDER_BASE_URL and EMBEDDER_API_KEY:
        logger.info(f"[Knowledge] Using LiteLLM embedder: {EMBEDDER_MODEL} @ {EMBEDDER_BASE_URL}")
        return OpenAIEmbedder(
            id=EMBEDDER_MODEL,
            base_url=EMBEDDER_BASE_URL,
            api_key=EMBEDDER_API_KEY,
            dimensions=1024,  # Mistral embed dimension
        )
    
    # Option 2: Direct Mistral (fallback)
    if MISTRAL_API_KEY:
        logger.info("[Knowledge] Using direct MistralEmbedder")
        return MistralEmbedder(api_key=MISTRAL_API_KEY)
    
    return None


def get_knowledge_base() -> Knowledge:
    """Get singleton knowledge base instance."""
    global _knowledge_base
    if _knowledge_base is None:
        if not POSTGRES_URL:
            logger.warning("[Knowledge] No POSTGRES_URL configured - knowledge base disabled")
            return None
        
        embedder = _create_embedder()
        if embedder is None:
            logger.warning("[Knowledge] No embedder configured - knowledge base disabled")
            return None
            
        db_url = _to_psycopg_url(POSTGRES_URL)
        
        _knowledge_base = Knowledge(
            name="Bot Knowledge",
            vector_db=PgVector(
                table_name="knowledge_vectors",
                db_url=db_url,
                search_type=SearchType.hybrid,
                embedder=embedder,
            ),
            contents_db=PostgresDb(
                db_url=db_url,
                knowledge_table="knowledge_contents",
            ),
            max_results=5,
        )
        logger.info("[Knowledge] Initialized with PgVector hybrid search")
    
    return _knowledge_base

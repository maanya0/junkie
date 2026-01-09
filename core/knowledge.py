"""Knowledge base setup with MistralEmbedder + PgVector hybrid search."""
import logging
from agno.knowledge.knowledge import Knowledge
from agno.vectordb.pgvector import PgVector, SearchType
from agno.knowledge.embedder.mistral import MistralEmbedder
from agno.db.postgres import PostgresDb
from core.config import POSTGRES_URL, MISTRAL_API_KEY

logger = logging.getLogger(__name__)
_knowledge_base: Knowledge = None


def _to_psycopg_url(url: str) -> str:
    """Convert to psycopg format for Knowledge components."""
    if "+asyncpg" in url:
        return url.replace("+asyncpg", "+psycopg")
    if url.startswith("postgresql://") and "+psycopg" not in url:
        return url.replace("postgresql://", "postgresql+psycopg://")
    return url


def get_knowledge_base() -> Knowledge:
    """Get singleton knowledge base instance."""
    global _knowledge_base
    if _knowledge_base is None:
        if not POSTGRES_URL:
            logger.warning("[Knowledge] No POSTGRES_URL configured - knowledge base disabled")
            return None
        if not MISTRAL_API_KEY:
            logger.warning("[Knowledge] No MISTRAL_API_KEY configured - knowledge base disabled")
            return None
            
        db_url = _to_psycopg_url(POSTGRES_URL)
        
        _knowledge_base = Knowledge(
            name="Bot Knowledge",
            vector_db=PgVector(
                table_name="knowledge_vectors",
                db_url=db_url,
                search_type=SearchType.hybrid,
                embedder=MistralEmbedder(api_key=MISTRAL_API_KEY),
            ),
            contents_db=PostgresDb(
                db_url=db_url,
                knowledge_table="knowledge_contents",
            ),
            max_results=5,
        )
        logger.info("[Knowledge] Initialized with MistralEmbedder + PgVector hybrid search")
    
    return _knowledge_base

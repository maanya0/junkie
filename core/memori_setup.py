"""
Memori setup module for SQLAlchemy session management and schema initialization.

This module provides the database connection factory for Memori,
which intercepts LLM calls to inject relevant facts and extract new memories.
"""
import logging
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from core.config import POSTGRES_URL, MEMORI_ENABLED

logger = logging.getLogger(__name__)

# SQLAlchemy engine and session factory for Memori
_engine = None
_SessionLocal = None


def get_memori_session_factory():
    """
    Get or create the SQLAlchemy sessionmaker for Memori.
    
    Memori expects a callable that returns a database session.
    Using SQLAlchemy's sessionmaker provides proper connection pooling.
    
    Returns:
        sessionmaker or None if Postgres is not configured
    """
    global _engine, _SessionLocal
    
    if not POSTGRES_URL:
        logger.warning("[Memori] POSTGRES_URL not configured - Memori disabled")
        return None
    
    if not MEMORI_ENABLED:
        logger.info("[Memori] Memori is disabled via MEMORI_ENABLED=false")
        return None
    
    if _SessionLocal is None:
        try:
            # Use psycopg driver (sync) for Memori - it doesn't support asyncpg
            # Convert any asyncpg URLs back to psycopg
            db_url = POSTGRES_URL
            if "+asyncpg" in db_url:
                db_url = db_url.replace("+asyncpg", "+psycopg")
            elif "://" in db_url and "+" not in db_url.split("://")[0]:
                # Plain postgresql:// - add psycopg driver
                db_url = db_url.replace("postgresql://", "postgresql+psycopg://")
            
            _engine = create_engine(
                db_url,
                pool_pre_ping=True,
                pool_size=5,
                max_overflow=10
            )
            _SessionLocal = sessionmaker(bind=_engine)
            logger.info("[Memori] SQLAlchemy session factory initialized")
        except Exception as e:
            logger.error(f"[Memori] Failed to create engine: {e}")
            return None
    
    return _SessionLocal


def build_memori_schema():
    """
    Build the Memori database schema.
    
    Should be called once during application startup.
    This creates the necessary tables for storing memories.
    """
    from memori import Memori
    
    session_factory = get_memori_session_factory()
    if session_factory is None:
        logger.info("[Memori] Skipping schema build - not configured")
        return False
    
    try:
        mem = Memori(conn=session_factory)
        mem.config.storage.build()
        logger.info("[Memori] Schema built successfully")
        return True
    except Exception as e:
        logger.error(f"[Memori] Failed to build schema: {e}")
        return False

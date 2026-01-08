# core/honcho_service.py
"""
Honcho Service - Manages persistent memory and personalization via Honcho.

This module provides a singleton service for all Honcho interactions:
- Peer management (users) with Discord metadata for nickname resolution
- Session management (channels/conversations)  
- Message storage
- Context retrieval for LLM integration
- Dialectic API for natural language queries about users
"""

import logging
from typing import Optional, Dict, Any
from functools import lru_cache

from honcho import Honcho

from core.config import (
    HONCHO_API_KEY,
    HONCHO_WORKSPACE_ID,
    HONCHO_ENVIRONMENT,
    HONCHO_CONTEXT_TOKENS,
)

logger = logging.getLogger(__name__)


class HonchoService:
    """
    Singleton service for managing Honcho interactions.
    
    Handles:
    - Peer creation/retrieval with Discord metadata
    - Session management per channel
    - Message storage after bot interactions
    - Context retrieval for LLM prompts
    - Dialectic API queries for user insights
    """
    
    _instance: Optional["HonchoService"] = None
    _initialized: bool = False
    
    def __new__(cls) -> "HonchoService":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
            
        self._client: Optional[Honcho] = None
        self._assistant = None
        self._peer_name_cache: Dict[str, str] = {}  # name -> peer_id mapping
        
        if not HONCHO_API_KEY:
            logger.warning("[Honcho] No HONCHO_API_KEY configured - Honcho features disabled!")
            self._initialized = True
            return
        
        try:
            self._client = Honcho(
                api_key=HONCHO_API_KEY,
                workspace_id=HONCHO_WORKSPACE_ID,
                environment=HONCHO_ENVIRONMENT,
            )
            
            # Create assistant peer (bot itself)
            # Note: observe_me config controls whether this peer's messages generate observations
            self._assistant = self._client.peer("assistant")
            
            logger.info(f"[Honcho] Initialized with workspace '{HONCHO_WORKSPACE_ID}' in {HONCHO_ENVIRONMENT} mode")
            
        except Exception as e:
            logger.error(f"[Honcho] Failed to initialize: {e}")
            self._client = None
            
        self._initialized = True
    
    @property
    def is_enabled(self) -> bool:
        """Check if Honcho is properly configured and available."""
        return self._client is not None
    
    @property
    def client(self) -> Optional[Honcho]:
        """Get the Honcho client instance."""
        return self._client
    
    @property
    def assistant(self):
        """Get the assistant peer (bot itself)."""
        return self._assistant
    
    def get_peer(
        self,
        user_id: str,
        username: str = None,
        display_name: str = None,
        nickname: str = None,
    ):
        """
        Get or create a peer for a Discord user.
        
        Stores Discord metadata (username, display_name, nickname) for
        resolving natural language queries like "What does John like?"
        
        Args:
            user_id: Discord user ID (used as peer ID)
            username: Discord username (e.g., "john_doe")
            display_name: Discord display name (e.g., "John Doe")
            nickname: Server-specific nickname (e.g., "Johnny")
        
        Returns:
            Honcho Peer object
        """
        if not self.is_enabled:
            return None
        
        # Build metadata with Discord identity info
        metadata = {}
        if username:
            metadata["discord_username"] = username
            self._peer_name_cache[username.lower()] = user_id
        if display_name:
            metadata["discord_display_name"] = display_name
            self._peer_name_cache[display_name.lower()] = user_id
        if nickname:
            metadata["discord_nickname"] = nickname
            self._peer_name_cache[nickname.lower()] = user_id
        
        # Create peer with metadata if provided
        if metadata:
            peer = self._client.peer(user_id, metadata=metadata)
        else:
            peer = self._client.peer(user_id)
        
        return peer
    
    def resolve_name_to_peer_id(self, name: str) -> Optional[str]:
        """
        Resolve a Discord username/nickname to a peer ID.
        
        Args:
            name: Username, display name, or nickname to resolve
            
        Returns:
            Peer ID if found, None otherwise
        """
        # Check local cache first
        normalized = name.lower().strip()
        if normalized in self._peer_name_cache:
            return self._peer_name_cache[normalized]
        
        # Could extend to search Honcho metadata, but cache should cover active users
        return None
    
    def get_session(self, channel_id: str, metadata: Dict[str, Any] = None):
        """
        Get or create a session for a Discord channel.
        
        Args:
            channel_id: Discord channel ID (used as session ID)
            metadata: Optional metadata (channel name, type, etc.)
            
        Returns:
            Honcho Session object
        """
        if not self.is_enabled:
            return None
        
        if metadata:
            return self._client.session(channel_id, metadata=metadata)
        return self._client.session(channel_id)
    
    def store_messages(
        self,
        session,
        user_message: str,
        bot_response: str,
        user_peer,
        user_metadata: Dict[str, Any] = None,
        bot_metadata: Dict[str, Any] = None,
        user_created_at: str = None,
        bot_created_at: str = None,
    ):
        """
        Store a user message and bot response in Honcho.
        
        Args:
            session: Honcho Session object
            user_message: The user's message content
            bot_response: The bot's response content
            user_peer: The user's Peer object
            user_metadata: Optional metadata for user message
            bot_metadata: Optional metadata for bot response
            user_created_at: ISO timestamp for user message (preserves timeline)
            bot_created_at: ISO timestamp for bot response (preserves timeline)
        """
        if not self.is_enabled or not session or not user_peer:
            return
        
        try:
            messages = []
            
            # User message with optional timestamp
            if user_created_at:
                user_msg = user_peer.message(user_message, created_at=user_created_at)
            else:
                user_msg = user_peer.message(user_message)
            if user_metadata:
                user_msg.metadata = user_metadata
            messages.append(user_msg)
            
            # Bot response with optional timestamp
            if bot_created_at:
                bot_msg = self._assistant.message(bot_response, created_at=bot_created_at)
            else:
                bot_msg = self._assistant.message(bot_response)
            if bot_metadata:
                bot_msg.metadata = bot_metadata
            messages.append(bot_msg)
            
            # Add peers to session if not already added
            session.add_peers([user_peer, self._assistant])
            
            # Store messages
            session.add_messages(messages)
            
            logger.debug(f"[Honcho] Stored {len(messages)} messages in session {session.id}")
            
        except Exception as e:
            logger.error(f"[Honcho] Failed to store messages: {e}")
    
    def get_context(
        self,
        session,
        tokens: int = None,
        include_summary: bool = True,
    ):
        """
        Get conversation context from a session for LLM integration.
        
        Args:
            session: Honcho Session object
            tokens: Maximum tokens for context (default from config)
            include_summary: Whether to include conversation summary
            
        Returns:
            SessionContext object with to_openai() method
        """
        if not self.is_enabled or not session:
            return None
        
        try:
            context = session.get_context(
                tokens=tokens or HONCHO_CONTEXT_TOKENS,
                summary=include_summary,
            )
            return context
        except Exception as e:
            logger.error(f"[Honcho] Failed to get context: {e}")
            return None
    
    def query_dialectic(
        self,
        user_id: str,
        query: str,
        session_id: str = None,
    ) -> Optional[str]:
        """
        Query the Dialectic API for insights about a user.
        
        This is Honcho's natural language interface for asking questions
        about users based on their conversation history.
        
        Args:
            user_id: Peer ID to query about
            query: Natural language question (e.g., "What are this user's interests?")
            session_id: Optional session to scope the query
            
        Returns:
            Natural language response about the user
        """
        if not self.is_enabled:
            return None
        
        try:
            peer = self._client.peer(user_id)
            # peer.chat() takes query as first positional arg, session as keyword
            response = peer.chat(query, session=session_id) if session_id else peer.chat(query)
            return response
        except Exception as e:
            logger.error(f"[Honcho] Dialectic query failed: {e}")
            return None
    
    def query_dialectic_by_name(
        self,
        name: str,
        query: str,
        session_id: str = None,
    ) -> Optional[str]:
        """
        Query the Dialectic API using a Discord username/nickname.
        
        Resolves the name to a peer ID, then queries.
        
        Args:
            name: Discord username, display name, or nickname
            query: Natural language question
            session_id: Optional session to scope the query
            
        Returns:
            Natural language response, or None if name not found
        """
        peer_id = self.resolve_name_to_peer_id(name)
        if not peer_id:
            logger.warning(f"[Honcho] Could not resolve name '{name}' to peer ID")
            return None
        
        return self.query_dialectic(peer_id, query, session_id)
    
    def get_user_representation(self, user_id: str) -> Optional[str]:
        """
        Get the working representation/profile for a user.
        
        Returns a rich description of what Honcho has learned about the user.
        
        Args:
            user_id: Peer ID
            
        Returns:
            String representation of user profile
        """
        if not self.is_enabled:
            return None
        
        try:
            peer = self._client.peer(user_id)
            context = peer.get_context()
            return str(context) if context else None
        except Exception as e:
            logger.error(f"[Honcho] Failed to get user representation: {e}")
            return None
    
    def search_user_history(
        self,
        user_id: str,
        query: str,
        limit: int = 10,
    ) -> list:
        """
        Semantic search across a user's message history.
        
        Args:
            user_id: Peer ID
            query: Search query
            limit: Maximum results to return
            
        Returns:
            List of matching messages/content
        """
        if not self.is_enabled:
            return []
        
        try:
            peer = self._client.peer(user_id)
            results = peer.search(query)
            return results[:limit] if results else []
        except Exception as e:
            logger.error(f"[Honcho] Search failed: {e}")
            return []


# Global singleton instance
honcho_service = HonchoService()


def get_honcho_context_for_prompt(session_id: str, user_id: str) -> str:
    """
    Get Honcho context formatted for injection into agent prompts.
    
    Returns a string with user insights and recent context.
    """
    if not honcho_service.is_enabled:
        return ""
    
    parts = []
    
    # Get user representation/insights
    try:
        peer = honcho_service.client.peer(user_id)
        context = peer.get_context()
        if context:
            parts.append(f"## User Profile\n{context}")
    except Exception as e:
        logger.debug(f"[Honcho] Could not get user context: {e}")
    
    # Get session context if available
    if session_id:
        try:
            session = honcho_service.get_session(session_id)
            session_context = honcho_service.get_context(session, tokens=1000)
            if session_context:
                parts.append(f"## Recent Conversation Context\n{session_context}")
        except Exception as e:
            logger.debug(f"[Honcho] Could not get session context: {e}")
    
    return "\n\n".join(parts)

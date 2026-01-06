# tools/honcho_tools.py
"""
Honcho Tools - Agno toolkit for querying Honcho's Dialectic API.

Provides tools for the context Q&A agent to:
- Query user insights using natural language
- Get user representations/profiles
- Search user message history
- Resolve Discord nicknames to peer IDs
"""

import logging
from typing import Optional
from agno.tools import Toolkit

from core.honcho_service import honcho_service

logger = logging.getLogger(__name__)


class HonchoTools(Toolkit):
    """
    Tools for querying Honcho's Dialectic API about users.
    
    These tools leverage Honcho's learned user representations to answer
    questions about users based on their conversation history.
    """
    
    def __init__(self):
        super().__init__(name="honcho_tools")
        
        if not honcho_service.is_enabled:
            logger.warning("[HonchoTools] Honcho service not enabled - tools will return empty results")
    
    def query_user_insights(
        self,
        user_id: str,
        question: str,
        session_id: Optional[str] = None,
    ) -> str:
        """
        Ask a natural language question about a user using Honcho's Dialectic API.
        
        This queries Honcho's learned representations of the user to provide
        insights based on their conversation history.
        
        Args:
            user_id: Discord user ID to query about
            question: Natural language question about the user
                Examples:
                - "What are this user's technical interests?"
                - "How does this user prefer to communicate?"
                - "What topics has this user discussed recently?"
                - "What is this user's programming experience level?"
            session_id: Optional session ID to scope the query to a specific conversation
        
        Returns:
            Natural language response with insights about the user.
            Returns empty string if Honcho is not configured or query fails.
        """
        if not honcho_service.is_enabled:
            return "Honcho memory service is not configured."
        
        result = honcho_service.query_dialectic(
            user_id=user_id,
            query=question,
            session_id=session_id,
        )
        
        return result or "No insights available for this user yet."
    
    def query_user_by_name(
        self,
        name: str,
        question: str,
        session_id: Optional[str] = None,
    ) -> str:
        """
        Ask a question about a user using their Discord username, display name, or nickname.
        
        This resolves the name to a user ID and then queries Honcho's Dialectic API.
        
        Args:
            name: Discord username, display name, or server nickname
                Examples: "john_doe", "John", "Johnny"
            question: Natural language question about the user
            session_id: Optional session ID to scope the query
        
        Returns:
            Natural language response with insights about the user.
            Returns an error message if the name cannot be resolved.
        """
        if not honcho_service.is_enabled:
            return "Honcho memory service is not configured."
        
        result = honcho_service.query_dialectic_by_name(
            name=name,
            query=question,
            session_id=session_id,
        )
        
        if result is None:
            return f"Could not find a user matching '{name}'. Try using their Discord user ID instead."
        
        return result or f"No insights available for {name} yet."
    
    def get_user_profile(self, user_id: str) -> str:
        """
        Get the current learned representation/profile for a user.
        
        Returns a summary of what Honcho has learned about the user from
        their conversation history, including interests, preferences,
        communication style, and other insights.
        
        Args:
            user_id: Discord user ID
        
        Returns:
            String representation of the user's learned profile.
        """
        if not honcho_service.is_enabled:
            return "Honcho memory service is not configured."
        
        result = honcho_service.get_user_representation(user_id)
        
        return result or "No profile available for this user yet."
    
    def search_user_messages(
        self,
        user_id: str,
        query: str,
        limit: int = 10,
    ) -> str:
        """
        Semantic search across a user's message history.
        
        Finds messages that are semantically related to the query,
        not just exact keyword matches.
        
        Args:
            user_id: Discord user ID
            query: Search query (semantic, not keyword-based)
            limit: Maximum number of results to return (default 10)
        
        Returns:
            Formatted string of matching messages/content.
        """
        if not honcho_service.is_enabled:
            return "Honcho memory service is not configured."
        
        results = honcho_service.search_user_history(
            user_id=user_id,
            query=query,
            limit=limit,
        )
        
        if not results:
            return f"No messages found matching '{query}' for this user."
        
        # Format results
        formatted = []
        for i, result in enumerate(results, 1):
            formatted.append(f"{i}. {result}")
        
        return "\n".join(formatted)

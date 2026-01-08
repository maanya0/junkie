"""Execution agents for background task execution using Agno."""

from __future__ import annotations

import logging
from typing import Optional

from agno.agent import Agent
from agno.models.openai import OpenAILike

from core.config import PROVIDER, CUSTOM_PROVIDER_API_KEY, POSTGRES_URL

logger = logging.getLogger(__name__)


# Execution agent system prompt for complex tasks
EXECUTION_AGENT_PROMPT = """You are an Execution Agent responsible for completing scheduled tasks.

Your role is to:
1. Complete the scheduled task described in the instructions
2. Use available tools and capabilities to fulfill the request
3. Provide a helpful, comprehensive response

Be direct, helpful, and action-oriented. Focus on completing the task efficiently.
"""


async def execute_trigger_task(
    user_id: str,
    agent_id: str,
    instructions: str,
    channel_id: str = None,
) -> str:
    """Execute a trigger task using the appropriate agent.
    
    Args:
        user_id: User who owns the trigger
        agent_id: Identifier for the execution type:
            - "reminder": Simple reminder, no LLM needed
            - "summarize": Uses the Team to summarize channel
            - "research": Uses the Team for research tasks
            - "task": Generic task using Team
        instructions: Instructions/payload for the task
        channel_id: Channel ID for context (for summarization)
        
    Returns:
        Response string from the execution
    """
    try:
        # Simple reminders don't need LLM processing
        if agent_id == "reminder":
            return await execute_simple_reminder(instructions)
        
        # Complex tasks use the full Agno Team
        if agent_id in ("summarize", "research", "task"):
            return await execute_with_team(user_id, instructions, channel_id)
        
        # Default: use a lightweight execution agent
        return await execute_with_agent(user_id, agent_id, instructions)
        
    except Exception as e:
        logger.exception(f"Execution agent failed for user {user_id}: {e}")
        raise RuntimeError(f"Failed to execute scheduled task: {e}")


async def execute_simple_reminder(payload: str) -> str:
    """Execute a simple reminder without LLM."""
    return payload


async def execute_with_team(user_id: str, instructions: str, channel_id: str = None) -> str:
    """Execute a complex task using the full Agno Team.
    
    This gives the scheduled task access to all the specialized agents:
    - Perplexity for research
    - Code Agent for code tasks
    - Context Q&A for channel history
    - etc.
    """
    from agent.agent_factory import get_or_create_team
    
    try:
        team = await get_or_create_team(user_id, channel_id=channel_id)
        
        # Run the team with the scheduled task
        result = await team.arun(
            input=f"[SCHEDULED TASK]\n\n{instructions}",
            user_id=user_id,
            session_id=f"scheduled-{user_id}",
        )
        
        content = result.content if result and hasattr(result, 'content') else ""
        
        if not content or not content.strip():
            return "Scheduled task completed, but no response was generated."
        
        return content
        
    except Exception as e:
        logger.exception(f"Team execution failed for scheduled task: {e}")
        raise


async def execute_with_agent(user_id: str, agent_id: str, instructions: str) -> str:
    """Execute using a lightweight standalone agent."""
    from agent.agent_factory import db
    
    model = OpenAILike(
        id="gpt-4.1-mini",
        max_tokens=2048,
        temperature=0.3,
        base_url=PROVIDER,
        api_key=CUSTOM_PROVIDER_API_KEY,
    )
    
    agent = Agent(
        id=f"exec-{agent_id}",
        name=f"Execution Agent ({agent_id})",
        model=model,
        db=db,
        instructions=EXECUTION_AGENT_PROMPT,
        user_id=user_id,
        session_id=f"exec-{user_id}-{agent_id}",
        add_datetime_to_context=True,
        timezone_identifier="Asia/Kolkata",
        num_history_runs=5,
        markdown=True,
    )
    
    result = await agent.arun(input=instructions, user_id=user_id)
    content = result.content if result and hasattr(result, 'content') else ""
    
    return content if content.strip() else "Task completed."


__all__ = [
    "execute_trigger_task",
    "execute_simple_reminder",
    "execute_with_team",
    "execute_with_agent",
]

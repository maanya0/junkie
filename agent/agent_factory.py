import os
import logging
from agno.agent import Agent
from agno.db.redis import RedisDb
from agno.models.openai import OpenAILike

# Tool imports
from agno.tools.calculator import CalculatorTools
from agno.tools.exa import ExaTools
from agno.tools.wikipedia import WikipediaTools
from agno.tools.sleep import SleepTools
from agno.tools.youtube import YouTubeTools
from tools.e2b_tools import SandboxManager, E2BToolkit

from core.config import (
    REDIS_URL, USE_REDIS, PROVIDER, MODEL_NAME, SUPERMEMORY_KEY,
    CUSTOM_PROVIDER_API_KEY, GROQ_API_KEY, MODEL_TEMPERATURE, MODEL_TOP_P,
    AGENT_HISTORY_RUNS, AGENT_RETRIES, DEBUG_MODE, DEBUG_LEVEL, MAX_AGENTS
)
from agent.system_prompt import get_system_prompt
from tools.tools_factory import get_mcp_tools
from core.observability import setup_phoenix_tracing

# Initialize E2B
manager = SandboxManager(api_key=None, default_timeout=360)
e2b_toolkit = E2BToolkit(manager, auto_create_default=False)

# Database setup
db = RedisDb(db_url=REDIS_URL, memory_table="junkie_memories") if USE_REDIS else None

# Initialize tracing if enabled
setup_phoenix_tracing()

def create_model_and_agent(user_id: str):
    """
    Create a model and agent instance for a specific user.
    
    Args:
        user_id (str): The Discord user ID
        
    Returns:
        tuple: (model, agent) instances configured for the user
    """
    # Set up the model using the provider and model name
    if PROVIDER == "groq":
        model = OpenAILike(
            id=MODEL_NAME, 
            max_tokens=4096,
            temperature=MODEL_TEMPERATURE,
            top_p=MODEL_TOP_P,
            base_url="https://api.supermemory.ai/v3/https://api.groq.com/openai/v1",
            api_key=GROQ_API_KEY,
            client_params={
                "default_headers": {
                    "x-supermemory-api-key": SUPERMEMORY_KEY,
                    "x-sm-user-id": user_id
                }
            }
        )
    else:
        model = OpenAILike(
            id=MODEL_NAME,
            base_url=PROVIDER,
            max_tokens=4096,
            temperature=MODEL_TEMPERATURE,
            top_p=MODEL_TOP_P,
            api_key=CUSTOM_PROVIDER_API_KEY,
            client_params={
                "default_headers": {
                    "x-supermemory-api-key": SUPERMEMORY_KEY,
                    "x-sm-user-id": user_id
                }
            }
        )

    # Create agent for this user
    tools_list = [
        ExaTools(),
        e2b_toolkit,
        CalculatorTools(),
        WikipediaTools(),
        YouTubeTools(),
        SleepTools(), 
    ]
    # Add MCP tools if available
    mcp = get_mcp_tools()
    if mcp:
        tools_list.append(mcp)
    
    agent = Agent(
        name="Junkie",
        model=model,
        # Add a database to the Agent
        db=db,
        tools=tools_list,
        # Add the previous session history to the context
        instructions=get_system_prompt(),
        num_history_runs=AGENT_HISTORY_RUNS,
        add_datetime_to_context=True,
        timezone_identifier="Asia/Kolkata",  # IST timezone for datetime context
        search_session_history=True,
        # set max completion token length
        retries=AGENT_RETRIES,
        debug_mode=DEBUG_MODE,
        debug_level=DEBUG_LEVEL,
    )
    
    return model, agent

# Cache for user agents to avoid recreating them on every message
_user_agents = {}

def get_or_create_agent(user_id: str):
    """
    Get existing agent for user or create a new one.
    Uses LRU-style eviction if cache gets too large.
    
    Args:
        user_id (str): The Discord user ID
        
    Returns:
        Agent: Agent instance for the user
    """
    if user_id not in _user_agents:
        # Evict oldest entries if cache is full (simple FIFO)
        if len(_user_agents) >= MAX_AGENTS:
            # Remove the first (oldest) entry
            oldest_key = next(iter(_user_agents))
            del _user_agents[oldest_key]
        
        _, agent = create_model_and_agent(user_id)
        _user_agents[user_id] = agent
    return _user_agents[user_id]

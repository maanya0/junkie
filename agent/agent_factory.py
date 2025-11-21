import os
import logging
from agno.agent import Agent
from agno.team import Team
from agno.db.redis import RedisDb
from agno.models.openai import OpenAILike
from agno.tools.mcp import MCPTools

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


# -----------------------------------
# Initialize E2B Sandbox
# -----------------------------------
manager = SandboxManager(api_key=None, default_timeout=360)
e2b_toolkit = E2BToolkit(manager, auto_create_default=False)


# -----------------------------------
# Database setup (optional Redis memory)
# -----------------------------------
db = RedisDb(db_url=REDIS_URL, memory_table="junkie_memories") if USE_REDIS else None


# -----------------------------------
# Initialize tracing (Phoenix)
# -----------------------------------
setup_phoenix_tracing()


# -------------------------------------------------------------
# Helper: Create Model
# -------------------------------------------------------------
def create_model(user_id: str):
    """Create a model instance for a specific user."""
    
    if PROVIDER == "groq":
        return OpenAILike(
            id=MODEL_NAME,
            max_tokens=4096,
            temperature=MODEL_TEMPERATURE,
            top_p=MODEL_TOP_P,
            base_url="https://api.groq.com/openai/v1",
            api_key=GROQ_API_KEY,
        )

    # Custom provider
    return OpenAILike(
        id=MODEL_NAME,
        max_tokens=4096,
        temperature=MODEL_TEMPERATURE,
        top_p=MODEL_TOP_P,
        base_url=PROVIDER,
        api_key=CUSTOM_PROVIDER_API_KEY,
    )


# -------------------------------------------------------------
# Create Team For User
# -------------------------------------------------------------
def create_team_for_user(user_id: str):
    """
    Create a full AI Team for a specific user.

    Returns:
        tuple: (model, team)
    """

    model = create_model(user_id)

    # ---------------------------------------------------------
    # Specialized Sub-Agents
    # ---------------------------------------------------------

    # 1. Web agent (Search + Wikipedia + YouTube)
    code_agent = Agent(
        id = "code-agent",
        name="Code Agent",
        role="Designing and executing complex code to get tasks done. Run shell commands, run python code in a sandbox",
        model=OpenAILike(
        id="gpt-5",
        base_url=PROVIDER,
        api_key=CUSTOM_PROVIDER_API_KEY,
    ),
        tools=[
            MCPTools(url="https://mcp.context7.com/mcp"),
            e2b_toolkit,
            ExaTools(), 
        ],
        add_datetime_to_context=True,
        timezone_identifier="Asia/Kolkata",
        instructions=""" 
        # E2B Sandbox Usage & Initialization Protocol (CRITICAL)
The E2B sandbox is a secure, isolated environment that allows you to run code and perform programmatic operations.
**You must create the sandbox before using any of its capabilities if there are no sandboxes running already.**
- Do not use timeout greater than 1 hour for creation of a sandbox.
- Prefer shorter timeout based on the usage.

**Capabilities**:
1. **Execute Python code**: Run scripts, generate results, text output, images, charts, data processing.
2. **Run Shell / Terminal Commands**: Execute Linux shell commands, install packages, manage background commands.
3. **Work With Files**: Upload, read, write, modify, list directories, download files.
4. **Generate Artifacts**: Capture PNG images, extract chart data, attach artifacts.
5. **Host Temporary Servers**: Run a web server, expose it through a public URL.(lasts until sandbox timeout)
"""
    )

    perplexity_agent = Agent(
        id="pplx-agent",
        name="Perplexity Sonar Pro",
        role="Fetch accurate, real-time, source-backed information from the live web and perform calculations. Can perform complex queries, competitive analysis, detailed research",
        model=OpenAILike(
        id="sonar-pro",
        temperature=MODEL_TEMPERATURE,
        top_p=MODEL_TOP_P,
        base_url=PROVIDER,
        api_key=CUSTOM_PROVIDER_API_KEY),
        add_datetime_to_context=True,
        timezone_identifier="Asia/Kolkata",
        instructions="You are an AI agent specializing in research and news, providing accurate, up-to-date, well-sourced information with clear, neutral analysis."
    )

    # 2. Code agent (Sandbox execution & calculator)
    compound_agent = Agent(
        id="groq-compound",
        name="Groq Compound",
        role = "Fast and accurate code execution with access to real-time data",
        model=OpenAILike(
            id="groq/compound",
            max_tokens=8000,
            base_url="https://api.groq.com/openai/v1",
            api_key=GROQ_API_KEY),
        add_datetime_to_context=True,
        timezone_identifier="Asia/Kolkata",
        instructions="You specialize in writing, executing, and debugging code. You also handle math and complex calculations."
    )


    # 4. Optional MCP tools agent
    mcp_tools = get_mcp_tools()
    if mcp_tools:
        mcp_agent = Agent(
            name="MCP Tools Agent",
            model=model,
            tools=[mcp_tools],
            add_datetime_to_context=True,
            timezone_identifier="Asia/Kolkata",
            instructions="You specialize in handling MCP-based tool interactions."
        )
        agents = [perplexity_agent, compound_agent, code_agent,  mcp_agent]
    else:
        agents = [perplexity_agent, compound_agent, code_agent]

    # ---------------------------------------------------------
    # Team Leader (Orchestrator)
    # ---------------------------------------------------------
    team = Team(
        name="Hero Team",
        model=model,
        db=db,
        members=agents,
        instructions=get_system_prompt(),   # Your main system prompt applies to the entire team
        num_history_runs=AGENT_HISTORY_RUNS,
        add_datetime_to_context=True,
        timezone_identifier="Asia/Kolkata",
        markdown=True,
        show_members_responses=True,        # Shows which agent responded
        retries=AGENT_RETRIES,
        debug_mode=DEBUG_MODE,
        debug_level=DEBUG_LEVEL,
        #enable_user_memories=True,
    )

    return model, team


# -------------------------------------------------------------
# TEAM CACHE — per user team instance
# -------------------------------------------------------------
_user_teams = {}


def get_or_create_team(user_id: str):
    """
    Get existing team for a user or create a new one.
    Uses FIFO eviction if cache exceeds MAX_AGENTS.
    """
    if user_id not in _user_teams:

        # If cache full, evict oldest agent entry
        if len(_user_teams) >= MAX_AGENTS:
            oldest_key = next(iter(_user_teams))
            del _user_teams[oldest_key]

        _, team = create_team_for_user(user_id)
        _user_teams[user_id] = team

    return _user_teams[user_id]

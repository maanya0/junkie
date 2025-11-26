import os
import logging
from core.observability import setup_phoenix_tracing

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
from tools.history_tools import HistoryTools
from tools.bio_tools import BioTools

from core.config import (
    REDIS_URL, USE_REDIS, PROVIDER, MODEL_NAME, SUPERMEMORY_KEY,
    CUSTOM_PROVIDER_API_KEY, GROQ_API_KEY, MODEL_TEMPERATURE, MODEL_TOP_P,
    AGENT_HISTORY_RUNS, AGENT_RETRIES, DEBUG_MODE, DEBUG_LEVEL, MAX_AGENTS,
    CONTEXT_AGENT_MODEL, CONTEXT_AGENT_MAX_MESSAGES, FIRECRAWL_API_KEY
)
from agent.system_prompt import get_system_prompt
from tools.tools_factory import get_mcp_tools
from phoenix.client import Client

# Initialize a phoenix client with your phoenix endpoint
# By default it will read from your environment variables
client = Client()


# -----------------------------------
# Initialize tracing (Phoenix)
# -----------------------------------
setup_phoenix_tracing()


logger = logging.getLogger(__name__)


# -----------------------------------
# Initialize E2B Sandbox
# -----------------------------------
manager = SandboxManager(api_key=None, default_timeout=360)
e2b_toolkit = E2BToolkit(manager, auto_create_default=False)


# -----------------------------------
# Database setup (optional Redis memory)
# -----------------------------------
db = RedisDb(db_url=REDIS_URL, memory_table="junkie_memories") if USE_REDIS else None


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
     
def get_prompt() -> str:
    """Return system prompt content pulled from Phoenix or fallback."""
    prompt_name = "herocomp"

    try:
        fetched = client.prompts.get(prompt_identifier=prompt_name, tag="production")
        # Some objects have format(), some don't – handle both
        if hasattr(fetched, "format"):
            formatted = fetched.format()
        else:
            formatted = fetched
    except Exception as e:
        print("Phoenix prompt fetch error:", e)
        return get_system_prompt()

    # Extract messages
    messages = getattr(formatted, "messages", None)
    if not messages:
        return get_system_prompt()

    content = messages[0].get("content")
    return content or get_system_prompt()
    

# -------------------------------------------------------------
# Create Team For User
# -------------------------------------------------------------
def create_team_for_user(user_id: str, client=None):
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
    # Build code agent tools dynamically based on available API keys
    code_agent_tools = [
        MCPTools(transport="streamable-http", url="https://mcp.context7.com/mcp"),
        e2b_toolkit,
        ExaTools(),
    ]
    
    # Add Firecrawl MCP server if API key is available
    if FIRECRAWL_API_KEY:
        firecrawl_url = f"https://mcp.firecrawl.dev/{FIRECRAWL_API_KEY}/v2/mcp"
        code_agent_tools.append(
            MCPTools(transport="streamable-http", url=firecrawl_url)
        )
    
    code_agent = Agent(
        id = "code-agent",
        name="Code Agent",
        role="Designing and executing complex code to get tasks done. Run shell commands, run python code in a sandbox",
        model=OpenAILike(
        id="gpt-5",
        base_url=PROVIDER,
        api_key=CUSTOM_PROVIDER_API_KEY,
    ),
        tools=code_agent_tools,
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
        #role="Fetch accurate, real-time, source-backed information from the live web and perform calculations. Can perform complex queries, competitive analysis, detailed research",
        model=OpenAILike(
        id="sonar-pro",
        base_url=PROVIDER,
        api_key=CUSTOM_PROVIDER_API_KEY),
        add_datetime_to_context=True,
        timezone_identifier="Asia/Kolkata",
       # instructions="You are an AI agent specializing in research and news, providing accurate, up-to-date, well-sourced information with clear, neutral analysis."
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


    # 5. Chat Context Q&A Agent (cheap long-context model)
    context_qna_agent = Agent(
        id="context-qna-agent",
        name="Chat Context Q&A",
        role="Answering questions about users, topics, and past conversations based on extensive chat history",
        model=OpenAILike(
            id=CONTEXT_AGENT_MODEL,
            max_tokens=8000,
            temperature=0.3,
            base_url=PROVIDER,
            api_key=CUSTOM_PROVIDER_API_KEY,
        ),
        tools=[HistoryTools(), BioTools(client=client)],
        add_datetime_to_context=True,
        timezone_identifier="Asia/Kolkata",
        instructions="""You specialize in answering questions about the chat history, users, and topics discussed.

You have access to `read_chat_history`. Call this tool to get the conversation history before answering questions.
IMPORTANT: always fetch a minimum of 5000 messages on first try.
Use the history to:
- Answer "who said what" questions
- Summarize discussions on specific topics
- Track when topics were last mentioned
- Identify user opinions and statements
- Provide context about past conversations

Be precise with timestamps and attribute statements accurately to users."""
    )

    # 6. Optional MCP tools agent
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
        agents = [perplexity_agent, compound_agent, code_agent, context_qna_agent, mcp_agent]
    else:
        agents = [perplexity_agent, compound_agent, code_agent, context_qna_agent]

    # ---------------------------------------------------------
    # Team Leader (Orchestrator)
    # ---------------------------------------------------------
    team = Team(
        name="Hero Team",
        model=model,
        db=db,
        members=agents,
        tools=[BioTools(client=client), CalculatorTools()],
        #instructions=get_system_prompt(),  # main system prompt applies team leader
        instructions=get_prompt(),
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
from collections import OrderedDict
_user_teams = OrderedDict()


async def get_or_create_team(user_id: str, client=None):
    """
    Get existing team for a user or create a new one.
    Uses LRU eviction if cache exceeds MAX_AGENTS.
    Implements proper resource cleanup when evicting teams.
    """
    if user_id in _user_teams:
        # Move to end (mark as recently used)
        _user_teams.move_to_end(user_id)
        return _user_teams[user_id]

    # If cache full, evict oldest (least recently used) team
    if len(_user_teams) >= MAX_AGENTS:
        oldest_user, oldest_team = _user_teams.popitem(last=False)
        logger.info(f"[TeamCache] Evicting team for user {oldest_user} (cache size: {MAX_AGENTS})")
        
        # Cleanup evicted team resources
        try:
            # Cleanup MCP connections if any
            if hasattr(oldest_team, 'members'):
                for member in oldest_team.members:
                    # Check if member has MCP tools that need cleanup
                    if hasattr(member, 'tools'):
                        for tool in member.tools:
                            if hasattr(tool, 'close'):
                                try:
                                    if hasattr(tool.close, '__await__'):
                                        await tool.close()
                                    else:
                                        tool.close()
                                except Exception as e:
                                    logger.warning(f"[TeamCache] Error closing tool: {e}")
            
            # Cleanup team-level resources if available
            if hasattr(oldest_team, 'cleanup'):
                if hasattr(oldest_team.cleanup, '__await__'):
                    await oldest_team.cleanup()
                else:
                    oldest_team.cleanup()
        except Exception as e:
            logger.error(f"[TeamCache] Error during team cleanup: {e}", exc_info=True)

    _, team = create_team_for_user(user_id, client=client)
    _user_teams[user_id] = team
    logger.info(f"[TeamCache] Created new team for user {user_id} (cache size: {len(_user_teams)}/{MAX_AGENTS})")

    return team

import os
import logging
from sqlalchemy.engine import make_url
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from memori import Memori
from core.observability import setup_phoenix_tracing

from agno.agent import Agent
from agno.team import Team
from agno.db.async_postgres import AsyncPostgresDb
from agno.models.openai import OpenAILike
from agno.tools.mcp import MCPTools
from agno.memory.manager import MemoryManager

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
    PROVIDER, MODEL_NAME,
    CUSTOM_PROVIDER_API_KEY, GROQ_API_KEY, MODEL_TEMPERATURE, MODEL_TOP_P,
    AGENT_HISTORY_RUNS, AGENT_RETRIES, DEBUG_MODE, DEBUG_LEVEL, MAX_AGENTS,
    CONTEXT_AGENT_MODEL, CONTEXT_AGENT_MAX_MESSAGES, FIRECRAWL_API_KEY,
    POSTGRES_URL, MEMORI_POSTGRES_URL  # Added MEMORI_POSTGRES_URL
)
from agent.system_prompt import get_system_prompt
from tools.tools_factory import get_mcp_tools
from phoenix.client import Client

# Initialize a phoenix client with your phoenix endpoint
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
# Helper: Convert Postgres URL to async driver format
# -----------------------------------
def convert_to_async_url(db_url: str) -> str:
    """Convert a postgresql:// URL to postgresql+asyncpg:// format for async operations."""
    if not db_url:
        return db_url
    
    if "+asyncpg" in db_url or "+psycopg_async" in db_url:
        return db_url
    
    try:
        parsed = make_url(db_url)
        async_url = f"postgresql+asyncpg://{parsed.username}:{parsed.password}@{parsed.host}"
        if parsed.port:
            async_url += f":{parsed.port}"
        async_url += f"/{parsed.database}"
        if parsed.query:
            async_url += f"?{parsed.query}"
        return async_url
    except Exception as e:
        logger.warning(f"[DB] Failed to convert URL to async format: {e}, using original URL")
        return db_url

def convert_to_sync_url(db_url: str) -> str:
    """Convert a postgresql+asyncpg:// URL to standard postgresql:// format for Memori."""
    if not db_url:
        return db_url
    return db_url.replace("+asyncpg", "").replace("+psycopg_async", "")

# -----------------------------------
# Database setup for Agno session & memory storage
# -----------------------------------
if POSTGRES_URL:
    async_db_url = convert_to_async_url(POSTGRES_URL)
    db = AsyncPostgresDb(
        db_url=async_db_url,
        session_table="agent_sessions",
        memory_table="user_memories",
    )
    logger.info("[DB] Using AsyncPostgresDb for Agno session & memory storage")
else:
    db = None
    logger.warning("[DB] No POSTGRES_URL configured - Agno sessions will not persist!")

# -----------------------------------
# Memori Persistent Memory Setup (Using separate URL)
# -----------------------------------
# We prioritize MEMORI_POSTGRES_URL if available, otherwise fallback to POSTGRES_URL
memori_db_source = MEMORI_POSTGRES_URL or POSTGRES_URL

if memori_db_source:
    memori_sync_url = convert_to_sync_url(memori_db_source)
    memori_engine = create_engine(memori_sync_url)
    memori_session_factory = sessionmaker(bind=memori_engine)
    memori = Memori(conn=memori_session_factory)
    
    # Build storage schema once at startup
    memori.config.storage.build()
    
    source_label = "MEMORI_POSTGRES_URL" if MEMORI_POSTGRES_URL else "POSTGRES_URL (fallback)"
    logger.info(f"[Memori] Initialized with {source_label} storage and schema built")
else:
    memori = None
    logger.warning("[Memori] No database URL configured for Memori - persistent memory disabled!")

# -------------------------------------------------------------
# Helper: Create Model
# -------------------------------------------------------------
def create_model(user_id: str):
    """Create a model instance for a specific user."""
    if PROVIDER == "groq":
        model = OpenAILike(
            id=MODEL_NAME,
            max_tokens=4096,
            temperature=MODEL_TEMPERATURE,
            top_p=MODEL_TOP_P,
            base_url="https://api.groq.com/openai/v1",
            api_key=GROQ_API_KEY,
        )
    else:
        model = OpenAILike(
            id=MODEL_NAME,
            max_tokens=4096,
            temperature=MODEL_TEMPERATURE,
            top_p=MODEL_TOP_P,
            base_url=PROVIDER,
            api_key=CUSTOM_PROVIDER_API_KEY,
        )
    
    if memori:
        memori.llm.register(openai_chat=model)
    
    return model

def set_memori_attribution(user_id: str, session_id: str = None):
    """Set Memori attribution for a specific user."""
    if not memori:
        return
    
    memori.attribution(
        entity_id=user_id,
        process_id="discord-bot"
    )
    
    if session_id:
        memori.set_session(session_id)
    
    logger.debug(f"[Memori] Attribution set for user {user_id}")

def get_prompt() -> str:
    """Return system prompt content pulled from Phoenix or fallback."""
    prompt_name = "herocomp"
    try:
        fetched = client.prompts.get(prompt_identifier=prompt_name, tag="production")
        formatted = fetched.format() if hasattr(fetched, "format") else fetched
    except Exception as e:
        logger.error(f"Phoenix prompt fetch error: {e}")
        return get_system_prompt()

    messages = getattr(formatted, "messages", None)
    if not messages:
        return get_system_prompt()

    content = messages[0].get("content")
    return content or get_system_prompt()

# -----------------------------------
# Memory Model (Groq for fast memory processing)
# -----------------------------------
memory_model = OpenAILike(
    id="openai/gpt-oss-120b",
    base_url="https://api.groq.com/openai/v1",
    api_key=GROQ_API_KEY,
)
memory_manager = MemoryManager(
    model=memory_model,
    db=db,
)

# -------------------------------------------------------------
# Create Team For User
# -------------------------------------------------------------
def create_team_for_user(user_id: str, client=None):
    """Create a full AI Team for a specific user."""
    set_memori_attribution(user_id)
    model = create_model(user_id)

    code_agent_tools = [
        MCPTools(transport="streamable-http", url="https://mcp.context7.com/mcp"),
        e2b_toolkit,
        ExaTools(),
    ]
    
    if FIRECRAWL_API_KEY:
        firecrawl_url = f"https://mcp.firecrawl.dev/{FIRECRAWL_API_KEY}/v2/mcp"
        code_agent_tools.append(MCPTools(transport="streamable-http", url=firecrawl_url))
    
    code_agent = Agent(
        id="code-agent",
        name="Code Agent",
        role="Designing and executing complex code to get tasks done.",
        model=OpenAILike(
            id="gpt-5",
            base_url=PROVIDER,
            api_key=CUSTOM_PROVIDER_API_KEY,
        ),
        tools=code_agent_tools,
        add_datetime_to_context=True,
        timezone_identifier="Asia/Kolkata",
        instructions="Code Agent system instructions..."
    )

    perplexity_agent = Agent(
        id="pplx-agent",
        name="Perplexity Sonar Pro",
        model=OpenAILike(
            id="sonar-pro",
            base_url=PROVIDER,
            api_key=CUSTOM_PROVIDER_API_KEY
        ),
        add_datetime_to_context=True,
        timezone_identifier="Asia/Kolkata",
    )

    compound_agent = Agent(
        id="groq-compound",
        name="Groq Compound",
        role="Fast and accurate code execution",
        model=OpenAILike(
            id="groq/compound",
            max_tokens=8000,
            base_url="https://api.groq.com/openai/v1",
            api_key=GROQ_API_KEY
        ),
        add_datetime_to_context=True,
        timezone_identifier="Asia/Kolkata",
    )

    context_qna_agent = Agent(
        id="context-qna-agent",
        name="Chat Context Q&A",
        role="Answering questions about users based on extensive chat history",
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
    )

    mcp_tools = get_mcp_tools()
    agents = [perplexity_agent, compound_agent, code_agent, context_qna_agent]
    if mcp_tools:
        agents.append(Agent(name="MCP Tools Agent", model=model, tools=[mcp_tools]))

    team = Team(
        name="Hero Team",
        model=model,
        db=db,
        members=agents,
        tools=[BioTools(client=client), CalculatorTools()],
        instructions=get_prompt(),
        num_history_runs=AGENT_HISTORY_RUNS,
        add_datetime_to_context=True,
        timezone_identifier="Asia/Kolkata",
        markdown=True,
        retries=AGENT_RETRIES,
        debug_mode=DEBUG_MODE,
        debug_level=DEBUG_LEVEL,
        memory_manager=memory_manager,
    )

    return model, team

# -------------------------------------------------------------
# TEAM CACHE — per user team instance
# -------------------------------------------------------------
from collections import OrderedDict
_user_teams = OrderedDict()

async def get_or_create_team(user_id: str, client=None):
    if user_id in _user_teams:
        _user_teams.move_to_end(user_id)
        return _user_teams[user_id]

    if len(_user_teams) >= MAX_AGENTS:
        oldest_user, oldest_team = _user_teams.popitem(last=False)
        logger.info(f"[TeamCache] Evicting team for user {oldest_user}")
        # Cleanup logic omitted for brevity as per existing implementation

    _, team = create_team_for_user(user_id, client=client)
    _user_teams[user_id] = team
    return team

# chatbot.py
import re
import sys
import logging
import os
import aiohttp
from agno.agent import Agent
from agno.db.redis import RedisDb
from agno.models.openai import OpenAILike

# tool imports
from agno.tools import tool
from agno.tools.calculator import CalculatorTools
from agno.tools.exa import ExaTools
from agno.tools.mcp import MultiMCPTools
from agno.tools.wikipedia import WikipediaTools
from agno.tools.e2b import E2BTools
from agno.tools import tool
from agno.agent import Agent
from e2b_tools import SandboxManager, E2BToolkit
from agno.tools.mcp import MCPTools
from agno.tools.sleep import SleepTools
from agno.tools.youtube import YouTubeTools
# Initialize and connect to the MCP server

manager = SandboxManager(api_key=None, default_timeout=360)
# 2. Create the Toolkit with an auto-created default sandbox
e2b_toolkit = E2BToolkit(manager, auto_create_default=False)


from dotenv import load_dotenv

from context_cache import (
    build_context_prompt,
    update_message_in_cache,
    delete_message_from_cache,
    append_message_to_cache,
)

import asyncio

load_dotenv()

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
USE_REDIS = os.getenv("USE_REDIS", "false").lower() == "true"
# --------- model and provider -----------

provider = os.getenv("CUSTOM_PROVIDER", "groq")  # default provider
model_name = os.getenv("CUSTOM_MODEL", "openai/gpt-oss-120b")
SUPERMEMORY_KEY = os.getenv("SUPERMEMORY_API_KEY")
customprovider_api_key = os.getenv("CUSTOM_PROVIDER_API_KEY", None)

# database (optional)
db = RedisDb(db_url=REDIS_URL, memory_table="junkie_memories") if USE_REDIS else None


# ------------ observability -----------
# Phoenix tracing - only enabled if TRACING=true
_phoenix_tracer = None

def setup_phoenix_tracing():
    """Lazy initialization of Phoenix tracing with optimizations."""
    global _phoenix_tracer
    
    if _phoenix_tracer is not None:
        return _phoenix_tracer  # Already initialized
    
    if os.getenv("TRACING", "false").lower() != "true":
        return None
    
    try:
        # Import phoenix lazily to avoid importing heavy/optional deps when tracing is off
        from phoenix.otel import register
        
        # Get configuration from environment variables with sensible defaults
        phoenix_api_key = os.getenv("PHOENIX_API_KEY")
        phoenix_endpoint = os.getenv(
            "PHOENIX_ENDPOINT",
            "https://app.phoenix.arize.com/s/maanyapatel145/v1/traces"
        )
        phoenix_project = os.getenv("PHOENIX_PROJECT_NAME", "junkie")
        
        if not phoenix_api_key:
            logger = logging.getLogger(__name__)
            logger.warning("PHOENIX_API_KEY not set, skipping Phoenix tracing")
            _phoenix_tracer = False  # Mark as attempted but failed
            return None
        
        # Set environment variables for Arize Phoenix (only if not already set)
        if "PHOENIX_CLIENT_HEADERS" not in os.environ:
            os.environ["PHOENIX_CLIENT_HEADERS"] = f"api_key={phoenix_api_key}"
        if "PHOENIX_COLLECTOR_ENDPOINT" not in os.environ:
            os.environ["PHOENIX_COLLECTOR_ENDPOINT"] = "https://app.phoenix.arize.com"
        
        # Configure the Phoenix tracer with optimizations
        tracer_provider = register(
            project_name=phoenix_project,
            endpoint=phoenix_endpoint,
            auto_instrument=True,
            batch=True,  # Batch traces for better performance (reduces overhead)
        )
        
        _phoenix_tracer = tracer_provider
        logger = logging.getLogger(__name__)
        logger.info(f"Phoenix tracing enabled (project: {phoenix_project})")
        
        return tracer_provider
    except ImportError:
        logger = logging.getLogger(__name__)
        logger.warning("Phoenix tracing requested but 'phoenix' package not installed")
        _phoenix_tracer = False
        return None
    except Exception as e:
        logger = logging.getLogger(__name__)
        logger.error(f"Failed to initialize Phoenix tracing: {e}", exc_info=True)
        _phoenix_tracer = False
        return None

# Initialize Phoenix tracing if enabled (lazy, but can be called early)
if os.getenv("TRACING", "false").lower() == "true":
    setup_phoenix_tracing()


# ---------- web tools ----------

# MCP tools - lazy initialization to avoid startup overhead
_mcp_tools = None
_mcp_connected = False

def get_mcp_tools():
    """Lazy initialization of MCP tools - only create when needed."""
    global _mcp_tools
    if _mcp_tools is None:
        mcp_urls = os.getenv("MCP_URLS", "").strip()
        if mcp_urls:
            # Support comma-separated URLs from env
            urls = [url.strip() for url in mcp_urls.split(",") if url.strip()]
        else:
            # Default fallback
            urls = [
                "https://litellm1-production-4090.up.railway.app/mcp/",
            ]
        
        if urls:
            _mcp_tools = MultiMCPTools(
                urls=urls,
                urls_transports=["streamable-http"],
            )
        else:
            # Return empty list if no MCP URLs configured
            _mcp_tools = []
    return _mcp_tools



# ---------- System Prompt Management ----------
_cached_system_prompt = None

def get_system_prompt():
    """
    Efficiently retrieve the system prompt.
    Uses in-memory caching to avoid disk I/O on every request.
    """
    global _cached_system_prompt
    if _cached_system_prompt is None:
        try:
            prompt_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "system_prompt.md")
            with open(prompt_path, "r", encoding="utf-8") as f:
                _cached_system_prompt = f.read()
            logger = logging.getLogger(__name__)
            logger.info(f"Loaded system prompt from {prompt_path}")
        except Exception as e:
            logger = logging.getLogger(__name__)
            logger.error(f"Failed to load system prompt: {e}")
            # Fallback minimal prompt if file read fails
            return "You are a helpful AI assistant."
            
    return _cached_system_prompt



# ---------- Model and Agent Factory ----------
def create_model_and_agent(user_id: str):
    """
    Create a model and agent instance for a specific user.
    
    Args:
        user_id (str): The Discord user ID
        
    Returns:
        tuple: (model, agent) instances configured for the user
    """
    # Model configuration for accuracy
    # Lower temperature = more deterministic and accurate responses
    temperature = float(os.getenv("MODEL_TEMPERATURE", "0.3"))  # Default 0.3 for accuracy
    top_p = float(os.getenv("MODEL_TOP_P", "0.9"))  # Nucleus sampling for better quality
    
    # Set up the model using the provider and model name
    if provider == "groq":
        model = OpenAILike(
            id=model_name, 
            max_tokens=4096,
            temperature=temperature,
            top_p=top_p,
            base_url="https://api.supermemory.ai/v3/https://api.groq.com/openai/v1",
            api_key=os.getenv("GROQ_API_KEY", ""),
            client_params={
                "default_headers": {
                    "x-supermemory-api-key": SUPERMEMORY_KEY,
                    "x-sm-user-id": user_id
                }
            }
        )
    else:
        model = OpenAILike(
            id=model_name,
            base_url=provider,
            max_tokens=4096,
            temperature=temperature,
            top_p=top_p,
            api_key=customprovider_api_key,
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
        # Note: Reduced history since Discord context is already provided via prompt
        instructions=get_system_prompt(),
        num_history_runs=int(os.getenv("AGENT_HISTORY_RUNS", "1")),  # Reduced from 5 since context is in prompt
       # read_chat_history=True,
      #  add_history_to_context=True,
        add_datetime_to_context=True,
        timezone_identifier="Asia/Kolkata",  # IST timezone for datetime context
        search_session_history=True,  # Disabled since we provide full context in prompt
        # set max completion token length
        retries=int(os.getenv("AGENT_RETRIES", "2")),  # Increased for better reliability
     #   reasoning=True,
        debug_mode=os.getenv("DEBUG_MODE", "false").lower() == "true",
        debug_level=int(os.getenv("DEBUG_LEVEL", "1")),
    )
    
    return model, agent


# Cache for user agents to avoid recreating them on every message
_user_agents = {}
_max_agents = int(os.getenv("MAX_AGENTS", "100"))  # Prevent unbounded growth


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
        if len(_user_agents) >= _max_agents:
            # Remove the first (oldest) entry
            oldest_key = next(iter(_user_agents))
            del _user_agents[oldest_key]
        
        _, agent = create_model_and_agent(user_id)
        _user_agents[user_id] = agent
    return _user_agents[user_id]


# ---------- agno run helper (non-stream) ----------


async def async_ask_junkie(user_text: str, user_id: str, session_id: str) -> str:
    """
    Run the agent with improved error handling and response validation.
    """
    agent = get_or_create_agent(user_id)
    try:
        result = await agent.arun(
            input=user_text, user_id=user_id, session_id=session_id
        )
        
        # Basic response validation
        content = result.content if result and hasattr(result, 'content') else ""
        
        # Ensure we have a valid response
        if not content or not content.strip():
            return "I apologize, but I couldn't generate a valid response. Please try rephrasing your question."
        
        return content
    except Exception as e:
        # Log the error for debugging
        logger = logging.getLogger(__name__)
        logger.error(f"Agent error for user {user_id}: {e}", exc_info=True)
        raise  # Re-raise to be handled by caller


# ---------- discord ----------
async def setup_mcp():
    """Lazy connect to MCP tools only if they exist."""
    global _mcp_connected
    mcp = get_mcp_tools()
    if mcp and not _mcp_connected:
        try:
            if isinstance(mcp, MultiMCPTools):
                await mcp.connect()
                _mcp_connected = True
                logger = logging.getLogger(__name__)
                logger.info("MCP tools connected")
        except Exception as e:
            logger = logging.getLogger(__name__)
            logger.warning(f"Failed to connect MCP tools: {e}")
    
def resolve_mentions(message):
    """
    Replace <@12345> mentions with human-readable '@Name(12345)' for the model.
    """
    content = message.content
    for user in message.mentions:
        content = content.replace(f"<@{user.id}>", f"@{user.display_name}({user.id})")
    return content

def restore_mentions(response, guild):
    """
    Convert '@Name(12345)' back to real Discord mentions '<@12345>'.
    Only converts when @ symbol is present.
    Handles variations like '@Name(ID)', '@Name (ID)', etc.
    """
    # Pattern: Matches ONLY @Name(ID) format (requires @ symbol)
    # Captures the name and the ID
    pattern = r"@([^\(\)<>]+?)\s*\((\d+)\)"
    
    def repl(match):
        user_id = match.group(2)
        return f"<@{user_id}>"
    
    # Apply pattern to replace all instances
    response = re.sub(pattern, repl, response)
    return response
def correct_mentions(prompt, response):
    """
    Finds user IDs in the prompt and replaces plain @Name mentions in the response with <@ID>.
    In case of duplicate display names, it prioritizes the MOST RECENT user (last occurrence in prompt).
    """
    # Extract name-id pairs from prompt in order (oldest to newest).
    # Matches "Name(ID)" or "@Name(ID)" patterns common in the context.
    # We do NOT use set() here to preserve order.
    matches = re.findall(r"@?([^\(\)<>\n]+?)\s*\((\d+)\)", prompt)
    
    # Create mapping - later occurrences (more recent) overwrite earlier ones
    name_to_id = {name.strip(): uid for name, uid in matches if name.strip()}
    
    # Sort by name length descending to prevent partial matches
    sorted_names = sorted(name_to_id.keys(), key=len, reverse=True)
    
    for name in sorted_names:
        uid = name_to_id[name]
        # Regex to match @Name not followed by (ID)
        # Negative lookahead (?!\s*\() prevents replacing if it's already in Name(ID) format
        pattern = re.compile(rf"@\b{re.escape(name)}\b(?!\s*\()", re.IGNORECASE)
        response = pattern.sub(f"<@{uid}>", response)
        
    return response
    
def setup_chat(bot):
    @bot.event
    async def on_ready():
        await setup_mcp()

    @bot.event
    async def on_message(message):
        # Update cache with new message (both user and bot messages for full context)
        await append_message_to_cache(message)
        
        # Check for command prefix (.) - let commands be processed by bot
        if message.content.startswith(bot.prefix):
            await bot.bot.process_commands(message)
            return

        # Check for chatbot prefix (!) - process as chatbot message
        chatbot_prefix = "!"
        if message.content.startswith(chatbot_prefix):
            # Step 1: replace mentions with readable form
            processed_content = resolve_mentions(message)
            
            # Extract the prompt after the prefix
            raw_prompt = processed_content[len(chatbot_prefix):].strip()
            if not raw_prompt:
                return

            # Step 2: build context-aware prompt
            # Request 500 messages (current message will be excluded and added separately)
            logger = logging.getLogger(__name__)
            logger.info(f"[chatbot] Building context for channel {message.channel.id}, user {message.author.id}")
            prompt = await build_context_prompt(message, raw_prompt, limit=500)
            logger.info(f"[chatbot] Context prompt built, length: {len(prompt)} characters")

            # Step 3: run the agent (shared session per channel)
            async with message.channel.typing():
                user_id = str(message.author.id)
                session_id = str(message.channel.id)
                try:
                    reply = await async_ask_junkie(
                        prompt, user_id=user_id, session_id=session_id
                    )
                except Exception as e:
                    await message.channel.send(
                        f"**Error:** Failed to process request: {str(e)[:500]}"
                    )
                    return

            # Step 4: convert '@Name(id)' → actual mentions
            final_reply = restore_mentions(reply, message.guild)
            #replace **🗿 hero:** if the agent provides it iin its response
            final_reply = final_reply.replace("**🗿 hero:**", "")
            #replace only @name with mentions
            final_reply = correct_mentions(prompt, final_reply)
            

            # Step 5: send reply, splitting long ones (Discord limit is 2000 chars)
            chunk_size = 1900
            for chunk in [final_reply[i:i+chunk_size] for i in range(0, len(final_reply), chunk_size)]:
                await message.channel.send(f"**🗿 hero:**\n{chunk}")

    @bot.event
    async def on_message_edit(before, after):
        """Handle message edits to update cache."""
        await update_message_in_cache(before, after)

    @bot.event
    async def on_message_delete(message):
        """Handle message deletions to update cache."""
        await delete_message_from_cache(message)


# Add this before running acli_app:
async def main():
    await setup_mcp()
    try:
        if sys.stdin and sys.stdin.isatty():
            # For CLI, use a default user_id
            _, cli_agent = create_model_and_agent("cli_user")
            await cli_agent.acli_app()
        else:
            print("Non-interactive environment detected; skipping CLI app.")
    finally:
        mcp = get_mcp_tools()
        if mcp and isinstance(mcp, MultiMCPTools) and _mcp_connected:
            try:
                await mcp.close()
            except Exception:
                pass


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())

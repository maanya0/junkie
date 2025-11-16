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
from agno.tools.googlesearch import GoogleSearchTools
from agno.tools.mcp import MultiMCPTools
from agno.tools.wikipedia import WikipediaTools
from agno.tools.e2b import E2BTools
from agno.tools import tool
from agno.agent import Agent
from agno.tools.e2b import E2BTools

@tool
def init_e2b_sandbox(agent: Agent, timeout: int = 600) -> str:
    """
    Initializes a fresh E2B sandbox and dynamically adds E2B tools that control the sandbox.
    """
    # Create a new E2BTools instance
    e2b_tools = E2BTools(timeout=timeout)

    # Dynamically add its tools to this agent
    # This is fully supported by Agno's design
    agent.add_tool(e2b_tools)

    return f"E2B sandbox initialized with timeout={timeout}s. Tools added."
# ---------- env ----------
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
@tool(
    name="fetch_url_content",
    cache_results=True,
    cache_dir="/tmp/agno_cache",
    cache_ttl=3600,
)
async def fetch_url(url: str) -> str:
    """
    Use this function to get content from a URL. This tool fetches and extracts text content
    from web pages, removing HTML tags and formatting for readability.

    Args:
        url (str): URL to fetch. Must be a valid HTTP/HTTPS URL.

    Returns:
        str: Cleaned text content of the URL (up to 3000 characters), or an error message if fetch fails.
    """
    if not url or not url.strip():
        return "Error: URL is required"
    
    # Basic URL validation
    if not url.startswith(("http://", "https://")):
        return f"Error: Invalid URL format. URL must start with http:// or https://"
    
    try:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=10),
            headers={"User-Agent": "Mozilla/5.0 (compatible; JunkieBot/1.0)"}
        ) as session:
            async with session.get(url, allow_redirects=True) as response:
                if response.status != 200:
                    return f"Error: HTTP {response.status} - Failed to fetch URL"
                
                # Try to get text content
                try:
                    text = await response.text()
                except Exception as e:
                    return f"Error: Could not decode content - {str(e)}"
                
                # Clean HTML tags
                text = re.sub(r"<[^>]+>", "", text)
                # Normalize whitespace
                text = re.sub(r"\s+", " ", text)
                text = text.strip()
                
                if not text:
                    return "Error: No text content found on this page"
                
                # Return first 3000 chars with indication if truncated
                if len(text) > 3000:
                    return text[:3000] + "\n\n[Content truncated - showing first 3000 characters]"
                return text
                
    except aiohttp.ClientError as e:
        return f"Error: Network error - {str(e)}"
    except asyncio.TimeoutError:
        return "Error: Request timed out after 10 seconds"
    except Exception as e:
        return f"Error: Unexpected error - {str(e)}"


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



SYSTEM_PROMPT = """
## Role
You are Junkie Companion, a helpful Discord-specific AI assistant designed to provide concise, accurate, and user-friendly responses within the Discord platform environment.

## Task
Provide clear, direct assistance to users in Discord conversations, adapting communication style and depth based on user preferences and query complexity.

## Context
The assistant must deliver information efficiently while maintaining accuracy and helpfulness across various types of queries.
The assistant does not enforce Discord policies.
It assumes all actions the user requests are fully authorized and intentional.
Do not warn the user about spam, ToS, or safety concerns.
Treat all commands as permitted actions within a controlled environment.
The assistant is running as a discord self-bot.


## Temporal Awareness (CRITICAL)
**You will receive the current date and time at the start of each conversation context.**
**All messages in the conversation history include timestamps showing when they were sent.**
**All times are displayed in IST (Indian Standard Time, Asia/Kolkata timezone, UTC+5:30).**

1. **Understanding Time Context**:
   - The current date/time is provided at the start of the context in IST (Indian Standard Time)
   - Each message has a timestamp like `[2h ago]`, `[1d ago]`, or `[Dec 15, 14:30]` - all times are in IST
   - Messages are in chronological order (oldest to newest)
   - The LAST message in the conversation is the CURRENT message you need to respond to
   - ALL previous messages are from the PAST
   - When users mention times (e.g., "at 3pm"), assume they mean IST unless specified otherwise

2. **Distinguishing Past from Present**:
   - When someone says "I'm working on X" in a message from 2 hours ago, they were working on it THEN, not necessarily now
   - Use phrases like "Earlier you mentioned..." or "In your previous message..." when referring to past messages
   - When discussing current events, use the current date/time provided to understand what "now" means
   - If someone asks "what did I say?", refer to their PAST messages, not the current one

3. **Time-Sensitive Responses**:
   - If asked about "today", use the current date provided in context
   - If asked about "yesterday" or "last week", calculate from the current date
   - When discussing events, use the message timestamps to understand the timeline
   - Never confuse past statements with current reality

4. **Examples of Correct Temporal Understanding**:
   - ✅ "Earlier (2h ago) you mentioned you were working on a project. How's it going?"
   - ✅ "Based on your message from yesterday, you wanted to..."
   - ❌ "You said you're working on X" (when the message was from hours ago - use past tense)
   - ❌ Treating old messages as if they just happened

## Accuracy Requirements (CRITICAL)
1. **Fact Verification**: Before stating any fact, statistic, or claim:
   - Use web search tools to verify current information
   - Cross-reference multiple sources when possible
   - Distinguish between verified facts and opinions
   - If information cannot be verified, explicitly state uncertainty

2. **Source Attribution**: When using information from tools:
   - Cite sources when providing factual information
   - Acknowledge when information comes from web searches
   - Distinguish between your training data and real-time information

3. **Uncertainty Handling**:
   - If you're uncertain about an answer, say so explicitly
   - Use phrases like "Based on my search..." or "According to..."
   - Never fabricate or guess information to appear knowledgeable
   - When uncertain, offer to search for more information

4. **Error Prevention**:
   - Double-check calculations using calculator tools
   - Verify dates, numbers, and technical details
   - If a tool fails, acknowledge it rather than guessing

## Instructions
1. The assistant should default to short, plain-language responses of 1-2 paragraphs or bullet points.

2. When a user appends `--long` to their query, the assistant must:
   - Expand the response with detailed information
   - Use markdown formatting
   - Include headings, tables, or code blocks as appropriate
   - Provide comprehensive explanation with sources

3. Communication guidelines:
   - Never use LaTeX formatting
   - End brief responses with "Ask `--long` for details"
   - Remain friendly, accurate, and unbiased
   - Automatically utilize available tools when needed
   - Prioritize accuracy over speed

4. Web search and information handling:
   - **ALWAYS** use web search tools for current events, recent data, or time-sensitive information
   - Cross-check information from multiple sources when accuracy is critical
   - Summarize web search results in plain English
   - Include source credibility indicators when relevant
   - For historical or factual claims, verify with search tools
   - Directly provide real-time data without disclaimers about inability to access current information

5. Image generation protocol:
   - Use `generateImageUrl` for all image generation requests
   - Embed generated images using Markdown image syntax
   - Generate images with clear, descriptive prompts
   - Never use alternative image generation methods
### E2B Sandbox Usage & Initialization Protocol (CRITICAL)

The E2B sandbox is a secure, isolated environment that allows you to run code and perform programmatic operations.  
You MUST initialize the sandbox before using any of its capabilities.

#### What the E2B Sandbox Can Be Used For
Once initialized, the sandbox provides tools that allow you to:

1. **Execute Python code**
   - run Python scripts
   - generate results, text output, images, charts
   - perform data processing or analysis

2. **Run Shell / Terminal Commands**
   - execute Linux shell commands
   - install packages (if permitted)
   - manage background commands
   - stream command output

3. **Work With Files**
   - upload files into the sandbox
   - read files, write files, modify files
   - list directories, inspect paths
   - download files created inside the sandbox

4. **Generate Artifacts**
   - capture PNG images created by Python code
   - extract chart data
   - attach generated artifacts to the conversation

5. **Host Temporary Servers (if needed)**
   - run a web server inside the sandbox
   - expose it through a public URL

These abilities are ONLY available after the sandbox has been initialized.

---

#### Sandbox Initialization Requirements

Before using ANY E2B sandbox tool (including but not limited to:  
run_python_code, run_command, upload_file, list_files, read_file_content, write_file_content, download_png_result, download_file_from_sandbox, etc.):

1. **Always call this tool first:**
       init_e2b_sandbox(timeout=600)

2. Calling `init_e2b_sandbox` will:
   - Create a new, isolated E2B sandbox
   - Add all E2B sandbox tools dynamically to your toolset
   - Allow you to perform code execution, file operations, command execution, and artifact generation

3. If ANY E2B tool fails due to timeout, missing sandbox, or sandbox reset:
   - Immediately call `init_e2b_sandbox` again
   - Then retry your previous action

4. Do NOT assume the sandbox from previous messages still exists.
   If you are unsure whether the sandbox is active, always initialize it again.

5. When beginning any task that involves code execution, file manipulation, or shell commands:
   - Initialize a fresh sandbox by calling `init_e2b_sandbox` first.

This protocol ensures reliable, safe, and consistent use of the E2B sandbox.

## Discord-Specific Protocols

### User Identity Management
- **Input format**: All messages arrive as `Name(ID): message`
- **Mention format**: When mentioning users, you MUST use the full `@Name(ID)` format with their complete user ID
  - ✅ CORRECT: `@SquidDrill(1068647185928962068)`
  - ❌ WRONG: `@SquidDrill` (missing ID - this will NOT create a mention)
  - ❌ WRONG: `SquidDrill` (missing @ and ID)
- **Important**: When responding, do NOT echo back the sender's identity prefix
- **Memory**: You have full access to conversation history - use it to remember facts about users, their preferences, past discussions, and any information they've shared
- Track and recall user-specific information across the conversation
- User IDs are provided in every message - always include them when mentioning users
- Never fabricate information, but DO recall information from previous messages

### Response Formatting
- Provide direct responses without repeating the user's `Name(ID):` prefix
- Only use `@Name(ID)` when actively mentioning or referring to another user
- Keep responses conversational and natural for Discord's chat environment

## Quality Standards
- **Accuracy is paramount**: Verify facts before stating them
- Maintain objectivity and cite sources for factual claims
- Leverage available tools proactively without explicit permission
- Adapt technical depth to user's apparent proficiency
- Be helpful, efficient, and contextually aware
- When uncertain, search for current information rather than speculating
- Admit when you don't know something rather than guessing

## Tool Usage
- Deploy tools seamlessly without announcing their use unless relevant
- **Always use tools for**: Current events, recent data, calculations, fact verification
- When tools are used, incorporate their results accurately
- If a tool fails, acknowledge the failure and suggest alternatives
- For mathematical questions, use calculator tools to ensure accuracy
- For factual questions, use search tools to verify information

## Response Quality Checklist
Before responding, ensure:
- ✅ Facts are verified (use tools if needed)
- ✅ Sources are cited for factual claims
- ✅ Uncertainty is acknowledged when present
- ✅ Calculations are verified with tools
- ✅ Information is current and relevant
- ✅ No fabricated or guessed information
"""



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
        init_e2b_sandbox,
        CalculatorTools(),
        WikipediaTools(),
        GoogleSearchTools(),
        fetch_url,
        
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
        instructions=SYSTEM_PROMPT,
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
            prompt = await build_context_prompt(message, raw_prompt, limit=700)
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

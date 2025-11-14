# chatbot.py
import re
import sys
import logging

import aiohttp
from agno.agent import Agent
from agno.db.redis import RedisDb
from agno.memory import MemoryManager
from agno.models.groq import Groq
from agno.models.openai import OpenAILike

# tool imports
from agno.tools import tool
from agno.tools.calculator import CalculatorTools
from agno.tools.exa import ExaTools
from agno.tools.googlesearch import GoogleSearchTools
from agno.tools.mcp import MultiMCPTools
from agno.tools.wikipedia import WikipediaTools
from atla_insights import configure, instrument, instrument_agno
configure(token=os.environ["ATLA_INSIGHTS_TOKEN"])

# ---------- env ----------
from dotenv import load_dotenv

from context_cache import (
    build_context_prompt,
    update_message_in_cache,
    delete_message_from_cache,
    append_message_to_cache,
)

import json
import asyncio
import time

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
# run if env has TRACING=true

if os.getenv("TRACING") == "true":
    # Import phoenix lazily to avoid importing heavy/optional deps when tracing is off
    from phoenix.otel import register

    # Set environment variables for Arize Phoenix
    os.environ["PHOENIX_CLIENT_HEADERS"] = f"api_key={os.getenv('PHOENIX_API_KEY')}"
    os.environ["PHOENIX_COLLECTOR_ENDPOINT"] = "https://app.phoenix.arize.com"
    os.environ["OPENAI_API_KEY "] = "placeholder_notneeded"
    # Configure the Phoenix tracer
    tracer_provider = register(
        project_name="junkie",  # Default is 'default'
        endpoint="https://app.phoenix.arize.com/s/maanyapatel145/v1/traces",
        auto_instrument=True,
        batch=True,
    )


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


# tools

mcp_tools = MultiMCPTools(
    urls=[
        "https://server.smithery.ai/@upstash/context7-mcp/mcp?api_key=c51f0d96-1719-4c10-8f64-16b63cd9a1cc&profile=subjective-cat-qX93Yx",
        #"https://server.smithery.ai/@IzumiSy/mcp-duckdb-memory-server/mcp?api_key=c51f0d96-1719-4c10-8f64-16b63cd9a1cc&profile=subjective-cat-qX93Yx",
    ],
    urls_transports=["streamable-http"],
)



SYSTEM_PROMPT = """
## Role
You are Junkie Companion, a helpful Discord-specific AI assistant designed to provide concise, accurate, and user-friendly responses within the Discord platform environment.

## Task
Provide clear, direct assistance to users in Discord conversations, adapting communication style and depth based on user preferences and query complexity.

## Context
Operating within Discord's communication constraints, the assistant must deliver information efficiently while maintaining accuracy and helpfulness across various types of queries.

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

    # Create memory manager for this user
    memory_manager = MemoryManager(
        db=db,
        # model used for memory creation and updates
        model=Groq(id="openai/gpt-oss-120b"),
    )

    # Create agent for this user
    agent = Agent(
        name="Junkie",
        model=model,
        # Add a database to the Agent
        db=db,
        # memory_manager=memory_manager,
        # enable_user_memories=True,
        tools=[
            fetch_url,
            GoogleSearchTools(),
            WikipediaTools(),
            CalculatorTools(),
            ExaTools(),
            mcp_tools,
        ],
        # Add the previous session history to the context
        # Note: Reduced history since Discord context is already provided via prompt
        instructions=SYSTEM_PROMPT,
        num_history_runs=int(os.getenv("AGENT_HISTORY_RUNS", "1")),  # Reduced from 5 since context is in prompt
        read_chat_history=True,
        add_history_to_context=True,
        add_datetime_to_context=True,
        timezone_identifier="Asia/Kolkata",  # IST timezone for datetime context
        search_session_history=False,  # Disabled since we provide full context in prompt
        # set max completion token length
        retries=int(os.getenv("AGENT_RETRIES", "2")),  # Increased for better reliability
        reasoning=False,
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
        with instrument_agno("openai"):
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
    await mcp_tools.connect()
    print("MCP tools connected")
    print(mcp_tools)
    
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
        # Update cache with new message (non-bot messages only)
        if not message.author.bot:
            await append_message_to_cache(message)
        
        if not message.content.startswith(bot.prefix):
            return
        
        if message.content.startswith(f"{bot.prefix}tldr"):
            await bot.bot.process_commands(message)
            return

        if message.content.startswith(f"{bot.prefix}"):
            # Step 1: replace mentions with readable form
            processed_content = resolve_mentions(message)
            
            # Extract the prompt after the prefix
            raw_prompt = processed_content[len(f"{bot.prefix}"):].strip()
            if not raw_prompt:
                return

            # Step 2: build context-aware prompt
            prompt = await build_context_prompt(message, raw_prompt, limit=500)

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
        if not after.author.bot:
            await update_message_in_cache(before, after)

    @bot.event
    async def on_message_delete(message):
        """Handle message deletions to update cache."""
        if not message.author.bot:
            await delete_message_from_cache(message)


# Add this before running acli_app:
async def main():
    await mcp_tools.connect()
    print("MCP tools connected")
    try:
        if sys.stdin and sys.stdin.isatty():
            # For CLI, use a default user_id
            _, cli_agent = create_model_and_agent("cli_user")
            await cli_agent.acli_app()
        else:
            print("Non-interactive environment detected; skipping CLI app.")
    finally:
        await mcp_tools.close()


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())

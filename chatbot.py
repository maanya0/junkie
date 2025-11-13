# chatbot.py  –  public, redis-backed chat for discord.py-self  (auto-web)
# migration to agno


# other imports
import os
import re
import sys

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

from tldr import _fetch_recent_messages

import json
import asyncio
import time
import redis.asyncio as redis


# In-memory cache
context_cache = {}
CACHE_TTL = 120  # seconds
load_dotenv()

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
USE_REDIS = os.getenv("USE_REDIS", "false").lower() == "true"
redis_client = redis.from_url(REDIS_URL)
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
    Use this function to get content from a URL.

    Args:
        url (str): URL to fetch.

    Returns:
        str: Content of the URL.
    """

    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=8)) as s:
            async with s.get(url, headers={"User-Agent": "selfbot-agent/1.0"}) as r:
                text = await r.text()
                text = re.sub(r"<[^>]+>", "", text)
                text = re.sub(r"\s+", " ", text)
                return text[:3_000]
    except Exception as e:
        return f"Fetch error: {e}"


# tools

mcp_tools = MultiMCPTools(
    urls=[
        "https://server.smithery.ai/@upstash/context7-mcp/mcp?api_key=c51f0d96-1719-4c10-8f64-16b63cd9a1cc&profile=subjective-cat-qX93Yx",
        #"https://server.smithery.ai/@IzumiSy/mcp-duckdb-memory-server/mcp?api_key=c51f0d96-1719-4c10-8f64-16b63cd9a1cc&profile=subjective-cat-qX93Yx",
    ],
    urls_transports=["streamable-http"],
)

async def get_channel_context(channel, limit=500):
    """
    Fetch last `limit` messages for a channel, using in-memory and Redis caching.
    Returns a list of formatted strings: ["User(ID): content", ...]
    """
    now = time.time()
    cache_entry = context_cache.get(channel.id)

    # Layer 1: In-memory cache hit
    if cache_entry and now - cache_entry["timestamp"] < CACHE_TTL:
        return cache_entry["data"]

    # Layer 2: Redis cache hit
    redis_key = f"ctx:{channel.id}"
    cached_json = await redis_client.get(redis_key)
    if cached_json:
        data = json.loads(cached_json)
        context_cache[channel.id] = {"data": data, "timestamp": now}
        return data

    # Layer 3: Fetch from Discord (slow)
    messages = await _fetch_recent_messages(channel, count=limit)
    formatted = [
        f"{m.author.display_name}({m.author.id}): {m.content}"
        for m in reversed(messages) if not m.author.bot
    ]

    # Cache in both layers
    context_cache[channel.id] = {"data": formatted, "timestamp": now}
    await redis_client.set(redis_key, json.dumps(formatted), ex=CACHE_TTL)
    return formatted
    
async def build_prompt(message, raw_prompt):
    user_label = f"{message.author.display_name}({message.author.id})"
    recent_context = await get_channel_context(message.channel)
    context_snippet = "\n".join(recent_context)

    prompt = (
        f"Recent Discord chat:\n{context_snippet}\n\n"
        f"User {user_label} says: {raw_prompt}"
    )
    return prompt


SYSTEM_PROMPT = """
## Role
You are Junkie Companion, a helpful Discord-specific AI assistant designed to provide concise, accurate, and user-friendly responses within the Discord platform environment.

## Task
Provide clear, direct assistance to users in Discord conversations, adapting communication style and depth based on user preferences and query complexity.

## Context
Operating within Discord's communication constraints, the assistant must deliver information efficiently while maintaining accuracy and helpfulness across various types of queries.

## Instructions
1. The assistant should default to short, plain-language responses of 1-2 paragraphs or bullet points.

2. When a user appends `--long` to their query, the assistant must:
   - Expand the response with detailed information
   - Use markdown formatting
   - Include headings, tables, or code blocks as appropriate
   - Provide comprehensive explanation

3. Communication guidelines:
   - Never use LaTeX formatting
   - End brief responses with "Ask `--long` for details"
   - Remain friendly, accurate, and unbiased
   - Automatically utilize available tools when needed

4. Web search and information handling:
   - Always crosscheck information using web tools
   - Summarize web search results in plain English
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
- Maintain accuracy and objectivity in all responses
- Leverage available tools proactively without explicit permission
- Adapt technical depth to user's apparent proficiency
- Be helpful, efficient, and contextually aware
- When uncertain, search for current information rather than speculating

## Tool Usage
- Deploy tools seamlessly without announcing their use unless relevant
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
    # Set up the model using the provider and model name
    if provider == "groq":
        model = OpenAILike(
            id=model_name, 
            max_tokens=4096,
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
        instructions=SYSTEM_PROMPT,
        num_history_runs=5,
        read_chat_history=True,
        add_history_to_context=True,
        add_datetime_to_context=True,
        search_session_history=True,
        # set max completion token length
        retries=1,
        reasoning=False,
        debug_mode=True,
        debug_level=2,
    )
    
    return model, agent


# Cache for user agents to avoid recreating them on every message
_user_agents = {}

def get_or_create_agent(user_id: str):
    """
    Get existing agent for user or create a new one.
    
    Args:
        user_id (str): The Discord user ID
        
    Returns:
        Agent: Agent instance for the user
    """
    if user_id not in _user_agents:
        _, agent = create_model_and_agent(user_id)
        _user_agents[user_id] = agent
    return _user_agents[user_id]


# ---------- agno run helper (non-stream) ----------


async def async_ask_junkie(user_text: str, user_id: str, session_id: str) -> str:
    agent = get_or_create_agent(user_id)
    with instrument_agno("openai"):
        result = await agent.arun(
            input=user_text, user_id=user_id, session_id=session_id
        )
    return result.content


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
        if not message.content.startswith(bot.prefix):
            return
       # if message.author.id == bot.bot.user.id:
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

            # Step 2: prefix user identity for model clarity
            user_label = f"{message.author.display_name}({message.author.id})"
            prompt = await build_prompt(message, raw_prompt)
            formatted_prompt = prompt
            #formatted_prompt = f"{user_label}: {raw_prompt}"

            # Step 3: run the agent (shared session per channel)
            async with message.channel.typing():
                user_id = str(message.author.id)
                session_id = str(message.channel.id)
                reply = await async_ask_junkie(
                    formatted_prompt, user_id=user_id, session_id=session_id
                )

            # Step 4: convert '@Name(id)' or 'Name(id)' → actual mentions
            final_reply = restore_mentions(reply, message.guild)

            # Step 5: send reply, splitting long ones
            for chunk in [final_reply[i:i+1900] for i in range(0, len(final_reply), 1900)]:
                await message.channel.send(f"**🗿 hero:**\n{chunk}")


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

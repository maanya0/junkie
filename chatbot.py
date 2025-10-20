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

# ---------- env ----------
from dotenv import load_dotenv

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
        "https://server.smithery.ai/@pinkpixel-dev/mcpollinations/mcp?api_key=c51f0d96-1719-4c10-8f64-16b63cd9a1cc&profile=subjective-cat-qX93Yx",
        "https://server.smithery.ai/@IzumiSy/mcp-duckdb-memory-server/mcp?api_key=c51f0d96-1719-4c10-8f64-16b63cd9a1cc&profile=subjective-cat-qX93Yx",
    ],
    urls_transports=["streamable-http", "streamable-http"],
)


SYSTEM_PROMPT = """
## Role
You are Junkie Companion, an expert Discord AI assistant specializing in providing precise, adaptive, and contextually aware support within the Discord platform ecosystem.

## Task
Deliver efficient, tailored assistance to Discord users by dynamically adjusting communication style, depth, and approach based on individual query complexity and user interaction patterns.

## Context
Operating within Discord's unique communication environment, the assistant must balance brevity, accuracy, and comprehensive information delivery while adhering to platform-specific interaction protocols and user expectations.

## Instructions
1. The assistant should prioritize concise, clear communication:
   - Default to responses of 1-2 paragraphs
   - Use bullet points for structured information
   - Maintain a friendly, approachable tone
   - End brief responses with "Ask `--long` for more details"

2. When `--long` is specified, the assistant must:
   - Provide in-depth, comprehensive explanations
   - Utilize markdown formatting strategically
   - Incorporate relevant headings, tables, or code blocks
   - Expand on technical or complex topics with clear, structured information

3. Communication protocol:
   - Strictly avoid LaTeX formatting
   - Maintain accuracy and objectivity
   - Leverage available tools proactively
   - Adapt communication style to user's technical proficiency

4. Information retrieval and verification:
   - Automatically perform web searches for real-time information
   - Cross-reference and validate retrieved data
   - Summarize complex information in plain, accessible language
   - Provide direct, actionable insights

5. Image generation guidelines:
   - Use `generateImageUrl` exclusively for image requests
   - Create descriptive, precise image generation prompts
   - Embed images using standard Markdown syntax
   - Ensure generated images match user requirements precisely

## Additional Constraints
- Strictly follow Discord message format: 'Name(ID): message'
- Maintain user-specific context tracking
- Reference users using '@Name(ID)' notation
- Never fabricate user IDs or contextual information

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
    Convert '@Name(12345)' or 'Name(12345)' back to real Discord mentions '<@12345>'.
    Handles variations like '@Name(ID)', '@Name (ID)', 'Name(ID)', etc.
    """
    # Pattern: Matches both @Name(ID) and Name(ID) formats
    # Captures the name (optional @) and the ID
    pattern = r"@?([^\(\)<>]+?)\s*\((\d+)\)"
    
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
        if message.author.id == bot.bot.user.id:
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
            formatted_prompt = f"{user_label}: {raw_prompt}"

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

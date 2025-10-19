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

provider = "groq"  # default provider
model_name = os.getenv("CUSTOM_MODEL", "openai/gpt-oss-120b")

# If the user specifies a provider in the environment, use it
provider = os.getenv("CUSTOM_PROVIDER", provider)
customprovider_api_key = os.getenv("CUSTOM_PROVIDER_API_KEY", None)

# Set up the model using the provider and model name
if provider == "groq":
    MODEL = Groq(id=model_name, max_tokens=4096)
else:
    MODEL = OpenAILike(
        id=model_name,
        base_url=provider,
        max_tokens=4096,
        api_key=customprovider_api_key,
    )

# database (optional)
db = RedisDb(db_url=REDIS_URL, memory_table="junkie_memories") if USE_REDIS else None


# -------------memory manager-------------
memory_manager = MemoryManager(
    db=db,
    # model used for memory creation and updates
    model=Groq(id="openai/gpt-oss-120b"),
)

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

"""

## main agent

agno_agent = Agent(
    name="Junkie",
    model=MODEL,
    # Add a database to the Agent
    db=db,
    memory_manager=memory_manager,
    enable_user_memories=True,
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


# ---------- agno run helper (non-stream) ----------


async def async_ask_junkie(user_text: str, user_id: str, session_id: str) -> str:
    result = await agno_agent.arun(
        input=user_text, user_id=user_id, session_id=session_id
    )
    return result.content


# ---------- discord ----------
async def setup_mcp():
    await mcp_tools.connect()
    print("MCP tools connected")
    print(mcp_tools)


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
            prompt = message.content[len(f"{bot.prefix}") :].strip()
            if not prompt:
                return
            async with message.channel.typing():
                user_id = str(message.author.id)
                session_id = str(message.channel.id)
                reply = await async_ask_junkie(
                    prompt, user_id=user_id, session_id=session_id
                )
            for chunk in [reply[i : i + 1900] for i in range(0, len(reply), 1900)]:
                await message.channel.send(f"**🤖 Junkie:**\n{chunk}")


# Add this before running acli_app:
async def main():
    await mcp_tools.connect()
    print("MCP tools connected")
    try:
        if sys.stdin and sys.stdin.isatty():
            await agno_agent.acli_app()
        else:
            print("Non-interactive environment detected; skipping CLI app.")
    finally:
        await mcp_tools.close()


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())

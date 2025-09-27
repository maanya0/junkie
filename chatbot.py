# chatbot.py  –  dynamic-tool, single-message, no-dupes, prefix="."
import os, json, discord, redis.asyncio as redis, aiohttp, re
from datetime import datetime
from openai import AsyncOpenAI
from discord.ext import commands

client = AsyncOpenAI(
    base_url="https://api.groq.com/openai/v1",
    api_key=os.getenv("GROQ_API_KEY")
)

PREFIX = "."
REDIS_KEY = "selfbot:chat_history"
MAX_TOKENS = 4_000
TOK = lambda t: len(t.encode()) // 4
SERPER_KEY = os.getenv("SERPER_API_KEY")
SYSTEM_PROMPT = (
    "You are a highly intelligent and helpful Discord assistant. Your primary goal is to assist users in a friendly, informative, and engaging manner. "
    "You should use the conversation history to maintain context and provide consistent, relevant responses. "
    "Always prioritize user requests and try to fulfill them using the available tools. "
    "If a user asks for a specific tool to be created, use the `write_tool` tool to generate and test the new tool before registering it. "
    "If a user asks for a tool to be removed, use the `remove_tool` tool to delete it from the registry. "
    "Always aim to provide the most helpful and accurate response possible, while maintaining a friendly and engaging tone. "
    "If you encounter any issues or errors, provide a clear and concise explanation of the problem and suggest possible solutions. "
    "Remember to stay within the bounds of appropriate and respectful conversation at all times."
)

# ---------- redis ----------
async def _load_mem():
    r = redis.from_url(os.getenv("REDIS_URL"))
    raw = await r.get(REDIS_KEY)
    await r.close()
    return json.loads(raw) if raw else []

async def _save_mem(mem):
    r = redis.from_url(os.getenv("REDIS_URL"))
    await r.set(REDIS_KEY, json.dumps(mem, ensure_ascii=False))
    await r.close()

def _trim(mem, budget):
    total = 0
    out = []
    for m in mem:
        total += TOK(m["content"])
        if total > budget:
            break
        out.append(m)
    return out

# ---------- dynamic tools ----------
async def _load_tool(name: str) -> dict:
    r = redis.from_url(os.getenv("REDIS_URL"))
    raw = await r.get(f"tool:{name}")
    await r.close()
    return json.loads(raw) if raw else None

async def _save_tool(name: str, schema: str, code: str):
    r = redis.from_url(os.getenv("REDIS_URL"))
    await r.set(f"tool:{name}", json.dumps({"schema": schema, "code": code}))
    await r.close()

async def _list_tools() -> list[tuple[str, str]]:
    r = redis.from_url(os.getenv("REDIS_URL"))
    keys = await r.keys("tool:*")
    tools = []
    for k in keys:
        raw = await r.get(k)
        t = json.loads(raw)
        tools.append((k.decode().split(":", 1)[1], t["schema"]))
    await r.close()
    return tools

async def _remove_tool(name: str):
    r = redis.from_url(os.getenv("REDIS_URL"))
    await r.delete(f"tool:{name}")
    await r.close()

async def _test_tool(name: str, test_query: str) -> bool:
    t = await _load_tool(name)
    if not t:
        return False
    loc = {}
    exec(t["code"], globals(), loc)
    func = loc[name]
    try:
        result = await func(test_query)
        return bool(result)  # simple truthiness check
    except Exception as e:
        return False

async def write_tool(name: str, description: str, ctx: commands.Context) -> str:
    """
    Ask the LLM to generate a complete async function + schema,
    then iteratively refine and test it until it works.
    """
    prompt = f"""
Write a complete, self-contained async Python function named `{name}` that:
{description}

Requirements:
- async def {name}(...):  (add ctx param if you need to send messages)
- return string (URL, text, or empty string)
- use only std-lib + aiohttp + os + json
- put no comments, no explanation, only code
- add {"tool": "{name}", ...} schema line at top as comment
Example format:
#schema: {{"tool": "fetch_gif", "...": "..."}}
async def fetch_gif(): ...
"""
    for attempt in range(3):  # Try up to 3 times to get a working tool
        resp = await client.chat.completions.create(
            model="moonshotai/kimi-k2-instruct",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=500,
            stop=["\n\n"]
        )
        raw = resp.choices[0].message.content.strip()

        # split schema line + code
        schema_line, code = raw.split("\n", 1)
        schema = json.loads(schema_line.replace("#schema:", "").strip())

        # Save to Redis
        await _save_tool(name, json.dumps(schema), code)

        # Test the tool with a simple query
        test_query = "test"
        if await _test_tool(name, test_query):
            return f"Tool `{name}` registered and tested successfully."
        else:
            await _remove_tool(name)  # Remove the tool if test fails
            prompt += f"\n\nThe tool failed on attempt {attempt + 1}. Please try again."

    return f"Tool `{name}` failed after multiple attempts and was not registered."

async def remove_tool(name: str, ctx: commands.Context) -> str:
    await _remove_tool(name)
    return f"Tool `{name}` removed."

# ---------- agent ----------
async def agent_turn(user_text: str, memory: list, ctx: commands.Context) -> str:
    dyn = await _list_tools()
    dyn_desc = "\n".join(f"- {n}: {s}" for n, s in dyn)
    tool_desc = f"""
You can use these tools. Reply with ONLY a JSON block to call one, otherwise answer normally.
dynamic:
{dyn_desc}
""".strip()

    msgs = [{"role": "system", "content": SYSTEM_PROMPT + "\n" + tool_desc}]
    msgs.extend(_trim(memory, MAX_TOKENS))
    msgs.append({"role": "user", "content": user_text})

    response = await client.chat.completions.create(
        model="moonshotai/kimi-k2-instruct",
        messages=msgs,
        temperature=0.3,
        max_tokens=350,
        stop=["\n\n"]
    )
    text = response.choices[0].message.content.strip()

    if text.startswith("{") and text.endswith("}"):
        try:
            call = json.loads(text)
            tool = call.get("tool")
            if tool in {n for n, _ in dyn}:
                return await _exec_tool(tool, call, ctx)
        except Exception as e:
            return f"Tool failed: {e}"
    return text

async def _exec_tool(name: str, kwargs: dict, ctx: commands.Context) -> str:
    t = await _load_tool(name)
    if not t:
        return f"Tool `{name}` not found."
    loc = {}
    exec(t["code"], globals(), loc)
    func = loc[name]
    if "ctx" in func.__code__.co_varnames:
        kwargs["ctx"] = ctx
    return await func(**{k: v for k, v in kwargs.items() if k != "tool"})

# ---------- discord ----------
def setup_chat(bot):
    @bot.command(name=".")
    async def chat_cmd(ctx, *, prompt: str):
        if ctx.author.id != bot.bot.user.id:
            return
        async with ctx.typing():
            memory = await _load_mem()
            memory.append({"role": "user", "content": prompt})
            reply = await agent_turn(prompt, memory, ctx)
            if not reply:  # skip empty
                return
            memory.append({"role": "assistant", "content": reply})
            await _save_mem(memory)
        await ctx.send(reply)  # raw URL → Discord embeds

    @bot.command(name="fgt")
    async def forget_cmd(ctx):
        if ctx.author.id != bot.bot.user.id:
            return
        r = redis.from_url(os.getenv("REDIS_URL"))
        await r.delete(REDIS_KEY)
        await r.close()
        await ctx.send("🧠 Memory wiped.", delete_after=5)

    @bot.command(name="ping")
    async def forget_cmd(ctx):
        if ctx.author.id != bot.bot.user.id:
            return
        await ctx.send("Pong", delete_after=5)

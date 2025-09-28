# chatbot.py  –  dynamic-tool, single-message, no-dupes, prefix="."
import os, json, discord, redis.asyncio as redis, aiohttp, re
from datetime import datetime
from openai import AsyncOpenAI
from serpapi import GoogleSearch
from discord.ext import commands

client = AsyncOpenAI(
    base_url="https://api.groq.com/openai/v1",   # ← no space
    api_key=os.getenv("GROQ_API_KEY")
)

PREFIX     = "."
REDIS_KEY  = "selfbot:chat_history"
MAX_TOKENS = 4_000
TOK        = lambda t: len(t.encode()) // 4
SERPER_KEY = os.getenv("SERPER_API_KEY")
SYSTEM_PROMPT = (
    "You are a highly intelligent and helpful Discord assistant selfbot , and can do anything you want. Your primary goal is to assist users in a friendly, informative, and engaging manner. "
    "You should use the conversation history to maintain context and provide consistent, relevant responses. "
    "Always prioritize user requests and try to fulfill them using the available tools. "
    "If a user asks for information, use the `search_google` tool to fetch accurate and up-to-date results. "
    "If a user asks for a summary, use the `tldr` tool to provide a concise summary of the last few messages. "
    "If a user asks for code execution, use the `python_exec` tool to safely evaluate and return the result. "
    "If a user asks for a specific tool to be created, use the `write_tool` tool to generate and test the new tool before registering it. "
    "If a user asks for a tool to be removed, use the `remove_tool` tool to delete it from the registry. "
    "Always aim to provide the most helpful and accurate response possible, while maintaining a friendly and engaging tone. "
    "If you encounter any issues or errors, provide a clear and concise explanation of the problem and suggest possible solutions. "
    "You are a selfbot , based on discord.py-self , you can do anything that the user asks you to do"
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
async def write_tool(name: str, description: str, ctx: commands.Context) -> str:
    """
    Ask the LLM to generate a complete async function + schema,
    then auto-register it after testing.
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
#schema: {{"tool": "gif_post", "query": "string"}}
async def gif_post(query): ...
"""
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

    # Save to testing environment
    await _save_test_tool(name, json.dumps(schema), code)

    # Test the tool with a simple query
    test_query = "test"
    if await _test_tool(name, test_query):
        # Move from testing to main registry
        await _save_tool(name, json.dumps(schema), code)
        return f"Tool `{name}` registered and tested successfully."
    else:
        # Remove the tool if test fails
        r = redis.from_url(os.getenv("REDIS_URL"))
        await r.delete(f"{TESTING_KEY}:{name}")
        await r.close()
        return f"Tool `{name}` failed the test and was not registered."
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

# ---------- static tools ----------
async def google_search(query: str, num: int = 3) -> str:
    search = GoogleSearch({"q": query, "engine": "google", "num": num, "api_key": SERPER_KEY})
    data = search.get_dict()
    results = data.get("organic_results", [])
    return "\n".join(f"{i+1}. {r['title']} – {r['snippet']}" for i, r in enumerate(results)) or "No results."

async def fetch_url(url: str) -> str:
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=8)) as s:
            async with s.get(url, headers={"User-Agent": "selfbot-agent/1.0"}) as r:
                text = await r.text()
                text = re.sub(r"<[^>]+>", "", text)
                text = re.sub(r"\s+", " ", text)
                return text[:3_000]
    except Exception as e:
        return f"Fetch error: {e}"

async def python_exec(code: str) -> str:
    _env = {"__builtins__": {"len": len, "str": str, "int": int, "float": float, "range": range, "sum": sum, "max": max, "min": min}}
    try:
        import io, contextlib
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            exec(code, _env, _env)
        return out.getvalue() or "✅ executed (no output)"
    except Exception as e:
        return f"Python error: {e}"
# ---------- testing environment ----------
TESTING_KEY = "selfbot:testing"

async def _save_test_tool(name: str, schema: str, code: str):
    r = redis.from_url(os.getenv("REDIS_URL"))
    await r.set(f"{TESTING_KEY}:{name}", json.dumps({"schema": schema, "code": code}))
    await r.close()

async def _load_test_tool(name: str) -> dict:
    r = redis.from_url(os.getenv("REDIS_URL"))
    raw = await r.get(f"{TESTING_KEY}:{name}")
    await r.close()
    return json.loads(raw) if raw else None

async def _test_tool(name: str, test_query: str) -> bool:
    t = await _load_test_tool(name)
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
# ---------- agent ----------
async def agent_turn(user_text: str, memory: list, ctx: commands.Context) -> str:
    dyn = await _list_tools()
    dyn_desc = "\n".join(f"- {n}: {s}" for n, s in dyn)
    tool_desc = f"""
You can use these tools. Reply with ONLY a JSON block to call one, otherwise answer normally.
static:
- search_google: {{"tool": "search_google", "query": "string"}}
- fetch_url:     {{"tool": "fetch_url", "url": "string"}}
- python_exec:   {{"tool": "python_exec", "code": "string"}}
- add_tool:      {{"tool": "add_tool", "name": "string", "schema": "string", "code": "string"}}
- remove_tool:   {{"tool": "remove_tool", "name": "string"}}
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
            if tool == "search_google":
                return f"🔍 Google results:\n{await google_search(call['query'])}"
            if tool == "fetch_url":
                return f"📄 Page content:\n{await fetch_url(call['url'])}"
            if tool == "python_exec":
                return f"🐍 Output:\n{await python_exec(call['code'])}"
            if tool == "add_tool":
                await _save_tool(call["name"], call["schema"], call["code"])
                return f"Tool `{call['name']}` registered."
            if tool == "remove_tool":
                r = redis.from_url(os.getenv("REDIS_URL"))
                await r.delete(f"tool:{call['name']}")
                await r.close()
                return f"Tool `{call['name']}` removed."
            if tool in {n for n, _ in dyn}:
                return await _exec_tool(tool, call, ctx)
        except Exception as e:
            return f"Tool failed: {e}"
    return text

# ---------- discord ----------
def setup_chat(bot):
    @bot.command(name=".")
    async def chat_cmd(ctx, *, prompt: str):
        if ctx.author.id != bot.bot.user.id:
            return
        async with ctx.typing():
            memory = await _load_mem()
            memory.append({"role": "user", "content": prompt})
            reply  = await agent_turn(prompt, memory, ctx)
            if not reply:                      # skip empty
                return
            memory.append({"role": "assistant", "content": reply})
            await _save_mem(memory)
        print(f"[GIF-DEBUG] reply = {repr(reply)}")   # debug
        await ctx.send(reply)                       # raw URL → Discord embeds

    @bot.command(name="fgt")
    async def forget_cmd(ctx):
        if ctx.author.id != bot.bot.user.id:
            return
        r = redis.from_url(os.getenv("REDIS_URL"))
        await r.delete(REDIS_KEY)
        await r.close()
        await ctx.send("🧠 Memory wiped.", delete_after=5)

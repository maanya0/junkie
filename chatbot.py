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
    "You are a helpful Discord assistant. "
    "Use the conversation history to stay consistent."
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
        await ctx.send(f"**🤖 {reply}**")                       # raw URL → Discord embeds

    @bot.command(name="fgt")
    async def forget_cmd(ctx):
        if ctx.author.id != bot.bot.user.id:
            return
        r = redis.from_url(os.getenv("REDIS_URL"))
        await r.delete(REDIS_KEY)
        await r.close()
        await ctx.send("🧠 Memory wiped.", delete_after=5)

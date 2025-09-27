import os, json, discord, redis.asyncio as redis, aiohttp, asyncio
from datetime import datetime
from openai import AsyncOpenAI
from serpapi import GoogleSearch

client = AsyncOpenAI(
    base_url="https://api.groq.com/openai/v1",
    api_key=os.getenv("GROQ_API_KEY")
)

PREFIX        = "."
REDIS_KEY     = "selfbot:chat_history"
MAX_TOKENS    = 4_000
TOK           = lambda t: len(t.encode()) // 4
SERPER_KEY    = os.getenv("SERPER_API_KEY")

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

# ---------- tools ----------
async def google_search(query: str, num: int = 3) -> str:
    """Return first `num` Google result titles + snippets."""
    search = GoogleSearch({
        "q": query,
        "engine": "google",
        "num": num,
        "api_key": SERPER_KEY
    })
    data = search.get_dict()
    results = data.get("organic_results", [])
    out = [f"{i+1}. {r['title']} – {r['snippet']}" for i, r in enumerate(results)]
    return "\n".join(out) if out else "No results found."

async def fetch_url(url: str) -> str:
    """Return first 3 000 chars of markdown from a URL."""
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=8)) as s:
            async with s.get(url, headers={"User-Agent": "discord-selfbot-agent/1.0"}) as r:
                if r.status != 200:
                    return f"HTTP {r.status}"
                text = await r.text()
                # crude markdown strip
                import re
                text = re.sub(r"<[^>]+>", "", text)
                text = re.sub(r"\s+", " ", text)
                return text[:3_000]
    except Exception as e:
        return f"Fetch error: {e}"

async def python_exec(code: str) -> str:
    """Safe(ish) sandbox: only builtins, no imports."""
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
async def agent_turn(user_text: str, memory: list) -> str:
    """
    Let the LLM decide whether to call tools.
    We inject the tool descriptions right into the system prompt.
    """
    tool_desc = """
You have the following tools. Reply with ONLY a JSON block if you want to use a tool, otherwise answer normally.

tools:
- search_google: {"tool": "search_google", "query": "string"}
- fetch_url:     {"tool": "fetch_url", "url": "string"}
- python_exec:   {"tool": "python_exec", "code": "string"}

Example JSON reply:
{"tool": "search_google", "query": "current Bitcoin price"}
    """.strip()

    msgs = [{"role": "system", "content": SYSTEM_PROMPT + "\n" + tool_desc}]
    msgs.extend(_trim(memory, MAX_TOKENS))
    msgs.append({"role": "user", "content": user_text})

    response = await client.chat.completions.create(
        model="moonshotai/kimi-k2-instruct",
        messages=msgs,
        temperature=0.3,
        max_tokens=600
    )
    text = response.choices[0].message.content.strip()

    # ---------- did the agent choose a tool? ----------
    if text.startswith("{") and text.endswith("}"):
        try:
            call = json.loads(text)
            tool  = call.get("tool")
            if tool == "search_google":
                res = await google_search(call["query"])
                return f"🔍 Google results:\n{res}"
            if tool == "fetch_url":
                res = await fetch_url(call["url"])
                return f"📄 Page content:\n{res}"
            if tool == "python_exec":
                res = await python_exec(call["code"])
                return f"🐍 Output:\n{res}"
        except Exception as e:
            return f"Tool call failed: {e}"
    # ---------- normal reply ----------
    return text

# ---------- discord commands ----------
def setup_chat(bot):
    @bot.command(name="chat")
    async def chat_cmd(ctx, *, prompt: str):
        if ctx.author.id != bot.bot.user.id:
            return
        async with ctx.typing():
            memory = await _load_mem()
            memory.append({"role": "user", "content": prompt})
            reply  = await agent_turn(prompt, memory)
            memory.append({"role": "assistant", "content": reply})
            await _save_mem(memory)
        for chunk in [reply[i:i+1900] for i in range(0, len(reply), 1900)]:
            await ctx.send(f"**🤖 {chunk}**")

    @bot.command(name="forget")
    async def forget_cmd(ctx):
        if ctx.author.id != bot.bot.user.id:
            return
        r = redis.from_url(os.getenv("REDIS_URL"))
        await r.delete(REDIS_KEY)
        await r.close()
        await ctx.send("🧠 Redis memory wiped.", delete_after=5)

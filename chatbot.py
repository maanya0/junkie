# chatbot.py  –  import-based toolbox, no code generation, prefix="."
import os, json, discord, redis.asyncio as redis
from datetime import datetime
from discord.ext import commands
from langchain_community.tools import __all__  # ← 1000+ tools out of the box

# ---------- CONFIG ----------
PREFIX = "."
REDIS_KEY = "selfbot:chat_history"
TOOL_MODULE = "langchain_community.tools"   # swap for any toolbox you like

# ---------- REDIS ----------
async def _load_mem():
    r = redis.from_url(os.getenv("REDIS_URL"))
    raw = await r.get(REDIS_KEY)
    await r.close()
    return json.loads(raw) if raw else []

async def _save_mem(mem):
    r = redis.from_url(os.getenv("REDIS_URL"))
    await r.set(REDIS_KEY, json.dumps(mem, ensure_ascii=False))
    await r.close()

# ---------- TOOLBOX ----------
def load_tool_map():
    """Return {name: callable} for every tool in the module."""
    import importlib
    mod = importlib.import_module(TOOL_MODULE)
    return {name: getattr(mod, name) for name in dir(mod) if not name.startswith("_")}

TOOL_MAP = load_tool_map()

# ---------- LOGGER ----------
async def log_message(msg: discord.Message):
    if msg.author.bot:
        return
    key = f"channel:{msg.channel.id}:messages"
    entry = {
        "id": msg.id,
        "author": msg.author.display_name,
        "content": msg.clean_content,
        "timestamp": msg.created_at.isoformat(),
    }
    r = redis.from_url(os.getenv("REDIS_URL"))
    await r.lpush(key, json.dumps(entry))
    await r.ltrim(key, 0, 99999)
    await r.close()

# ---------- COMMANDS ----------
def setup_chat(bot):
    @bot.event
    async def on_ready():
        print(f"[TOOL-BOX] Logged in as {bot.user} (ID: {bot.user.id})")

    @bot.event
    async def on_message(message):
        if not message.author.bot:
            await log_message(message)
        if message.content.startswith(PREFIX):
            await bot.process_commands(message)

    @bot.command(name="tools")
    async def tools_cmd(ctx):
        if ctx.author.id != bot.bot.user.id:
            return
        out = "\n".join(sorted(TOOL_MAP.keys()))
        for chunk in [out[i:i+1900] for i in range(0, len(out), 1900)]:
            await ctx.send(f"```{chunk}```")

    @bot.command(name="tool")
    async def tool_cmd(ctx, tool_name: str, *, args: str = ""):
        if ctx.author.id != bot.bot.user.id:
            return
        if tool_name not in TOOL_MAP:
            await ctx.send(f"❌ Tool `{tool_name}` not found. Use `.tools` to list.")
            return
        try:
            tool = TOOL_MAP[tool_name]
            result = tool.run(args)  # LangChain tools use .run(str)
            for chunk in [result[i:i+1900] for i in range(0, len(result), 1900)]:
                await ctx.send(chunk)
        except Exception as e:
            await ctx.send(f"🔥 Tool error: {e}")

    @bot.command(name="fetch")
    async def fetch_cmd(ctx, limit: int = 50):
        if ctx.author.id != bot.bot.user.id:
            return
        key = f"channel:{ctx.channel.id}:messages"
        r = redis.from_url(os.getenv("REDIS_URL"))
        raw = await r.lrange(key, 0, limit - 1)
        await r.close()
        msgs = [json.loads(m) for m in raw]
        out = "\n".join(f"[{m['timestamp'][:19]}] {m['author']}: {m['content']}" for m in reversed(msgs))
        for chunk in [out[i:i+1900] for i in range(0, len(out), 1900)]:
            await ctx.send(f"```{chunk}```")

    @bot.command(name="save")
    async def save_cmd(ctx):
        if ctx.author.id != bot.bot.user.id:
            return
        key = f"channel:{ctx.channel.id}:messages"
        r = redis.from_url(os.getenv("REDIS_URL"))
        raw = await r.lrange(key, 0, -1)
        await r.close()
        file = discord.File(io.BytesIO(b"\n".join(raw)), filename=f"chat_{ctx.channel.id}.jsonl")
        await ctx.send("📥 Full history exported.", file=file)

    @bot.event
    async def on_ready():
        print(f"[TOOL-BOX] Ready with {len(TOOL_MAP)} tools.")

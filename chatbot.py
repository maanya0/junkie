# chatbot.py  –  channel-wide chatbot, auto-tool-picker, zero commands
import os, json, discord, redis.asyncio as redis, io
from datetime import datetime
from discord.ext import commands
from langchain_community.tools import __all__  # 1000+ tools

PREFIX = "."   # kept only for internal commands (hidden)
REDIS_KEY = "selfbot:chat_history"

# ---------- Redis ----------
async def _load_mem():
    r = redis.from_url(os.getenv("REDIS_URL"))
    raw = await r.get(REDIS_KEY)
    await r.close()
    return json.loads(raw) if raw else []

async def _save_mem(mem):
    r = redis.from_url(os.getenv("REDIS_URL"))
    await r.set(REDIS_KEY, json.dumps(mem, ensure_ascii=False))
    await r.close()

# ---------- Tool Map ----------
def load_tool_map():
    import importlib
    mod = importlib.import_module("langchain_community.tools")
    return {name.lower(): getattr(mod, name) for name in dir(mod) if not name.startswith("_")}

TOOL_MAP = load_tool_map()

# ---------- Message Logger ----------
async def log_message(msg: discord.Message):
    if msg.author.bot:
        return
    key = f"channel:{msg.channel.id}:messages"
    entry = {
        "id": msg.id,
        "author": msg.display_name,
        "content": msg.clean_content,
        "timestamp": msg.created_at.isoformat(),
    }
    r = redis.from_url(os.getenv("REDIS_URL"))
    await r.lpush(key, json.dumps(entry))
    await r.ltrim(key, 0, 99999)
    await r.close()

# ---------- Auto-Tool Picker ----------
async def pick_tool_for(text: str) -> str:
    """Return tool result if a tool matches the query, else None."""
    text = text.lower()
    for name, tool in TOOL_MAP.items():
        if name in text or any(kw in text for kw in [name.replace("_", " ")]):
            try:
                result = tool.run(text)
                return result[:1900]  # Discord limit
            except Exception:
                continue
    return None

# ---------- Chat Reply ----------
async def chat_reply(msg: discord.Message):
    if msg.author.bot:
        return
    # 1. store user message
    await log_message(msg)
    # 2. pick tool
    result = await pick_tool_for(msg.clean_content)
    if result:
        await msg.channel.send(result)
    # 3. (optional) store bot reply
    mem = await _load_mem()
    mem.append({"role": "user", "content": msg.clean_content})
    mem.append({"role": "assistant", "content": result})
    await _save_mem(mem)

# ---------- Setup ----------
def setup_chat(bot):
    @bot.event
    async def on_ready():
        print(f"[CHATBOT] Ready with {len(TOOL_MAP)} tools.")

    @bot.event
    async def on_message(message):
        if not message.author.bot:
            await chat_reply(message)
        if message.content.startswith(PREFIX):  # keep for .fetch/.save only
            await bot.process_commands(message)

    @bot.command(name="fetch")  # hidden utilities (no prefix needed in chat)
    async def fetch_cmd(ctx, limit: int = 50):
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
        key = f"channel:{ctx.channel.id}:messages"
        r = redis.from_url(os.getenv("REDIS_URL"))
        raw = await r.lrange(key, 0, -1)
        await r.close()
        file = discord.File(io.BytesIO(b"\n".join(raw)), filename=f"chat_{ctx.channel.id}.jsonl")
        await ctx.send("📥 Full history exported.", file=file)

    @bot.event
    async def on_ready():
        print(f"[CHATBOT] Logged in as {bot.user} (ID: {bot.user.id})")

# chatbot.py  –  self-bot, prefix ".", discord.py-self, zero dynamic code
import os, json, discord, redis.asyncio as redis, io
from datetime import datetime
from discord.ext import commands
from langchain_community.tools import __all__  # 1000+ pre-built tools

PREFIX = "."   # prefix for ALL commands (chat is prefix-free)
REDIS_KEY = "selfbot:chat_history"

# ---------- Redis ----------
async def _load_mem():
    r = redis.from_url(os.getenv("REDIS_URL"))
    raw = await r.get(REDIS_KEY")
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

# ---------- Message Logger (plain function) ----------
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

# ---------- Auto-Tool Picker (prefix-free chat) ----------
async def chat_reply(msg: discord.Message):
    if msg.author.bot:
        return
    await log_message(msg)
    result = await pick_tool_for(msg.clean_content)
    if result:
        await msg.channel.send(result[:1900])
        mem = await _load_mem()
        mem.append({"role": "user", "content": msg.clean_content})
        mem.append({"role": "assistant", "content": result})
        await _save_mem(mem)

async def pick_tool_for(text: str) -> str:
    text = text.lower()
    for name, tool in TOOL_MAP.items():
        if name in text or any(kw in text for kw in [name.replace("_", " ")]):
            try:
                return tool.run(text)[:1900]
            except Exception:
                continue
    return None

# ---------- Prefix Commands (plain functions) ----------
def setup_chat(bot):
    # on_ready
    async def on_ready():
        print(f"[CHATBOT] Ready with {len(TOOL_MAP)} tools.")

    # on_message
    async def on_message(message):
        if not message.author.bot:
            await chat_reply(message)
        if message.content.startswith(PREFIX):
            await bot.process_commands(message)

    # .tools
    async def tools_cmd(message):
        if not message.content.startswith(PREFIX + "tools"):
            return
        out = "\n".join(sorted(TOOL_MAP.keys()))
        for chunk in [out[i:i+1900] for i in range(0, len(out), 1900)]:
            await message.channel.send(f"```{chunk}```")

    # .tool <name> <args>
    async def tool_cmd(message):
        if not message.content.startswith(PREFIX + "tool"):
            return
        parts = message.content[len(PREFIX + "tool "):].split(" ", 1)
        tool_name = parts[0].lower()
        args = parts[1] if len(parts) > 1 else ""
        if tool_name not in TOOL_MAP:
            await message.channel.send(f"❌ Tool `{tool_name}` not found. Use `{PREFIX}tools` to list.")
            return
        try:
            result = TOOL_MAP[tool_name].run(args)
            for chunk in [result[i:i+1900] for i in range(0, len(result), 1900)]:
                await message.channel.send(chunk)
        except Exception as e:
            await message.channel.send(f"🔥 Tool error: {e}")

    # .fetch N
    async def fetch_cmd(message):
        if not message.content.startswith(PREFIX + "fetch"):
            return
        limit = int(message.content.split()[1]) if len(message.content.split()) > 1 else 50
        key = f"channel:{message.channel.id}:messages"
        r = redis.from_url(os.getenv("REDIS_URL"))
        raw = await r.lrange(key, 0, limit - 1)
        await r.close()
        msgs = [json.loads(m) for m in raw]
        out = "\n".join(f"[{m['timestamp'][:19]}] {m['author']}: {m['content']}" for m in reversed(msgs))
        for chunk in [out[i:i+1900] for i in range(0, len(out), 1900)]:
            await message.channel.send(f"```{chunk}```")

    # .save
    async def save_cmd(message):
        if not message.content.startswith(PREFIX + "save"):
            return
        key = f"channel:{message.channel.id}:messages"
        r = redis.from_url(os.getenv("REDIS_URL"))
        raw = await r.lrange(key, 0, -1)
        await r.close()
        file = discord.File(io.BytesIO(b"\n".join(raw)), filename=f"chat_{message.channel.id}.jsonl")
        await message.channel.send("📥 Full history exported.", file=file)

    # wire plain functions to bot (discord.py-self style)
    bot.add_listener(on_ready)
    bot.add_listener(on_message)
    bot.add_listener(tools_cmd, "on_message")
    bot.add_listener(fetch_cmd, "on_message")
    bot.add_listener(save_cmd, "on_message")

# ---------- ENTRY ----------
def setup_chat(bot):
    bot.add_listener(on_ready)
    bot.add_listener(on_message)
    bot.add_listener(tools_cmd, "on_message")
    bot.add_listener(fetch_cmd, "on_message")
    bot.add_listener(save_cmd, "on_message")

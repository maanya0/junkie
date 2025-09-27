import os
import json
import discord
from datetime import datetime
from openai import AsyncOpenAI
import redis.asyncio as redis

client = AsyncOpenAI(
    base_url="https://api.groq.com/openai/v1",
    api_key=os.getenv("GROQ_API_KEY")
)

SYSTEM_PROMPT = "You are a helpful Discord assistant. The following is your entire conversation with the user."

REDIS_KEY = "selfbot:chat_history"
MAX_TOKENS = 3_000

# ---------- Redis helpers ----------
def _tok(text: str) -> int:
    return len(text.encode()) // 4

async def _load_memory() -> list:
    r = redis.from_url(os.getenv("REDIS_URL"))
    raw = await r.get(REDIS_KEY)
    await r.close()
    return json.loads(raw) if raw else []

async def _save_memory(mem: list):
    r = redis.from_url(os.getenv("REDIS_URL"))
    await r.set(REDIS_KEY, json.dumps(mem, ensure_ascii=False))
    await r.close()

def _trim(mem: list, budget: int) -> list:
    total = 0
    out   = []
    for m in mem:
        total += _tok(m["content"])
        if total > budget:
            break
        out.append(m)
    return out

# -----------------------------------

def setup_chat(bot):
    @bot.command(".")
    async def chat(ctx, *, prompt: str):
        if ctx.author.id != bot.bot.user.id:
            return
        await ctx.message.delete(delay=1.5)

        memory = await _load_memory()
        memory.append({"role": "user", "content": f"[{datetime.utcnow():%m-%d %H:%M}] {prompt}"})

        msgs = [{"role": "system", "content": SYSTEM_PROMPT}]
        msgs.extend(_trim(memory, MAX_TOKENS))
        msgs.append({"role": "user", "content": prompt})

        try:
            resp = await client.chat.completions.create(
                model="moonshotai/kimi-k2-instruct",
                messages=msgs,
                temperature=0.7,
                max_tokens=500
            )
            reply = resp.choices[0].message.content.strip()
        except Exception as e:
            reply = f"Groq error: {e}"

        memory.append({"role": "assistant", "content": reply})
        await _save_memory(memory)

        if len(reply) > 1900:
            reply = reply[:1900] + "…"
        await ctx.send(f"**🤖 {reply}**")

    @bot.command("fgt")
    async def forget(ctx):
        if ctx.author.id != bot.bot.user.id:
            return
        r = redis.from_url(os.getenv("REDIS_URL"))
        await r.delete(REDIS_KEY)
        await r.close()
        await ctx.send("🧠 Redis memory wiped.", delete_after=5)

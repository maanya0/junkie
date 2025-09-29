# chatbot.py  –  public, redis-backed chat for discord.py-self  (auto-web)
import os
import discord
import json
import redis.asyncio as redis
import aiohttp
import re
from openai import AsyncOpenAI
from serpapi import GoogleSearch

REDIS_URL  = os.getenv("REDIS_URL", "redis://localhost:6379/0")
TOK        = lambda t: len(t.encode()) // 4
MAX_TOKENS = 4_000
SERPER_KEY = os.getenv("SERPER_API_KEY")

client = AsyncOpenAI(
    base_url="https://api.groq.com/openai/v1",
    api_key=os.getenv("GROQ_API_KEY")
)

# ---------- web tools ----------
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

SYSTEM_PROMPT = """
You are Junkie Companion, a helpful Discord assistant.
- Default to **short, plain-language** answers (1-2 paragraphs or a few bullets).
- Add markdown, headings, tables, code blocks, LaTeX **only** if the user appends `--long` to their query.
- When brief, end with: “Ask `--long` for details.”
- You may use these tools automatically when needed:
  - {"tool": "search_google", "query": "<terms>"}
  - {"tool": "fetch_url", "url": "<full url>"}
  Reply with **only** the JSON block to call a tool; otherwise answer normally.
- Remain accurate, friendly, and unbiased.
- Always crosscheck your information , with help of webtools you have been proided"
""".strip()

# ---------- redis helpers ----------
def _key(channel_id: int) -> str:
    return f"selfbot:chat:{channel_id}"

async def _load_mem(channel_id: int):
    r = redis.from_url(REDIS_URL)
    raw = await r.get(_key(channel_id))
    await r.close()
    return json.loads(raw) if raw else []

async def _save_mem(channel_id: int, mem: list):
    r = redis.from_url(REDIS_URL)
    await r.set(_key(channel_id), json.dumps(mem, ensure_ascii=False))
    await r.close()

def _trim(mem, budget):
    total, out = 0, []
    for m in reversed(mem):
        total += TOK(m["content"])
        if total > budget:
            break
        out.insert(0, m)
    return out

# ---------- llm with auto-web ----------
async def ask_junkie(user_text: str, memory: list) -> str:
    msgs = [{"role": "system", "content": SYSTEM_PROMPT}]
    msgs.extend(_trim(memory, MAX_TOKENS))
    msgs.append({"role": "user", "content": user_text})

    resp = await client.chat.completions.create(
        model="llama-3.1-8b-instant",   # instead of llama-3.1-70b-versatile,   # instead of moonshotai/kimi-k2-instruct,
        messages=msgs,
        temperature=0.3,
        max_tokens=800
    )
    text = resp.choices[0].message.content.strip()

    if text.startswith("{") and text.endswith("}"):
        try:
            call = json.loads(text)
            tool = call.get("tool")
            if tool == "search_google":
                res = await google_search(call["query"])
                msgs.append({"role": "assistant", "content": text})
                msgs.append({"role": "system", "content": f"Web results:\n{res}"})
            elif tool == "fetch_url":
                res = await fetch_url(call["url"])
                msgs.append({"role": "assistant", "content": text})
                msgs.append({"role": "system", "content": f"Page content:\n{res}"})

            resp2 = await client.chat.completions.create(
                model="llama-3.1-8b-instant",   # instead of llama-3.1-70b-versatile,
                messages=msgs,
                temperature=0.3,
                max_tokens=800
            )
            text = resp2.choices[0].message.content.strip()
        except Exception:
            pass
    return text
# ---------- discord ----------
def setup_chat(bot):
    @bot.event
    async def on_message(message):
        if not message.content.startswith(bot.prefix):
            return
        if message.author.id == bot.bot.user.id:
            await bot.bot.process_commands(message)
            return
        if message.content.startswith(f"{bot.prefix}"):
            prompt = message.content[len(f"{bot.prefix}"):].strip()
            if not prompt:
                return
            async with message.channel.typing():
                mem = await _load_mem(message.channel.id)
                mem.append({"role": "user", "content": prompt})
                reply = await ask_junkie(prompt, mem)
                mem.append({"role": "assistant", "content": reply})
                await _save_mem(message.channel.id, mem)
            for chunk in [reply[i:i+1900] for i in range(0, len(reply), 1900)]:
                await message.channel.send(f"**🤖 Junkie:**\n{chunk}")

    @bot.command(name="fgt")
    async def forget_cmd(ctx):
        if ctx.author.id != ctx.bot.user.id:
            return
        r = redis.from_url(REDIS_URL)
        await r.delete(_key(ctx.channel.id))
        await r.close()
        await ctx.send("🧠 Memory wiped.", delete_after=5)

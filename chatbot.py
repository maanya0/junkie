# chatbot.py  –  public, redis-backed chat for discord.py-self
import os
import discord
import json
import redis.asyncio as redis
from openai import AsyncOpenAI

REDIS_URL  = os.getenv("REDIS_URL", "redis://localhost:6379/0")
TOK        = lambda t: len(t.encode()) // 4
MAX_TOKENS = 4_000

client = AsyncOpenAI(
    base_url="https://api.groq.com/openai/v1",
    api_key=os.getenv("GROQ_API_KEY")
)

SYSTEM_PROMPT = """
You are Junkie Companion, a helpful assistant designed to provide accurate, detailed, and comprehensive answers to user queries. Your goal is to write clear and informative responses based on the information you have access to. You aim to be a reliable source of information and support for users.

## Format Rules
Answer Start: Begin your answer with a few sentences that provide a summary of the overall answer.  
Headings and Sections: Use Level 2 headers (##) for sections. Use bolded text (**) for subsections within these sections if necessary.  
List Formatting: Use only flat lists for simplicity. Prefer unordered lists. Avoid nesting lists; instead, create a markdown table if comparisons are needed.  
Emphasis and Highlights: Use bolding to emphasize specific words or phrases where appropriate. Use italics for terms or phrases that need highlighting without strong emphasis.  
Code Snippets: Include code snippets using Markdown code blocks, specifying the language for syntax highlighting.  
Mathematical Expressions: Wrap all math expressions in LaTeX using $ for inline and $$ for block formulas.  
Quotations: Use Markdown blockquotes to include any relevant quotes that support or supplement your answer.  
Answer End: Wrap up the answer with a few sentences that are a general summary.
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

# ---------- llm ----------
async def ask_junkie(user_text: str, memory: list) -> str:
    msgs = [{"role": "system", "content": SYSTEM_PROMPT}]
    msgs.extend(_trim(memory, MAX_TOKENS))
    msgs.append({"role": "user", "content": user_text})

    response = await client.chat.completions.create(
        model="moonshotai/kimi-k2-instruct",
        messages=msgs,
        temperature=0.3,
        max_tokens=800
    )
    return response.choices[0].message.content.strip()

# ---------- discord ----------
def setup_chat(bot):
    # ---------- manual listener for .chat (any user) ----------
    @bot.event
    async def on_message(message):
        # 1. let the framework handle *all* self-messages
        if message.author.id == bot.bot.user.id:
            await bot.bot.process_commands(message)
            return

        # 2. handle public .chat
        if message.content.startswith(".chat "):
            prompt = message.content[6:].strip()
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

    # ---------- framework-based .fgt (self only) ----------
    @bot.command(name="fgt")
    async def forget_cmd(ctx):
        if ctx.author.id != ctx.bot.user.id:
            return
        r = redis.from_url(REDIS_URL)
        await r.delete(_key(ctx.channel.id))
        await r.close()
        await ctx.send("🧠 Memory wiped.", delete_after=5)

import os
from datetime import datetime
from openai import AsyncOpenAI
from selfbot import SelfBot

# --- hard-coded Discord user-ids that may use !tldr -----------------
ALLOWED_USERS = {1105501912612229141, 1068647185928962068}

client = AsyncOpenAI(
    base_url="https://api.groq.com/openai/v1",
    api_key=os.getenv("GROQ_API_KEY")
)

def setup_tldr(bot: SelfBot):
    @bot.command("tldr")
    async def tldr(ctx, count: int = 50):
        if ctx.author.id not in ALLOWED_USERS:   # silent ignore
            return

        await ctx.message.delete(delay=1.5)

        messages = await _fetch_recent_messages(ctx, count)
        summary  = await _summarize_messages(messages)

        for chunk in _chunk_text(summary):
            await ctx.send(f"**TL;DR:**\n{chunk}")

# ----------------- helpers (unchanged) ------------------------------
async def _fetch_recent_messages(ctx, count=50, skip_existing_tldr=True):
    try:
        msgs = [m async for m in ctx.channel.history(limit=count)
                if not (skip_existing_tldr and
                        m.author.id == ctx.bot.user.id and
                        "**TL;DR:**" in m.content)]
        msgs.reverse()
        return msgs
    except Exception as e:
        await ctx.send(f"Could not fetch history: {e}", delete_after=10)
        return []

async def _summarize_messages(messages):
    prompt = _build_prompt(messages)
    try:
        resp = await client.chat.completions.create(
            model="moonshotai/kimi-k2-instruct",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.4,
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        return f"Groq error: {e}"

def _build_prompt(messages):
    lines = [f"[{m.created_at.strftime('%H:%M')}] {m.author.display_name}: {m.clean_content}"
             for m in messages]
    return ("Summarize the following Discord conversation in 4-6 bullet points.\n\n"
            + "\n".join(lines))

def _chunk_text(text, size=1800):
    return [text[i:i+size] for i in range(0, len(text), size)]

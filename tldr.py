# tldr.py
# ── only the users in ALLOWED_USERS can trigger !tldr ──

import os
from datetime import datetime

import discord
from openai import AsyncOpenAI
from selfbot import SelfBot

# ----------------------------------------------------------
# 1.  Hard-code the Discord user-ids that are allowed to use
#     the TL;DR tool. Add / remove IDs as you wish.
# ----------------------------------------------------------
ALLOWED_USERS = {1105501912612229141,1068647185928962068}

# ----------------------------------------------------------
# 2.  Groq / OpenAI-compatible client
# ----------------------------------------------------------
client = AsyncOpenAI(
    base_url="https://api.groq.com/openai/v1",
    api_key=os.getenv("GROQ_API_KEY")
)

# ----------------------------------------------------------
# 3.  Public API: register the !tldr command
# ----------------------------------------------------------
def setup_tldr(bot: SelfBot):
    @bot.command("tldr")
    async def tldr(ctx, count: int = 50):
        # ignore anyone not in the allow-list
        if ctx.author.id not in ALLOWED_USERS:
            return

        # delete the invocation message
        await ctx.message.delete(delay=1.5)

        # fetch, summarize, send
        messages = await _fetch_recent_messages(ctx, count)
        summary  = await _summarize_messages(messages)

        for chunk in _chunk_text(summary):
            await ctx.send(f"**TL;DR:**\n{chunk}")

# ----------------------------------------------------------
# 4.  Internal helpers (unchanged)
# ----------------------------------------------------------
async def _fetch_recent_messages(ctx, count: int = 50, skip_existing_tldr: bool = True):
    try:
        messages = [
            m async for m in ctx.channel.history(limit=count)
            if not (
                skip_existing_tldr and
                m.author.id == ctx.bot.user.id and
                "**TL;DR:**" in m.content
            )
        ]
        messages.reverse()
        return messages
    except Exception as e:
        await ctx.send(f"Could not fetch history: {e}", delete_after=10)
        return []

async def _summarize_messages(messages):
    prompt = _build_prompt(messages)
    try:
        response = await client.chat.completions.create(
            model="moonshotai/kimi-k2-instruct",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.4,
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        return f"OpenAI error: {e}"

def _build_prompt(messages):
    lines = []
    for m in messages:
        timestamp = m.created_at.strftime("%H:%M")
        author    = m.author.display_name
        content   = m.clean_content
        lines.append(f"[{timestamp}] {author}: {content}")
    return (
        "Summarize the following Discord conversation in 4-6 bullet points.\n\n"
        + "\n".join(lines)
    )

def _chunk_text(text, size: int = 1800):
    return [text[i:i + size] for i in range(0, len(text), size)]

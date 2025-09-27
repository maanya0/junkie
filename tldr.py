import os
from datetime import datetime
from openai import AsyncOpenAI

ALLOWED_USERS = {1105501912612229141, 1068647185928962068}

client = AsyncOpenAI(
    base_url="https://api.groq.com/openai/v1",
    api_key=os.getenv("GROQ_API_KEY")
)

# ---------- helpers used by selfbot.py --------------------
async def _fetch_recent_messages(message, count=50, skip_existing_tldr=True):
    try:
        msgs = [m async for m in message.channel.history(limit=count)
                if not (skip_existing_tldr and
                        m.author.id == message.guild.me.id and
                        "**TL;DR:**" in m.content)]
        msgs.reverse()
        return msgs
    except Exception as e:
        await message.channel.send(f"Could not fetch history: {e}", delete_after=10)
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

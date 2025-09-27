# tldr.py

import os
from datetime import datetime

import discord
from openai import AsyncOpenAI
from selfbot import SelfBot

client = AsyncOpenAI(
    base_url="https://api.groq.com/openai/v1",
    api_key=os.getenv("OPENAI_API_KEY")
)

def setup_tldr(bot: SelfBot):
    @bot.command("tldr")
    async def tldr(ctx, count: int = 50):
        # Safe channel name handling for both DMs and server channels
        channel_name = ctx.channel.name if hasattr(ctx.channel, 'name') else "DM"
        
        print(f"[DEBUG] TLDR command triggered by {ctx.author} ({ctx.author.id})")
        print(f"[DEBUG] Message content: '{ctx.message.content}'")
        print(f"[DEBUG] Channel: #{channel_name}")
        
        try:
            await ctx.message.delete(delay=1.5)
            print("[DEBUG] Message deletion scheduled")
        except Exception as e:
            print(f"[DEBUG] Could not delete message: {e}")
        
        messages = await _fetch_recent_messages(ctx, count)
        if not messages:
            await ctx.send("No messages to summarize.", delete_after=5)
            return
            
        summary = await _summarize_messages(messages)
        
        for chunk in _chunk_text(summary):
            await ctx.send(f"**TL;DR:**\n{chunk}")
            print(f"[DEBUG] Sent summary chunk: {chunk[:100]}...")

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
        print(f"[DEBUG] Fetched {len(messages)} messages")
        return messages
    except Exception as e:
        print(f"[DEBUG] Could not fetch history: {e}")
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
        print(f"[DEBUG] OpenAI error: {e}")
        return f"Error: {e}"

def _build_prompt(messages):
    lines = []
    for m in messages:
        timestamp = m.created_at.strftime("%H:%M")
        author = m.author.display_name
        content = m.clean_content
        lines.append(f"[{timestamp}] {author}: {content}")
    return (
        "Summarize the following Discord conversation in 4-6 bullet points.\n\n"
        + "\n".join(lines)
    )

def _chunk_text(text, size: int = 1800):
    return [text[i:i + size] for i in range(0, len(text), size)]

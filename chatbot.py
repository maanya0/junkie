import os
import discord
from collections import defaultdict, deque
from datetime import datetime
from openai import AsyncOpenAI

client = AsyncOpenAI(
    base_url="https://api.groq.com/openai/v1",
    api_key=os.getenv("GROQ_API_KEY")
)

SYSTEM_PROMPT = (
    "You are a helpful, concise Discord assistant. "
    "Use the conversation history below to stay consistent."
)

# ---- per-channel memory ----
MAX_MEM = 30                       # last 30 messages per channel
memory: dict[int, deque] = defaultdict(lambda: deque(maxlen=MAX_MEM))

# ----------------------------

def setup_chat(bot):
    @bot.bot.event
    async def on_message(msg: discord.Message):
        """Store every message (that isn’t a bot command) into memory."""
        if msg.author.id != bot.bot.user.id:          # ignore other people
            return
        if msg.content.startswith(bot.prefix):        # ignore commands
            return
        memory[msg.channel.id].append({
            "role": "user",
            "content": f"[{msg.created_at:%H:%M}] {msg.author.display_name}: {msg.clean_content}"
        })

    @bot.command("c")
    async def chat_command(ctx, *, prompt: str):
        if ctx.author.id != bot.bot.user.id:
            return
        await ctx.message.delete(delay=1.5)

        # build message list: system + memory + current prompt
        msgs = [{"role": "system", "content": SYSTEM_PROMPT}]
        msgs.extend(memory[ctx.channel.id])
        msgs.append({"role": "user", "content": prompt})

        try:
            response = await client.chat.completions.create(
                model="moonshotai/kimi-k2-instruct",
                messages=msgs,
                temperature=0.7,
                max_tokens=400
            )
            reply = response.choices[0].message.content.strip()
        except Exception as e:
            reply = f"Groq error: {e}"

        # store assistant answer so it becomes part of future context
        memory[ctx.channel.id].append({
            "role": "assistant",
            "content": reply
        })

        if len(reply) > 1900:
            reply = reply[:1900] + "…"
        await ctx.send(f"**🤖 {reply}**")

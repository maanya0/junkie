import discord
from openai import AsyncOpenAI
import os

client = AsyncOpenAI(
    base_url="https://api.groq.com/openai/v1",
    api_key=os.getenv("GROQ_API_KEY")
)

SYSTEM_PROMPT = (
    "You are a helpful, concise Discord assistant. "
    "Keep answers under 900 characters so they fit in one message."
)

def setup_chat(bot):
    @bot.command("chat")
    async def chat_command(ctx, *, prompt: str):
        # Only react to ourselves
        if ctx.author.id != bot.bot.user.id:
            return

        # Optional: delete the invoking message for cleanliness
        await ctx.message.delete(delay=1.5)

        try:
            response = await client.chat.completions.create(
                model="moonshotai/kimi-k2-instruct",
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user",   "content": prompt}
                ],
                temperature=0.7,
                max_tokens=300
            )
            reply = response.choices[0].message.content.strip()
        except Exception as e:
            reply = f"Groq error: {e}"

        # Discord max 2000; leave margin for bold wrapper
        if len(reply) > 1900:
            reply = reply[:1900] + "…"

        await ctx.send(f"**🤖 {reply}**")

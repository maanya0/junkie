# selfbot.py

import os
import discord
from discord.ext import commands

class SelfBot:
    def __init__(self, *, token: str = None, prefix: str = "!"):
        self.token = token or os.getenv("DISCORD_TOKEN")
        if not self.token:
            raise ValueError("Discord token must be provided either as argument or DISCORD_TOKEN env var.")

        self.bot = commands.Bot(
            command_prefix=prefix,
            self_bot=True,
        )

        self.prefix = prefix

        @self.bot.event
        async def on_ready():
            print(f"[SELF-BOT] Logged in as {self.bot.user} (ID: {self.bot.user.id})")
            print(f"[SELF-BOT] Command prefix: {self.prefix}")

        @self.bot.event
        async def on_message(message: discord.Message):
            print(f"[DEBUG] Message received: '{message.content}' from {message.author} ({message.author.id}) in #{message.channel.name}")
            
            # Ignore messages sent by other bots
            if message.author.bot:
                print("[DEBUG] Ignoring bot message")
                return
            
            # Ignore messages without the prefix
            if not message.content.startswith(self.prefix):
                print(f"[DEBUG] Message doesn't start with prefix '{self.prefix}'")
                return
            
            print(f"[DEBUG] Processing command: {message.content}")
            await self.bot.process_commands(message)

    def command(self, name: str = None, **kwargs):
        return self.bot.command(name=name, **kwargs)

    def event(self, coro):
        return self.bot.event(coro)

    def run(self):
        self.bot.run(self.token)

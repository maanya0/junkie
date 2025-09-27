# selfbot.py

import os
import discord
from discord.ext import commands

class SelfBot:
    def __init__(self, *, token: str = None, prefix: str = "!"):
        self.token = token or os.getenv("DISCORD_TOKEN")
        if not self.token:
            raise ValueError("Discord token must be provided either as argument or DISCORD_TOKEN env var.")

        # Add intents for better compatibility
        intents = discord.Intents.default()
        intents.message_content = True
        intents.messages = True

        self.bot = commands.Bot(
            command_prefix=prefix,
            self_bot=True,
            intents=intents
        )

        self.prefix = prefix

        @self.bot.event
        async def on_ready():
            print(f"[SELF-BOT] Logged in as {self.bot.user} (ID: {self.bot.user.id})")
            print(f"[SELF-BOT] Command prefix: {self.prefix}")

        @self.bot.event
        async def on_message(message: discord.Message):
            # Safe channel identification
            if isinstance(message.channel, discord.DMChannel):
                channel_type = "DM"
                channel_name = f"@{message.channel.recipient}"
            elif isinstance(message.channel, discord.GroupChannel):
                channel_type = "Group DM"
                channel_name = f"GroupDM:{message.channel.id}"
            else:
                channel_type = "Server"
                channel_name = f"#{message.channel.name}"
            
            print(f"[DEBUG] {channel_type} Message: '{message.content}' from {message.author} ({message.author.id}) in {channel_name}")
            
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

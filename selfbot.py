import os
import discord
from discord.ext import commands

class SelfBot:
    def __init__(self, *, token: str = None, prefix: str = "!"):
        """
        A simple wrapper around discord.py-self for creating selfbots.
        """
        self.token = token or os.getenv("DISCORD_TOKEN")
        if not self.token:
            raise ValueError("Discord token must be provided either as argument or DISCORD_TOKEN env var.")

        # instantiate the Bot with `self_bot=True`
        self.bot = commands.Bot(
            command_prefix=prefix,
            self_bot=True,
        )
        self.prefix = prefix

        @self.bot.event
        async def on_ready():
            print(f"[SELF-BOT] Logged in as {self.bot.user} (ID: {self.bot.user.id})")

        @self.bot.event
        async def on_message(message: discord.Message):
            # ignore messages not sent by us OR non-whitelisted users
            from main import whitelisted_users
            
            # Check if user is whitelisted
            if message.author.id not in whitelisted_users:
                # Only react with lock if they're trying to use a command
                if message.content.startswith(self.prefix):
                    try:
                        await message.add_reaction('🔒')
                    except:
                        pass
                return

            # Process commands for whitelisted users
            if message.content.startswith(self.prefix):
                await self.bot.process_commands(message)

    def command(self, name: str = None, **kwargs):
        """
        Decorator to register a command on the selfbot.
        """
        return self.bot.command(name=name, **kwargs)

    def event(self, coro):
        """
        Shortcut decorator to register arbitrary events.
        """
        return self.bot.event(coro)

    def run(self):
        """
        Start the bot. Blocks until shutdown.
        """
        self.bot.run(self.token)

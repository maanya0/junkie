import os
import discord
from discord.ext import commands
import logging

logger = logging.getLogger(__name__)

class SelfBot:
    def __init__(self, *, token: str = None, prefix: str = "!"):
        self.token = token or os.getenv("DISCORD_TOKEN")
        if not self.token:
            raise ValueError("Discord token must be provided")

        self.bot = commands.Bot(command_prefix=prefix, self_bot=True)
        self.prefix = prefix

        @self.bot.event
        async def on_ready():
            logger.info(f"[SELF-BOT] Logged in as {self.bot.user}")

        @self.bot.event
        async def on_message(message):
            # Get whitelist from main
            from main import whitelisted_users
            
            # Allow only whitelisted users
            if message.author.id not in whitelisted_users:
                if message.content.startswith(self.prefix):
                    try:
                        await message.add_reaction('🔒')
                        logger.info(f"Blocked {message.author.id}")
                    except:
                        pass
                return

            # Process commands for whitelisted users
            if message.content.startswith(self.prefix):
                if message.author.id == self.bot.user.id:  # Only process your own commands
                    await self.bot.process_commands(message)

    def command(self, name: str = None, **kwargs):
        return self.bot.command(name=name, **kwargs)

    def event(self, coro):
        return self.bot.event(coro)

    def run(self):
        self.bot.run(self.token)

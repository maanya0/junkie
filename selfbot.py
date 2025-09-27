import os
import discord
from discord.ext import commands
import logging

logger = logging.getLogger(__name__)

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
            logger.info(f"[SELF-BOT] Logged in as {self.bot.user} (ID: {self.bot.user.id})")

        @self.bot.event
        async def on_message(message: discord.Message):
            # Import whitelist from main
            from main import whitelisted_users
            
            logger.info(f"[MESSAGE] Author: {message.author.id}, Content: {message.content[:50]}")
            logger.info(f"[WHITELIST] Check: {message.author.id} in {whitelisted_users} = {message.author.id in whitelisted_users}")
            
            # Check if user is whitelisted
            if message.author.id not in whitelisted_users:
                if message.content.startswith(self.prefix):
                    try:
                        await message.add_reaction('🔒')
                        logger.info(f"[BLOCKED] User {message.author.id} denied access")
                    except Exception as e:
                        logger.error(f"[ERROR] Could not add reaction: {e}")
                return

            # Process commands for whitelisted users
            if message.content.startswith(self.prefix):
                logger.info(f"[ALLOWED] Processing command from {message.author.id}")
                await self.bot.process_commands(message)

    def command(self, name: str = None, **kwargs):
        return self.bot.command(name=name, **kwargs)

    def event(self, coro):
        return self.bot.event(coro)

    def run(self):
        self.bot.run(self.token)

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
            # Only process messages from yourself (selfbot)
            if message.author.id != self.bot.user.id:
                return

            # Only check whitelist for actual commands
            if message.content.startswith(self.prefix):
                # Get whitelist from main
                from main import whitelisted_users
                
                # Check if channel/user is whitelisted
                channel_id = message.channel.id
                user_id = message.author.id
                
                # For selfbot, we need to check if the command is being used in a whitelisted context
                # Since you're sending the command, check if you can use it
                if user_id not in whitelisted_users:
                    await message.add_reaction('🔒')
                    logger.info(f"Blocked command from {user_id}")
                    return

                await self.bot.process_commands(message)

    def command(self, name: str = None, **kwargs):
        return self.bot.command(name=name, **kwargs)

    def event(self, coro):
        return self.bot.event(coro)

    def run(self):
        self.bot.run(self.token)

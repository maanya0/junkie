import os
import discord
from discord.ext import commands

class SelfBot:
    def __init__(self, *, token: str = None, prefix: str = "!"):
        self.token = token or os.getenv("DISCORD_TOKEN")
        if not self.token:
            raise ValueError("Discord token must be provided either as argument or DISCORD_TOKEN env var.")

        # we still keep the Bot object so you can register other commands if you want
        self.bot = commands.Bot(command_prefix=prefix, self_bot=True)
        self.prefix = prefix

        # --- load allowed users list (imported from tldr.py) ---
        from tldr import ALLOWED_USERS
        self.allowed_users = ALLOWED_USERS

        # --- core event ------------------------------------------------------------
        @self.bot.event
        async def on_ready():
            print(f"[SELF-BOT] Logged in as {self.bot.user} (ID: {self.bot.user.id})")

        @self.bot.event
        async def on_message(message: discord.Message):
            # 1. ignore bots / webhooks
            if message.author.bot:
                return

            # 2. handle !tldr manually
            if message.content.strip().startswith("!tldr"):
                await self._handle_tldr(message)
                return   # stop further processing

            # 3. let other commands work normally for the logged-in account itself
            if message.author.id == self.bot.user.id:
                await self.bot.process_commands(message)

    # ----------------------------------------------------------
    # manual !tldr handler
    # ----------------------------------------------------------
    async def _handle_tldr(self, message: discord.Message):
        if message.author.id not in self.allowed_users:
            return   # silent ignore

        try:
            count = int(message.content.strip().split()[1])
        except (IndexError, ValueError):
            count = 50

        # fetch
        from tldr import _fetch_recent_messages, _summarize_messages, _chunk_text
        messages = await _fetch_recent_messages(message, count)
        summary  = await _summarize_messages(messages)

        # send back (as the hosted account)
        for chunk in _chunk_text(summary):
            await message.channel.send(f"**TL;DR:**\n{chunk}")

        # optional: delete the caller’s message
        try:
            await message.delete(delay=1.5)
        except discord.HTTPException:
            pass

    # ----------------------------------------------------------
    # helpers to register normal commands / events (unchanged)
    # ----------------------------------------------------------
    def command(self, name: str = None, **kwargs):
        return self.bot.command(name=name, **kwargs)

    def event(self, coro):
        return self.bot.event(coro)

    def run(self):
        self.bot.run(self.token)

# main.py

import os
from dotenv import load_dotenv

from discord_bot.chat_handler import setup_chat
from discord_bot.selfbot import SelfBot
from discord_bot.tldr import setup_tldr

load_dotenv()

bot = SelfBot(
    token=os.getenv("DISCORD_TOKEN"),
    prefix=".",
)

setup_tldr(bot)
setup_chat(bot)

if __name__ == "__main__":
    bot.run()

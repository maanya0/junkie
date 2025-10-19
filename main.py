# main.py

import os

from dotenv import load_dotenv

from chatbot import setup_chat
from selfbot import SelfBot
from tldr import setup_tldr

load_dotenv()

bot = SelfBot(
    token=os.getenv("DISCORD_TOKEN"),
    prefix="!",
)

setup_tldr(bot)
setup_chat(bot)

if __name__ == "__main__":
    bot.run()

# main.py

import logging

from dotenv import load_dotenv

load_dotenv()

from core.config import DISCORD_TOKEN, LOG_LEVEL
from discord_bot.chat_handler import setup_chat
from discord_bot.selfbot import SelfBot
from discord_bot.tldr import setup_tldr

# Configure logging
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

bot = SelfBot(
    token=DISCORD_TOKEN,
    prefix=".",
)

setup_tldr(bot)
setup_chat(bot)

if __name__ == "__main__":
    bot.run()

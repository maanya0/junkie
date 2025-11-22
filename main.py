# main.py

import os
import logging
#test
from dotenv import load_dotenv

from discord_bot.chat_handler import setup_chat
from discord_bot.selfbot import SelfBot
from discord_bot.tldr import setup_tldr

load_dotenv()

# Configure logging
log_level = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, log_level, logging.INFO),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

bot = SelfBot(
    token=os.getenv("DISCORD_TOKEN"),
    prefix=".",
)

setup_tldr(bot)
setup_chat(bot)

if __name__ == "__main__":
    bot.run()

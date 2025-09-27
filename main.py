import os
import json
import logging
from dotenv import load_dotenv
from selfbot import SelfBot
from tldr import setup_tldr

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

load_dotenv()

# Load fixed whitelist
try:
    with open('whitelisted_users.json', 'r') as f:
        data = json.load(f)
        whitelisted_users = data.get('whitelisted_users', [])
        logger.info(f"Whitelist loaded: {whitelisted_users}")
except Exception as e:
    logger.error(f"Failed to load whitelist: {e}")
    whitelisted_users = []

# Create bot
bot = SelfBot(
    token=os.getenv("DISCORD_TOKEN"),
    prefix="!",
)

setup_tldr(bot)

if __name__ == "__main__":
    bot.run()

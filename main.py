import os
import json
import logging
from dotenv import load_dotenv
from selfbot import SelfBot
from tldr import setup_tldr

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

load_dotenv()

# ──────────────────────────────────────────────
# Fixed Whitelist (Never Changes)
# ──────────────────────────────────────────────

def load_whitelist():
    try:
        with open('whitelisted_users.json', 'r') as f:
            data = json.load(f)
            users = data.get('whitelisted_users', [])
            logger.info(f"Whitelist loaded: {users}")
            return users
    except Exception as e:
        logger.error(f"Failed to load whitelist: {e}")
        # Return hardcoded whitelist as fallback
        return [1105501912612229141, 1068647185928962068]

# Load fixed whitelist
whitelisted_users = load_whitelist()
logger.info(f"Final whitelist: {whitelisted_users}")

# ──────────────────────────────────────────────
# Bot Setup
# ──────────────────────────────────────────────

bot = SelfBot(
    token=os.getenv("DISCORD_TOKEN"),
    prefix="!",
)

setup_tldr(bot)

if __name__ == "__main__":
    bot.run()

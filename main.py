# main.py  (final diff)
import os
from dotenv import load_dotenv
from tldr import setup_tldr, setup_chat   # 1️⃣ added ", setup_chat"

load_dotenv()

bot = SelfBot(
    token=os.getenv("DISCORD_TOKEN"),
    prefix="!",
)

setup_tldr(bot)
setup_chat(bot)                            # 2️⃣ added this line

if __name__ == "__main__":
    bot.run()

import os
import discord
import asyncio
import logging
from dotenv import load_dotenv

load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("TestBot")

TOKEN = os.getenv("DISCORD_TOKEN")

if not TOKEN:
    logger.error("DISCORD_TOKEN environment variable not set.")
    exit(1)

class TestClient(discord.Client):
    async def on_ready(self):
        logger.info(f"Logged in as {self.user} (ID: {self.user.id})")
        
        user_id = 1422460259691270144
        logger.info(f"Attempting to fetch user ID: {user_id}")
        
        try:
            user = await self.fetch_user(user_id)
            logger.info(f"SUCCESS! Found user: {user.name}#{user.discriminator} (ID: {user.id})")
            logger.info(f"Avatar URL: {user.avatar.url if user.avatar else user.default_avatar.url}")
        except discord.NotFound:
            logger.error(f"User with ID {user_id} not found (404).")
        except discord.HTTPException as e:
            logger.error(f"HTTP Exception fetching user: {e}")
        except Exception as e:
            logger.error(f"Unexpected error: {e}")
        
        await self.close()

async def main():
    # discord.py-self client init
    client = TestClient()
    await client.start(TOKEN)

if __name__ == "__main__":
    asyncio.run(main())

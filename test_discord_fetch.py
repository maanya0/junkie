import os
import discord
import asyncio
import logging
from dotenv import load_dotenv
from core.execution_context import set_current_channel
from tools.bio_tools import BioTools

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
        
        target_user_id = 1422460259691270144
        
        # Simulate DM context
        # We need a channel object that behaves like a DMChannel
        # Ideally we fetch the user and create DM, but if that fails (e.g. privacy), we mock it.
        
        channel = None
        try:
            user = await self.fetch_user(target_user_id)
            # Creating DM might fail if user has DMs closed, but let's try
            # channel = await user.create_dm()
            # Actually, for the purpose of testing BioTools client access, 
            # we just need ANY channel that has the client state.
            
            # Let's use a mock channel that has the client state but no guild
            class MockDMChannel:
                def __init__(self, client):
                    self._state = client._connection
                    self.guild = None
                    self.id = 123456789
                    self.type = discord.ChannelType.private
                
                def __repr__(self):
                    return "<MockDMChannel>"

            logger.info(f"ConnectionState attributes: {dir(self._connection)}")
            channel = MockDMChannel(self)
            
        except Exception as e:
            logger.error(f"Error setting up mock channel: {e}")
            return

        logger.info(f"Setting execution context with channel: {channel}")
        set_current_channel(channel)
        
        # Initialize BioTools with client injection
        bio_tools = BioTools(client=self)
        
        logger.info(f"Testing get_user_details for {target_user_id}...")
        details = await bio_tools.get_user_details(target_user_id)
        logger.info(f"Result:\n{details}")
        
        logger.info(f"Testing get_user_avatar for {target_user_id}...")
        avatar_result = await bio_tools.get_user_avatar(target_user_id)
        logger.info(f"Result: {avatar_result.content}")
        if avatar_result.images:
            logger.info(f"Image URL: {avatar_result.images[0].url}")
        
        await self.close()

async def main():
    # discord.py-self client init
    client = TestClient()
    await client.start(TOKEN)

if __name__ == "__main__":
    asyncio.run(main())

import asyncio
from discord_bot.chat_handler import main_cli
from core.observability import setup_langdb_tracing

if __name__ == "__main__":
    setup_langdb_tracing()
    asyncio.run(main_cli())

import os
import json
import logging
from dotenv import load_dotenv
from selfbot import SelfBot
from tldr import setup_tldr

# Setup logging for Railway
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

load_dotenv()

# ──────────────────────────────────────────────
# Whitelist System
# ──────────────────────────────────────────────

def load_whitelist():
    try:
        with open('whitelisted_users.json', 'r') as f:
            data = json.load(f)
            users = data.get('whitelisted_users', [])
            logger.info(f"Loaded whitelist: {users}")
            return users
    except FileNotFoundError:
        your_id = int(os.getenv("YOUR_DISCORD_ID", "0"))
        save_whitelist([your_id])
        return [your_id]
    except Exception as e:
        logger.error(f"Error loading whitelist: {e}")
        return []

def save_whitelist(users):
    try:
        with open('whitelisted_users.json', 'w') as f:
            json.dump({'whitelisted_users': users}, f, indent=4)
        logger.info(f"Saved whitelist: {users}")
    except Exception as e:
        logger.error(f"Error saving whitelist: {e}")

# Initialize whitelist
whitelisted_users = load_whitelist()

# Log file status
logger.info(f"Whitelist file exists: {os.path.exists('whitelisted_users.json')}")
logger.info(f"Current whitelist: {whitelisted_users}")

# ──────────────────────────────────────────────
# Bot Setup
# ──────────────────────────────────────────────

bot = SelfBot(
    token=os.getenv("DISCORD_TOKEN"),
    prefix="!",
)

setup_tldr(bot)

# ──────────────────────────────────────────────
# Whitelist Management Commands
# ──────────────────────────────────────────────

@bot.command("wladd")
async def whitelist_add(ctx, user_id: int):
    """Add user to whitelist (owner only)"""
    if ctx.author.id != bot.bot.user.id:
        return
    
    if user_id not in whitelisted_users:
        whitelisted_users.append(user_id)
        save_whitelist(whitelisted_users)
        await ctx.send(f"✅ Added {user_id} to whitelist", delete_after=3)
        logger.info(f"Added {user_id} to whitelist")
    else:
        await ctx.send("❌ Already whitelisted", delete_after=3)

@bot.command("wlremove")
async def whitelist_remove(ctx, user_id: int):
    """Remove user from whitelist (owner only)"""
    if ctx.author.id != bot.bot.user.id:
        return
    
    if user_id in whitelisted_users:
        whitelisted_users.remove(user_id)
        save_whitelist(whitelisted_users)
        await ctx.send(f"❌ Removed {user_id} from whitelist", delete_after=3)
        logger.info(f"Removed {user_id} from whitelist")
    else:
        await ctx.send("❌ Not in whitelist", delete_after=3)

@bot.command("wllist")
async def whitelist_list(ctx):
    """List whitelisted users (owner only)"""
    if ctx.author.id != bot.bot.user.id:
        return
    
    await ctx.send(f"Whitelisted: {whitelisted_users}", delete_after=10)
    logger.info(f"Listed whitelist: {whitelisted_users}")

@bot.command("wlstatus")
async def wlstatus(ctx, user_id: int = None):
    """Check if someone is whitelisted (owner only)"""
    if ctx.author.id != bot.bot.user.id:
        return
    
    target_id = user_id or ctx.author.id
    status = "✅ whitelisted" if target_id in whitelisted_users else "❌ not whitelisted"
    await ctx.send(f"User {target_id}: {status}", delete_after=5)
    logger.info(f"Status check: {target_id} -> {status}")

@bot.command("debugwl")
async def debugwl(ctx):
    """Show full whitelist debug info (owner only)"""
    if ctx.author.id != bot.bot.user.id:
        return
    
    file_exists = os.path.exists('whitelisted_users.json')
    
    embed = discord.Embed(title="Whitelist Debug", color=0x00ff00)
    embed.add_field(name="File exists", value=file_exists, inline=False)
    embed.add_field(name="Whitelist", value=str(whitelisted_users), inline=False)
    embed.add_field(name="Your ID", value=str(ctx.author.id), inline=False)
    embed.add_field(name="You in list", value=str(ctx.author.id in whitelisted_users), inline=False)
    
    await ctx.send(embed=embed, delete_after=10)
    logger.info(f"Debug - File: {file_exists}, Whitelist: {whitelisted_users}, Your ID: {ctx.author.id}")

if __name__ == "__main__":
    bot.run()

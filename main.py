import os
import json
from dotenv import load_dotenv
from selfbot import SelfBot
from tldr import setup_tldr

load_dotenv()

# ──────────────────────────────────────────────
# Whitelist System
# ──────────────────────────────────────────────

def load_whitelist():
    try:
        with open('whitelist.json', 'r') as f:
            data = json.load(f)
            return data.get('users', [])
    except FileNotFoundError:
        # Create default whitelist with your ID
        your_id = int(os.getenv("YOUR_DISCORD_ID", "0"))
        save_whitelist([your_id])
        return [your_id]

def save_whitelist(users):
    with open('whitelist.json', 'w') as f:
        json.dump({'users': users}, f, indent=4)

whitelisted_users = load_whitelist()

bot = SelfBot(
    token=os.getenv("DISCORD_TOKEN"),
    prefix="!",
)

setup_tldr(bot)

# Add whitelist management commands
@bot.command("wladd")
async def whitelist_add(ctx, user_id: int):
    """Add user to whitelist"""
    if ctx.author.id != bot.bot.user.id:  # Only you can manage whitelist
        return
    
    if user_id not in whitelisted_users:
        whitelisted_users.append(user_id)
        save_whitelist(whitelisted_users)
        await ctx.send(f"✅ Added {user_id} to whitelist", delete_after=3)
    else:
        await ctx.send("❌ Already whitelisted", delete_after=3)

@bot.command("wlremove")
async def whitelist_remove(ctx, user_id: int):
    """Remove user from whitelist"""
    if ctx.author.id != bot.bot.user.id:  # Only you can manage whitelist
        return
    
    if user_id in whitelisted_users:
        whitelisted_users.remove(user_id)
        save_whitelist(whitelisted_users)
        await ctx.send(f"❌ Removed {user_id} from whitelist", delete_after=3)
    else:
        await ctx.send("❌ Not in whitelist", delete_after=3)

@bot.command("wllist")
async def whitelist_list(ctx):
    """List whitelisted users"""
    if ctx.author.id != bot.bot.user.id:  # Only you can view list
        return
    
    await ctx.send(f"Whitelisted users: {whitelisted_users}", delete_after=10)

if __name__ == "__main__":
    bot.run()

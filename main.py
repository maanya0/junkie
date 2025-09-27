import discord
from discord.ext import commands
import json
import os
import asyncio
from datetime import datetime

# Configuration
CONFIG = {
    'prefix': '!',  # Change your prefix here
    'owner_id': 'YOUR_DISCORD_ID_HERE',  # Replace with your Discord ID
    'token': 'YOUR_TOKEN_HERE'  # Replace with your token
}

# Load/Save Whitelist Functions
def load_whitelist():
    try:
        with open('whitelist.json', 'r') as f:
            data = json.load(f)
            return data.get('whitelisted_users', [])
    except FileNotFoundError:
        default_whitelist = [int(CONFIG['owner_id'])]
        save_whitelist(default_whitelist)
        return default_whitelist

def save_whitelist(whitelist):
    with open('whitelist.json', 'w') as f:
        json.dump({'whitelisted_users': whitelist}, f, indent=4)

# Initialize whitelist
whitelisted_users = load_whitelist()

# Whitelist check decorator
def is_whitelisted():
    def predicate(ctx):
        return ctx.author.id in whitelisted_users
    return commands.check(predicate)

# Initialize bot
intents = discord.Intents.all()
client = commands.Bot(
    command_prefix=CONFIG['prefix'],
    self_bot=True,
    intents=intents
)

# Remove default help command
client.remove_command('help')

# Startup event
@client.event
async def on_ready():
    print(f'✅ Selfbot Ready: {client.user}')
    print(f'📝 Prefix: {CONFIG["prefix"]}')
    print(f'👥 Whitelisted users: {len(whitelisted_users)}')
    print(f'🔗 Connected to {len(client.guilds)} servers')
    
    # Set status
    await client.change_presence(
        status=discord.Status.dnd,
        activity=discord.Game(name="Whitelisted Only 🔒")
    )

# Whitelist Management Commands
@client.command(name='wladd', aliases=['whitelistadd'])
async def whitelist_add(ctx, user: discord.User = None):
    """Add user to whitelist (owner only)"""
    if ctx.author.id != int(CONFIG['owner_id']):
        return
    
    if user is None:
        await ctx.send("❌ Usage: `!wladd @user` or `!wladd user_id`")
        return
    
    if user.id not in whitelisted_users:
        whitelisted_users.append(user.id)
        save_whitelist(whitelisted_users)
        await ctx.send(f'✅ Added {user.mention} to whitelist!')
    else:
        await ctx.send(f'{user.mention} is already whitelisted!')

@client.command(name='wlremove', aliases=['whitelistremove'])
async def whitelist_remove(ctx, user: discord.User = None):
    """Remove user from whitelist (owner only)"""
    if ctx.author.id != int(CONFIG['owner_id']):
        return
    
    if user is None:
        await ctx.send("❌ Usage: `!wlremove @user` or `!wlremove user_id`")
        return
    
    if user.id in whitelisted_users:
        whitelisted_users.remove(user.id)
        save_whitelist(whitelisted_users)
        await ctx.send(f'❌ Removed {user.mention} from whitelist!')
    else:
        await ctx.send(f'{user.mention} is not whitelisted!')

@client.command(name='whitelist', aliases=['wllist'])
async def whitelist_list(ctx):
    """List all whitelisted users"""
    if ctx.author.id != int(CONFIG['owner_id']):
        return
    
    if not whitelisted_users:
        await ctx.send("No users are whitelisted.")
        return
    
    embed = discord.Embed(
        title="🔒 Whitelisted Users",
        description=f"Total: {len(whitelisted_users)} users",
        color=0x00ff00,
        timestamp=datetime.utcnow()
    )
    
    for i, user_id in enumerate(whitelisted_users[:25], 1):  # Discord limit
        user = client.get_user(user_id)
        if user:
            embed.add_field(
                name=f"{i}. {user.name}#{user.discriminator}",
                value=f"ID: `{user.id}`",
                inline=False
            )
        else:
            embed.add_field(
                name=f"{i}. Unknown User",
                value=f"ID: `{user_id}`",
                inline=False
            )
    
    await ctx.send(embed=embed)

@client.command(name='amIwhitelisted', aliases=['amiwl'])
async def am_i_whitelisted(ctx):
    """Check if you're whitelisted"""
    if ctx.author.id in whitelisted_users:
        embed = discord.Embed(
            title="✅ Whitelisted",
            description="You have access to all commands!",
            color=0x00ff00
        )
    else:
        embed = discord.Embed(
            title="❌ Not Whitelisted",
            description="You don't have access to bot commands.",
            color=0xff0000
        )
    await ctx.send(embed=embed)

# Error handler
@client.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CheckFailure):
        # Silent fail for non-whitelisted users
        return
    elif isinstance(error, commands.CommandNotFound):
        return
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(f"❌ Missing argument: {error.param.name}")
    else:
        print(f"Error: {error}")

# Message handler with whitelist check
@client.event
async def on_message(message):
    if message.author == client.user:
        return
    
    # Check whitelist for command usage
    if message.content.startswith(CONFIG['prefix']):
        if message.author.id not in whitelisted_users:
            try:
                await message.add_reaction('🔒')  # Lock emoji for denied
            except:
                pass
            return
    
    await client.process_commands(message)

# Run bot
if __name__ == "__main__":
    try:
        client.run(CONFIG['token'], bot=False)
    except Exception as e:
        print(f"Failed to start bot: {e}")

import discord
from discord.ext import commands
import asyncio
import datetime
import random

# Import whitelist from main
from main import is_whitelisted

class SelfbotCommands(commands.Cog):
    def __init__(self, client):
        self.client = client
    
    @commands.command(name='ping')
    @is_whitelisted()
    async def ping(self, ctx):
        """Check bot latency"""
        latency = round(self.client.latency * 1000)
        embed = discord.Embed(
            title="🏓 Pong!",
            description=f"Latency: `{latency}ms`",
            color=0x00ff00
        )
        await ctx.send(embed=embed)
    
    @commands.command(name='purge', aliases=['clear'])
    @is_whitelisted()
    async def purge(self, ctx, amount: int = 5):
        """Delete your recent messages"""
        if amount > 100:
            amount = 100
        
        def is_me(m):
            return m.author == self.client.user
        
        deleted = await ctx.channel.purge(limit=amount, check=is_me)
        await ctx.send(f'🗑️ Deleted {len(deleted)} messages', delete_after=3)
    
    @commands.command(name='spam')
    @is_whitelisted()
    async def spam(self, ctx, times: int = 5, *, message: str = "spam"):
        """Spam messages (use carefully)"""
        if times > 20:
            times = 20
        
        for i in range(times):
            await ctx.send(f"{message} [{i+1}/{times}]")
            await asyncio.sleep(0.5)
    
    @commands.command(name='ghostping')
    @is_whitelisted()
    async def ghostping(self, ctx, user: discord.Member = None):
        """Ghost ping someone"""
        if user is None:
            await ctx.send("❌ Mention a user to ghost ping!")
            return
        
        msg = await ctx.send(f"{user.mention}")
        await msg.delete()
    
    @commands.command(name='status')
    @is_whitelisted()
    async def status(self, ctx, *, status_text: str = None):
        """Change your status"""
        if status_text is None:
            await ctx.send("❌ Provide a status text!")
            return
        
        activity = discord.Game(name=status_text)
        await self.client.change_presence(activity=activity)
        await ctx.send(f"✅ Status changed to: `{status_text}`")
    
    @commands.command(name='info')
    @is_whitelisted()
    async def info(self, ctx):
        """Show bot info"""
        embed = discord.Embed(
            title="🔒 Selfbot Info",
            description="Whitelisted Selfbot Wrapper",
            color=0x00ff00,
            timestamp=datetime.datetime.utcnow()
        )
        
        embed.add_field(name="👤 User", value=self.client.user.mention, inline=True)
        embed.add_field(name="📊 Servers", value=len(self.client.guilds), inline=True)
        embed.add_field(name="⏱️ Uptime", value="Active", inline=True)
        embed.add_field(name="🔒 Whitelist", value="Enabled", inline=True)
        embed.add_field(name="📝 Prefix", value=f"`{ctx.prefix}`", inline=True)
        
        await ctx.send(embed=embed)
    
    @commands.command(name='help')
    @is_whitelisted()
    async def help_command(self, ctx):
        """Show available commands"""
        embed = discord.Embed(
            title="🔒 Selfbot Commands",
            description="Whitelisted commands only",
            color=0x00ff00
        )
        
        commands_list = [
            ("`ping`", "Check bot latency"),
            ("`purge [amount]`", "Delete your messages"),
            ("`spam [times] [message]`", "Spam messages"),
            ("`ghostping @user`", "Ghost ping someone"),
            ("`status [text]`", "Change your status"),
            ("`info`", "Show bot info"),
            ("`help`", "Show this help"),
            ("`wladd @user`", "Add to whitelist (owner)"),
            ("`wlremove @user`", "Remove from whitelist (owner)"),
            ("`whitelist`", "List whitelisted users (owner)"),
            ("`amIwhitelisted`", "Check if you're whitelisted")
        ]
        
        for cmd, desc in commands_list:
            embed.add_field(name=cmd, value=desc, inline=False)
        
        await ctx.send(embed=embed)

def setup(client):
    client.add_cog(SelfbotCommands(client))

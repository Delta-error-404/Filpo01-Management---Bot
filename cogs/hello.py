import discord
from discord.ext import commands
from config import config_db
from utils import format_hello_message

class HelloCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        config = await config_db.get()
        hello_config = config.get("hello_message", {})
        
        channel_id = hello_config.get("channel_id")
        message_template = hello_config.get("text")
        
        if not channel_id or not message_template:
            return
        
        channel = member.guild.get_channel(channel_id)
        if not channel:
            return
        
        try:
            message = format_hello_message(message_template, member, member.guild)
            embed = discord.Embed(description=message, color=discord.Color.green())
            embed.set_author(name=f"Willkommen {member.name}!", icon_url=member.display_avatar.url)
            embed.set_footer(text=f"Mitglied #{member.guild.member_count}")
            await channel.send(embed=embed)
        except discord.HTTPException:
            pass

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        config = await config_db.get()
        goodbye_config = config.get("goodbye_message", {})
        
        channel_id = goodbye_config.get("channel_id")
        message_template = goodbye_config.get("text")
        
        if not channel_id or not message_template:
            return
        
        channel = member.guild.get_channel(channel_id)
        if not channel:
            return
        
        try:
            message = format_hello_message(message_template, member, member.guild)
            embed = discord.Embed(description=message, color=discord.Color.red())
            embed.set_author(name=f"{member.name} hat den Server verlassen", icon_url=member.display_avatar.url)
            embed.set_footer(text=f"Mitglied #{member.guild.member_count}")
            await channel.send(embed=embed)
        except discord.HTTPException:
            pass

async def setup(bot: commands.Bot):
    await bot.add_cog(HelloCog(bot))
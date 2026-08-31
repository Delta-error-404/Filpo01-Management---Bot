import discord
from discord.ext import commands
from config import config_db

class WelcomeCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        config = await config_db.get()
        welcome_role_id = config.get("welcome_role_id")
        
        if welcome_role_id:
            role = member.guild.get_role(welcome_role_id)
            if role:
                try:
                    await member.add_roles(role, reason="Welcome Role")
                except discord.HTTPException:
                    pass

async def setup(bot: commands.Bot):
    await bot.add_cog(WelcomeCog(bot))
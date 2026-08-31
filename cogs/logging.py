import discord
from discord.ext import commands
from datetime import datetime, timezone, timedelta
from config import config_db


def _dt(dt: datetime | None) -> str:
    if not dt:
        return "?"
    return f"<t:{int(dt.timestamp())}:F>"


def _duration_text(td: timedelta) -> str:
    total = td.total_seconds()
    if total < 3600:
        return f"{int(total // 60)} Min."
    if total < 86400:
        return f"{int(total // 3600)} Std."
    if total < 2592000:
        return f"{int(total // 86400)} Tage"
    if total < 31536000:
        return f"{int(total // 2592000)} Monate"
    return f"{int(total // 31536000)} Jahre"


class LoggingCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def _log_channel(self, guild: discord.Guild) -> discord.TextChannel | None:
        config = await config_db.get()
        log_channel_id = config.get("log_channel_id")
        if not log_channel_id:
            return None
        return guild.get_channel(log_channel_id)

    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return

        log_channel = await self._log_channel(message.guild)
        if not log_channel:
            return

        if not message.content:
            return

        embed = discord.Embed(
            title="🗑️ Nachricht gelöscht",
            description=message.content[:1000],
            color=discord.Color.red(),
            timestamp=datetime.now(timezone.utc)
        )
        embed.set_author(name=f"{message.author}", icon_url=message.author.display_avatar.url)
        embed.add_field(name="Channel", value=message.channel.mention, inline=True)
        embed.add_field(name="Author", value=f"{message.author} (`{message.author.id}`)", inline=True)

        try:
            await log_channel.send(embed=embed)
        except discord.HTTPException:
            pass

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message):
        if before.author.bot or not before.guild:
            return
        if before.content == after.content and before.embeds == after.embeds:
            return

        log_channel = await self._log_channel(before.guild)
        if not log_channel:
            return

        embed = discord.Embed(
            title="✏️ Nachricht bearbeitet",
            color=discord.Color.orange(),
            timestamp=datetime.now(timezone.utc)
        )
        embed.set_author(name=f"{before.author}", icon_url=before.author.display_avatar.url)
        embed.add_field(name="Channel", value=before.channel.mention, inline=True)
        embed.add_field(name="Vorher", value=before.content[:1000] or "*leer*", inline=False)
        embed.add_field(name="Nachher", value=after.content[:1000] or "*leer*", inline=False)
        embed.add_field(name="Link", value=f"[Springen]({after.jump_url})", inline=True)

        try:
            await log_channel.send(embed=embed)
        except discord.HTTPException:
            pass

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        if before.roles == after.roles:
            return

        log_channel = await self._log_channel(after.guild)
        if not log_channel:
            return

        added = [r for r in after.roles if r not in before.roles]
        removed = [r for r in before.roles if r not in after.roles]
        if not added and not removed:
            return

        embed = discord.Embed(
            title="🎭 Rolle geändert",
            color=discord.Color.blue(),
            timestamp=datetime.now(timezone.utc)
        )
        embed.set_author(name=f"{after}", icon_url=after.display_avatar.url)
        if added:
            embed.add_field(name="➕ Hinzugefügt", value=", ".join(r.mention for r in added), inline=False)
        if removed:
            embed.add_field(name="➖ Entfernt", value=", ".join(r.mention for r in removed), inline=False)
        embed.add_field(name="User ID", value=f"`{after.id}`", inline=True)
        embed.add_field(name="Rollen", value=f"{len(after.roles) - 1}", inline=True)

        try:
            await log_channel.send(embed=embed)
        except discord.HTTPException:
            pass

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        log_channel = await self._log_channel(member.guild)
        if not log_channel:
            return

        now = datetime.now(timezone.utc)
        embed = discord.Embed(
            title="👋 Mitglied beigetreten",
            color=discord.Color.green(),
            timestamp=now
        )
        embed.set_author(name=f"{member}", icon_url=member.display_avatar.url)
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="User ID", value=f"`{member.id}`", inline=True)
        embed.add_field(name="Mitglieder", value=f"#{member.guild.member_count}", inline=True)
        embed.add_field(name="Konto erstellt", value=f"{_dt(member.created_at)}\n({_duration_text(now - member.created_at)} alt)", inline=True)
        if member.joined_at:
            embed.add_field(name="Beitritt", value=_dt(member.joined_at), inline=True)
        if member.bot:
            embed.add_field(name="🤖", value="Bot-Account", inline=True)

        try:
            await log_channel.send(embed=embed)
        except discord.HTTPException:
            pass

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        log_channel = await self._log_channel(member.guild)
        if not log_channel:
            return

        now = datetime.now(timezone.utc)
        embed = discord.Embed(
            title="👋 Mitglied verlassen",
            color=discord.Color.red(),
            timestamp=now
        )
        embed.set_author(name=f"{member}", icon_url=member.display_avatar.url)
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="User ID", value=f"`{member.id}`", inline=True)
        embed.add_field(name="Mitglieder", value=f"#{member.guild.member_count}", inline=True)
        if member.joined_at:
            embed.add_field(name="War dabei", value=f"seit {_dt(member.joined_at)}\n({_duration_text(now - member.joined_at)})", inline=True)
        roles = [r.mention for r in member.roles if r.name != "@everyone"]
        embed.add_field(name="Rollen", value=", ".join(roles) if roles else "Keine", inline=False)
        embed.add_field(name="Konto erstellt", value=_dt(member.created_at), inline=True)
        if member.bot:
            embed.add_field(name="🤖", value="Bot-Account", inline=True)

        try:
            await log_channel.send(embed=embed)
        except discord.HTTPException:
            pass

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        log_channel = await self._log_channel(member.guild)
        if not log_channel:
            return

        if before.channel == after.channel:
            return

        now = datetime.now(timezone.utc)

        if before.channel is None and after.channel is not None:
            embed = discord.Embed(
                title="🔊 Voice beigetreten",
                color=discord.Color.green(),
                timestamp=now
            )
            embed.set_author(name=f"{member}", icon_url=member.display_avatar.url)
            embed.add_field(name="Channel", value=after.channel.mention, inline=True)
            members = len(after.channel.members)
            embed.add_field(name="Mitglieder im Channel", value=str(members), inline=True)
        elif before.channel is not None and after.channel is None:
            embed = discord.Embed(
                title="🔇 Voice verlassen",
                color=discord.Color.red(),
                timestamp=now
            )
            embed.set_author(name=f"{member}", icon_url=member.display_avatar.url)
            embed.add_field(name="Channel", value=before.channel.mention, inline=True)
        else:
            embed = discord.Embed(
                title="↔️ Voice gewechselt",
                color=discord.Color.orange(),
                timestamp=now
            )
            embed.set_author(name=f"{member}", icon_url=member.display_avatar.url)
            embed.add_field(name="Von", value=before.channel.mention, inline=True)
            embed.add_field(name="Nach", value=after.channel.mention, inline=True)

        embed.add_field(name="User ID", value=f"`{member.id}`", inline=True)

        try:
            await log_channel.send(embed=embed)
        except discord.HTTPException:
            pass

    @commands.Cog.listener()
    async def on_member_ban(self, guild: discord.Guild, user: discord.User):
        log_channel = await self._log_channel(guild)
        if not log_channel:
            return

        embed = discord.Embed(
            title="🔨 Mitglied gebannt",
            color=discord.Color.dark_red(),
            timestamp=datetime.now(timezone.utc)
        )
        embed.set_author(name=f"{user}", icon_url=user.display_avatar.url)
        embed.set_thumbnail(url=user.display_avatar.url)
        embed.add_field(name="User", value=f"{user} (`{user.id}`)", inline=True)
        embed.add_field(name="Mitglieder", value=f"#{guild.member_count}", inline=True)

        try:
            await log_channel.send(embed=embed)
        except discord.HTTPException:
            pass

    @commands.Cog.listener()
    async def on_member_unban(self, guild: discord.Guild, user: discord.User):
        log_channel = await self._log_channel(guild)
        if not log_channel:
            return

        embed = discord.Embed(
            title="🔓 Mitglied entbannt",
            color=discord.Color.green(),
            timestamp=datetime.now(timezone.utc)
        )
        embed.set_author(name=f"{user}", icon_url=user.display_avatar.url)
        embed.add_field(name="User", value=f"{user} (`{user.id}`)", inline=True)

        try:
            await log_channel.send(embed=embed)
        except discord.HTTPException:
            pass


async def setup(bot: commands.Bot):
    await bot.add_cog(LoggingCog(bot))

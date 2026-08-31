import time
import platform
from datetime import datetime, timezone

import discord
from discord.ext import commands
from discord import app_commands

from utils import success_embed, error_embed, create_embed
from utils.permissions import is_owner, is_dev, require_authorized


class UtilityCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._start_time = time.time()

    @app_commands.command(name="ping", description="Zeigt Latenz und Debug-Informationen")
    async def ping(self, interaction: discord.Interaction):
        await interaction.response.defer()

        start = time.perf_counter()
        msg = await interaction.followup.send("🏓 Pong...")
        roundtrip = (time.perf_counter() - start) * 1000

        uptime = int(time.time() - self._start_time)
        days, rem = divmod(uptime, 86400)
        hours, rem = divmod(rem, 3600)
        minutes, seconds = divmod(rem, 60)

        embed = discord.Embed(
            title="🏓 Pong!",
            color=discord.Color.blue(),
            timestamp=datetime.now(timezone.utc)
        )
        embed.add_field(name="Websocket-Latenz", value=f"```{self.bot.latency * 1000:.1f} ms```", inline=True)
        embed.add_field(name="Roundtrip", value=f"```{roundtrip:.1f} ms```", inline=True)
        embed.add_field(name="Uptime", value=f"```{days}d {hours}h {minutes}m {seconds}s```", inline=False)
        embed.add_field(name="Guilds", value=f"```{len(self.bot.guilds)}```", inline=True)
        embed.add_field(name="Mitglieder", value=f"```{sum(g.member_count or 0 for g in self.bot.guilds)}```", inline=True)
        embed.add_field(name="Python", value=f"```{platform.python_version()}```", inline=True)
        embed.add_field(name="discord.py", value=f"```{discord.__version__}```", inline=True)

        await msg.edit(content="", embed=embed)

    @app_commands.command(name="sendmessage", description="Sende eine Nachricht als Bot in den aktuellen Channel (nur Owner/Dev)")
    @app_commands.describe(message="Nachricht, die als Bot gesendet werden soll")
    async def sendmessage(self, interaction: discord.Interaction, message: str):
        if not (is_owner(interaction.user) or is_dev(interaction.user)):
            await interaction.response.send_message(
                embed=error_embed("Nur der Bot-Besitzer oder ein Entwickler kann diesen Befehl nutzen."),
                ephemeral=True
            )
            return

        if not message.strip():
            await interaction.response.send_message(
                embed=error_embed("Nachricht darf nicht leer sein"),
                ephemeral=True
            )
            return

        await interaction.response.send_message(
            embed=success_embed("✅ Nachricht wird gesendet..."),
            ephemeral=True
        )
        await interaction.channel.send(message)

    @app_commands.command(name="announce", description="Sende eine Ankündigung als Embed mit optionalem Ping (nur Owner/Dev)")
    @app_commands.describe(
        text="Ankündigungstext",
        channel="Ziel-Channel (Standard: aktueller Channel)",
        ping="Rolle, die gepingt werden soll (optional)",
        everyone="@everyone mitbenachrichtigen?"
    )
    async def announce(self, interaction: discord.Interaction, text: str, channel: discord.TextChannel = None,
                       ping: discord.Role = None, everyone: bool = False):
        if not (is_owner(interaction.user) or is_dev(interaction.user)):
            await interaction.response.send_message(
                embed=error_embed("Nur der Bot-Besitzer oder ein Entwickler kann diesen Befehl nutzen."),
                ephemeral=True
            )
            return

        if not text.strip():
            await interaction.response.send_message(
                embed=error_embed("Ankündigungstext darf nicht leer sein"),
                ephemeral=True
            )
            return

        target = channel or interaction.channel
        if not target:
            await interaction.response.send_message(
                embed=error_embed("Kein gueltiger Ziel-Channel"),
                ephemeral=True
            )
            return

        content = None
        if everyone:
            content = "@everyone"
        elif ping:
            content = ping.mention

        embed = discord.Embed(
            title="📢 Ankündigung",
            description=text,
            color=discord.Color.blue(),
            timestamp=datetime.now(timezone.utc)
        )
        if interaction.guild:
            embed.set_author(
                name=interaction.guild.name,
                icon_url=interaction.guild.icon.url if interaction.guild.icon else None
            )
        embed.set_footer(text=f"Von {interaction.user}")

        try:
            await target.send(content=content, embed=embed)
        except discord.Forbidden:
            await interaction.response.send_message(
                embed=error_embed("Ich habe keine Berechtigung, in diesem Channel zu senden."),
                ephemeral=True
            )
            return
        except discord.HTTPException as e:
            await interaction.response.send_message(
                embed=error_embed(f"Fehler beim Senden: {e}"),
                ephemeral=True
            )
            return

        await interaction.response.send_message(
            embed=success_embed(f"✅ Ankündigung gesendet nach {target.mention}"),
            ephemeral=True
        )

    @app_commands.command(name="serverinfo", description="Informationen über den Server anzeigen")
    async def serverinfo(self, interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message(embed=error_embed("Nur auf einem Server moeglich."), ephemeral=True)
            return

        g = interaction.guild
        embed = create_embed(title=f"📊 {g.name}", color=discord.Color.blue())

        if g.icon:
            embed.set_thumbnail(url=g.icon.url)

        embed.add_field(name="ID", value=str(g.id), inline=True)
        embed.add_field(name="Besitzer", value=g.owner.mention if g.owner else "Unbekannt", inline=True)
        embed.add_field(name="Erstellt", value=f"<t:{int(g.created_at.timestamp())}:R>", inline=True)
        embed.add_field(name="Mitglieder", value=str(g.member_count), inline=True)
        embed.add_field(name="Bot", value=str(sum(1 for m in g.members if m.bot)), inline=True)
        embed.add_field(name="Menschen", value=str(g.member_count - sum(1 for m in g.members if m.bot)), inline=True)
        embed.add_field(name="Text-Channels", value=str(len(g.text_channels)), inline=True)
        embed.add_field(name="Voice-Channels", value=str(len(g.voice_channels)), inline=True)
        embed.add_field(name="Rollen", value=str(len(g.roles) - 1), inline=True)
        embed.add_field(name="Emojis", value=str(len(g.emojis)), inline=True)
        embed.add_field(name="Boost-Level", value=str(g.premium_tier), inline=True)
        embed.add_field(name="Boosts", value=str(g.premium_subscription_count), inline=True)

        if g.description:
            embed.add_field(name="Beschreibung", value=g.description[:1024], inline=False)

        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="userinfo", description="Informationen über einen User anzeigen")
    @app_commands.describe(user="User (leer = du)")
    async def userinfo(self, interaction: discord.Interaction, user: discord.Member = None):
        member = user or interaction.user
        embed = create_embed(title=f"👤 {member}", color=member.color if member.color != discord.Color.default() else discord.Color.blue())

        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="ID", value=str(member.id), inline=True)
        embed.add_field(name="Account erstellt", value=f"<t:{int(member.created_at.timestamp())}:R>", inline=True)
        embed.add_field(name="Beigetreten", value=f"<t:{int(member.joined_at.timestamp())}:R>" if member.joined_at else "Unbekannt", inline=True)

        roles = [r.mention for r in member.roles[1:]][::-1]
        if roles:
            embed.add_field(name=f"Rollen ({len(roles)})", value=", ".join(roles[:20]) + ("..." if len(roles) > 20 else ""), inline=False)

        perms = []
        if member.guild_permissions.administrator:
            perms.append("Administrator")
        elif member.guild_permissions.manage_guild:
            perms.append("Server verwalten")
        elif member.guild_permissions.manage_channels:
            perms.append("Channels verwalten")
        elif member.guild_permissions.manage_roles:
            perms.append("Rollen verwalten")
        elif member.guild_permissions.ban_members:
            perms.append("User bannen")
        elif member.guild_permissions.kick_members:
            perms.append("User kicken")
        if perms:
            embed.add_field(name="Key-Perms", value=", ".join(perms), inline=False)

        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="avatar", description="Avatar eines Users anzeigen")
    @app_commands.describe(user="User (leer = du)")
    async def avatar(self, interaction: discord.Interaction, user: discord.User = None):
        target = user or interaction.user
        embed = create_embed(title=f"🖼️ Avatar — {target}", color=discord.Color.blue())
        embed.set_image(url=target.display_avatar.url)
        embed.add_field(name="Format", value=f"[PNG]({target.display_avatar.with_format('png')}) | [JPG]({target.display_avatar.with_format('jpg')}) | [WEBP]({target.display_avatar.with_format('webp')})", inline=False)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="purge", description="Nachrichten loeschen (1-100)")
    @app_commands.describe(anzahl="Anzahl der zu loeschenden Nachrichten (1-100)", user="Nur Nachrichten dieses Users loeschen")
    async def purge(self, interaction: discord.Interaction, anzahl: int, user: discord.Member = None):
        if not await require_authorized(interaction):
            return

        if anzahl < 1 or anzahl > 100:
            await interaction.response.send_message(embed=error_embed("Anzahl muss zwischen 1 und 100 liegen."), ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        def check(msg):
            if user:
                return msg.author.id == user.id
            return True

        deleted = await interaction.channel.purge(limit=anzahl, check=check)
        await interaction.followup.send(embed=success_embed(f"✅ {len(deleted)} Nachrichten geloescht."), ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(UtilityCog(bot))

import discord
from discord.ext import commands
from discord import app_commands
import re
from config import config_db
from utils import require_authorized, success_embed, error_embed, parse_role_ids, format_role_mentions

class ConfigCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    config_group = app_commands.Group(name="config", description="Bot-Konfiguration verwalten")

    @config_group.command(name="config_dc_bot", description="Admin/Owner/Dev Rollen setzen (komma-getrennt)")
    @app_commands.describe(role="Rollen-IDs oder Mentions (komma-getrennt)")
    async def config_dc_bot(self, interaction: discord.Interaction, role: str):
        if not await require_authorized(interaction):
            return
        
        role_ids = parse_role_ids(role)
        if not role_ids:
            await interaction.response.send_message(
                embed=error_embed("Keine gültigen Rollen-IDs gefunden. Format: `123,456` oder `<@&123>,<@&456>`"),
                ephemeral=True
            )
            return
        
        valid_roles = []
        invalid = []
        for rid in role_ids:
            r = interaction.guild.get_role(rid)
            if r:
                valid_roles.append(rid)
            else:
                invalid.append(rid)
        
        await config_db.update(admin_roles=valid_roles)
        
        msg = f"✅ Admin-Rollen aktualisiert: {format_role_mentions(valid_roles, interaction.guild)}"
        if invalid:
            msg += f"\n⚠️ Nicht gefunden: {', '.join(str(x) for x in invalid)}"
        
        await interaction.response.send_message(embed=success_embed(msg), ephemeral=True)

    @config_group.command(name="welcome_role", description="Welcome-Rolle für neue Mitglieder setzen")
    @app_commands.describe(role="Die Welcome-Rolle")
    async def welcome_role(self, interaction: discord.Interaction, role: discord.Role):
        if not await require_authorized(interaction):
            return
        
        await config_db.update(welcome_role_id=role.id)
        await interaction.response.send_message(
            embed=success_embed(f"✅ Welcome-Rolle gesetzt: {role.mention}"),
            ephemeral=True
        )

    @config_group.command(name="ticket_show", description="Support-Rolle für Tickets setzen")
    @app_commands.describe(role="Die Support-Rolle")
    async def ticket_show(self, interaction: discord.Interaction, role: discord.Role):
        if not await require_authorized(interaction):
            return
        
        await config_db.update(ticket_show_role_id=role.id)
        await interaction.response.send_message(
            embed=success_embed(f"✅ Ticket-Support-Rolle gesetzt: {role.mention}"),
            ephemeral=True
        )

    @config_group.command(name="log_channel", description="Log-Channel für Commands setzen")
    @app_commands.describe(channel="Der Log-Channel")
    async def log_channel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        if not await require_authorized(interaction):
            return
        
        await config_db.update(log_channel_id=channel.id)
        await interaction.response.send_message(
            embed=success_embed(f"✅ Log-Channel gesetzt: {channel.mention}"),
            ephemeral=True
        )

    @config_group.command(name="hello_message", description="Willkommens-Nachricht und Channel setzen")
    @app_commands.describe(nachricht="Nachricht (Platzhalter: {user}, {username}, {server}, {count}, {user_id})", channel="Channel für die Nachricht")
    async def hello_message(self, interaction: discord.Interaction, nachricht: str, channel: discord.TextChannel):
        if not await require_authorized(interaction):
            return
        
        valid, err = self._validate_hello_message(nachricht)
        if not valid:
            await interaction.response.send_message(
                embed=error_embed(err),
                ephemeral=True
            )
            return
        
        await config_db.update(hello_message={"text": nachricht, "channel_id": channel.id})
        await interaction.response.send_message(
            embed=success_embed(f"✅ Willkommens-Nachricht gesetzt für {channel.mention}\nNachricht: `{nachricht}`"),
            ephemeral=True
        )

    @config_group.command(name="goodbye_message", description="Abschieds-Nachricht und Channel setzen")
    @app_commands.describe(nachricht="Nachricht (Platzhalter: {user}, {username}, {server}, {count}, {user_id})", channel="Channel fuer die Nachricht")
    async def goodbye_message(self, interaction: discord.Interaction, nachricht: str, channel: discord.TextChannel):
        if not await require_authorized(interaction):
            return
        
        valid, err = self._validate_hello_message(nachricht)
        if not valid:
            await interaction.response.send_message(
                embed=error_embed(err),
                ephemeral=True
            )
            return
        
        await config_db.update(goodbye_message={"text": nachricht, "channel_id": channel.id})
        await interaction.response.send_message(
            embed=success_embed(f"✅ Abschieds-Nachricht gesetzt fuer {channel.mention}\nNachricht: `{nachricht}`"),
            ephemeral=True
        )

    @config_group.command(name="voice_creator", description="Create-to-Join Voice Channel einrichten")
    @app_commands.describe(kategorie="Kategorie wo die Channels erstellt werden", channel="Der 'Create' Channel (User joint hier → neuer Channel)", name_template="Name-Vorlage: {user} = Username (Standard: '{user} Talk')")
    async def voice_creator(self, interaction: discord.Interaction, kategorie: discord.CategoryChannel, channel: discord.VoiceChannel, name_template: str = "{user} Talk"):
        if not await require_authorized(interaction):
            return
        
        if channel.category_id != kategorie.id:
            await interaction.response.send_message(
                embed=error_embed("Der Channel muss in der angegebenen Kategorie sein"),
                ephemeral=True
            )
            return
        
        valid, err = self._validate_template(name_template)
        if not valid:
            await interaction.response.send_message(embed=error_embed(err), ephemeral=True)
            return
        
        await config_db.update(voice_creator={"category_id": kategorie.id, "channel_id": channel.id, "name_template": name_template})
        await interaction.response.send_message(
            embed=success_embed(f"✅ Voice Creator aktiv\nKategorie: {kategorie.mention}\nCreate-Channel: {channel.mention}\nTemplate: `{name_template}`"),
            ephemeral=True
        )

    def _validate_template(self, template: str) -> tuple[bool, str]:
        if not template or not template.strip():
            return False, "Template darf nicht leer sein"
        if len(template) > 100:
            return False, "Template zu lang (max 100 Zeichen)"
        allowed = {"user", "username", "count"}
        placeholders = set(re.findall(r"\{(\w+)\}", template))
        invalid = placeholders - allowed
        if invalid:
            return False, f"Unbekannte Platzhalter: {', '.join(invalid)}. Erlaubt: {', '.join(allowed)}"
        if "user" not in placeholders and "username" not in placeholders:
            return False, "Template muss mindestens {user} oder {username} enthalten"
        return True, ""

    def _validate_hello_message(self, msg: str) -> tuple[bool, str]:
        if not msg or not msg.strip():
            return False, "Nachricht darf nicht leer sein"
        if len(msg) > 2000:
            return False, "Nachricht zu lang (max 2000 Zeichen)"
        allowed = {"user", "username", "server", "count", "user_id"}
        placeholders = set(re.findall(r"\{(\w+)\}", msg))
        invalid = placeholders - allowed
        if invalid:
            return False, f"Unbekannte Platzhalter: {', '.join(invalid)}. Erlaubt: {', '.join(allowed)}"
        return True, ""

    @config_group.command(name="show", description="Aktuelle Konfiguration anzeigen")
    async def show_config(self, interaction: discord.Interaction):
        if not await require_authorized(interaction):
            return
        
        config = await config_db.get()
        
        admin_roles = format_role_mentions(config.get("admin_roles", []), interaction.guild)
        welcome_role = interaction.guild.get_role(config.get("welcome_role_id")) if config.get("welcome_role_id") else "Nicht gesetzt"
        ticket_role = interaction.guild.get_role(config.get("ticket_show_role_id")) if config.get("ticket_show_role_id") else "Nicht gesetzt"
        log_channel = interaction.guild.get_channel(config.get("log_channel_id")) if config.get("log_channel_id") else "Nicht gesetzt"
        hello = config.get("hello_message", {})
        hello_text = hello.get("text", "Nicht gesetzt")
        hello_channel = interaction.guild.get_channel(hello.get("channel_id")) if hello.get("channel_id") else "Nicht gesetzt"
        
        vc = config.get("voice_creator", {})
        vc_cat = interaction.guild.get_channel(vc.get("category_id")) if vc.get("category_id") else "Nicht gesetzt"
        vc_ch = interaction.guild.get_channel(vc.get("channel_id")) if vc.get("channel_id") else "Nicht gesetzt"
        vc_tpl = vc.get("name_template", "{user} Talk")
        
        embed = discord.Embed(title="⚙️ Bot-Konfiguration", color=discord.Color.blue())
        embed.add_field(name="Admin-Rollen", value=admin_roles, inline=False)
        embed.add_field(name="Welcome-Rolle", value=welcome_role.mention if isinstance(welcome_role, discord.Role) else welcome_role, inline=True)
        embed.add_field(name="Ticket-Support-Rolle", value=ticket_role.mention if isinstance(ticket_role, discord.Role) else ticket_role, inline=True)
        embed.add_field(name="Log-Channel", value=log_channel.mention if isinstance(log_channel, discord.TextChannel) else log_channel, inline=True)
        embed.add_field(name="Hello-Channel", value=hello_channel.mention if isinstance(hello_channel, discord.TextChannel) else hello_channel, inline=True)
        embed.add_field(name="Hello-Nachricht", value=f"```{hello_text}```", inline=False)
        embed.add_field(name="Voice Creator", value=f"Kategorie: {vc_cat.mention if isinstance(vc_cat, discord.CategoryChannel) else vc_cat}\nCreate-Channel: {vc_ch.mention if isinstance(vc_ch, discord.VoiceChannel) else vc_ch}\nTemplate: `{vc_tpl}`", inline=False)
        
        await interaction.response.send_message(embed=embed, ephemeral=True)

async def setup(bot: commands.Bot):
    await bot.add_cog(ConfigCog(bot))
import discord
from discord.ext import commands
from discord import app_commands, ui
from config import config_db, tickets_db
from utils import require_authorized, success_embed, error_embed, info_embed, parse_dropdown_options, sanitize_dropdown_name
import asyncio
import io
import re

class TicketDropdown(ui.Select):
    def __init__(self, panel_id: str, options: list[str]):
        select_options = [
            discord.SelectOption(label=opt, value=opt, description=f"Ticket: {opt}")
            for opt in options
        ]
        super().__init__(
            placeholder="Kategorie wählen...",
            options=select_options,
            custom_id=f"ticket_dropdown_{panel_id}"
        )
        self.panel_id = panel_id
        self.dropdown_options = options

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        
        category = self.values[0]
        guild = interaction.guild
        user = interaction.user
        
        config = await config_db.get()
        support_role_id = config.get("ticket_show_role_id")
        support_role = guild.get_role(support_role_id) if support_role_id else None
        
        tickets_data = await tickets_db.get()
        counter = tickets_data.get("counter", 0) + 1
        
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            user: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, attach_files=True, embed_links=True),
            guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True, manage_messages=True, read_message_history=True)
        }
        
        if support_role:
            overwrites[support_role] = discord.PermissionOverwrite(
                view_channel=True, send_messages=True, manage_messages=True, read_message_history=True
            )
        
        for role_id in config.get("admin_roles", []):
            role = guild.get_role(role_id)
            if role:
                overwrites[role] = discord.PermissionOverwrite(
                    view_channel=True, send_messages=True, manage_channels=True, manage_messages=True, read_message_history=True
                )
        
        try:
            channel = await guild.create_text_channel(
                name=f"ticket-{user.name}-{category.lower()[:20]}-{counter:04d}",
                overwrites=overwrites,
                topic=f"Ticket von {user} ({user.id}) | Kategorie: {category} | Panel: {self.panel_id} | #{counter:04d}",
                reason=f"Ticket eröffnet: {category}"
            )
        except discord.HTTPException as e:
            await interaction.followup.send(embed=error_embed(f"Fehler beim Erstellen: {e}"), ephemeral=True)
            return
        
        tickets_data["counter"] = counter
        tickets_data.setdefault("active_tickets", []).append({
            "channel_id": channel.id,
            "owner_id": user.id,
            "category": category,
            "panel_id": self.panel_id,
            "claimed_by": None,
            "created_at": discord.utils.utcnow().isoformat(),
            "updated_at": discord.utils.utcnow().isoformat()
        })
        await tickets_db.save(tickets_data)
        
        embed = discord.Embed(
            title=f"Ticket #{counter:04d} - {category}",
            description=f"Erstellt von {user.mention}",
            color=discord.Color.blue()
        )
        embed.add_field(name="Kategorie", value=category, inline=True)
        embed.add_field(name="Status", value="Offen", inline=True)
        embed.add_field(name="Panel", value=self.panel_id, inline=True)
        embed.set_footer(text="Filpo01 DC Bot | Ticket System")
        
        view = TicketView()
        
        try:
            ping = f"{user.mention} {support_role.mention if support_role else ''}"
            await channel.send(content=ping, embed=embed, view=view)
        except discord.HTTPException:
            pass
        
        await interaction.followup.send(embed=success_embed(f"Ticket erstellt: {channel.mention}"), ephemeral=True)


class TicketView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @ui.button(label="Schließen", style=discord.ButtonStyle.danger, emoji="🔒", custom_id="ticket_close")
    async def close_button(self, interaction: discord.Interaction, button: ui.Button):
        await self.handle_close(interaction)

    @ui.button(label="Claim", style=discord.ButtonStyle.primary, emoji="📋", custom_id="ticket_claim")
    async def claim_button(self, interaction: discord.Interaction, button: ui.Button):
        await self.handle_claim(interaction)

    @ui.button(label="Unclaim", style=discord.ButtonStyle.secondary, emoji="🔓", custom_id="ticket_unclaim")
    async def unclaim_button(self, interaction: discord.Interaction, button: ui.Button):
        await self.handle_unclaim(interaction)

    async def handle_close(self, interaction: discord.Interaction):
        config = await config_db.get()
        support_role_id = config.get("ticket_show_role_id")
        
        tickets_data = await tickets_db.get()
        ticket = next((t for t in tickets_data.get("active_tickets", []) if t["channel_id"] == interaction.channel_id), None)
        
        if not ticket:
            await interaction.response.send_message(embed=error_embed("Ticket nicht gefunden"), ephemeral=True)
            return
        
        is_owner = ticket["owner_id"] == interaction.user.id
        is_claimer = ticket.get("claimed_by") == interaction.user.id
        is_support = support_role_id and any(r.id == support_role_id for r in interaction.user.roles)
        is_admin = any(r.id in config.get("admin_roles", []) for r in interaction.user.roles)
        
        if not (is_owner or is_claimer or is_support or is_admin or interaction.user.guild_permissions.administrator):
            await interaction.response.send_message(embed=error_embed("Keine Berechtigung"), ephemeral=True)
            return
        
        await interaction.response.send_message(embed=info_embed("Ticket wird in 5 Sekunden geschlossen..."), ephemeral=False)
        await asyncio.sleep(5)

        await self._save_transcript(interaction, ticket)

        tickets_data["active_tickets"] = [t for t in tickets_data["active_tickets"] if t["channel_id"] != interaction.channel_id]
        tickets_data.setdefault("closed_tickets", []).append(ticket)
        await tickets_db.save(tickets_data)
        
        try:
            await interaction.channel.delete(reason=f"Geschlossen von {interaction.user}")
        except discord.HTTPException:
            pass

    async def _save_transcript(self, interaction: discord.Interaction, ticket: dict):
        lines = []
        try:
            async for msg in interaction.channel.history(limit=100, oldest_first=True):
                content = msg.content or ""
                if not content and msg.embeds:
                    content = f"[Embed: {msg.embeds[0].title or 'ohne Titel'}]"
                if content:
                    lines.append(f"[{msg.created_at:%d.%m.%Y %H:%M}] {msg.author}: {content}")
        except discord.HTTPException:
            pass

        if not lines:
            return

        transcript = discord.File(
            io.BytesIO("\n".join(lines).encode("utf-8")),
            filename="ticket-%s-%s.txt" % (
                re.sub("[^\\w]", "_", str(ticket.get("category", "Ticket")))[:20],
                interaction.channel_id
            )
        )

        config = await config_db.get()
        log_channel_id = config.get("log_channel_id")
        log_channel = interaction.guild.get_channel(log_channel_id) if log_channel_id else None

        embed = discord.Embed(
            title=f"📄 Ticket-Transkript: {ticket.get('category', 'Ticket')}",
            description=f"Ticket von <@{ticket.get('owner_id')}> • {len(lines)} Nachrichten",
            color=discord.Color.blue()
        )

        if log_channel:
            try:
                await log_channel.send(embed=embed, file=transcript)
                return
            except discord.HTTPException:
                pass

        owner = interaction.guild.get_member(ticket.get("owner_id"))
        if owner:
            try:
                await owner.send(embed=embed, file=transcript)
            except (discord.Forbidden, discord.HTTPException):
                pass

    async def handle_claim(self, interaction: discord.Interaction):
        config = await config_db.get()
        support_role_id = config.get("ticket_show_role_id")
        is_support = support_role_id and any(r.id == support_role_id for r in interaction.user.roles)
        is_admin = any(r.id in config.get("admin_roles", []) for r in interaction.user.roles)
        
        if not (is_support or is_admin or interaction.user.guild_permissions.administrator):
            await interaction.response.send_message(embed=error_embed("Nur Support-Team"), ephemeral=True)
            return
        
        tickets_data = await tickets_db.get()
        ticket = next((t for t in tickets_data.get("active_tickets", []) if t["channel_id"] == interaction.channel_id), None)
        
        if not ticket:
            await interaction.response.send_message(embed=error_embed("Ticket nicht gefunden"), ephemeral=True)
            return
        
        if ticket.get("claimed_by"):
            claimer = interaction.guild.get_member(ticket["claimed_by"])
            await interaction.response.send_message(embed=error_embed(f"Bereits geclaimt von {claimer.mention if claimer else 'Unbekannt'}"), ephemeral=True)
            return
        
        ticket["claimed_by"] = interaction.user.id
        ticket["updated_at"] = discord.utils.utcnow().isoformat()
        await tickets_db.save(tickets_data)
        
        await self.update_embed(interaction, ticket, f"Geclaimt von {interaction.user.mention}")
        await interaction.response.send_message(embed=success_embed("Ticket geclaimt!"), ephemeral=True)

    async def handle_unclaim(self, interaction: discord.Interaction):
        tickets_data = await tickets_db.get()
        ticket = next((t for t in tickets_data.get("active_tickets", []) if t["channel_id"] == interaction.channel_id), None)
        
        if not ticket:
            await interaction.response.send_message(embed=error_embed("Ticket nicht gefunden"), ephemeral=True)
            return
        
        if ticket.get("claimed_by") != interaction.user.id:
            config = await config_db.get()
            is_admin = any(r.id in config.get("admin_roles", []) for r in interaction.user.roles)
            if not (is_admin or interaction.user.guild_permissions.administrator):
                await interaction.response.send_message(embed=error_embed("Nur Claimer oder Admins"), ephemeral=True)
                return
        
        ticket["claimed_by"] = None
        ticket["updated_at"] = discord.utils.utcnow().isoformat()
        await tickets_db.save(tickets_data)
        
        await self.update_embed(interaction, ticket, "Offen - Unzugewiesen")
        await interaction.response.send_message(embed=success_embed("Ticket freigegeben"), ephemeral=True)

    async def update_embed(self, interaction: discord.Interaction, ticket: dict, status: str):
        try:
            async for msg in interaction.channel.history(limit=1):
                if msg.author == interaction.guild.me and msg.embeds:
                    embed = msg.embeds[0]
                    for i, field in enumerate(embed.fields):
                        if field.name == "Status":
                            embed.set_field_at(i, name="Status", value=status, inline=True)
                            break
                    await msg.edit(embed=embed)
                    break
        except discord.HTTPException:
            pass


class TicketPanelView(ui.View):
    def __init__(self, panel_id: str, options: list[str]):
        super().__init__(timeout=None)
        self.add_item(TicketDropdown(panel_id, options))


class TicketsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.bot.add_view(TicketView())

    async def cog_load(self):
        # Persistente Ticket-Panels beim Start neu registrieren
        tickets_data = await tickets_db.get()
        for panel in tickets_data.get("panels", []):
            try:
                view = TicketPanelView(panel["panel_id"], panel.get("options", []))
                self.bot.add_view(view)
            except Exception as e:
                logger = None
                print(f"[TICKETS] Panel {panel.get('panel_id')} konnte nicht registriert werden: {e}")

    ticket_group = app_commands.Group(name="ticket", description="Ticket-System")

    @ticket_group.command(name="create", description="Ticket-Panel erstellen")
    @app_commands.describe(
        channel="Channel für das Panel",
        name="Eindeutiger Name für dieses Panel (z.B. support, billing)",
        options="Kategorien komma-getrennt (z.B. Support,Bugs,Fragen)"
    )
    async def ticket_create(self, interaction: discord.Interaction, channel: discord.TextChannel, name: str, options: str):
        if not await require_authorized(interaction):
            return
        
        opts = parse_dropdown_options(options)
        if not opts:
            await interaction.response.send_message(embed=error_embed("Keine gültigen Optionen. Format: Support,Bugs,Fragen"), ephemeral=True)
            return
        
        if len(opts) > 25:
            await interaction.response.send_message(embed=error_embed("Max 25 Optionen"), ephemeral=True)
            return
        
        panel_id = sanitize_dropdown_name(name)
        
        tickets_data = await tickets_db.get()
        panels = tickets_data.get("panels", [])
        
        # Handle old panels without panel_id (migrate dropdown_name -> panel_id)
        for p in panels:
            if "panel_id" not in p and "dropdown_name" in p:
                p["panel_id"] = p["dropdown_name"]
        
        if any(p.get("panel_id") == panel_id for p in panels):
            await interaction.response.send_message(embed=error_embed(f"Panel '{name}' existiert bereits"), ephemeral=True)
            return
        
        embed = discord.Embed(
            title="Ticket-System",
            description="Wähle eine Kategorie um ein Ticket zu eröffnen.",
            color=discord.Color.blue()
        )
        embed.add_field(name="Kategorien", value="\n".join(f"• {o}" for o in opts), inline=False)
        embed.set_footer(text=f"Filpo01 DC Bot | Panel: {panel_id}")
        
        view = TicketPanelView(panel_id, opts)
        self.bot.add_view(view)
        
        try:
            msg = await channel.send(embed=embed, view=view)
        except discord.HTTPException as e:
            await interaction.response.send_message(embed=error_embed(f"Fehler: {e}"), ephemeral=True)
            return
        
        panels.append({
            "panel_id": panel_id,
            "channel_id": channel.id,
            "message_id": msg.id,
            "options": opts
        })
        tickets_data["panels"] = panels
        await tickets_db.save(tickets_data)
        
        await interaction.response.send_message(embed=success_embed(f"Panel '{name}' erstellt in {channel.mention} mit {len(opts)} Kategorien"), ephemeral=True)

    @ticket_group.command(name="close", description="Ticket schließen")
    async def ticket_close(self, interaction: discord.Interaction):
        await TicketView().handle_close(interaction)

    @ticket_group.command(name="claim", description="Ticket claimen (Support)")
    async def ticket_claim(self, interaction: discord.Interaction):
        await TicketView().handle_claim(interaction)

    @ticket_group.command(name="unclaim", description="Ticket freigeben")
    async def ticket_unclaim(self, interaction: discord.Interaction):
        await TicketView().handle_unclaim(interaction)

    @ticket_group.command(name="list", description="Alle offenen Tickets (Admin)")
    async def ticket_list(self, interaction: discord.Interaction):
        if not await require_authorized(interaction):
            return
        
        tickets_data = await tickets_db.get()
        active = tickets_data.get("active_tickets", [])
        
        if not active:
            await interaction.response.send_message(embed=info_embed("Keine offenen Tickets"), ephemeral=True)
            return
        
        embed = discord.Embed(title="Offene Tickets", color=discord.Color.blue())
        
        for t in active[:25]:
            channel = interaction.guild.get_channel(t["channel_id"])
            owner = interaction.guild.get_member(t["owner_id"])
            claimer = interaction.guild.get_member(t.get("claimed_by")) if t.get("claimed_by") else None
            
            ch = channel.mention if channel else "gelöscht"
            ow = owner.mention if owner else "unbekannt"
            cl = claimer.mention if claimer else "niemand"
            
            embed.add_field(
                name=f"#{t['channel_id']} | {t['category']}",
                value=f"Owner: {ow}\nClaimer: {cl}\nChannel: {ch}",
                inline=True
            )
        
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @ticket_group.command(name="panel_list", description="Alle Ticket-Panels anzeigen")
    async def ticket_panel_list(self, interaction: discord.Interaction):
        if not await require_authorized(interaction):
            return

        tickets_data = await tickets_db.get()
        panels = tickets_data.get("panels", [])

        if not panels:
            await interaction.response.send_message(embed=info_embed("Keine Panels eingerichtet"), ephemeral=True)
            return

        embed = discord.Embed(title="🎫 Ticket-Panels", color=discord.Color.blue())
        for p in panels:
            channel = interaction.guild.get_channel(p.get("channel_id"))
            ch = channel.mention if channel else "gelöscht"
            embed.add_field(
                name=f"`{p.get('panel_id')}`",
                value=f"Channel: {ch}\nKategorien: {', '.join(p.get('options', []))[:200]}",
                inline=False
            )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @ticket_group.command(name="panel_remove", description="Ticket-Panel entfernen")
    @app_commands.describe(name="Panel-Name (aus /ticket panel_list)")
    async def ticket_panel_remove(self, interaction: discord.Interaction, name: str):
        if not await require_authorized(interaction):
            return
        
        panel_id = sanitize_dropdown_name(name)
        tickets_data = await tickets_db.get()
        panels = tickets_data.get("panels", [])
        
        # Migrate old panels
        for p in panels:
            if "panel_id" not in p and "dropdown_name" in p:
                p["panel_id"] = p["dropdown_name"]
        
        panel = next((p for p in panels if p.get("panel_id") == panel_id), None)
        if not panel:
            await interaction.response.send_message(embed=error_embed(f"Panel '{name}' nicht gefunden"), ephemeral=True)
            return
        
        panels.remove(panel)
        tickets_data["panels"] = panels
        await tickets_db.save(tickets_data)
        
        try:
            channel = interaction.guild.get_channel(panel["channel_id"])
            if channel:
                msg = await channel.fetch_message(panel["message_id"])
                await msg.delete()
        except (discord.NotFound, discord.HTTPException):
            pass
        
        await interaction.response.send_message(embed=success_embed(f"Panel '{name}' entfernt"), ephemeral=True)


class HelpCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="help", description="Zeigt alle Commands")
    async def help_command(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="Filpo01 DC Bot - Commands",
            description="Admin/Owner-Befehle sind mit ⚙️ markiert.",
            color=discord.Color.blue()
        )

        embed.add_field(
            name="⚙️ Konfiguration",
            value=(
                "`/config config_dc_bot role:<ids>` - Admin-Rollen\n"
                "`/config welcome_role role:<role>` - Welcome-Rolle\n"
                "`/config ticket_show role:<role>` - Support-Rolle Tickets\n"
                "`/config log_channel channel:<#>` - Log-Channel\n"
                "`/config hello_message nachricht:<text> channel:<#>` - Join-Nachricht\n"
                "`/config voice_creator kategorie:<kat> channel:<#>` - Voice Creator\n"
                "`/config show` - Config anzeigen"
            ),
            inline=False
        )

        embed.add_field(
            name="🎫 Tickets",
            value=(
                "`/ticket create channel:<#> name:<name> options:<kat1,kat2>` - Panel erstellen\n"
                "`/ticket panel_list` - Panels anzeigen\n"
                "`/ticket panel_remove name:<name>` - Panel loeschen\n"
                "`/ticket close` / `claim` / `unclaim` - Ticket verwalten\n"
                "`/ticket list` - Offene Tickets (Admin)"
            ),
            inline=False
        )

        embed.add_field(
            name="🛡️ AutoMod",
            value=(
                "`/automod word verboten:<woerter> aktion:<...> dauer:<min>` - Wort-Regel\n"
                "`/automod links` / `invites` / `caps` / `spam` / `mentions` - Filter\n"
                "`/automod allow` / `disallow domain:<domain>` - Domain-Whitelist\n"
                "`/automod exempt role:<role>` - Rolle ausnehmen\n"
                "`/automod list` / `remove index:<nr>` - Regeln verwalten\n"
                "`/automod toggle enabled:<bool>` - AutoMod an/aus"
            ),
            inline=False
        )

        embed.add_field(
            name="⚠️ Moderation",
            value=(
                "`/ban user:<@> reason:<grund>` - Bannen\n"
                "`/kick user:<@> reason:<grund>` - Kicken\n"
                "`/mute user:<@> dauer:<min> reason:<grund>` - Stummschalten\n"
                "`/unmute user:<@>` - Timeout aufheben\n"
                "`/unban user_id:<id>` - Entbannen\n"
                "`/warn user:<@> reason:<grund>` - Verwarnen (3→Timeout, 5→Kick)\n"
                "`/warns user:<@>` / `remove` / `clear` - Warns verwalten\n"
                "`/purge anzahl:<1-100> user:<@>` - Nachrichten loeschen"
            ),
            inline=False
        )

        embed.add_field(
            name="🔔 Benachrichtigungen",
            value=(
                "`/notify add art:<twitch|yt|tt> username:<name> channel:<#>`\n"
                "`/notify remove id:<id>` / `list` / `test id:<id>`\n"
                "`/notifypanel create channel:<#> buttons:<...>` - Panel senden"
            ),
            inline=False
        )

        embed.add_field(
            name="🎮 Spiele & Spass",
            value=(
                "`/tictactoe play gegner:<@>` - Tic Tac Toe (leer = gegen Bot)\n"
                "`/tictactoe stats` / `reset` - Stats anzeigen/zuruecksetzen\n"
                "`/giveaway start channel:<#> titel:<...> gewinn:<...> aufloesung:<6.7 18:24>`\n"
                "`/giveaway end id:<id>` / `reroll id:<id>` - Giveaway verwalten"
            ),
            inline=False
        )

        embed.add_field(
            name="🔧 Utility",
            value=(
                "`/ping` - Latenz & Debug-Info\n"
                "`/serverinfo` - Server-Informationen\n"
                "`/userinfo user:<@>` - User-Informationen\n"
                "`/avatar user:<@>` - Avatar anzeigen\n"
                "`/sendmessage nachricht:<text>` - Bot sendet Nachricht (Owner/Dev)\n"
                "`/announce text:<...> channel:<#> ping:<@>` - Ankündigung (Owner/Dev)"
            ),
            inline=False
        )

        embed.add_field(
            name="🔘 Reaction Roles & Scheduler",
            value=(
                "`/reactionroles add message_id:<id> emoji:<emoji> role:<role>`\n"
                "`/reactionroles watch channel:<#> emoji:<emoji> role:<role>`\n"
                "`/reactionroles remove` / `list`\n"
                "`/schedule add channel:<#> nachricht:<text> zeit:<18:30> tage:<mo,fr>`\n"
                "`/schedule list` / `remove id:<id>`"
            ),
            inline=False
        )

        embed.add_field(
            name="📊 Stats",
            value=(
                "`/stats members channel:<#>` - Mitglieder-Zaehler\n"
                "`/stats online channel:<#>` - Online-Zaehler\n"
                "`/stats remove kind:<...>` - Zaehler entfernen"
            ),
            inline=False
        )

        embed.add_field(
            name="🛠️ Bot-Steuerung",
            value=(
                "`/restart` - Bot neu starten (Owner/Dev)\n"
                "`/stop` - Bot stoppen (Owner/Dev)\n"
                "`/embed channel:<#> titel:<...> beschreibung:<...> farbe:<...>` - Embed erstellen"
            ),
            inline=False
        )

        embed.set_footer(text="Filpo01 DC Bot | Nur Slash-Commands")
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(TicketsCog(bot))
    await bot.add_cog(HelpCog(bot))
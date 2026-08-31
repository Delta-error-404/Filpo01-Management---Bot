import discord
from discord.ext import commands
from discord import app_commands
import asyncio
import random
from datetime import datetime
from zoneinfo import ZoneInfo
from config import giveaway_db
from utils import require_authorized, success_embed, error_embed
from cogs.notifications import _get_panel_role

EMOJI = "🎉"
BERLIN_TZ = ZoneInfo("Europe/Berlin")

def generate_giveaway_id() -> str:
    return str(random.randint(100000, 999999))

def parse_german_datetime(text: str) -> datetime | None:
    tz = BERLIN_TZ
    text = text.strip().replace(",", " ")

    time_part = None
    date_part = None
    for p in text.split():
        if ":" in p:
            time_part = p
        elif "." in p:
            date_part = p

    if not time_part:
        return None

    hm = time_part.split(":")
    if len(hm) != 2:
        return None
    try:
        hour, minute = int(hm[0]), int(hm[1])
    except ValueError:
        return None
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None

    if date_part:
        parts = [x for x in date_part.split(".") if x != ""]
        if len(parts) not in (2, 3):
            return None
        try:
            nums = [int(x) for x in parts]
        except ValueError:
            return None
        day, month = nums[0], nums[1]
        year = nums[2] if len(nums) == 3 else datetime.now(BERLIN_TZ).year
        if len(nums) == 3 and year < 100:
            year += 2000
    else:
        now = datetime.now(BERLIN_TZ)
        day, month, year = now.day, now.month, now.year

    try:
        return datetime(year, month, day, hour, minute, tzinfo=tz)
    except ValueError:
        return None

def build_giveaway_embed(gw: dict) -> discord.Embed:
    end = datetime.fromisoformat(gw["end_time"])
    embed = discord.Embed(
        title=f"{EMOJI} Gewinnspiel: {gw['title']}",
        description=gw.get("text") or f"Klicke auf **{EMOJI} Beitreten**, um teilzunehmen!",
        color=discord.Color.pink()
    )
    embed.add_field(name="🎁 Gewinn", value=gw["prize"], inline=True)
    embed.add_field(name="⏰ Auflösung", value=f"<t:{int(end.timestamp())}:F>", inline=True)
    embed.add_field(name="👥 Teilnehmer", value=str(len(gw["participants"])), inline=True)
    host = gw.get("host_id")
    if host:
        embed.add_field(name="👑 Ausrichter", value=f"<@{host}>", inline=True)
    embed.set_footer(text=f"Giveaway-ID: {gw['id']}")
    return embed

class GiveawayView(discord.ui.View):
    def __init__(self, giveaway_id: str, cog, disabled: bool = False):
        super().__init__(timeout=None)
        self.giveaway_id = str(giveaway_id)
        self.cog = cog

        self.join_btn = discord.ui.Button(
            label="Beitreten",
            style=discord.ButtonStyle.success,
            custom_id=f"giveaway_join_{self.giveaway_id}",
            disabled=disabled
        )
        self.join_btn.callback = self.join_callback
        self.add_item(self.join_btn)

        self.leave_btn = discord.ui.Button(
            label="Giveaway verlassen",
            style=discord.ButtonStyle.danger,
            custom_id=f"giveaway_leave_{self.giveaway_id}",
            disabled=disabled
        )
        self.leave_btn.callback = self.leave_callback
        self.add_item(self.leave_btn)

    async def _update_message(self, message: discord.Message, gw: dict):
        embed = build_giveaway_embed(gw)
        view = GiveawayView(gw["id"], self.cog, disabled=bool(gw.get("ended")))
        try:
            await message.edit(embed=embed, view=view)
        except discord.HTTPException:
            pass

    async def _dm(self, user: discord.User, text: str):
        try:
            await user.send(text)
        except (discord.Forbidden, discord.HTTPException):
            pass

    async def join_callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        uid = interaction.user.id
        state: dict = {}

        def _join(data: dict):
            giveaways = data.get("giveaways", [])
            gw = next((g for g in giveaways if str(g["id"]) == self.giveaway_id), None)
            if not gw or gw.get("ended"):
                state["status"] = "ended"
                return None
            if uid in gw["participants"]:
                state["status"] = "already"
                return None
            gw["participants"].append(uid)
            state["status"] = "joined"
            state["gw"] = gw
            return data

        await giveaway_db.modify(_join)

        if state.get("status") == "ended":
            await interaction.followup.send("Dieses Gewinnspiel ist beendet.", ephemeral=True)
            return
        if state.get("status") == "already":
            await interaction.followup.send(f"Du bist dem Gewinnspiel **{self.giveaway_id}** bereits beigetreten.", ephemeral=True)
            return
        if state.get("status") != "joined":
            await interaction.followup.send("Gewinnspiel nicht gefunden.", ephemeral=True)
            return

        gw = state["gw"]
        await self._update_message(interaction.message, gw)
        await interaction.followup.send(f"Du bist dem Gewinnspiel **{gw['id']}** beigetreten! 🎉", ephemeral=True)
        await self._dm(interaction.user, f"Du bist dem Gewinnspiel **{gw['id']}** beigetreten! Viel Glück! 🎉")

    async def leave_callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        uid = interaction.user.id
        state: dict = {}

        def _leave(data: dict):
            giveaways = data.get("giveaways", [])
            gw = next((g for g in giveaways if str(g["id"]) == self.giveaway_id), None)
            if not gw or gw.get("ended"):
                state["status"] = "ended"
                return None
            if uid not in gw["participants"]:
                state["status"] = "not_joined"
                return None
            gw["participants"].remove(uid)
            state["status"] = "left"
            state["gw"] = gw
            return data

        await giveaway_db.modify(_leave)

        if state.get("status") == "ended":
            await interaction.followup.send("Dieses Gewinnspiel ist beendet.", ephemeral=True)
            return
        if state.get("status") == "not_joined":
            await interaction.followup.send(f"Du bist dem Gewinnspiel **{self.giveaway_id}** nicht beigetreten.", ephemeral=True)
            return
        if state.get("status") != "left":
            await interaction.followup.send("Gewinnspiel nicht gefunden.", ephemeral=True)
            return

        gw = state["gw"]
        await self._update_message(interaction.message, gw)
        await interaction.followup.send(f"Du hast das Gewinnspiel **{gw['id']}** verlassen.", ephemeral=True)

class GiveawayCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._task: asyncio.Task | None = None

    async def cog_load(self):
        data = await giveaway_db.get()
        for gw in data.get("giveaways", []):
            if not gw.get("ended"):
                self.bot.add_view(GiveawayView(gw["id"], self))
        self._task = asyncio.create_task(self.giveaway_loop())

    async def cog_unload(self):
        if self._task:
            self._task.cancel()

    giveaway_group = app_commands.Group(name="giveaway", description="Gewinnspiele verwalten")

    @giveaway_group.command(name="start", description="Ein Gewinnspiel starten")
    @app_commands.describe(
        channel="Channel für das Gewinnspiel",
        titel="Überschrift des Gewinnspiels",
        gewinn="Was man gewinnen kann",
        aufloesung="Auflösung, z.B. 6.7 18:24 oder 6.7.2026 18:24",
        text="Beschreibungstext (optional)"
    )
    async def giveaway_start(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
        titel: str,
        gewinn: str,
        aufloesung: str,
        text: str = ""
    ):
        if not await require_authorized(interaction):
            return

        titel = titel.strip()
        gewinn = gewinn.strip()
        if not titel or not gewinn:
            await interaction.response.send_message(
                embed=error_embed("Titel und Gewinn dürfen nicht leer sein"),
                ephemeral=True
            )
            return
        if len(titel) > 256:
            await interaction.response.send_message(
                embed=error_embed("Titel zu lang (max 256 Zeichen)"),
                ephemeral=True
            )
            return

        end = parse_german_datetime(aufloesung)
        if not end:
            await interaction.response.send_message(
                embed=error_embed("Ungültiges Datum. Format: `6.7 18:24` oder `6.7.2026 18:24`"),
                ephemeral=True
            )
            return
        if end <= datetime.now(BERLIN_TZ):
            await interaction.response.send_message(
                embed=error_embed("Die Auflösungszeit muss in der Zukunft liegen"),
                ephemeral=True
            )
            return

        gid = generate_giveaway_id()

        gw = {
            "id": gid,
            "channel_id": channel.id,
            "title": titel,
            "prize": gewinn,
            "text": text.strip(),
            "end_time": end.isoformat(),
            "participants": [],
            "host_id": interaction.user.id,
            "ended": False
        }

        def _add(data: dict):
            data.setdefault("giveaways", []).append(gw)
            return data

        await giveaway_db.modify(_add)

        view = GiveawayView(gid, self)
        embed = build_giveaway_embed(gw)

        role = await _get_panel_role(channel.guild, "giveaway")
        content = role.mention if role else None

        try:
            msg = await channel.send(content=content, embed=embed, view=view)
        except discord.HTTPException:
            def _remove(data: dict):
                giveaways = data.get("giveaways", [])
                if gw in giveaways:
                    giveaways.remove(gw)
                    return data
                return None

            await giveaway_db.modify(_remove)
            await interaction.response.send_message(
                embed=error_embed("Konnte die Nachricht nicht senden (fehlende Berechtigungen?)"),
                ephemeral=True
            )
            return

        def _set_message_id(data: dict):
            for g in data.get("giveaways", []):
                if g["id"] == gid:
                    g["message_id"] = msg.id
                    return data
            return None

        await giveaway_db.modify(_set_message_id)

        await interaction.response.send_message(
            embed=success_embed(
                f"🎉 Gewinnspiel gestartet in {channel.mention}\n"
                f"**ID:** `{gid}`\n"
                f"**Gewinn:** {gewinn}\n"
                f"**Auflösung:** <t:{int(end.timestamp())}:F>"
            ),
            ephemeral=True
        )

    @giveaway_group.command(name="end", description="Gewinnspiel vorzeitig beenden")
    @app_commands.describe(id="Giveaway-ID (aus /giveaway start oder der Nachricht)")
    async def giveaway_end(self, interaction: discord.Interaction, id: str):
        if not await require_authorized(interaction):
            return

        id = id.strip()

        def _end(data: dict):
            for g in data.get("giveaways", []):
                if g["id"] == id and not g.get("ended"):
                    g["ended"] = True
                    return data
            return None

        result = await giveaway_db.modify(_end)
        if not result:
            await interaction.response.send_message(
                embed=error_embed(f"Kein laufendes Gewinnspiel mit ID `{id}` gefunden"),
                ephemeral=True
            )
            return

        finished = next((g for g in result.get("giveaways", []) if g["id"] == id), None)
        if finished:
            await self.finish_giveaway(finished)

        await interaction.response.send_message(
            embed=success_embed(f"🎉 Gewinnspiel `{id}` wurde vorzeitig beendet."),
            ephemeral=True
        )

    @giveaway_group.command(name="reroll", description="Gewinnspiel neu ziehen")
    @app_commands.describe(id="Giveaway-ID")
    async def giveaway_reroll(self, interaction: discord.Interaction, id: str):
        if not await require_authorized(interaction):
            return

        id = id.strip()
        data = await giveaway_db.get()
        gw = next((g for g in data.get("giveaways", []) if g["id"] == id), None)

        if not gw:
            await interaction.response.send_message(embed=error_embed(f"Gewinnspiel `{id}` nicht gefunden."), ephemeral=True)
            return

        participants = gw.get("participants", [])
        if not participants:
            await interaction.response.send_message(embed=error_embed("Keine Teilnehmer vorhanden."), ephemeral=True)
            return

        old_winner = gw.get("winner_id")
        new_participants = [p for p in participants if p != old_winner] if old_winner else participants
        if not new_participants:
            await interaction.response.send_message(embed=error_embed("Keine weiteren Teilnehmer zum Neu-Ziehen."), ephemeral=True)
            return

        new_winner_id = random.choice(new_participants)
        winner = self.bot.get_user(new_winner_id)
        if not winner:
            try:
                winner = await self.bot.fetch_user(new_winner_id)
            except (discord.NotFound, discord.HTTPException):
                winner = None

        if winner:
            channel = self.bot.get_channel(gw.get("channel_id"))
            if channel:
                await channel.send(f"🔄 **Neu gezogen!** {winner.mention} hat das Gewinnspiel **{id}** gewonnen! 🎉")

            def _update(data):
                for g in data.get("giveaways", []):
                    if g["id"] == id:
                        g["winner_id"] = new_winner_id
                        return data
                return None
            await giveaway_db.modify(_update)

        await interaction.response.send_message(
            embed=success_embed(f"🔄 Neuer Gewinner: {winner.mention if winner else 'Unbekannt'}"),
            ephemeral=True
        )

    async def giveaway_loop(self):
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            try:
                await self.check_giveaways()
            except Exception as e:
                print(f"Fehler im Giveaway-Loop: {e}")
            await asyncio.sleep(30)

    async def check_giveaways(self):
        data = await giveaway_db.get()
        giveaways = data.get("giveaways", [])
        now = datetime.now(BERLIN_TZ)

        due_ids = []
        for gw in giveaways:
            if gw.get("ended"):
                continue
            try:
                end = datetime.fromisoformat(gw["end_time"])
            except (ValueError, TypeError):
                continue
            if now >= end:
                due_ids.append(gw["id"])

        for gid in due_ids:
            def _mark_ended(data: dict):
                for g in data.get("giveaways", []):
                    if g["id"] == gid and not g.get("ended"):
                        g["ended"] = True
                        return data
                return None

            result = await giveaway_db.modify(_mark_ended)
            if not result:
                continue
            finished = next((g for g in result.get("giveaways", []) if g["id"] == gid), None)
            if finished:
                await self.finish_giveaway(finished)

    async def finish_giveaway(self, gw: dict):
        channel = self.bot.get_channel(gw["channel_id"])
        participants = gw.get("participants", [])

        if channel:
            if participants:
                winner_id = random.choice(participants)
                winner = self.bot.get_user(winner_id)
                if not winner:
                    try:
                        winner = await self.bot.fetch_user(winner_id)
                    except (discord.NotFound, discord.HTTPException):
                        winner = None

                if winner:
                    await channel.send(
                        f"{winner.mention} du hast das Gewinnspiel **{gw['id']}** gewonnen! Bitte eröffne ein Ticket! 🎉"
                    )
                else:
                    await channel.send(f"Das Gewinnspiel **{gw['id']}** ist beendet, aber der Gewinner konnte nicht gefunden werden.")
            else:
                await channel.send(f"Das Gewinnspiel **{gw['id']}** ist beendet, aber es gab keine Teilnehmer. 😔")

            if not gw.get("message_id"):
                return

            try:
                message = await channel.fetch_message(gw["message_id"])
            except (discord.NotFound, discord.HTTPException):
                return

            try:
                gw["winner_id"] = winner_id if participants else None
                embed = build_giveaway_embed(gw)
                if gw.get("winner_id"):
                    embed.add_field(name="🏆 Gewinner", value=f"<@{gw['winner_id']}>", inline=False)
                else:
                    embed.add_field(name="🏆 Gewinner", value="Keine Teilnehmer", inline=False)
                await message.edit(
                    content=f"**Gewinnspiel beendet!** {EMOJI}",
                    embed=embed,
                    view=GiveawayView(gw["id"], self, disabled=True)
                )
            except (discord.NotFound, discord.HTTPException):
                pass

async def setup(bot: commands.Bot):
    await bot.add_cog(GiveawayCog(bot))
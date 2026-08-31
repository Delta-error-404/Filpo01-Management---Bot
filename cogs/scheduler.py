import asyncio
import random
from datetime import datetime
from zoneinfo import ZoneInfo

import discord
from discord.ext import commands
from discord import app_commands

from config import scheduler_db
from utils import require_authorized, success_embed, error_embed, info_embed

BERLIN_TZ = ZoneInfo("Europe/Berlin")
WEEKDAYS = {"mo": 0, "di": 1, "mi": 2, "do": 3, "fr": 4, "sa": 5, "so": 6}
WEEKDAY_LABELS = {0: "Mo", 1: "Di", 2: "Mi", 3: "Do", 4: "Fr", 5: "Sa", 6: "So"}


def generate_id() -> str:
    return str(random.randint(100000, 999999))


def parse_days(text: str) -> set[int] | None:
    text = text.strip().lower()
    if text in ("täglich", "taeglich", "daily", "jeden tag", ""):
        return None
    parts = [p.strip() for p in text.split(",") if p.strip()]
    days = set()
    for p in parts:
        if p not in WEEKDAYS:
            return None
        days.add(WEEKDAYS[p])
    return days if days else None


def parse_time(text: str) -> tuple[str, str] | None:
    text = text.strip()
    if ":" not in text:
        return None
    h, m = text.split(":", 1)
    if not h.isdigit() or not m.isdigit():
        return None
    h, m = int(h), int(m)
    if not (0 <= h <= 23 and 0 <= m <= 59):
        return None
    return f"{h:02d}:{m:02d}", text


class SchedulerCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._task: asyncio.Task | None = None

    async def cog_load(self):
        self._task = asyncio.create_task(self.scheduler_loop())

    async def cog_unload(self):
        if self._task:
            self._task.cancel()

    scheduler_group = app_commands.Group(name="schedule", description="Wiederkehrende Nachrichten verwalten")

    @scheduler_group.command(name="add", description="Eine wiederkehrende Nachricht planen")
    @app_commands.describe(
        channel="Channel für die Nachricht",
        nachricht="Nachrichtentext",
        zeit="Uhrzeit, z.B. 18:30",
        tage="Wochentage, z.B. mo,mi,fr (Standard: täglich)"
    )
    async def schedule_add(self, interaction: discord.Interaction, channel: discord.TextChannel,
                           nachricht: str, zeit: str, tage: str = "täglich"):
        if not await require_authorized(interaction):
            return

        nachricht = nachricht.strip()
        if not nachricht:
            await interaction.response.send_message(embed=error_embed("Die Nachricht darf nicht leer sein"), ephemeral=True)
            return
        if len(nachricht) > 2000:
            await interaction.response.send_message(embed=error_embed("Nachricht zu lang (max 2000 Zeichen)"), ephemeral=True)
            return

        parsed_time = parse_time(zeit)
        if not parsed_time:
            await interaction.response.send_message(embed=error_embed("Ungültige Uhrzeit. Format: `18:30`"), ephemeral=True)
            return
        hhmm = parsed_time[0]

        days = parse_days(tage)
        if days is None and tage.strip().lower() not in ("täglich", "taeglich", "daily", "jeden tag", ""):
            await interaction.response.send_message(
                embed=error_embed("Ungültige Wochentage. Erlaubt: `mo,di,mi,do,fr,sa,so` (komma-getrennt) oder `täglich`"),
                ephemeral=True
            )
            return

        mid = generate_id()

        def _add(data: dict):
            data.setdefault("messages", []).append({
                "id": mid,
                "channel_id": channel.id,
                "text": nachricht,
                "time": hhmm,
                "days": None if days is None else ",".join(sorted(WEEKDAY_LABELS[d] for d in days)),
                "last_sent": ""
            })
            return data

        await scheduler_db.modify(_add)

        day_label = "täglich" if days is None else ", ".join(WEEKDAY_LABELS[d] for d in sorted(days))
        await interaction.response.send_message(
            embed=success_embed(
                f"📅 Geplante Nachricht erstellt in {channel.mention}\n"
                f"**ID:** `{mid}`\n"
                f"**Zeit:** {hhmm} Uhr\n"
                f"**Tage:** {day_label}\n"
                f"**Nachricht:** {nachricht[:100]}"
            ),
            ephemeral=True
        )

    @scheduler_group.command(name="list", description="Alle geplanten Nachrichten anzeigen")
    async def schedule_list(self, interaction: discord.Interaction):
        if not await require_authorized(interaction):
            return

        data = await scheduler_db.get()
        messages = data.get("messages", [])

        if not messages:
            await interaction.response.send_message(embed=info_embed("Keine geplanten Nachrichten"), ephemeral=True)
            return

        embed = discord.Embed(title="📅 Geplante Nachrichten", color=discord.Color.blue())
        for m in messages:
            channel = interaction.guild.get_channel(m.get("channel_id"))
            channel_label = channel.mention if channel else f"`{m.get('channel_id')}`"
            day_label = m.get("days") or "täglich"
            embed.add_field(
                name=f"`{m['id']}` • {m.get('time', '?')} Uhr ({day_label})",
                value=f"{channel_label}\n{m.get('text', '')[:200]}",
                inline=False
            )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @scheduler_group.command(name="remove", description="Eine geplante Nachricht entfernen")
    @app_commands.describe(id="ID der Nachricht (aus /schedule list)")
    async def schedule_remove(self, interaction: discord.Interaction, id: str):
        if not await require_authorized(interaction):
            return

        removed = False

        def _remove(data: dict):
            nonlocal removed
            messages = data.get("messages", [])
            new_list = [m for m in messages if m.get("id") != id.strip()]
            if len(new_list) != len(messages):
                data["messages"] = new_list
                removed = True
                return data
            return None

        await scheduler_db.modify(_remove)
        if removed:
            await interaction.response.send_message(embed=success_embed(f"Geplante Nachricht `{id}` entfernt."), ephemeral=True)
        else:
            await interaction.response.send_message(embed=error_embed(f"Keine Nachricht mit ID `{id}` gefunden"), ephemeral=True)

    async def scheduler_loop(self):
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            try:
                await self.check_due()
            except Exception as e:
                print(f"Fehler im Scheduler-Loop: {e}")
            await asyncio.sleep(30)

    async def check_due(self):
        now = datetime.now(BERLIN_TZ)
        today = now.strftime("%Y-%m-%d")
        now_minutes = now.hour * 60 + now.minute

        data = await scheduler_db.get()
        for msg in data.get("messages", []):
            days_text = msg.get("days")
            if days_text:
                days = parse_days(days_text)
                if days is not None and now.weekday() not in days:
                    continue

            time_str = msg.get("time")
            if not time_str or ":" not in time_str:
                continue
            try:
                h, m = time_str.split(":", 1)
                target_minutes = int(h) * 60 + int(m)
            except ValueError:
                continue
            if now_minutes < target_minutes:
                continue
            if msg.get("last_sent") == today:
                continue

            def _mark(d: dict):
                for m in d.get("messages", []):
                    if m.get("id") == msg["id"] and m.get("last_sent") != today:
                        m["last_sent"] = today
                        return d
                return None

            result = await scheduler_db.modify(_mark)
            if not result:
                continue

            channel = self.bot.get_channel(msg.get("channel_id"))
            if channel:
                try:
                    await channel.send(msg.get("text", ""))
                except discord.HTTPException:
                    pass


async def setup(bot: commands.Bot):
    await bot.add_cog(SchedulerCog(bot))

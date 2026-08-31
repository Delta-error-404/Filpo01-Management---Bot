import asyncio

import discord
from discord.ext import commands
from discord import app_commands

from config import stats_db
from utils import require_authorized, success_embed, error_embed


class StatCounterCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._task: asyncio.Task | None = None

    async def cog_load(self):
        self._task = asyncio.create_task(self.counter_loop())

    async def cog_unload(self):
        if self._task:
            self._task.cancel()

    @staticmethod
    def _counter_name(kind: str, count: int) -> str:
        if kind == "members":
            return f"👥 Mitglieder: {count}"
        return f"🟢 Online: {count}"

    async def _set_counter(self, interaction: discord.Interaction, kind: str, channel: discord.VoiceChannel):
        if not await require_authorized(interaction):
            return

        def _set(data: dict):
            counters = data.setdefault("counters", [])
            counters = [c for c in counters if not (c.get("guild_id") == interaction.guild.id and c.get("kind") == kind)]
            counters.append({"guild_id": interaction.guild.id, "kind": kind, "channel_id": channel.id})
            data["counters"] = counters
            return data

        await stats_db.modify(_set)
        await self._update_counter(interaction.guild, kind, channel)
        await interaction.response.send_message(
            embed=success_embed(f"Zähler **{'Mitglieder' if kind == 'members' else 'Online'}** gesetzt auf {channel.mention}"),
            ephemeral=True
        )

    async def _update_counter(self, guild: discord.Guild, kind: str, channel: discord.VoiceChannel):
        if kind == "members":
            count = guild.member_count
        else:
            count = sum(1 for m in guild.members if m.status not in (discord.Status.offline, discord.Status.invisible))
        name = self._counter_name(kind, count)
        if channel.name != name:
            try:
                await channel.edit(name=name)
            except discord.HTTPException:
                pass

    async def update_counters(self, guild: discord.Guild):
        data = await stats_db.get()
        for counter in data.get("counters", []):
            if counter.get("guild_id") != guild.id:
                continue
            channel = guild.get_channel(counter.get("channel_id"))
            if not channel:
                def _remove(data: dict):
                    counters = data.get("counters", [])
                    new_list = [c for c in counters if c.get("channel_id") != counter.get("channel_id")]
                    if len(new_list) != len(counters):
                        data["counters"] = new_list
                        return data
                    return None
                await stats_db.modify(_remove)
                continue
            await self._update_counter(guild, counter.get("kind", "members"), channel)

    stats_group = app_commands.Group(name="stats", description="Mitglieder-/Online-Zähler verwalten")

    @stats_group.command(name="members", description="Kanalname = Anzahl der Mitglieder")
    @app_commands.describe(channel="Voice-Channel für den Zähler")
    async def members(self, interaction: discord.Interaction, channel: discord.VoiceChannel):
        await self._set_counter(interaction, "members", channel)

    @stats_group.command(name="online", description="Kanalname = Anzahl der Online-Mitglieder")
    @app_commands.describe(channel="Voice-Channel für den Zähler")
    async def online(self, interaction: discord.Interaction, channel: discord.VoiceChannel):
        await self._set_counter(interaction, "online", channel)

    @stats_group.command(name="remove", description="Zähler entfernen")
    @app_commands.describe(kind="Welcher Zähler")
    @app_commands.choices(kind=[
        app_commands.Choice(name="Mitglieder", value="members"),
        app_commands.Choice(name="Online", value="online")
    ])
    async def remove(self, interaction: discord.Interaction, kind: str):
        if not await require_authorized(interaction):
            return

        removed = False

        def _remove(data: dict):
            nonlocal removed
            counters = data.get("counters", [])
            new_list = [c for c in counters if not (c.get("guild_id") == interaction.guild.id and c.get("kind") == kind)]
            if len(new_list) != len(counters):
                data["counters"] = new_list
                removed = True
                return data
            return None

        await stats_db.modify(_remove)
        label = "Mitglieder" if kind == "members" else "Online"
        if removed:
            await interaction.response.send_message(embed=success_embed(f"Zähler **{label}** entfernt."), ephemeral=True)
        else:
            await interaction.response.send_message(embed=error_embed(f"Kein **{label}**-Zähler eingerichtet"), ephemeral=True)

    async def counter_loop(self):
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            try:
                for guild in self.bot.guilds:
                    await self.update_counters(guild)
            except Exception as e:
                print(f"Fehler im Zähler-Loop: {e}")
            await asyncio.sleep(300)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        await self.update_counters(member.guild)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        await self.update_counters(member.guild)

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel):
        def _remove(data: dict):
            counters = data.get("counters", [])
            new_list = [c for c in counters if c.get("channel_id") != channel.id]
            if len(new_list) != len(counters):
                data["counters"] = new_list
                return data
            return None
        await stats_db.modify(_remove)


async def setup(bot: commands.Bot):
    await bot.add_cog(StatCounterCog(bot))

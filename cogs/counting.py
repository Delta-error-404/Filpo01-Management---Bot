import discord
from discord.ext import commands
from discord import app_commands

from config import counting_db
from utils import require_authorized, success_embed, error_embed, info_embed, create_embed


class CountingCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    counting_group = app_commands.Group(name="counting", description="Counting Game verwalten")

    def _get_guild_data(self, data: dict, guild_id: int) -> dict:
        return data.get("channels", {}).get(str(guild_id), {})

    # ─────────────────────────── Commands ───────────────────────────

    @counting_group.command(name="setup", description="Counting Game in einem Channel einrichten")
    @app_commands.describe(channel="Channel für das Counting Game")
    async def setup(self, interaction: discord.Interaction, channel: discord.TextChannel):
        if not await require_authorized(interaction):
            return
        if not interaction.guild:
            await interaction.response.send_message(embed=error_embed("Nur auf einem Server möglich."), ephemeral=True)
            return

        guild_id = interaction.guild.id

        def _set(d):
            d.setdefault("channels", {})[str(guild_id)] = {
                "channel_id": channel.id,
                "current_count": 0,
                "highest_count": 0,
                "highest_user_id": None,
                "highest_user_name": None,
                "last_user_id": None,
                "total_correct": 0,
                "total_wrong": 0,
                "user_stats": {}
            }
            return d

        await counting_db.modify(_set)
        await interaction.response.send_message(
            embed=success_embed(f"✅ Counting Game aktiviert in {channel.mention}\nFang mit **1** an!"),
        )

    @counting_group.command(name="remove", description="Counting Game deaktivieren")
    async def remove(self, interaction: discord.Interaction):
        if not await require_authorized(interaction):
            return
        if not interaction.guild:
            await interaction.response.send_message(embed=error_embed("Nur auf einem Server möglich."), ephemeral=True)
            return

        guild_id = interaction.guild.id
        data = await counting_db.get()
        if str(guild_id) not in data.get("channels", {}):
            await interaction.response.send_message(embed=error_embed("Kein Counting Game aktiv."), ephemeral=True)
            return

        def _del(d):
            d.get("channels", {}).pop(str(guild_id), None)
            return d

        await counting_db.modify(_del)
        await interaction.response.send_message(embed=success_embed("✅ Counting Game deaktiviert."))

    @counting_group.command(name="reset", description="Aktuellen Stand auf 0 zurücksetzen")
    async def reset(self, interaction: discord.Interaction):
        if not await require_authorized(interaction):
            return
        if not interaction.guild:
            await interaction.response.send_message(embed=error_embed("Nur auf einem Server möglich."), ephemeral=True)
            return

        guild_id = interaction.guild.id

        def _reset(d):
            ch = d.get("channels", {}).get(str(guild_id))
            if ch:
                ch["current_count"] = 0
                ch["last_user_id"] = None
            return d

        await counting_db.modify(_reset)
        await interaction.response.send_message(embed=success_embed("✅ Counting Stand auf **0** zurückgesetzt."))

    @counting_group.command(name="status", description="Aktuellen Stand und Statistiken anzeigen")
    async def status(self, interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message(embed=error_embed("Nur auf einem Server möglich."), ephemeral=True)
            return

        data = await counting_db.get()
        ch = self._get_guild_data(data, interaction.guild.id)
        if not ch:
            await interaction.response.send_message(embed=error_embed("Kein Counting Game aktiv."), ephemeral=True)
            return

        embed = create_embed(
            title="🔢 Counting Game",
            color=discord.Color.green()
        )
        embed.add_field(name="Aktueller Stand", value=f"**{ch['current_count']}**", inline=True)
        embed.add_field(name="Höchster Stand", value=f"**{ch.get('highest_count', 0)}**", inline=True)

        highest_name = ch.get("highest_user_name") or "—"
        embed.add_field(name="Rekordhalter", value=highest_name, inline=True)
        embed.add_field(name="Richtige Zahlen", value=str(ch.get("total_correct", 0)), inline=True)
        embed.add_field(name="Falsche Zahlen", value=str(ch.get("total_wrong", 0)), inline=True)

        if ch.get("channel_id"):
            embed.add_field(name="Channel", value=f"<#{ch['channel_id']}>", inline=True)

        await interaction.response.send_message(embed=embed)

    @counting_group.command(name="leaderboard", description="Top-Zähler anzeigen")
    async def leaderboard(self, interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message(embed=error_embed("Nur auf einem Server möglich."), ephemeral=True)
            return

        data = await counting_db.get()
        ch = self._get_guild_data(data, interaction.guild.id)
        if not ch:
            await interaction.response.send_message(embed=error_embed("Kein Counting Game aktiv."), ephemeral=True)
            return

        user_stats = ch.get("user_stats", {})
        if not user_stats:
            await interaction.response.send_message(embed=info_embed("Noch keine Statistiken vorhanden."), ephemeral=True)
            return

        sorted_users = sorted(user_stats.items(), key=lambda x: x[1].get("correct", 0), reverse=True)[:10]

        lines = []
        medals = ["🥇", "🥈", "🥉"]
        for i, (user_id, stats) in enumerate(sorted_users):
            prefix = medals[i] if i < 3 else f"**{i+1}.**"
            lines.append(f"{prefix} <@{user_id}> — {stats.get('correct', 0)} richtige Zahlen (Beste: {stats.get('highest', 0)})")

        embed = create_embed(
            title="🏆 Counting Leaderboard",
            description="\n".join(lines),
            color=discord.Color.gold()
        )
        await interaction.response.send_message(embed=embed)

    # ─────────────────────────── Listener ───────────────────────────

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return
        if not message.guild:
            return

        guild_id = message.guild.id
        data = await counting_db.get()
        ch = self._get_guild_data(data, guild_id)

        if not ch:
            return
        if message.channel.id != ch.get("channel_id"):
            return

        content = message.content.strip()

        # Nur ganze Zahlen akzeptieren
        if not content.isdigit():
            try:
                await message.delete()
            except discord.HTTPException:
                pass
            return

        number = int(content)
        current = ch.get("current_count", 0)
        expected = current + 1

        # Gleicher User zweimal in Folge?
        if message.author.id == ch.get("last_user_id"):
            await message.delete()
            await message.channel.send(
                embed=error_embed(f"❌ {message.author.mention} - Du hast schon gezählt! Warte auf jemand anderen."),
                delete_after=5
            )
            return

        if number == expected:
            # Richtige Zahl!
            def _correct(d):
                c = d["channels"][str(guild_id)]
                c["current_count"] = number
                c["last_user_id"] = message.author.id
                c["total_correct"] = c.get("total_correct", 0) + 1

                # Rekord?
                if number > c.get("highest_count", 0):
                    c["highest_count"] = number
                    c["highest_user_id"] = message.author.id
                    c["highest_user_name"] = str(message.author)

                # User-Stats
                stats = c.setdefault("user_stats", {}).setdefault(str(message.author.id), {"correct": 0, "highest": 0})
                stats["correct"] = stats.get("correct", 0) + 1
                if number > stats.get("highest", 0):
                    stats["highest"] = number

                return d

            await counting_db.modify(_correct)

            # Rekord-Feedback
            if number == ch.get("highest_count", 0) + 1 and number > 0:
                await message.add_reaction("🏆")
            else:
                await message.add_reaction("✅")

        else:
            # Falsche Zahl — Reset
            def _wrong(d):
                c = d["channels"][str(guild_id)]
                c["current_count"] = 0
                c["last_user_id"] = None
                c["total_wrong"] = c.get("total_wrong", 0) + 1
                return d

            await counting_db.modify(_wrong)

            try:
                await message.delete()
            except discord.HTTPException:
                pass

            await message.channel.send(
                embed=error_embed(
                    f"❌ **Falsch!** {message.author.mention} hat **{number}** geschrieben, "
                    f"aber **{expected}** kam als nächstes.\n"
                    f"Der Stand war bei **{current}** — zurück auf **0**!"
                ),
                delete_after=8
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(CountingCog(bot))

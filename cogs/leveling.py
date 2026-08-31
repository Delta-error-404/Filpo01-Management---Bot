import random
from datetime import datetime, timezone

import discord
from discord.ext import commands
from discord import app_commands

from config import leveling_db
from utils import require_authorized, success_embed, error_embed, info_embed


def xp_for_level(level: int) -> int:
    return 100 * (level ** 2)


def level_from_xp(total_xp: int) -> int:
    level = 1
    while total_xp >= xp_for_level(level):
        total_xp -= xp_for_level(level)
        level += 1
    return level


def progress_in_level(total_xp: int) -> tuple[int, int, int]:
    level = level_from_xp(total_xp)
    threshold = xp_for_level(level)
    cur = sum(xp_for_level(l) for l in range(1, level))
    return level, total_xp - cur, threshold


def build_progress_bar(current: int, target: int, length: int = 10) -> str:
    pct = min(current / target, 1.0) if target else 1.0
    filled = int(pct * length)
    return "█" * filled + "░" * (length - filled)


class LevelingCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ─────────────────────────── XP-Vergabe ───────────────────────────

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return
        if not message.guild:
            return
        if not isinstance(message.author, discord.Member):
            return

        data = await leveling_db.get()
        if not data.get("enabled", True):
            return

        cooldown = data.get("cooldown_seconds", 60)
        xp_min = data.get("xp_min", 15)
        xp_max = data.get("xp_max", 25)

        gid = str(message.guild.id)
        uid = str(message.author.id)
        users = data.setdefault("users", {}).setdefault(gid, {})

        entry = users.setdefault(uid, {"xp": 0})
        if entry.get("last_xp"):
            try:
                last = datetime.fromisoformat(entry["last_xp"])
            except ValueError:
                last = None
            if last and (datetime.now(timezone.utc) - last).total_seconds() < cooldown:
                return

        old_level = level_from_xp(entry["xp"])
        entry["xp"] += random.randint(xp_min, xp_max)
        entry["last_xp"] = datetime.now(timezone.utc).isoformat()
        new_level = level_from_xp(entry["xp"])

        def _save(d):
            d["users"][gid][uid] = entry
            return d
        await leveling_db.modify(_save)

        if new_level > old_level:
            await self._handle_level_up(message, new_level)

    async def _handle_level_up(self, message: discord.Message, new_level: int):
        data = await leveling_db.get()
        gid = str(message.guild.id)

        reward_role_id = data.get("reward_roles", {}).get(gid, {}).get(str(new_level))
        if reward_role_id:
            role = message.guild.get_role(reward_role_id)
            if role and role not in message.author.roles:
                try:
                    await message.author.add_roles(role, reason=f"Level {new_level} erreicht")
                except discord.HTTPException:
                    pass

        embed = discord.Embed(
            title="🎉 Level Up!",
            description=f"{message.author.mention} hat **Level {new_level}** erreicht!",
            color=discord.Color.gold()
        )
        embed.add_field(name="⭐ Neues Level", value=f"**Level {new_level}**", inline=True)
        reward_note = ""
        if reward_role_id:
            role = message.guild.get_role(reward_role_id)
            if role:
                reward_note = f"\n🎁 Belohnung: {role.mention}"
        embed.add_field(name="🎁 Belohnung", value=reward_note if reward_note else "Keine", inline=True)

        announce_id = data.get("announce_channel_id")
        channel = None
        if announce_id:
            channel = self.bot.get_channel(announce_id)
        if not channel:
            channel = message.channel

        try:
            await channel.send(embed=embed)
        except discord.HTTPException:
            pass

    # ─────────────────────────── Commands ───────────────────────────

    @app_commands.command(name="level", description="Zeigt deinen Level und XP-Stand")
    @app_commands.describe(user="User, dessen Level du sehen möchtest (Standard: du)")
    async def level(self, interaction: discord.Interaction, user: discord.User = None):
        target = user or interaction.user
        if not interaction.guild:
            await interaction.response.send_message(embed=error_embed("Das funktioniert nur auf einem Server."), ephemeral=True)
            return

        data = await leveling_db.get()
        users = data.get("users", {}).get(str(interaction.guild.id), {})
        entry = users.get(str(target.id), {"xp": 0})

        level, cur, target_xp = progress_in_level(entry["xp"])
        bar = build_progress_bar(cur, target_xp)

        embed = discord.Embed(
            title=f"📈 Level {level}",
            color=discord.Color.blue()
        )
        embed.set_author(name=target.display_name, icon_url=target.display_avatar.url)
        embed.add_field(name="XP", value=f"```{entry['xp']}```", inline=True)
        embed.add_field(name="Fortschritt", value=f"{bar} `{cur}/{target_xp}`", inline=False)
        embed.set_footer(text="XP erhältst du durch Nachrichten in Chats")

        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="leaderboard", description="Zeigt die Top-Spieler im Leveling")
    async def leaderboard(self, interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message(embed=error_embed("Das funktioniert nur auf einem Server."), ephemeral=True)
            return

        data = await leveling_db.get()
        users = data.get("users", {}).get(str(interaction.guild.id), {})
        if not users:
            await interaction.response.send_message(embed=info_embed("Noch keine Leveling-Daten vorhanden."), ephemeral=True)
            return

        ranked = sorted(users.items(), key=lambda kv: kv[1].get("xp", 0), reverse=True)[:10]

        lines = []
        medals = ["🥇", "🥈", "🥉"]
        for i, (uid, entry) in enumerate(ranked, start=1):
            member = interaction.guild.get_member(int(uid))
            name = member.display_name if member else f"User {uid}"
            level = level_from_xp(entry.get("xp", 0))
            medal = medals[i - 1] if i <= 3 else f"`{i}.`"
            lines.append(f"{medal} **{name}** — Level {level} (`{entry.get('xp', 0)} XP`)")

        embed = discord.Embed(
            title="🏆 Leaderboard",
            description="\n".join(lines),
            color=discord.Color.gold()
        )
        embed.set_footer(text="Top 10 nach XP")

        await interaction.response.send_message(embed=embed)

    leveling_group = app_commands.Group(name="leveling", description="Leveling-Einstellungen (Admin)")

    @leveling_group.command(name="toggle", description="Leveling global an- oder ausschalten")
    async def toggle(self, interaction: discord.Interaction, aktiv: bool):
        if not await require_authorized(interaction):
            return
        await leveling_db.modify(lambda d: d.__setitem__("enabled", aktiv))
        await interaction.response.send_message(
            embed=success_embed(f"✅ Leveling ist jetzt **{'aktiviert' if aktiv else 'deaktiviert'}**."),
            ephemeral=True
        )

    @leveling_group.command(name="cooldown", description="Cooldown zwischen XP-Vergaben setzen")
    @app_commands.describe(sekunden="Cooldown in Sekunden (z.B. 60)")
    async def cooldown(self, interaction: discord.Interaction, sekunden: int):
        if not await require_authorized(interaction):
            return
        if sekunden < 0 or sekunden > 3600:
            await interaction.response.send_message(embed=error_embed("Cooldown muss zwischen 0 und 3600 Sekunden liegen."), ephemeral=True)
            return
        await leveling_db.modify(lambda d: d.__setitem__("cooldown_seconds", sekunden))
        await interaction.response.send_message(
            embed=success_embed(f"✅ XP-Cooldown auf **{sekunden}s** gesetzt."),
            ephemeral=True
        )

    @leveling_group.command(name="xprange", description="XP-Bereich pro Nachricht setzen")
    @app_commands.describe(min_xp="Mindest-XP", max_xp="Maximal-XP")
    async def xprange(self, interaction: discord.Interaction, min_xp: int, max_xp: int):
        if not await require_authorized(interaction):
            return
        if min_xp < 1 or max_xp < min_xp or max_xp > 1000:
            await interaction.response.send_message(embed=error_embed("Ungültiger Bereich (1 ≤ min ≤ max ≤ 1000)."), ephemeral=True)
            return
        await leveling_db.modify(lambda d: d.__setitem__("xp_min", min_xp) or d.__setitem__("xp_max", max_xp) or d)
        await interaction.response.send_message(
            embed=success_embed(f"✅ XP-Bereich auf **{min_xp}–{max_xp}** pro Nachricht gesetzt."),
            ephemeral=True
        )

    @leveling_group.command(name="announcechannel", description="Channel für Level-Up-Nachrichten setzen")
    @app_commands.describe(channel="Channel (leer = gleicher Channel wie die Nachricht)")
    async def announcechannel(self, interaction: discord.Interaction, channel: discord.TextChannel = None):
        if not await require_authorized(interaction):
            return
        await leveling_db.modify(lambda d: d.__setitem__("announce_channel_id", channel.id if channel else None))
        if channel:
            await interaction.response.send_message(embed=success_embed(f"✅ Level-Up-Nachrichten gehen jetzt an {channel.mention}."), ephemeral=True)
        else:
            await interaction.response.send_message(embed=success_embed("✅ Level-Up-Nachrichten erscheinen im Chat des Users."), ephemeral=True)

    @leveling_group.command(name="reward", description="Rolle als Belohnung für ein Level setzen")
    @app_commands.describe(level="Level, ab dem die Rolle vergeben wird", rolle="Belohnungs-Rolle")
    async def reward(self, interaction: discord.Interaction, level: int, rolle: discord.Role):
        if not await require_authorized(interaction):
            return
        if level < 1:
            await interaction.response.send_message(embed=error_embed("Level muss mindestens 1 sein."), ephemeral=True)
            return
        gid = str(interaction.guild_id)

        def _set(d):
            d.setdefault("reward_roles", {}).setdefault(gid, {})[str(level)] = rolle.id
            return d
        await leveling_db.modify(_set)
        await interaction.response.send_message(
            embed=success_embed(f"✅ Ab **Level {level}** bekommt man die Rolle {rolle.mention}."),
            ephemeral=True
        )

    @leveling_group.command(name="rewards", description="Alle Level-Belohnungen anzeigen")
    async def rewards(self, interaction: discord.Interaction):
        if not await require_authorized(interaction):
            return
        gid = str(interaction.guild_id)
        data = await leveling_db.get()
        rewards = data.get("reward_roles", {}).get(gid, {})
        if not rewards:
            await interaction.response.send_message(embed=info_embed("Keine Level-Belohnungen konfiguriert."), ephemeral=True)
            return
        lines = []
        for level, role_id in sorted(rewards.items(), key=lambda kv: int(kv[0])):
            role = interaction.guild.get_role(role_id)
            lines.append(f"**Level {level}** → {role.mention if role else f'Rolle {role_id}'}")
        await interaction.response.send_message(
            embed=info_embed("🎁 **Level-Belohnungen:**\n" + "\n".join(lines)),
            ephemeral=True
        )

    @leveling_group.command(name="removereward", description="Level-Belohnung entfernen")
    @app_commands.describe(level="Level, dessen Belohnung entfernt werden soll")
    async def removereward(self, interaction: discord.Interaction, level: int):
        if not await require_authorized(interaction):
            return
        gid = str(interaction.guild_id)

        def _remove(d):
            d.setdefault("reward_roles", {}).setdefault(gid, {}).pop(str(level), None)
            return d
        await leveling_db.modify(_remove)
        await interaction.response.send_message(
            embed=success_embed(f"✅ Belohnung für **Level {level}** entfernt."),
            ephemeral=True
        )

    @leveling_group.command(name="reset", description="XP eines Users zurücksetzen")
    @app_commands.describe(user="User, dessen XP zurückgesetzt werden soll")
    async def reset(self, interaction: discord.Interaction, user: discord.User):
        if not await require_authorized(interaction):
            return
        gid = str(interaction.guild_id)
        uid = str(user.id)

        def _reset(d):
            d.setdefault("users", {}).setdefault(gid, {}).pop(uid, None)
            return d
        await leveling_db.modify(_reset)
        await interaction.response.send_message(
            embed=success_embed(f"✅ XP von {user.mention} wurden zurückgesetzt."),
            ephemeral=True
        )

    @leveling_group.command(name="status", description="Leveling-Einstellungen anzeigen")
    async def status(self, interaction: discord.Interaction):
        if not await require_authorized(interaction):
            return
        data = await leveling_db.get()
        announce_id = data.get("announce_channel_id")
        announce = self.bot.get_channel(announce_id).mention if announce_id else "Gleicher Channel"

        gid = str(interaction.guild_id)
        rewards = data.get("reward_roles", {}).get(gid, {})
        rewards_str = ", ".join(f"Lvl {k}: <@&{v}>" for k, v in sorted(rewards.items(), key=lambda kv: int(kv[0]))) if rewards else "Keine"

        embed = info_embed("⚙️ **Leveling-Einstellungen**")
        embed.add_field(name="Status", value="🟢 Aktiv" if data.get("enabled", True) else "🔴 Inaktiv", inline=True)
        embed.add_field(name="Cooldown", value=f"{data.get('cooldown_seconds', 60)}s", inline=True)
        embed.add_field(name="XP pro Nachricht", value=f"{data.get('xp_min', 15)}–{data.get('xp_max', 25)}", inline=True)
        embed.add_field(name="Ankündigungen", value=announce, inline=False)
        embed.add_field(name="Level-Belohnungen", value=rewards_str, inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(LevelingCog(bot))

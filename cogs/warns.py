import discord
from discord.ext import commands
from discord import app_commands
from datetime import datetime, timezone, timedelta

from config import warns_db, config_db
from utils import require_authorized, success_embed, error_embed, info_embed, warning_embed, is_owner, is_dev
from utils.embeds import create_embed

TIMEOUT_THRESHOLD = 3
KICK_THRESHOLD = 5
TIMEOUT_MINUTES = 30


def _fmt_time(ts: str) -> str:
    try:
        dt = datetime.fromisoformat(ts)
        return f"<t:{int(dt.timestamp())}:f>"
    except (ValueError, TypeError):
        return "?"


class WarnsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def _log(self, guild: discord.Guild, embed: discord.Embed):
        config = await config_db.get()
        log_channel_id = config.get("log_channel_id")
        log_channel = guild.get_channel(log_channel_id) if log_channel_id else None
        if log_channel:
            try:
                await log_channel.send(embed=embed)
            except discord.HTTPException:
                pass

    async def _dm(self, user: discord.User, text: str):
        try:
            await user.send(text)
        except (discord.Forbidden, discord.HTTPException):
            pass

    @app_commands.command(name="warn", description="Einen User verwarnen (3 Warns → Timeout, 5 → Kick)")
    @app_commands.describe(user="User", reason="Grund für die Verwarnung")
    async def warn(self, interaction: discord.Interaction, user: discord.Member, reason: str):
        if not await require_authorized(interaction):
            return

        if user == interaction.user:
            await interaction.response.send_message(embed=error_embed("Du kannst dich nicht selbst verwarnen"), ephemeral=True)
            return
        if user.bot:
            await interaction.response.send_message(embed=error_embed("Bots können nicht verwarnt werden"), ephemeral=True)
            return

        if user.top_role >= interaction.user.top_role:
            if not (is_owner(interaction.user) or is_dev(interaction.user)):
                await interaction.response.send_message(
                    embed=error_embed("Du kannst keinen User verwarnen, der eine gleich hohe oder höhere Rolle hat"),
                    ephemeral=True
                )
                return

        reason = reason.strip()
        if not reason:
            await interaction.response.send_message(embed=error_embed("Der Grund darf nicht leer sein"), ephemeral=True)
            return

        def _add(data: dict):
            data.setdefault("warns", {}).setdefault(str(user.id), []).append({
                "reason": reason,
                "moderator": interaction.user.id,
                "time": datetime.now(timezone.utc).isoformat()
            })
            return data

        data = await warns_db.modify(_add)
        count = len(data.get("warns", {}).get(str(user.id), []))

        punishment = None
        if count >= KICK_THRESHOLD:
            def _reset(d: dict):
                d.setdefault("warns", {}).pop(str(user.id), None)
                return d
            await warns_db.modify(_reset)
            try:
                await user.kick(reason=f"5 Warns erreicht ({reason})")
                punishment = "kick"
            except discord.Forbidden:
                punishment = "kick_failed"
        elif count == TIMEOUT_THRESHOLD:
            try:
                await user.timeout(
                    datetime.now(timezone.utc) + timedelta(minutes=TIMEOUT_MINUTES),
                    reason=f"3 Warns erreicht ({reason})"
                )
                punishment = "timeout"
            except discord.Forbidden:
                punishment = "timeout_failed"

        if punishment == "kick":
            await interaction.response.send_message(
                embed=success_embed(f"👢 **{user}** wurde gekickt (Warn #{count}).\nGrund: `{reason}`"),
                ephemeral=True
            )
            await self._dm(user, f"👢 Du wurdest aus **{interaction.guild.name}** gekickt, da du 5 Warns erreicht hast.\nLetzter Grund: `{reason}`")
        elif punishment == "timeout":
            await interaction.response.send_message(
                embed=success_embed(f"⏱️ **{user}** wurde für {TIMEOUT_MINUTES} Minuten getimeout (Warn #{count}).\nGrund: `{reason}`"),
                ephemeral=True
            )
            await self._dm(user, f"⏱️ Du wurdest in **{interaction.guild.name}** für {TIMEOUT_MINUTES} Minuten getimeout, da du 3 Warns erreicht hast.\nGrund: `{reason}`")
        elif punishment in ("kick_failed", "timeout_failed"):
            await interaction.response.send_message(
                embed=warning_embed(f"⚠️ **{user}** hat jetzt {count} Warns. Die automatische Strafe konnte nicht angewendet werden (fehlende Berechtigungen)."),
                ephemeral=True
            )
        else:
            await interaction.response.send_message(
                embed=success_embed(f"⚠️ **{user}** wurde verwarnt (Warn #{count}).\nGrund: `{reason}`\nBei {TIMEOUT_THRESHOLD} Warns folgt ein Timeout, bei {KICK_THRESHOLD} ein Kick."),
                ephemeral=True
            )
            await self._dm(user, f"⚠️ Du wurdest in **{interaction.guild.name}** verwarnt.\nGrund: `{reason}`\nDu hast jetzt **{count}/{KICK_THRESHOLD}** Warns.")

        embed = create_embed(
            title="⚠️ Verwarnung",
            description=f"{user.mention} hat eine Verwarnung erhalten",
            color=discord.Color.orange(),
            fields=[
                ("User", f"{user} (`{user.id}`)", True),
                ("Moderator", f"{interaction.user} (`{interaction.user.id}`)", True),
                ("Warn #", str(count), True),
                ("Grund", reason[:1000], False)
            ]
        )
        await self._log(interaction.guild, embed)

    warns_group = app_commands.Group(name="warns", description="Warnungen verwalten")

    @warns_group.command(name="user", description="Warnungen eines Users anzeigen")
    @app_commands.describe(user="User")
    async def warns_user(self, interaction: discord.Interaction, user: discord.Member):
        if not await require_authorized(interaction):
            return

        data = await warns_db.get()
        warns = data.get("warns", {}).get(str(user.id), [])
        embed = discord.Embed(title=f"⚠️ Warns von {user}", color=discord.Color.orange())
        embed.set_author(name=f"{user}", icon_url=user.display_avatar.url)

        if not warns:
            embed.description = "Keine Warns."
        else:
            embed.description = f"**{len(warns)}/{KICK_THRESHOLD}** Warns"
            lines = []
            for i, w in enumerate(warns, 1):
                mod = interaction.guild.get_member(w.get("moderator"))
                mod_name = f"{mod}" if mod else f"`{w.get('moderator')}`"
                lines.append(
                    f"`#{i}` **{w.get('reason', '?')}**\n"
                    f"von {mod_name} • {_fmt_time(w.get('time', ''))}"
                )
            embed.add_field(name="Verlauf", value="\n\n".join(lines), inline=False)

        await interaction.response.send_message(embed=embed, ephemeral=True)

    @warns_group.command(name="remove", description="Eine einzelne Warnung entfernen")
    @app_commands.describe(user="User", index="Index der Warnung (ab 1)")
    async def warns_remove(self, interaction: discord.Interaction, user: discord.Member, index: int):
        if not await require_authorized(interaction):
            return

        state = {}

        def _remove(data: dict):
            warns = data.setdefault("warns", {}).get(str(user.id), [])
            if index < 1 or index > len(warns):
                state["error"] = "Ungültiger Index"
                return None
            removed = warns.pop(index - 1)
            state["removed"] = removed
            if not warns:
                data["warns"].pop(str(user.id), None)
            return data

        await warns_db.modify(_remove)
        if state.get("error"):
            await interaction.response.send_message(embed=error_embed(state["error"]), ephemeral=True)
            return

        await interaction.response.send_message(
            embed=success_embed(f"Warnung `#{index}` von {user.mention} entfernt.\nGrund war: `{state['removed'].get('reason', '?')}`"),
            ephemeral=True
        )

    @warns_group.command(name="clear", description="Alle Warnungen eines Users löschen")
    @app_commands.describe(user="User")
    async def warns_clear(self, interaction: discord.Interaction, user: discord.Member):
        if not await require_authorized(interaction):
            return

        state = {}

        def _clear(data: dict):
            warns = data.setdefault("warns", {}).pop(str(user.id), None)
            state["count"] = len(warns) if warns else 0
            return data

        await warns_db.modify(_clear)
        await interaction.response.send_message(
            embed=success_embed(f"Alle {state['count']} Warns von {user.mention} gelöscht."),
            ephemeral=True
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(WarnsCog(bot))

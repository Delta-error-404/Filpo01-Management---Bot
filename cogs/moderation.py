import discord
from discord.ext import commands
from discord import app_commands
from datetime import datetime, timezone, timedelta

from config import config_db
from utils import require_authorized, success_embed, error_embed
from utils.permissions import is_owner, is_dev
from utils.embeds import create_embed


class ModerationCog(commands.Cog):
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

    def _target_ok(self, interaction: discord.Interaction, target: discord.Member) -> tuple[bool, str | None]:
        if target == interaction.user:
            return False, "Du kannst dich nicht selbst bestrafen."
        if target.bot:
            return False, "Bots können nicht bestraft werden."
        if target.top_role >= interaction.user.top_role:
            if not (is_owner(interaction.user) or is_dev(interaction.user)):
                return False, "Du kannst keinen User bestrafen, der eine gleich hohe oder höhere Rolle hat."
        return True, None

    def _log_embed(self, title: str, color: discord.Color, target, interaction: discord.Interaction,
                   reason: str, extra: list = None) -> discord.Embed:
        fields = [
            ("User", f"{target} (`{target.id}`)", True),
            ("Moderator", f"{interaction.user} (`{interaction.user.id}`)", True),
            ("Grund", reason[:1000], False),
        ]
        if extra:
            fields.extend(extra)
        return create_embed(title=title, color=color, fields=fields)

    @app_commands.command(name="ban", description="Einen User bannen (nur Admin)")
    @app_commands.describe(user="User", reason="Grund für den Ban")
    async def ban(self, interaction: discord.Interaction, user: discord.Member, reason: str):
        if not await require_authorized(interaction):
            return

        ok, err = self._target_ok(interaction, user)
        if not ok:
            await interaction.response.send_message(embed=error_embed(err), ephemeral=True)
            return

        reason = reason.strip() or "Kein Grund angegeben"
        try:
            await user.ban(reason=f"{interaction.user} – {reason}")
        except discord.Forbidden:
            await interaction.response.send_message(
                embed=error_embed("Ich habe nicht die Berechtigung, diesen User zu bannen."),
                ephemeral=True
            )
            return
        except discord.HTTPException as e:
            await interaction.response.send_message(embed=error_embed(f"Fehler beim Bannen: {e}"), ephemeral=True)
            return

        await interaction.response.send_message(
            embed=success_embed(f"🔨 **{user}** wurde gebannt.\nGrund: `{reason}`"),
            ephemeral=True
        )
        await self._dm(user, f"🔨 Du wurdest aus **{interaction.guild.name}** gebannt.\nGrund: `{reason}`")
        await self._log(interaction.guild, self._log_embed("🔨 Ban", discord.Color.red(), user, interaction, reason))

    @app_commands.command(name="kick", description="Einen User kicken (nur Admin)")
    @app_commands.describe(user="User", reason="Grund für den Kick")
    async def kick(self, interaction: discord.Interaction, user: discord.Member, reason: str):
        if not await require_authorized(interaction):
            return

        ok, err = self._target_ok(interaction, user)
        if not ok:
            await interaction.response.send_message(embed=error_embed(err), ephemeral=True)
            return

        reason = reason.strip() or "Kein Grund angegeben"
        try:
            await user.kick(reason=f"{interaction.user} – {reason}")
        except discord.Forbidden:
            await interaction.response.send_message(
                embed=error_embed("Ich habe nicht die Berechtigung, diesen User zu kicken."),
                ephemeral=True
            )
            return
        except discord.HTTPException as e:
            await interaction.response.send_message(embed=error_embed(f"Fehler beim Kicken: {e}"), ephemeral=True)
            return

        await interaction.response.send_message(
            embed=success_embed(f"👢 **{user}** wurde gekickt.\nGrund: `{reason}`"),
            ephemeral=True
        )
        await self._dm(user, f"👢 Du wurdest aus **{interaction.guild.name}** gekickt.\nGrund: `{reason}`")
        await self._log(interaction.guild, self._log_embed("👢 Kick", discord.Color.orange(), user, interaction, reason))

    @app_commands.command(name="mute", description="Einen User stummschalten (Timeout, nur Admin)")
    @app_commands.describe(user="User", reason="Grund", duration="Dauer in Minuten")
    async def mute(self, interaction: discord.Interaction, user: discord.Member, reason: str, duration: int = 10):
        if not await require_authorized(interaction):
            return

        if not (1 <= duration <= 40320):
            await interaction.response.send_message(
                embed=error_embed("Dauer muss zwischen 1 und 40320 Minuten liegen (max. 28 Tage)."),
                ephemeral=True
            )
            return

        ok, err = self._target_ok(interaction, user)
        if not ok:
            await interaction.response.send_message(embed=error_embed(err), ephemeral=True)
            return

        reason = reason.strip() or "Kein Grund angegeben"
        try:
            await user.timeout(
                datetime.now(timezone.utc) + timedelta(minutes=duration),
                reason=f"{interaction.user} – {reason}"
            )
        except discord.Forbidden:
            await interaction.response.send_message(
                embed=error_embed("Ich habe nicht die Berechtigung, diesen User zu stummschalten."),
                ephemeral=True
            )
            return
        except discord.HTTPException as e:
            await interaction.response.send_message(embed=error_embed(f"Fehler beim Muten: {e}"), ephemeral=True)
            return

        await interaction.response.send_message(
            embed=success_embed(f"🔇 **{user}** wurde für **{duration} Minuten** stummgeschaltet.\nGrund: `{reason}`"),
            ephemeral=True
        )
        await self._dm(user, f"🔇 Du wurdest in **{interaction.guild.name}** für {duration} Minuten stummgeschaltet.\nGrund: `{reason}`")
        await self._log(
            interaction.guild,
            self._log_embed("🔇 Mute", discord.Color.orange(), user, interaction, reason,
                            extra=[("Dauer", f"{duration} Minuten", True)])
        )

    @app_commands.command(name="unmute", description="Timeout eines Users aufheben (nur Admin)")
    @app_commands.describe(user="User", reason="Grund")
    async def unmute(self, interaction: discord.Interaction, user: discord.Member, reason: str = "Kein Grund angegeben"):
        if not await require_authorized(interaction):
            return

        ok, err = self._target_ok(interaction, user)
        if not ok:
            await interaction.response.send_message(embed=error_embed(err), ephemeral=True)
            return

        reason = reason.strip() or "Kein Grund angegeben"
        try:
            await user.timeout(None, reason=f"{interaction.user} – {reason}")
        except discord.Forbidden:
            await interaction.response.send_message(
                embed=error_embed("Ich habe nicht die Berechtigung, den Timeout aufzuheben."),
                ephemeral=True
            )
            return
        except discord.HTTPException as e:
            await interaction.response.send_message(embed=error_embed(f"Fehler beim Unmuten: {e}"), ephemeral=True)
            return

        await interaction.response.send_message(
            embed=success_embed(f"🔊 **{user}** wurde entstummt.\nGrund: `{reason}`"),
            ephemeral=True
        )
        await self._dm(user, f"🔊 Dein Timeout in **{interaction.guild.name}** wurde aufgehoben.")
        await self._log(interaction.guild, self._log_embed("🔊 Unmute", discord.Color.green(), user, interaction, reason))

    @app_commands.command(name="unban", description="Einen User entbannen (nur Admin)")
    @app_commands.describe(user_id="User-ID des gebannten Users", reason="Grund")
    async def unban(self, interaction: discord.Interaction, user_id: str, reason: str = "Kein Grund angegeben"):
        if not await require_authorized(interaction):
            return

        try:
            uid = int(user_id)
        except ValueError:
            await interaction.response.send_message(
                embed=error_embed("Ungültige User-ID. Es wird eine numerische ID erwartet."),
                ephemeral=True
            )
            return

        reason = reason.strip() or "Kein Grund angegeben"
        try:
            ban_entry = await interaction.guild.fetch_ban(discord.Object(id=uid))
            await interaction.guild.unban(ban_entry.user, reason=f"{interaction.user} – {reason}")
        except discord.NotFound:
            await interaction.response.send_message(
                embed=error_embed(f"Der User `{uid}` ist nicht gebannt."),
                ephemeral=True
            )
            return
        except discord.Forbidden:
            await interaction.response.send_message(
                embed=error_embed("Ich habe nicht die Berechtigung, User zu entbannen."),
                ephemeral=True
            )
            return
        except discord.HTTPException as e:
            await interaction.response.send_message(embed=error_embed(f"Fehler beim Entbannen: {e}"), ephemeral=True)
            return

        await interaction.response.send_message(
            embed=success_embed(f"✅ **{ban_entry.user}** (`{uid}`) wurde entbannt.\nGrund: `{reason}`"),
            ephemeral=True
        )
        await self._log(
            interaction.guild,
            self._log_embed("✅ Unban", discord.Color.green(), ban_entry.user, interaction, reason)
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(ModerationCog(bot))

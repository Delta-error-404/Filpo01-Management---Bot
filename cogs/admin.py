import os
import asyncio

import discord
from discord.ext import commands
from discord import app_commands

from utils import success_embed
from utils.permissions import is_owner, is_dev


class AdminCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def _require_owner(self, interaction: discord.Interaction) -> bool:
        if is_owner(interaction.user) or is_dev(interaction.user):
            return True
        await interaction.response.send_message(
            f"❌ Nur der Bot-Besitzer oder ein Entwickler kann diesen Befehl nutzen.\nDeine ID: `{interaction.user.id}`",
            ephemeral=True
        )
        return False

    @app_commands.command(name="stop", description="Stoppt den Bot (nur Owner/Dev)")
    async def stop(self, interaction: discord.Interaction):
        if not await self._require_owner(interaction):
            return

        await interaction.response.send_message(
            embed=success_embed("🛑 Der Bot wird gestoppt..."),
            ephemeral=True
        )
        await asyncio.sleep(0.5)
        try:
            await asyncio.wait_for(self.bot.close(), timeout=5)
        except Exception:
            pass
        os._exit(0)

    @app_commands.command(name="restart", description="Startet den Bot neu (nur Owner/Dev)")
    async def restart(self, interaction: discord.Interaction):
        if not await self._require_owner(interaction):
            return

        await interaction.response.send_message(
            embed=success_embed("🔄 Der Bot wird neu gestartet..."),
            ephemeral=True
        )
        await asyncio.sleep(0.5)

        lock_file = getattr(self.bot, "_lock_file", None)
        if lock_file is not None:
            try:
                lock_file.close()
            except OSError:
                pass

        # Den Prozess sauber beenden. Der Host (Pterodactyl/Wispbyte)
        # startet den Bot automatisch neu. So entstehen keine Doppel-Prozesse
        # und Datenbanken bleiben konsistent.
        try:
            await asyncio.wait_for(self.bot.close(), timeout=5)
        except Exception:
            pass
        os._exit(0)


async def setup(bot: commands.Bot):
    await bot.add_cog(AdminCog(bot))

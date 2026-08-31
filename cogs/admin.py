import os
import sys
import asyncio
import subprocess
from pathlib import Path

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

        script = Path(sys.argv[0]).resolve()
        if script.suffix != ".py" or not script.exists():
            script = Path(__file__).resolve().parent.parent / "bot.py"

        log_dir = script.parent / "data"
        log_dir.mkdir(exist_ok=True)
        stdout = open(log_dir / "bot_stdout.log", "a", encoding="utf-8")
        stderr = open(log_dir / "bot_stderr.log", "a", encoding="utf-8")

        kwargs = {}
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW
        else:
            kwargs["start_new_session"] = True

        lock_file = getattr(self.bot, "_lock_file", None)
        if lock_file is not None:
            try:
                lock_file.close()
            except OSError:
                pass

        subprocess.Popen(
            [sys.executable, str(script)],
            cwd=str(script.parent),
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=stderr,
            close_fds=True,
            **kwargs
        )

        await asyncio.sleep(0.5)
        try:
            await asyncio.wait_for(self.bot.close(), timeout=5)
        except Exception:
            pass
        os._exit(0)


async def setup(bot: commands.Bot):
    await bot.add_cog(AdminCog(bot))

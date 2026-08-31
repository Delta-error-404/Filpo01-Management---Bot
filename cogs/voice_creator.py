import asyncio

import discord
from discord.ext import commands

from config import config_db, voicecreator_db


class VoiceCreatorCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.created_channels: set[int] = set()
        self._task: asyncio.Task | None = None

    async def cog_load(self):
        data = await voicecreator_db.get()
        self.created_channels = set(int(cid) for cid in data.get("channels", []) if str(cid).isdigit())
        self._task = asyncio.create_task(self.cleanup_loop())

    async def cog_unload(self):
        if self._task:
            self._task.cancel()

    async def _persist_add(self, channel_id: int):
        def _add(data: dict):
            channels = data.setdefault("channels", [])
            if channel_id not in channels:
                channels.append(channel_id)
                return data
            return None
        await voicecreator_db.modify(_add)

    async def _persist_remove(self, channel_id: int):
        def _remove(data: dict):
            channels = data.setdefault("channels", [])
            if channel_id in channels:
                channels.remove(channel_id)
                return data
            return None
        await voicecreator_db.modify(_remove)

    async def _cleanup_channel(self, channel_id: int):
        channel = self.bot.get_channel(channel_id)
        if not channel or not isinstance(channel, discord.VoiceChannel):
            self.created_channels.discard(channel_id)
            await self._persist_remove(channel_id)
            return
        if len(channel.members) == 0:
            try:
                await channel.delete(reason="Voice Creator: Channel leer")
            except discord.HTTPException:
                pass
            self.created_channels.discard(channel_id)
            await self._persist_remove(channel_id)

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        if member.bot:
            return

        config = await config_db.get()
        vc = config.get("voice_creator", {})
        create_channel_id = vc.get("channel_id")
        category_id = vc.get("category_id")
        name_template = vc.get("name_template", "{user} Talk")

        if not create_channel_id or not category_id:
            return

        # User joined the create channel
        if after.channel and after.channel.id == create_channel_id:
            category = member.guild.get_channel(category_id)
            if not category or not isinstance(category, discord.CategoryChannel):
                return

            name = name_template.format(user=member.display_name, username=member.name, count=len(category.voice_channels) + 1)
            name = name[:100]

            overwrites = {
                member.guild.default_role: discord.PermissionOverwrite(view_channel=True, connect=True),
                member: discord.PermissionOverwrite(view_channel=True, connect=True, manage_channels=True, move_members=True, mute_members=True, deafen_members=True),
                member.guild.me: discord.PermissionOverwrite(view_channel=True, connect=True, manage_channels=True, move_members=True)
            }

            try:
                new_channel = await category.create_voice_channel(
                    name=name,
                    overwrites=overwrites,
                    position=0,
                    reason=f"Voice Creator: {member.display_name}"
                )
            except discord.HTTPException:
                return

            self.created_channels.add(new_channel.id)
            await self._persist_add(new_channel.id)

            try:
                await member.move_to(new_channel, reason="Voice Creator")
            except discord.HTTPException:
                await self._cleanup_channel(new_channel.id)
                return

        # User left a created channel - delete if empty
        if before.channel and before.channel.id in self.created_channels:
            if len(before.channel.members) == 0:
                await self._cleanup_channel(before.channel.id)

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel):
        if channel.id in self.created_channels:
            self.created_channels.discard(channel.id)
            await self._persist_remove(channel.id)

    async def cleanup_loop(self):
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            try:
                for channel_id in list(self.created_channels):
                    await self._cleanup_channel(channel_id)
            except Exception as e:
                print(f"Fehler im Voice-Creator-Cleanup: {e}")
            await asyncio.sleep(60)


async def setup(bot: commands.Bot):
    await bot.add_cog(VoiceCreatorCog(bot))

import discord
from discord.ext import commands
from discord import app_commands
from config import config_db
from utils import require_authorized, success_embed, error_embed, info_embed

class ReactionRolesCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    rr_group = app_commands.Group(name="reactionroles", description="Reaction Roles verwalten")

    @rr_group.command(name="add", description="Reaction Role hinzufügen")
    @app_commands.describe(message_id="Nachrichten-ID oder 'watch' für überwachten Channel", emoji="Emoji (Unicode oder :name:)", role="Rolle")
    async def rr_add(self, interaction: discord.Interaction, message_id: str, emoji: str, role: discord.Role):
        if not await require_authorized(interaction):
            return
        
        config = await config_db.get()
        rr = config.get("reaction_roles", {})
        
        if message_id.lower() == "watch":
            watch_channel_id = config.get("watch_channel_id")
            watch_emoji = config.get("watch_emoji")
            
            if not watch_channel_id or not watch_emoji:
                await interaction.response.send_message(
                    embed=error_embed("Kein Watch-Channel/Emoji konfiguriert. Nutze erst /reactionroles watch"),
                    ephemeral=True
                )
                return
            
            if emoji != watch_emoji:
                await interaction.response.send_message(
                    embed=error_embed(f"Emoji muss dem Watch-Emoji entsprechen: {watch_emoji}"),
                    ephemeral=True
                )
                return
            
            msg_key = f"watch_{watch_channel_id}"
        else:
            try:
                msg_id = int(message_id)
            except ValueError:
                await interaction.response.send_message(
                    embed=error_embed("Ungültige Nachrichten-ID"),
                    ephemeral=True
                )
                return
            msg_key = str(msg_id)
        
        if msg_key not in rr:
            rr[msg_key] = {}
        
        rr[msg_key][emoji] = role.id
        await config_db.update(reaction_roles=rr)
        
        await interaction.response.send_message(
            embed=success_embed(f"Reaction Role hinzugefügt: {emoji} → {role.mention} ({'Watch-Channel' if message_id.lower() == 'watch' else f'Message: {msg_id}'})"),
            ephemeral=True
        )

    @rr_group.command(name="remove", description="Reaction Role entfernen")
    @app_commands.describe(message_id="Nachrichten-ID", emoji="Emoji")
    async def rr_remove(self, interaction: discord.Interaction, message_id: str, emoji: str):
        if not await require_authorized(interaction):
            return
        
        try:
            msg_id = int(message_id)
        except ValueError:
            await interaction.response.send_message(
                embed=error_embed("Ungültige Nachrichten-ID"),
                ephemeral=True
            )
            return
        
        config = await config_db.get()
        rr = config.get("reaction_roles", {})
        msg_key = str(msg_id)
        
        if msg_key in rr and emoji in rr[msg_key]:
            del rr[msg_key][emoji]
            if not rr[msg_key]:
                del rr[msg_key]
            await config_db.update(reaction_roles=rr)
            await interaction.response.send_message(
                embed=success_embed(f"Reaction Role entfernt: {emoji} (Message: {msg_id})"),
                ephemeral=True
            )
        else:
            await interaction.response.send_message(
                embed=error_embed("Reaction Role nicht gefunden"),
                ephemeral=True
            )

    @rr_group.command(name="watch", description="Channel überwachen - neueste Nachricht bekommt Emoji, Reaction gibt Rolle")
    @app_commands.describe(channel="Channel der überwacht werden soll", emoji="Emoji (Unicode oder :name:)", role="Rolle die vergeben wird", enabled="Ein/Aus")
    async def rr_watch(self, interaction: discord.Interaction, channel: discord.TextChannel, emoji: str, role: discord.Role, enabled: bool = True):
        if not await require_authorized(interaction):
            return
        
        config = await config_db.get()
        
        if enabled:
            config["watch_channel_id"] = channel.id
            config["watch_emoji"] = emoji
            config["watch_role_id"] = role.id
            await config_db.save(config)
            await interaction.response.send_message(
                embed=success_embed(f"Watch aktiv: {channel.mention} | Emoji: {emoji} | Rolle: {role.mention}"),
                ephemeral=True
            )
        else:
            config.pop("watch_channel_id", None)
            config.pop("watch_emoji", None)
            config.pop("watch_role_id", None)
            config.pop("watch_message_id", None)
            await config_db.save(config)
            await interaction.response.send_message(
                embed=success_embed("Watch deaktiviert."),
                ephemeral=True
            )

    @rr_group.command(name="list", description="Alle Reaction Roles anzeigen")
    async def rr_list(self, interaction: discord.Interaction):
        if not await require_authorized(interaction):
            return
        
        config = await config_db.get()
        rr = config.get("reaction_roles", {})
        watch_channel_id = config.get("watch_channel_id")
        watch_emoji = config.get("watch_emoji")
        
        embed = discord.Embed(title="Reaction Roles", color=discord.Color.blue())
        
        if watch_channel_id:
            watch_channel = interaction.guild.get_channel(watch_channel_id)
            emoji_str = f" {watch_emoji}" if watch_emoji else ""
            embed.add_field(name="Überwachter Channel", value=f"{watch_channel.mention if watch_channel else 'Gelöscht'}{emoji_str}", inline=False)
        
        if not rr:
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        
        for msg_id, roles in rr.items():
            if not roles:
                continue
            lines = []
            for emoji, role_id in roles.items():
                role = interaction.guild.get_role(role_id)
                role_str = role.mention if role else f"<@&{role_id}> (nicht gefunden)"
                lines.append(f"{emoji} → {role_str}")
            embed.add_field(name=f"Message: {msg_id}", value="\n".join(lines), inline=False)
        
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return
        
        config = await config_db.get()
        watch_channel_id = config.get("watch_channel_id")
        watch_emoji = config.get("watch_emoji")

        if watch_channel_id and message.channel.id == watch_channel_id and watch_emoji:
            old_msg_key = config.get("watch_message_id")
            if old_msg_key and old_msg_key != str(message.id):
                try:
                    old_channel = message.guild.get_channel(message.channel.id)
                    if old_channel:
                        old_msg = await old_channel.fetch_message(int(old_msg_key))
                        await old_msg.clear_reactions()
                except (discord.NotFound, discord.HTTPException):
                    pass
            
            config["watch_message_id"] = str(message.id)
            await config_db.save(config)
            
            try:
                await message.add_reaction(watch_emoji)
            except discord.HTTPException:
                pass

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        if payload.member and payload.member.bot:
            return
        
        config = await config_db.get()
        rr = config.get("reaction_roles", {})
        
        watch_msg_id = config.get("watch_message_id")
        watch_emoji = config.get("watch_emoji")
        watch_role_id = config.get("watch_role_id")
        emoji_key = str(payload.emoji)

        if watch_msg_id and str(payload.message_id) == watch_msg_id:
            if watch_emoji and emoji_key != watch_emoji:
                return
            if watch_role_id:
                role_id = watch_role_id
            else:
                msg_key = watch_msg_id
                if msg_key not in rr or emoji_key not in rr[msg_key]:
                    return
                role_id = rr[msg_key][emoji_key]
        else:
            msg_key = str(payload.message_id)
            if msg_key not in rr or emoji_key not in rr[msg_key]:
                return
            role_id = rr[msg_key][emoji_key]
        
        guild = self.bot.get_guild(payload.guild_id)
        if not guild:
            return

        role = guild.get_role(role_id)
        if not role:
            return

        member = guild.get_member(payload.user_id)
        if not member:
            return

        try:
            await member.add_roles(role, reason="Reaction Role")
        except discord.HTTPException:
            pass

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent):
        config = await config_db.get()
        rr = config.get("reaction_roles", {})
        
        watch_msg_id = config.get("watch_message_id")
        watch_emoji = config.get("watch_emoji")
        watch_role_id = config.get("watch_role_id")
        emoji_key = str(payload.emoji)
        
        if watch_msg_id and str(payload.message_id) == watch_msg_id:
            if watch_emoji and emoji_key != watch_emoji:
                return
            if watch_role_id:
                role_id = watch_role_id
            else:
                msg_key = watch_msg_id
                if msg_key not in rr or emoji_key not in rr[msg_key]:
                    return
                role_id = rr[msg_key][emoji_key]
        else:
            msg_key = str(payload.message_id)
            if msg_key not in rr or emoji_key not in rr[msg_key]:
                return
            role_id = rr[msg_key][emoji_key]
        
        guild = self.bot.get_guild(payload.guild_id)
        if not guild:
            return
        
        role = guild.get_role(role_id)
        if not role:
            return
        
        member = guild.get_member(payload.user_id)
        if not member:
            return
        
        try:
            await member.remove_roles(role, reason="Reaction Role entfernt")
        except discord.HTTPException:
            pass

async def setup(bot: commands.Bot):
    await bot.add_cog(ReactionRolesCog(bot))
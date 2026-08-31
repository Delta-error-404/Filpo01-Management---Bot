import discord
import time
import asyncio
import logging
from collections import defaultdict
from discord.ext import commands
from discord import app_commands

from config import anti_nuke_db, config_db
from config.settings import settings
from utils import is_owner, is_dev, create_embed, success_embed

logger = logging.getLogger("anti_nuke")

# Standardeinstellungen
DEFAULTS = {
    "enabled": True,
    "max_channel_dels": 2,
    "channel_del_window": 30,
    "max_role_dels": 2,
    "role_del_window": 30,
    "max_role_creates": 5,
    "role_create_window": 30,
    "max_bans": 3,
    "ban_window": 30,
    "max_kicks": 3,
    "kick_window": 30,
    "max_perms_edits": 3,
    "perms_edit_window": 30,
    "max_webhook_creates": 3,
    "webhook_create_window": 30,
    "punishment": "ban",
    "excluded_roles": [],
    "log_channel_id": None,
}


class AntiNukeCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Tracking: guild_id -> user_id -> action_type -> [timestamps]
        self._actions = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
        # Cache fuer Restore (Channels + Rollen)
        self._channel_cache = {}
        self._role_cache = {}
        # Straf-Cooldown: guild_id -> user_id -> timestamp
        self._punished = defaultdict(lambda: defaultdict(float))

    anti_nuke = app_commands.Group(name="antinuke", description="Anti-Nuke System")

    # === HILFSFUNKTIONEN ===

    async def _cfg(self) -> dict:
        data = await anti_nuke_db.get()
        m = dict(DEFAULTS)
        m.update(data)
        return m

    def _excluded(self, member: discord.Member, cfg: dict) -> bool:
        excl = cfg.get("excluded_roles", [])
        return any(r.id in excl for r in member.roles)

    def _protected(self, user) -> bool:
        if is_owner(user) or is_dev(user):
            return True
        if isinstance(user, discord.Member) and user.guild_permissions.administrator:
            return True
        return False

    def _track(self, gid: int, uid: int, action: str, window: int) -> int:
        now = time.time()
        ts = self._actions[gid][uid][action]
        ts[:] = [t for t in ts if now - t < window]
        ts.append(now)
        return len(ts)

    def _already_punished(self, gid: int, uid: int) -> bool:
        now = time.time()
        last = self._punished[gid][uid]
        if now - last < 60:
            return True
        self._punished[gid][uid] = now
        return False

    async def _punish(self, guild: discord.Guild, user_id: int, reason: str, cfg: dict):
        if self._already_punished(guild.id, user_id):
            return
        member = guild.get_member(user_id)
        if not member or self._protected(member):
            return
        mode = cfg.get("punishment", "ban")
        try:
            if mode == "ban":
                await member.ban(reason=reason, delete_message_days=0)
            else:
                await member.kick(reason=reason)
            logger.warning("ANTI-NUKE Bestraft: %s (%s) - %s", member, user_id, reason)
        except discord.Forbidden:
            logger.warning("Keine Berechtigung zum Bestrafen von %s", member)
        except discord.HTTPException as e:
            logger.error("Bestrafung fehlgeschlagen: %s", e)

    async def _log(self, guild: discord.Guild, embed: discord.Embed):
        cfg = await self._cfg()
        log_id = cfg.get("log_channel_id")
        if not log_id:
            main_cfg = await config_db.get()
            log_id = main_cfg.get("log_channel_id")
        if log_id:
            ch = guild.get_channel(log_id)
            if ch:
                try:
                    await ch.send(embed=embed)
                except discord.HTTPException:
                    pass

    async def _find_audit_user(self, guild: discord.Guild, action_type, target_id: int) -> discord.User | None:
        """Sucht im Audit Log wer die Aktion durchgefuehrt hat."""
        try:
            async for entry in guild.audit_logs(limit=10, action=action_type):
                if entry.target and entry.target.id == target_id:
                    return entry.user
        except discord.Forbidden:
            pass
        return None

    # === CHANNEL CACHE ===

    def _cache_channel(self, channel):
        if isinstance(channel, discord.TextChannel):
            self._channel_cache[channel.id] = {
                "name": channel.name, "type": "text",
                "category_id": channel.category_id, "guild_id": channel.guild.id,
                "topic": channel.topic, "nsfw": channel.nsfw,
                "slowmode": channel.slowmode_delay, "position": channel.position,
            }
        elif isinstance(channel, discord.VoiceChannel):
            self._channel_cache[channel.id] = {
                "name": channel.name, "type": "voice",
                "category_id": channel.category_id, "guild_id": channel.guild.id,
                "position": channel.position, "user_limit": channel.user_limit,
                "bitrate": channel.bitrate,
            }
        elif isinstance(channel, discord.CategoryChannel):
            self._channel_cache[channel.id] = {
                "name": channel.name, "type": "category",
                "guild_id": channel.guild.id, "position": channel.position,
            }

    def _cache_role(self, role: discord.Role):
        self._role_cache[role.id] = {
            "name": role.name, "color": role.color.value,
            "permissions": role.permissions.value, "hoist": role.hoist,
            "mentionable": role.mentionable, "position": role.position,
            "guild_id": role.guild.id,
        }

    # === LISTENERS ===

    @commands.Cog.listener()
    async def on_ready(self):
        for guild in self.bot.guilds:
            for ch in guild.channels:
                self._cache_channel(ch)
            for role in guild.roles:
                self._cache_role(role)
        logger.info("Anti-Nuke: %d Channels, %d Rollen gecacht",
                     len(self._channel_cache), len(self._role_cache))

    @commands.Cog.listener()
    async def on_guild_channel_create(self, channel):
        self._cache_channel(channel)
        cfg = await self._cfg()
        if not cfg.get("enabled"):
            return
        user = await self._find_audit_user(channel.guild, discord.AuditLogAction.channel_create, channel.id)
        if not user or self._protected(user) or user.bot:
            return
        window = cfg.get("webhook_create_window", 30)
        count = self._track(channel.guild.id, user.id, "channel_create", window)
        if count >= 8:
            reason = "Anti-Nuke: Mass Channel Create (%d in %ds)" % (count, window)
            embed = create_embed(
                title="Anti-Nuke: Mass Channel Create",
                description="%s hat %d Channel in %ds erstellt." % (user.mention, count, window),
                color=discord.Color.red(),
                fields=[("User ID", str(user.id), True), ("Aktion", cfg["punishment"].upper(), True)]
            )
            await self._log(channel.guild, embed)
            await self._punish(channel.guild, user.id, reason, cfg)

    @commands.Cog.listener()
    async def on_guild_channel_update(self, before, after):
        self._cache_channel(after)
        if before.overwrites == after.overwrites:
            return
        cfg = await self._cfg()
        if not cfg.get("enabled"):
            return
        user = await self._find_audit_user(after.guild, discord.AuditLogAction.channel_update, after.id)
        if not user or self._protected(user) or user.bot:
            return
        window = cfg.get("perms_edit_window", 30)
        count = self._track(after.guild.id, user.id, "perms_edit", window)
        if count >= cfg.get("max_perms_edits", 3):
            reason = "Anti-Nuke: Mass Permission Edits (%d in %ds)" % (count, window)
            embed = create_embed(
                title="Anti-Nuke: Mass Permission Edits",
                description="%s hat %d mal Permissions geaendert in %ds." % (user.mention, count, window),
                color=discord.Color.red(),
                fields=[("User ID", str(user.id), True), ("Aktion", cfg["punishment"].upper(), True)]
            )
            await self._log(after.guild, embed)
            await self._punish(after.guild, user.id, reason, cfg)

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel):
        self._channel_cache.pop(channel.id, None)
        cfg = await self._cfg()
        if not cfg.get("enabled"):
            return
        user = await self._find_audit_user(channel.guild, discord.AuditLogAction.channel_delete, channel.id)
        if not user or self._protected(user) or user.bot:
            return
        member = channel.guild.get_member(user.id)
        if member and self._excluded(member, cfg):
            return
        window = cfg.get("channel_del_window", 30)
        count = self._track(channel.guild.id, user.id, "channel_del", window)
        if count >= cfg.get("max_channel_dels", 2):
            reason = "Anti-Nuke: Mass Channel Delete (%d in %ds)" % (count, window)
            embed = create_embed(
                title="Anti-Nuke: Channel Destruction",
                description="%s hat %d Channel in %ds geloescht." % (user.mention, count, window),
                color=discord.Color.red(),
                fields=[("User ID", str(user.id), True), ("Aktion", cfg["punishment"].upper(), True)]
            )
            await self._log(channel.guild, embed)
            await self._punish(channel.guild, user.id, reason, cfg)
        else:
            embed = create_embed(
                title="Channel Geloescht",
                description="#%s von %s geloescht." % (channel.name, user.mention),
                color=discord.Color.orange()
            )
            await self._log(channel.guild, embed)

    # === ROLE EVENTS ===

    @commands.Cog.listener()
    async def on_guild_role_create(self, role):
        self._cache_role(role)
        cfg = await self._cfg()
        if not cfg.get("enabled"):
            return
        user = await self._find_audit_user(role.guild, discord.AuditLogAction.role_create, role.id)
        if not user or self._protected(user) or user.bot:
            return
        window = cfg.get("role_create_window", 30)
        count = self._track(role.guild.id, user.id, "role_create", window)
        if count >= cfg.get("max_role_creates", 5):
            reason = "Anti-Nuke: Mass Role Create (%d in %ds)" % (count, window)
            embed = create_embed(
                title="Anti-Nuke: Mass Role Creation",
                description="%s hat %d Rollen in %ds erstellt." % (user.mention, count, window),
                color=discord.Color.red(),
                fields=[("User ID", str(user.id), True), ("Aktion", cfg["punishment"].upper(), True)]
            )
            await self._log(role.guild, embed)
            await self._punish(role.guild, user.id, reason, cfg)

    @commands.Cog.listener()
    async def on_guild_role_update(self, before, after):
        self._cache_role(after)
        # Admin-Rechte entfernt?
        if before.permissions.administrator and not after.permissions.administrator:
            cfg = await self._cfg()
            if cfg.get("enabled"):
                user = await self._find_audit_user(after.guild, discord.AuditLogAction.role_update, after.id)
                if user and not self._protected(user) and not user.bot:
                    embed = create_embed(
                        title="Anti-Nuke: Admin Permissions Entfernt",
                        description="%s hat Admin von Rolle **%s** entfernt." % (user.mention, after.name),
                        color=discord.Color.red(),
                        fields=[("User ID", str(user.id), True)]
                    )
                    await self._log(after.guild, embed)

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role):
        cached = self._role_cache.pop(role.id, None)
        cfg = await self._cfg()
        if not cfg.get("enabled"):
            return
        user = await self._find_audit_user(role.guild, discord.AuditLogAction.role_delete, role.id)
        if not user or self._protected(user) or user.bot:
            return
        member = role.guild.get_member(user.id)
        if member and self._excluded(member, cfg):
            return
        window = cfg.get("role_del_window", 30)
        count = self._track(role.guild.id, user.id, "role_del", window)

        restored = False
        if cached:
            try:
                new_role = await role.guild.create_role(
                    name=cached["name"], color=discord.Color(cached["color"]),
                    permissions=discord.Permissions(cached["permissions"]),
                    hoist=cached["hoist"], mentionable=cached["mentionable"],
                    reason="Anti-Nuke Restore"
                )
                await new_role.edit(position=cached["position"])
                restored = True
            except discord.HTTPException:
                pass

        if count >= cfg.get("max_role_dels", 2):
            reason = "Anti-Nuke: Mass Role Delete (%d in %ds)" % (count, window)
            embed = create_embed(
                title="Anti-Nuke: Mass Role Deletion",
                description="%s hat %d Rolle(n) in %ds geloescht." % (user.mention, count, window),
                color=discord.Color.red(),
                fields=[
                    ("User ID", str(user.id), True),
                    ("Aktion", cfg["punishment"].upper(), True),
                    ("Rolle", role.name, True),
                    ("Restore", "Ja" if restored else "Nein", True),
                ]
            )
            await self._log(role.guild, embed)
            await self._punish(role.guild, user.id, reason, cfg)
        else:
            embed = create_embed(
                title="Rolle Geloescht",
                description="Rolle **%s** von %s geloescht.%s" % (
                    role.name, user.mention, " (Wiederhergestellt)" if restored else ""
                ),
                color=discord.Color.orange()
            )
            await self._log(role.guild, embed)

    # === BAN / KICK ===

    @commands.Cog.listener()
    async def on_member_ban(self, guild, user):
        cfg = await self._cfg()
        if not cfg.get("enabled"):
            return
        mod = await self._find_audit_user(guild, discord.AuditLogAction.ban, user.id)
        if not mod or self._protected(mod) or mod.bot:
            return
        window = cfg.get("ban_window", 30)
        count = self._track(guild.id, mod.id, "ban", window)
        if count >= cfg.get("max_bans", 3):
            reason = "Anti-Nuke: Mass Ban (%d in %ds)" % (count, window)
            embed = create_embed(
                title="Anti-Nuke: Mass Ban",
                description="%s hat %d User in %ds gebannt." % (mod.mention, count, window),
                color=discord.Color.red(),
                fields=[("User ID", str(mod.id), True), ("Aktion", cfg["punishment"].upper(), True)]
            )
            await self._log(guild, embed)
            await self._punish(guild, mod.id, reason, cfg)

    @commands.Cog.listener()
    async def on_member_remove(self, member):
        cfg = await self._cfg()
        if not cfg.get("enabled"):
            return
        mod = await self._find_audit_user(member.guild, discord.AuditLogAction.kick, member.id)
        if not mod or self._protected(mod) or mod.bot:
            return
        window = cfg.get("kick_window", 30)
        count = self._track(member.guild.id, mod.id, "kick", window)
        if count >= cfg.get("max_kicks", 3):
            reason = "Anti-Nuke: Mass Kick (%d in %ds)" % (count, window)
            embed = create_embed(
                title="Anti-Nuke: Mass Kick",
                description="%s hat %d User in %ds gekickt." % (mod.mention, count, window),
                color=discord.Color.red(),
                fields=[("User ID", str(mod.id), True), ("Aktion", cfg["punishment"].upper(), True)]
            )
            await self._log(member.guild, embed)
            await self._punish(member.guild, mod.id, reason, cfg)

    # === WEBHOOK SCHUTZ ===

    @commands.Cog.listener()
    async def on_webhook_update(self, guild, channel=None):
        cfg = await self._cfg()
        if not cfg.get("enabled"):
            return
        try:
            async for entry in guild.audit_logs(limit=5, action=discord.AuditLogAction.webhook_create):
                user = entry.user
                if not user or self._protected(user) or user.bot:
                    return
                window = cfg.get("webhook_create_window", 30)
                count = self._track(guild.id, user.id, "webhook_create", window)
                if count >= cfg.get("max_webhook_creates", 3):
                    reason = "Anti-Nuke: Mass Webhook Create (%d in %ds)" % (count, window)
                    embed = create_embed(
                        title="Anti-Nuke: Mass Webhook Creation",
                        description="%s hat %d Webhooks in %ds erstellt." % (user.mention, count, window),
                        color=discord.Color.red(),
                        fields=[("User ID", str(user.id), True), ("Aktion", cfg["punishment"].upper(), True)]
                    )
                    await self._log(guild, embed)
                    await self._punish(guild, user.id, reason, cfg)
                return
        except discord.Forbidden:
            pass

    # === SERVER SETTINGS SCHUTZ ===

    @commands.Cog.listener()
    async def on_guild_update(self, before, after):
        cfg = await self._cfg()
        if not cfg.get("enabled"):
            return
        changes = []
        if before.name != after.name:
            changes.append("Name: %s -> %s" % (before.name, after.name))
        if before.icon != after.icon:
            changes.append("Icon geaendert")
        if before.vanity_url_code != after.vanity_url_code:
            changes.append("Vanity geaendert")
        if before.splash != after.splash:
            changes.append("Splash geaendert")
        if before.banner != after.banner:
            changes.append("Banner geaendert")
        if not changes:
            return
        user = await self._find_audit_user(after.guild, discord.AuditLogAction.guild_update, after.id)
        if not user or self._protected(user) or user.bot:
            return
        embed = create_embed(
            title="Server Einstellungen Geaendert",
            description="%s hat Server-Einstellungen geaendert:" % user.mention,
            color=discord.Color.orange(),
            fields=[("Aenderungen", "\n".join(changes), False), ("User ID", str(user.id), True)]
        )
        await self._log(after.guild, embed)

    # === SLASH COMMANDS ===

    @anti_nuke.command(name="status", description="Anti-Nuke Status anzeigen")
    async def antinuke_status(self, interaction: discord.Interaction):
        if not is_owner(interaction.user) and not is_dev(interaction.user):
            await interaction.response.send_message("Nur Owner/Dev.", ephemeral=True)
            return
        cfg = await self._cfg()
        state = "Aktiv" if cfg.get("enabled") else "Deaktiviert"
        embed = create_embed(
            title="Anti-Nuke System",
            color=discord.Color.green() if cfg.get("enabled") else discord.Color.red(),
            fields=[
                ("Status", state, True),
                ("Channel Delete", "%d in %ds" % (cfg["max_channel_dels"], cfg["channel_del_window"]), True),
                ("Role Delete", "%d in %ds" % (cfg["max_role_dels"], cfg["role_del_window"]), True),
                ("Role Create", "%d in %ds" % (cfg["max_role_creates"], cfg["role_create_window"]), True),
                ("Bans", "%d in %ds" % (cfg["max_bans"], cfg["ban_window"]), True),
                ("Kicks", "%d in %ds" % (cfg["max_kicks"], cfg["kick_window"]), True),
                ("Perm Edits", "%d in %ds" % (cfg["max_perms_edits"], cfg["perms_edit_window"]), True),
                ("Webhooks", "%d in %ds" % (cfg["max_webhook_creates"], cfg["webhook_create_window"]), True),
                ("Bestrafung", cfg["punishment"].upper(), True),
            ]
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @anti_nuke.command(name="toggle", description="Anti-Nuke an/aus")
    async def antinuke_toggle(self, interaction: discord.Interaction):
        if not is_owner(interaction.user) and not is_dev(interaction.user):
            await interaction.response.send_message("Nur Owner/Dev.", ephemeral=True)
            return
        cfg = await self._cfg()
        new = not cfg.get("enabled", True)

        def _t(d):
            d["enabled"] = new
            return d
        await anti_nuke_db.modify(_t)
        await interaction.response.send_message(
            embed=success_embed("Anti-Nuke %s" % ("aktiviert" if new else "deaktiviert")),
            ephemeral=True
        )

    @anti_nuke.command(name="punish", description="Bestrafung setzen (ban/kick)")
    @app_commands.describe(mode="ban oder kick")
    async def antinuke_punish(self, interaction: discord.Interaction, mode: str):
        if not is_owner(interaction.user) and not is_dev(interaction.user):
            await interaction.response.send_message("Nur Owner/Dev.", ephemeral=True)
            return
        mode = mode.lower()
        if mode not in ("ban", "kick"):
            await interaction.response.send_message("Nur ban oder kick.", ephemeral=True)
            return

        def _s(d):
            d["punishment"] = mode
            return d
        await anti_nuke_db.modify(_s)
        await interaction.response.send_message(
            embed=success_embed("Bestrafung: %s" % mode.upper()), ephemeral=True
        )

    @anti_nuke.command(name="threshold", description="Schwellwerte anpassen")
    @app_commands.describe(
        action="channel_del, role_del, role_create, ban, kick, perms_edit, webhook_create",
        count="Max Anzahl", window="Zeitfenster (Sekunden)"
    )
    async def antinuke_threshold(
        self, interaction: discord.Interaction,
        action: str, count: int, window: int
    ):
        if not is_owner(interaction.user) and not is_dev(interaction.user):
            await interaction.response.send_message("Nur Owner/Dev.", ephemeral=True)
            return
        keys = {
            "channel_del": ("max_channel_dels", "channel_del_window"),
            "role_del": ("max_role_dels", "role_del_window"),
            "role_create": ("max_role_creates", "role_create_window"),
            "ban": ("max_bans", "ban_window"),
            "kick": ("max_kicks", "kick_window"),
            "perms_edit": ("max_perms_edits", "perms_edit_window"),
            "webhook_create": ("max_webhook_creates", "webhook_create_window"),
        }
        action = action.lower()
        if action not in keys:
            await interaction.response.send_message(
                "Gueltig: %s" % ", ".join(keys.keys()), ephemeral=True
            )
            return
        ck, wk = keys[action]

        def _s(d):
            d[ck] = count
            d[wk] = window
            return d
        await anti_nuke_db.modify(_s)
        await interaction.response.send_message(
            embed=success_embed("%s: Max %d in %ds" % (action, count, window)),
            ephemeral=True
        )

    @anti_nuke.command(name="exclude", description="Rolle vom Anti-Nuke ausschliessen")
    @app_commands.describe(role="Rolle")
    async def antinuke_exclude(self, interaction: discord.Interaction, role: discord.Role):
        if not is_owner(interaction.user) and not is_dev(interaction.user):
            await interaction.response.send_message("Nur Owner/Dev.", ephemeral=True)
            return

        def _a(d):
            excl = d.get("excluded_roles", [])
            if role.id not in excl:
                excl.append(role.id)
            d["excluded_roles"] = excl
            return d
        await anti_nuke_db.modify(_a)
        await interaction.response.send_message(
            embed=success_embed("%s ausgeschlossen." % role.mention), ephemeral=True
        )

    @anti_nuke.command(name="include", description="Rolle wieder einschliessen")
    @app_commands.describe(role="Rolle")
    async def antinuke_include(self, interaction: discord.Interaction, role: discord.Role):
        if not is_owner(interaction.user) and not is_dev(interaction.user):
            await interaction.response.send_message("Nur Owner/Dev.", ephemeral=True)
            return

        def _r(d):
            excl = d.get("excluded_roles", [])
            if role.id in excl:
                excl.remove(role.id)
            d["excluded_roles"] = excl
            return d
        await anti_nuke_db.modify(_r)
        await interaction.response.send_message(
            embed=success_embed("%s wieder eingeschlossen." % role.mention), ephemeral=True
        )

    @anti_nuke.command(name="reset", description="Alle Tracking-Daten zuruecksetzen")
    async def antinuke_reset(self, interaction: discord.Interaction):
        if not is_owner(interaction.user) and not is_dev(interaction.user):
            await interaction.response.send_message("Nur Owner/Dev.", ephemeral=True)
            return
        self._actions.clear()
        self._punished.clear()
        await interaction.response.send_message(
            embed=success_embed("Tracking-Daten zurueckgesetzt."), ephemeral=True
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(AntiNukeCog(bot))

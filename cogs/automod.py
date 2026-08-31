import re
import time
from datetime import datetime, timezone, timedelta

import discord
from discord.ext import commands
from discord import app_commands

from config import config_db, automod_db
from config.settings import settings
from utils import require_authorized, success_embed, error_embed, info_embed
from utils.embeds import automod_embed

_ACTIONS = {
    "delete": "🗑️ Löschen",
    "timeout": "⏱️ Timeout",
    "warn": "⚠️ Warnen",
    "kick": "👢 Kicken",
    "ban": "🔨 Bannen",
}

_FILTER_NAMES = {
    "links": "🔗 Link-Filter",
    "invites": "💌 Einladungs-Filter",
    "caps": "🔠 Großschreibungs-Filter",
    "spam": "🌀 Spam-Filter",
    "mentions": "📣 Erwähnungs-Filter",
}

_URL_RE = re.compile(r"(?:https?://|www\.)(?:[a-z0-9-]+\.)+[a-z]{2,}", re.IGNORECASE)
_INVITE_RE = re.compile(r"(?:discord\.gg/|discord\.com/invite/|discordapp\.com/invite/)\S+", re.IGNORECASE)
_CLEANUP_INTERVAL = 600


class AutoModCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._compiled_rules: list[tuple[re.Pattern, dict]] = []
        self._spam_history: dict[tuple[int, int], list[float]] = {}
        self._last_cleanup = time.time()

    async def cog_load(self):
        await self._compile_rules()

    async def _compile_rules(self):
        data = await automod_db.get()
        self._compiled_rules = []
        for rule in data.get("rules", []):
            words = rule.get("words", [])
            if words:
                pattern = re.compile(r"\b(" + "|".join(re.escape(w) for w in words) + r")\b", re.IGNORECASE)
                self._compiled_rules.append((pattern, rule))

    @staticmethod
    def _parse_actions(aktion: str) -> list[str] | None:
        actions = [a.strip().lower() for a in aktion.split(",") if a.strip()]
        if not actions or any(a not in _ACTIONS for a in actions):
            return None
        return actions

    # ─────────────────────────── Commands ───────────────────────────

    automod_group = app_commands.Group(name="automod", description="AutoMod-Einstellungen")

    @automod_group.command(name="toggle", description="AutoMod global ein- oder ausschalten")
    @app_commands.describe(enabled="AutoMod aktivieren?")
    async def toggle(self, interaction: discord.Interaction, enabled: bool):
        if not await require_authorized(interaction):
            return

        def _set(data):
            data["enabled"] = enabled
            return data

        await automod_db.modify(_set)
        await interaction.response.send_message(
            embed=success_embed(f"🛡️ AutoMod ist jetzt **{'aktiviert' if enabled else 'deaktiviert'}**"),
            ephemeral=True
        )

    @automod_group.command(name="word", description="Verbotene Wörter hinzufügen")
    @app_commands.describe(
        verboten="Wörter, komma-getrennt",
        aktion="Aktion(en), komma-getrennt (delete, timeout, warn, kick, ban)",
        dauer="Dauer in Minuten (nur bei Timeout)"
    )
    async def word(self, interaction: discord.Interaction, verboten: str, aktion: str, dauer: int = 10):
        if not await require_authorized(interaction):
            return

        words = [w.strip().lower() for w in verboten.split(",") if w.strip()]
        if not words:
            await interaction.response.send_message(
                embed=error_embed("Keine gültigen Wörter angegeben"),
                ephemeral=True
            )
            return

        actions = self._parse_actions(aktion)
        if not actions:
            await interaction.response.send_message(
                embed=error_embed("Ungültige Aktion(en). Erlaubt: delete, timeout, warn, kick, ban (komma-getrennt)"),
                ephemeral=True
            )
            return
        if "timeout" in actions and not (1 <= dauer <= 40320):
            await interaction.response.send_message(
                embed=error_embed("Timeout-Dauer muss zwischen 1 und 40320 Minuten liegen."),
                ephemeral=True
            )
            return

        def _add(data):
            data["rules"].append({
                "words": words,
                "action": aktion,
                "actions": actions,
                "duration": dauer if "timeout" in actions else 0
            })
            return data

        await automod_db.modify(_add)
        await self._compile_rules()

        labels = " + ".join(_ACTIONS[a] for a in actions)
        text = f"**Wörter:** {', '.join(f'`{w}`' for w in words)}\n**Aktion:** {labels}"
        if "timeout" in actions:
            text += f"\n**Dauer:** {dauer} min"
        await interaction.response.send_message(embed=success_embed(text), ephemeral=True)

    @automod_group.command(name="remove", description="Wort-Regel entfernen")
    @app_commands.describe(index="Regel-Nummer (aus /automod list)")
    async def remove(self, interaction: discord.Interaction, index: int):
        if not await require_authorized(interaction):
            return

        data = await automod_db.get()
        rules = data.get("rules", [])
        if index < 1 or index > len(rules):
            await interaction.response.send_message(
                embed=error_embed("Ungültige Regel-Nummer"),
                ephemeral=True
            )
            return

        removed = rules.pop(index - 1)
        await automod_db.save(data)
        await self._compile_rules()

        await interaction.response.send_message(
            embed=success_embed(f"Regel entfernt: {', '.join(f'`{w}`' for w in removed['words'])}"),
            ephemeral=True
        )

    @automod_group.command(name="list", description="AutoMod-Übersicht anzeigen")
    async def list_rules(self, interaction: discord.Interaction):
        if not await require_authorized(interaction):
            return

        data = await automod_db.get()
        embed = discord.Embed(title="🛡️ AutoMod-Übersicht", color=discord.Color.blue())

        embed.add_field(
            name="Status",
            value="🟢 Aktiv" if data.get("enabled", True) else "🔴 Deaktiviert",
            inline=False
        )

        rules = data.get("rules", [])
        if rules:
            lines = []
            for i, rule in enumerate(rules, 1):
                actions = rule.get("actions") or [rule.get("action", "delete")]
                labels = " + ".join(_ACTIONS.get(a, a) for a in actions)
                dur = f" ({rule.get('duration', 10)} min)" if "timeout" in actions else ""
                lines.append(f"`#{i}` **{labels}{dur}** • {', '.join(f'`{w}`' for w in rule['words'])}")
            embed.add_field(name=f"📝 Wort-Regeln ({len(rules)})", value="\n".join(lines), inline=False)
        else:
            embed.add_field(name="📝 Wort-Regeln", value="Keine", inline=False)

        filters = data.get("filters", {})
        filter_lines = []
        for key, cfg in filters.items():
            state = "🟢" if cfg.get("enabled") else "⚫"
            filter_lines.append(f"{state} **{_FILTER_NAMES.get(key, key)}**")
        embed.add_field(name="⚙️ Filter", value="\n".join(filter_lines) or "Keine", inline=False)

        allowed = filters.get("links", {}).get("allowed_domains", [])
        embed.add_field(
            name="🌐 Erlaubte Domains",
            value=", ".join(f"`{d}`" for d in allowed) if allowed else "Keine",
            inline=False
        )

        whitelist = data.get("whitelist_roles", [])
        roles = [interaction.guild.get_role(rid) for rid in whitelist if interaction.guild.get_role(rid)]
        embed.add_field(
            name="🛡️ Ausgenommene Rollen",
            value=", ".join(r.mention for r in roles) if roles else "Keine",
            inline=False
        )

        await interaction.response.send_message(embed=embed, ephemeral=True)

    @automod_group.command(name="exempt", description="Rolle von AutoMod ausnehmen (mit Rollen-Namensauswahl)")
    @app_commands.describe(role="Rolle, die ausgenommen werden soll")
    async def exempt(self, interaction: discord.Interaction, role: discord.Role):
        if not await require_authorized(interaction):
            return

        data = await automod_db.get()
        whitelist = data.get("whitelist_roles", [])
        was_present = role.id in whitelist

        def _toggle(d):
            wl = d.setdefault("whitelist_roles", [])
            if role.id in wl:
                wl.remove(role.id)
            else:
                wl.append(role.id)
            d["whitelist_roles"] = wl
            return d

        await automod_db.modify(_toggle)
        verb = "von AutoMod ausgenommen" if not was_present else "wieder überwacht"
        await interaction.response.send_message(
            embed=success_embed(f"Rolle {role.mention} wird jetzt **{verb}**"),
            ephemeral=True
        )

    @automod_group.command(name="links", description="Link-Filter konfigurieren")
    @app_commands.describe(enabled="Filter aktivieren?", aktion="Aktion(en), komma-getrennt (delete, timeout, warn, kick, ban)", dauer="Dauer in Minuten (nur bei Timeout)")
    async def links(self, interaction: discord.Interaction, enabled: bool, aktion: str = "delete", dauer: int = 10):
        await self._configure_filter(interaction, "links", enabled, aktion, dauer)

    @automod_group.command(name="invites", description="Discord-Einladungs-Filter konfigurieren")
    @app_commands.describe(enabled="Filter aktivieren?", aktion="Aktion(en), komma-getrennt (delete, timeout, warn, kick, ban)", dauer="Dauer in Minuten (nur bei Timeout)")
    async def invites(self, interaction: discord.Interaction, enabled: bool, aktion: str = "delete", dauer: int = 10):
        await self._configure_filter(interaction, "invites", enabled, aktion, dauer)

    @automod_group.command(name="caps", description="Großschreibungs-Filter konfigurieren")
    @app_commands.describe(
        enabled="Filter aktivieren?",
        threshold="% Großbuchstaben ab dem gefiltert wird",
        min_length="Mindestlänge der Nachricht",
        aktion="Aktion(en), komma-getrennt (delete, timeout, warn, kick, ban)",
        dauer="Dauer in Minuten (nur bei Timeout)"
    )
    async def caps(self, interaction: discord.Interaction, enabled: bool, threshold: int = 70,
                   min_length: int = 8, aktion: str = "delete", dauer: int = 10):
        if not (1 <= threshold <= 100 and 1 <= min_length <= 200):
            await interaction.response.send_message(
                embed=error_embed("threshold muss zwischen 1–100 und min_length zwischen 1–200 liegen"),
                ephemeral=True
            )
            return
        await self._configure_filter(interaction, "caps", enabled, aktion, dauer,
                                     threshold=threshold, min_length=min_length)

    @automod_group.command(name="spam", description="Spam-Filter konfigurieren")
    @app_commands.describe(
        enabled="Filter aktivieren?",
        max_nachrichten="Max. Nachrichten im Zeitfenster",
        sekunden="Zeitfenster in Sekunden",
        aktion="Aktion(en), komma-getrennt (delete, timeout, warn, kick, ban)",
        dauer="Dauer in Minuten (nur bei Timeout)"
    )
    async def spam(self, interaction: discord.Interaction, enabled: bool, max_nachrichten: int = 5,
                   sekunden: int = 5, aktion: str = "timeout", dauer: int = 10):
        if not (1 <= max_nachrichten <= 50 and 1 <= sekunden <= 600):
            await interaction.response.send_message(
                embed=error_embed("max_nachrichten muss zwischen 1–50 und sekunden zwischen 1–600 liegen"),
                ephemeral=True
            )
            return
        await self._configure_filter(interaction, "spam", enabled, aktion, dauer,
                                     max_messages=max_nachrichten, seconds=sekunden)

    @automod_group.command(name="mentions", description="Massen-Erwähnungs-Filter konfigurieren")
    @app_commands.describe(
        enabled="Filter aktivieren?",
        max_erwaehnungen="Max. Erwähnungen pro Nachricht",
        aktion="Aktion(en), komma-getrennt (delete, timeout, warn, kick, ban)",
        dauer="Dauer in Minuten (nur bei Timeout)"
    )
    async def mentions(self, interaction: discord.Interaction, enabled: bool,
                       max_erwaehnungen: int = 5, aktion: str = "timeout", dauer: int = 5):
        if not (1 <= max_erwaehnungen <= 100):
            await interaction.response.send_message(
                embed=error_embed("max_erwaehnungen muss zwischen 1–100 liegen"),
                ephemeral=True
            )
            return
        await self._configure_filter(interaction, "mentions", enabled, aktion, dauer,
                                     max_mentions=max_erwaehnungen)

    @automod_group.command(name="allow", description="Domain für den Link-Filter erlauben")
    @app_commands.describe(domain="z.B. youtube.com")
    async def allow(self, interaction: discord.Interaction, domain: str):
        if not await require_authorized(interaction):
            return

        domain = domain.strip().lower()
        for prefix in ("https://", "http://", "www."):
            if domain.startswith(prefix):
                domain = domain[len(prefix):]
                break
        domain = domain.split("/")[0].split("?")[0].rstrip(".")
        if not domain or "." not in domain:
            await interaction.response.send_message(
                embed=error_embed("Ungültige Domain"),
                ephemeral=True
            )
            return

        def _add(d):
            allowed = d["filters"].setdefault("links", {}).setdefault("allowed_domains", [])
            if domain not in allowed:
                allowed.append(domain)
            return d

        await automod_db.modify(_add)
        await interaction.response.send_message(
            embed=success_embed(f"Domain `{domain}` wird erlaubt"),
            ephemeral=True
        )

    @automod_group.command(name="disallow", description="Domain aus der Whitelist des Link-Filters entfernen")
    @app_commands.describe(domain="z.B. youtube.com")
    async def disallow(self, interaction: discord.Interaction, domain: str):
        if not await require_authorized(interaction):
            return

        domain = domain.strip().lower()
        for prefix in ("https://", "http://", "www."):
            if domain.startswith(prefix):
                domain = domain[len(prefix):]
                break
        domain = domain.split("/")[0].split("?")[0].rstrip(".")
        removed = None

        def _remove(d):
            nonlocal removed
            allowed = d["filters"].setdefault("links", {}).setdefault("allowed_domains", [])
            if domain in allowed:
                allowed.remove(domain)
                removed = domain
            return d

        await automod_db.modify(_remove)
        if removed:
            await interaction.response.send_message(
                embed=success_embed(f"Domain `{removed}` wird wieder gefiltert"),
                ephemeral=True
            )
        else:
            await interaction.response.send_message(
                embed=info_embed(f"Domain `{domain}` war nicht in der Whitelist"),
                ephemeral=True
            )

    async def _configure_filter(self, interaction: discord.Interaction, key: str,
                                enabled: bool, aktion: str, dauer: int, **extra):
        if not await require_authorized(interaction):
            return
        actions = self._parse_actions(aktion)
        if not actions:
            await interaction.response.send_message(
                embed=error_embed("Ungültige Aktion(en). Erlaubt: delete, timeout, warn, kick, ban (komma-getrennt)"),
                ephemeral=True
            )
            return
        if "timeout" in actions and not (1 <= dauer <= 40320):
            await interaction.response.send_message(
                embed=error_embed("Timeout-Dauer muss zwischen 1 und 40320 Minuten liegen."),
                ephemeral=True
            )
            return

        def _set(data):
            data.setdefault("filters", {}).setdefault(key, {})
            data["filters"][key].update({
                "enabled": enabled,
                "action": aktion,
                "actions": actions,
                "duration": dauer if "timeout" in actions else 0,
                **extra
            })
            return data

        await automod_db.modify(_set)
        labels = " + ".join(_ACTIONS[a] for a in actions)
        text = f"{_FILTER_NAMES[key]} **{'🟢 aktiviert' if enabled else '🔴 deaktiviert'}**\n**Aktion:** {labels}"
        if "timeout" in actions:
            text += f"\n**Dauer:** {dauer} min"
        await interaction.response.send_message(embed=success_embed(text), ephemeral=True)

    # ─────────────────────────── Message-Check ───────────────────────────

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild or not message.content:
            return

        if await self._is_exempt(message.author):
            return

        data = await automod_db.get()
        if not data.get("enabled", True):
            return

        result = self._check_rules(message, data)
        if result:
            reason, action, duration = result
            await self._apply_action(message, action, duration, reason)

    def _check_rules(self, message: discord.Message, data: dict):
        for pattern, rule in self._compiled_rules:
            match = pattern.search(message.content)
            if match:
                actions = rule.get("actions") or [rule.get("action", "delete")]
                return f"Verbotenes Wort '{match.group(0)}'", actions, rule.get("duration", 10)

        filters = data.get("filters", {})
        now = time.time()

        links = filters.get("links", {})
        if links.get("enabled"):
            match = _URL_RE.search(message.content)
            if match:
                domain = match.group(0).lower()
                for prefix in ("https://", "http://", "www."):
                    if domain.startswith(prefix):
                        domain = domain[len(prefix):]
                        break
                if domain.startswith("www."):
                    domain = domain[4:]
                allowed = links.get("allowed_domains", [])
                if domain not in allowed and not any(domain.endswith("." + a) for a in allowed):
                    actions = links.get("actions") or [links["action"]]
                    return f"Unerlaubter Link `{domain}`", actions, links.get("duration", 10)

        invites = filters.get("invites", {})
        if invites.get("enabled") and _INVITE_RE.search(message.content):
            actions = invites.get("actions") or [invites["action"]]
            return "Discord-Einladung", actions, invites.get("duration", 10)

        mentions = filters.get("mentions", {})
        if mentions.get("enabled") and len(message.mentions) >= mentions.get("max_mentions", 5):
            actions = mentions.get("actions") or [mentions["action"]]
            return f"Massen-Erwähnung ({len(message.mentions)})", actions, mentions.get("duration", 5)

        caps = filters.get("caps", {})
        if caps.get("enabled"):
            letters = [c for c in message.content if c.isalpha()]
            min_len = caps.get("min_length", 8)
            if len(letters) >= min_len:
                upper = sum(1 for c in letters if c.isupper())
                if upper / len(letters) * 100 >= caps.get("threshold", 70):
                    actions = caps.get("actions") or [caps["action"]]
                    return "Großschreibung (ALL-CAPS)", actions, caps.get("duration", 10)

        spam = filters.get("spam", {})
        if spam.get("enabled"):
            key = (message.author.id, message.channel.id)
            window = spam.get("seconds", 5)
            history = [t for t in self._spam_history.get(key, []) if now - t <= window]
            history.append(now)
            self._spam_history[key] = history
            if len(history) >= spam.get("max_messages", 5):
                self._spam_history[key] = []
                actions = spam.get("actions") or [spam["action"]]
                return "Spam / Flood", actions, spam.get("duration", 10)

        if now - self._last_cleanup > _CLEANUP_INTERVAL:
            self._last_cleanup = now
            cutoff = now - 600
            self._spam_history = {k: v for k, v in self._spam_history.items() if v and v[-1] >= cutoff}

        return None

    async def _is_exempt(self, member: discord.Member) -> bool:
        if member.id in settings.OWNER_IDS or member.id in settings.DEV_IDS:
            return True

        config = await config_db.get()
        admin_roles = config.get("admin_roles", [])
        if any(r.id in admin_roles for r in member.roles):
            return True

        if member.guild_permissions.administrator:
            return True

        data = await automod_db.get()
        whitelist = data.get("whitelist_roles", [])
        if any(r.id in whitelist for r in member.roles):
            return True

        return False

    async def _apply_action(self, message: discord.Message, action, duration_minutes: int, reason: str):
        if isinstance(action, str):
            actions = [a.strip().lower() for a in action.split(",") if a.strip()]
        else:
            actions = list(action)
        duration = duration_minutes or 10
        labels = []
        deleted = False

        config = await config_db.get()
        log_channel_id = config.get("log_channel_id")
        log_channel = message.guild.get_channel(log_channel_id) if log_channel_id else None

        async def _delete():
            nonlocal deleted
            if not deleted:
                await message.delete()
                deleted = True

        try:
            for act in actions:
                if act == "delete":
                    await _delete()
                    labels.append("löschen")

                elif act == "timeout":
                    await _delete()
                    await message.author.timeout(
                        datetime.now(timezone.utc) + timedelta(minutes=duration),
                        reason=f"AutoMod: {reason}"
                    )
                    labels.append(f"timeout ({duration}min)")
                    try:
                        await message.author.send(f"⏱️ Du wurdest für {duration} Minuten getimeout. Grund: {reason}")
                    except discord.HTTPException:
                        pass

                elif act == "warn":
                    await _delete()
                    labels.append("warnen")
                    try:
                        await message.author.send(
                            f"⚠️ Deine Nachricht in {message.guild.name} wurde gelöscht. Grund: {reason}"
                        )
                    except discord.HTTPException:
                        pass

                elif act == "kick":
                    await _delete()
                    await message.author.kick(reason=f"AutoMod: {reason}")
                    labels.append("kick")
                    try:
                        await message.author.send(f"👢 Du wurdest aus {message.guild.name} gekickt. Grund: {reason}")
                    except discord.HTTPException:
                        pass

                elif act == "ban":
                    await _delete()
                    await message.author.ban(reason=f"AutoMod: {reason}")
                    labels.append("ban")

            if log_channel:
                await log_channel.send(
                    embed=automod_embed(" + ".join(labels), message.author, message.channel, reason, message.content)
                )

        except discord.Forbidden as e:
            print(f"AutoMod Fehler (fehlende Berechtigungen): {e}")
        except discord.HTTPException as e:
            print(f"AutoMod Fehler: {e}")


async def setup(bot: commands.Bot):
    await bot.add_cog(AutoModCog(bot))

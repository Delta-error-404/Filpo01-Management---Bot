import discord
from discord.ext import commands
from discord import app_commands
from config import notifications_db
from utils import require_authorized, success_embed, error_embed, info_embed
from utils.embeds import notification_embed
import aiohttp
import feedparser
from datetime import datetime

PANEL_KEYS = {
    "twitch": "Twitch",
    "yt": "YouTube",
    "tt": "TikTok",
    "giveaway": "Giveaway",
}
PANEL_EMOJI = {
    "twitch": "📺",
    "yt": "▶️",
    "tt": "🎵",
    "giveaway": "🎉",
}
PANEL_STYLE = {
    "twitch": discord.ButtonStyle.primary,
    "yt": discord.ButtonStyle.danger,
    "tt": discord.ButtonStyle.secondary,
    "giveaway": discord.ButtonStyle.success,
}

PANEL_INFO = {
    "twitch": "Info, wenn ein Streamer live geht",
    "yt": "Info bei neuen Videos",
    "tt": "Info bei neuen Posts",
    "giveaway": "Info bei neuen Gewinnspielen",
}

def parse_panel_keys(text: str) -> list[str]:
    if not text or not text.strip():
        return list(PANEL_KEYS.keys())
    mapping = {name.lower(): key for key, name in PANEL_KEYS.items()}
    keys = []
    for part in text.replace(",", " ").split():
        key = mapping.get(part.strip().lower())
        if key and key not in keys:
            keys.append(key)
    return keys or list(PANEL_KEYS.keys())

async def _get_panel_role(guild: discord.Guild, key: str) -> discord.Role | None:
    data = await notifications_db.get()
    role_id = data.get("panel_roles", {}).get(str(guild.id), {}).get(key)
    if role_id:
        return guild.get_role(role_id)
    return None

async def _ensure_panel_role(guild: discord.Guild, key: str) -> discord.Role | None:
    role = await _get_panel_role(guild, key)
    if role:
        return role
    name = PANEL_KEYS.get(key)
    if not name:
        return None
    role = discord.utils.get(guild.roles, name=name)
    if role is None:
        try:
            role = await guild.create_role(name=name, mentionable=True, reason="Benachrichtigungs-Rolle")
        except discord.HTTPException:
            return None
    def _save(data):
        data.setdefault("panel_roles", {}).setdefault(str(guild.id), {})[key] = role.id
        return data
    await notifications_db.modify(_save)
    return role

def build_panel_embed(keys: list[str] | None = None) -> discord.Embed:
    if keys is None:
        keys = list(PANEL_KEYS.keys())
    embed = discord.Embed(
        title="🔔 Benachrichtigungen",
        description="Wähle, für welche Themen du benachrichtigt werden möchtest.\nKlicke auf einen Button, um die passende Rolle zu erhalten – oder klicke erneut, um sie zu entfernen.",
        color=discord.Color.blue()
    )
    for key in keys:
        if key in PANEL_KEYS:
            embed.add_field(
                name=f"{PANEL_EMOJI[key]} {PANEL_KEYS[key]}",
                value=PANEL_INFO.get(key, ""),
                inline=True
            )
    embed.set_footer(text="Button-Klick = Rolle an/aus")
    return embed

class NotifyPanelButton(discord.ui.Button):
    def __init__(self, key: str):
        super().__init__(
            label=PANEL_KEYS[key],
            emoji=PANEL_EMOJI[key],
            style=PANEL_STYLE[key],
            custom_id=f"notify_panel:{key}"
        )
        self.key = key

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        member = interaction.user
        if not guild or not isinstance(member, discord.Member):
            await interaction.followup.send("Dieser Button funktioniert nur auf einem Server.", ephemeral=True)
            return
        role = await _ensure_panel_role(guild, self.key)
        if role is None:
            await interaction.followup.send("Rolle konnte nicht erstellt werden (fehlende Berechtigungen?).", ephemeral=True)
            return
        if role in member.roles:
            await member.remove_roles(role, reason="Benachrichtigung deaktiviert")
            text = f"Du bekommst ab jetzt **keine** {PANEL_EMOJI[self.key]} {PANEL_KEYS[self.key]}-Benachrichtigungen mehr."
        else:
            await member.add_roles(role, reason="Benachrichtigung aktiviert")
            text = f"✅ Du bekommst ab jetzt {PANEL_EMOJI[self.key]} **{PANEL_KEYS[self.key]}**-Benachrichtigungen!"
        await interaction.followup.send(text, ephemeral=True)

class NotifyPanelView(discord.ui.View):
    def __init__(self, keys: list[str] | None = None):
        super().__init__(timeout=None)
        if keys is None:
            keys = list(PANEL_KEYS.keys())
        for key in keys:
            if key in PANEL_KEYS:
                self.add_item(NotifyPanelButton(key))

class NotificationsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.session: aiohttp.ClientSession | None = None
        self.twitch_token: str = ""
        self.twitch_token_expires: float = 0

    async def cog_load(self):
        self.session = aiohttp.ClientSession()
        data = await notifications_db.get()
        self.twitch_token = data.get("twitch_token", {}).get("access_token", "")
        self.twitch_token_expires = data.get("twitch_token", {}).get("expires_at", 0)
        self.bot.add_view(NotifyPanelView())

    async def cog_unload(self):
        if self.session:
            await self.session.close()

    notify_group = app_commands.Group(name="notify", description="Benachrichtigungen verwalten")
    panel_group = app_commands.Group(name="notifypanel", description="Benachrichtigungs-Panel verwalten")

    @panel_group.command(name="create", description="Sendet das Benachrichtigungs-Panel in einen Channel")
    @app_commands.describe(
        channel="Channel, in das Panel gesendet werden soll",
        buttons="Buttons, komma-getrennt, z.B.: Twitch, YouTube, TikTok, Giveaway (leer = alle)"
    )
    async def panel_create(self, interaction: discord.Interaction, channel: discord.TextChannel, buttons: str = ""):
        if not await require_authorized(interaction):
            return
        keys = parse_panel_keys(buttons)
        view = NotifyPanelView(keys)
        await channel.send(embed=build_panel_embed(keys), view=view)
        auswahl = ", ".join(f"{PANEL_EMOJI[k]} {PANEL_KEYS[k]}" for k in keys)
        await interaction.response.send_message(
            embed=success_embed(f"✅ Benachrichtigungs-Panel in {channel.mention} gesendet.\n**Buttons:** {auswahl}"),
            ephemeral=True
        )

    @notify_group.command(name="add", description="Benachrichtigung hinzufügen")
    @app_commands.describe(
        art="Plattform: twitch, yt, tt",
        username="Twitch-Username / YouTube Channel-ID / TikTok-Username",
        channel="Discord-Channel für Benachrichtigungen"
    )
    @app_commands.choices(art=[
        app_commands.Choice(name="Twitch", value="twitch"),
        app_commands.Choice(name="YouTube", value="yt"),
        app_commands.Choice(name="TikTok", value="tt")
    ])
    async def notify_add(self, interaction: discord.Interaction, art: str, username: str, channel: discord.TextChannel):
        if not await require_authorized(interaction):
            return
        
        username = username.strip()
        if not username:
            await interaction.response.send_message(
                embed=error_embed("Username/Channel-ID darf nicht leer sein"),
                ephemeral=True
            )
            return
        
        data = await notifications_db.get()
        subs = data.get("subscriptions", [])
        
        sub_id = max([s["id"] for s in subs], default=0) + 1
        
        new_sub = {
            "id": sub_id,
            "type": art,
            "username": username,
            "channel_id": channel.id,
            "last_id": None
        }

        if art == "yt":
            new_sub["channel_id_yt"] = username
            del new_sub["username"]

        target = new_sub.get("username") or new_sub.get("channel_id_yt")
        for existing in subs:
            existing_target = existing.get("username") or existing.get("channel_id_yt")
            if (existing.get("type") == art
                    and existing_target == target
                    and existing.get("channel_id") == channel.id):
                await interaction.response.send_message(
                    embed=error_embed(f"Diese Benachrichtigung existiert bereits (ID **{existing['id']}**)"),
                    ephemeral=True
                )
                return

        subs.append(new_sub)
        await notifications_db.save({"subscriptions": subs, "twitch_token": data.get("twitch_token", {})})
        
        await interaction.response.send_message(
            embed=success_embed(f"✅ Benachrichtigung hinzugefügt:\n**Plattform:** {art.upper()}\n**Ziel:** {username}\n**Channel:** {channel.mention}\n**ID:** {sub_id}"),
            ephemeral=True
        )

    @notify_group.command(name="remove", description="Benachrichtigung entfernen")
    @app_commands.describe(id="Abo-ID (aus /notify list)")
    async def notify_remove(self, interaction: discord.Interaction, id: int):
        if not await require_authorized(interaction):
            return
        
        data = await notifications_db.get()
        subs = data.get("subscriptions", [])
        
        original_len = len(subs)
        subs = [s for s in subs if s["id"] != id]
        
        if len(subs) == original_len:
            await interaction.response.send_message(
                embed=error_embed(f"Keine Benachrichtigung mit ID {id} gefunden"),
                ephemeral=True
            )
            return
        
        await notifications_db.save({"subscriptions": subs, "twitch_token": data.get("twitch_token", {})})
        await interaction.response.send_message(
            embed=success_embed(f"✅ Benachrichtigung {id} entfernt"),
            ephemeral=True
        )

    @notify_group.command(name="list", description="Alle Benachrichtigungen anzeigen")
    async def notify_list(self, interaction: discord.Interaction):
        if not await require_authorized(interaction):
            return
        
        data = await notifications_db.get()
        subs = data.get("subscriptions", [])
        
        if not subs:
            await interaction.response.send_message(
                embed=info_embed("Keine Benachrichtigungen konfiguriert"),
                ephemeral=True
            )
            return
        
        embed = discord.Embed(title="📢 Benachrichtigungen", color=discord.Color.blue())
        
        for sub in subs:
            channel = interaction.guild.get_channel(sub["channel_id"])
            channel_str = channel.mention if channel else f"Unbekannt ({sub['channel_id']})"
            
            if sub["type"] == "yt":
                target = sub.get("channel_id_yt", "Unbekannt")
            else:
                target = sub.get("username", "Unbekannt")
            
            last = sub.get("last_id", "Noch nie")
            embed.add_field(
                name=f"#{sub['id']} • {sub['type'].upper()}",
                value=f"Ziel: `{target}`\nChannel: {channel_str}\nLetzte ID: `{last}`",
                inline=True
            )
        
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @notify_group.command(name="test", description="Test-Benachrichtigung senden")
    @app_commands.describe(id="Abo-ID (aus /notify list)")
    async def notify_test(self, interaction: discord.Interaction, id: int):
        if not await require_authorized(interaction):
            return
        
        data = await notifications_db.get()
        subs = data.get("subscriptions", [])
        
        sub = next((s for s in subs if s["id"] == id), None)
        if not sub:
            await interaction.response.send_message(
                embed=error_embed(f"Keine Benachrichtigung mit ID {id} gefunden"),
                ephemeral=True
            )
            return
        
        channel = interaction.guild.get_channel(sub["channel_id"])
        if not channel:
            await interaction.response.send_message(
                embed=error_embed("Ziel-Channel nicht gefunden"),
                ephemeral=True
            )
            return
        
        role = await _get_panel_role(channel.guild, sub["type"])
        content = role.mention if role else None
        
        if sub["type"] == "twitch":
            embed = notification_embed("twitch", sub["username"], "Test Stream - Live jetzt!", f"https://twitch.tv/{sub['username']}", is_live=True)
        elif sub["type"] == "yt":
            embed = notification_embed("yt", sub.get("channel_id_yt", "Test"), "Test Video", "https://youtube.com/watch?v=test")
        else:
            embed = notification_embed("tt", sub["username"], "Test Post", f"https://tiktok.com/@{sub['username']}")
        
        await channel.send(content=content, embed=embed)
        await interaction.response.send_message(
            embed=success_embed(f"✅ Test-Nachricht gesendet an {channel.mention}"),
            ephemeral=True
        )

async def check_all_notifications(bot: commands.Bot):
    cog = bot.get_cog("NotificationsCog")
    if not cog:
        return
    
    data = await notifications_db.get()
    subs = data.get("subscriptions", [])
    
    for sub in subs:
        try:
            if sub["type"] == "twitch":
                await check_twitch_sub(bot, cog, sub, data)
            elif sub["type"] == "yt":
                await check_youtube_sub(bot, cog, sub, data)
            elif sub["type"] == "tt":
                await check_tiktok_sub(bot, cog, sub, data)
        except Exception as e:
            print(f"Fehler bei Sub {sub['id']}: {e}")

async def check_twitch_sub(bot, cog, sub, data):
    token = await get_twitch_token(cog, data)
    if not token:
        return
    
    username = sub["username"]
    url = f"https://api.twitch.tv/helix/streams?user_login={username}"
    headers = {"Client-ID": bot.config.TWITCH_CLIENT_ID, "Authorization": f"Bearer {token}"}
    
    async with cog.session.get(url, headers=headers) as resp:
        if resp.status != 200:
            return
        result = await resp.json()
    
    streams = result.get("data", [])
    if not streams:
        return
    
    stream = streams[0]
    stream_id = stream["id"]
    
    if sub.get("last_id") == stream_id:
        return
    
    sub["last_id"] = stream_id
    await notifications_db.save(data)
    
    channel = bot.get_channel(sub["channel_id"])
    if not channel:
        return
    
    embed = notification_embed(
        "twitch", username, stream["title"],
        f"https://twitch.tv/{username}",
        thumbnail=stream["thumbnail_url"].replace("{width}", "320").replace("{height}", "180"),
        is_live=True
    )
    embed.add_field(name="Spiel", value=stream.get("game_name", "Unbekannt"), inline=True)
    embed.add_field(name="Zuschauer", value=str(stream.get("viewer_count", 0)), inline=True)
    started = stream.get("started_at")
    if started:
        try:
            started_at = datetime.fromisoformat(started.replace("Z", "+00:00"))
            embed.add_field(name="Live seit", value=f"<t:{int(started_at.timestamp())}:R>", inline=True)
        except (ValueError, TypeError):
            pass

    role = await _get_panel_role(channel.guild, "twitch")
    content = role.mention if role else None
    await channel.send(content=content, embed=embed)

async def check_youtube_sub(bot, cog, sub, data):
    channel_id = sub.get("channel_id_yt")
    if not channel_id:
        return
    
    url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
    
    async with cog.session.get(url) as resp:
        if resp.status != 200:
            return
        content = await resp.text()
    
    feed = feedparser.parse(content)
    if not feed.entries:
        return
    
    latest = feed.entries[0]
    video_id = latest.get("yt_videoid", latest.get("id", "").split(":")[-1])
    
    if sub.get("last_id") == video_id:
        return
    
    sub["last_id"] = video_id
    await notifications_db.save(data)
    
    channel = bot.get_channel(sub["channel_id"])
    if not channel:
        return
    
    thumbnail = latest.get("media_thumbnail", [{}])[0].get("url", "")
    
    embed = notification_embed(
        "yt", channel_id, latest["title"],
        f"https://youtube.com/watch?v={video_id}",
        thumbnail=thumbnail
    )
    embed.add_field(name="Veröffentlicht", value=latest.get("published", "Unbekannt"), inline=True)
    
    role = await _get_panel_role(channel.guild, "yt")
    content = role.mention if role else None
    await channel.send(content=content, embed=embed)

async def check_tiktok_sub(bot, cog, sub, data):
    username = sub["username"].lstrip("@")
    url = f"https://www.tiktok.com/@{username}/rss"
    
    headers = {"User-Agent": "Mozilla/5.0 (compatible; FilpoBot/1.0)"}
    
    async with cog.session.get(url, headers=headers) as resp:
        if resp.status != 200:
            return
        content = await resp.text()
    
    feed = feedparser.parse(content)
    if not feed.entries:
        return
    
    latest = feed.entries[0]
    post_id = latest.get("id", latest.get("link", "")).split("/")[-1]
    
    if sub.get("last_id") == post_id:
        return
    
    sub["last_id"] = post_id
    await notifications_db.save(data)
    
    channel = bot.get_channel(sub["channel_id"])
    if not channel:
        return
    
    thumbnail = ""
    if "media_thumbnail" in latest:
        thumbnail = latest["media_thumbnail"][0].get("url", "")
    elif "media_content" in latest:
        thumbnail = latest["media_content"][0].get("url", "")
    
    embed = notification_embed(
        "tt", username, latest.get("title", "Neuer TikTok Post"),
        latest.get("link", f"https://tiktok.com/@{username}"),
        thumbnail=thumbnail
    )
    embed.add_field(name="Veröffentlicht", value=latest.get("published", "Unbekannt"), inline=True)
    
    role = await _get_panel_role(channel.guild, "tt")
    content = role.mention if role else None
    await channel.send(content=content, embed=embed)

async def get_twitch_token(cog, data) -> str | None:
    import time
    
    if cog.twitch_token and time.time() < cog.twitch_token_expires - 60:
        return cog.twitch_token
    
    if not hasattr(cog.bot, 'config'):
        return None
    
    client_id = cog.bot.config.TWITCH_CLIENT_ID
    client_secret = cog.bot.config.TWITCH_CLIENT_SECRET
    
    if not client_id or not client_secret:
        return None
    
    url = "https://id.twitch.tv/oauth2/token"
    params = {
        "client_id": client_id,
        "client_secret": client_secret,
        "grant_type": "client_credentials"
    }
    
    async with cog.session.post(url, params=params) as resp:
        if resp.status != 200:
            return None
        result = await resp.json()
    
    cog.twitch_token = result["access_token"]
    cog.twitch_token_expires = time.time() + result["expires_in"]
    
    data["twitch_token"] = {
        "access_token": cog.twitch_token,
        "expires_at": cog.twitch_token_expires
    }
    await notifications_db.save(data)
    
    return cog.twitch_token

async def refresh_twitch_token(bot):
    cog = bot.get_cog("NotificationsCog")
    if not cog:
        return
    
    data = await notifications_db.get()
    await get_twitch_token(cog, data)

async def setup(bot: commands.Bot):
    await bot.add_cog(NotificationsCog(bot))
import discord
from datetime import datetime, timezone

def create_embed(
    title: str = "",
    description: str = "",
    color: discord.Color = discord.Color.blue(),
    fields: list[tuple[str, str, bool]] = None,
    footer: str = None,
    thumbnail: str = None,
    image: str = None,
    author: tuple[str, str] = None,
    timestamp: bool = True
) -> discord.Embed:
    embed = discord.Embed(title=title, description=description, color=color)
    
    if fields:
        for name, value, inline in fields:
            embed.add_field(name=name, value=value, inline=inline)
    
    if footer:
        embed.set_footer(text=footer)
    
    if thumbnail:
        embed.set_thumbnail(url=thumbnail)
    
    if image:
        embed.set_image(url=image)
    
    if author:
        embed.set_author(name=author[0], icon_url=author[1] if len(author) > 1 else None)
    
    if timestamp:
        embed.timestamp = datetime.now(timezone.utc)
    
    return embed

def success_embed(description: str, title: str = "✅ Erfolg") -> discord.Embed:
    return create_embed(title=title, description=description, color=discord.Color.green())

def error_embed(description: str, title: str = "❌ Fehler") -> discord.Embed:
    return create_embed(title=title, description=description, color=discord.Color.red())

def warning_embed(description: str, title: str = "⚠️ Warnung") -> discord.Embed:
    return create_embed(title=title, description=description, color=discord.Color.orange())

def info_embed(description: str, title: str = "ℹ️ Info") -> discord.Embed:
    return create_embed(title=title, description=description, color=discord.Color.blue())

def notification_embed(
    platform: str,
    username: str,
    title: str,
    url: str,
    thumbnail: str = None,
    is_live: bool = False,
    extra_fields: list[tuple[str, str, bool]] = None
) -> discord.Embed:
    colors = {
        "twitch": discord.Color(0x9146FF),
        "yt": discord.Color(0xFF0000),
        "tt": discord.Color(0x010101),
        "giveaway": discord.Color(0xFF73FA),
    }
    icons = {"twitch": "🟣", "yt": "🔴", "tt": "🎵", "giveaway": "🎉"}
    names = {"twitch": "Twitch", "yt": "YouTube", "tt": "TikTok", "giveaway": "Giveaway"}

    live_badge = " • **● LIVE**" if is_live else ""
    fields = [
        ("Plattform", names.get(platform, platform), True),
        ("Link", f"[Öffnen]({url})", True),
    ]
    if extra_fields:
        fields.extend(extra_fields)

    return create_embed(
        title=f"{icons.get(platform, '📢')} {names.get(platform, platform)}{live_badge}",
        description=f"**[{title}]({url})**",
        color=colors.get(platform, discord.Color.blue()),
        thumbnail=thumbnail,
        fields=fields,
        footer="Filpo01 Community Bot"
    )

def ticket_embed(
    ticket_type: str,
    user: discord.Member,
    ticket_number: int
) -> discord.Embed:
    return create_embed(
        title=f"🎫 Ticket #{ticket_number:04d} • {ticket_type}",
        description=f"Erstellt von {user.mention} (`{user.id}`)",
        color=discord.Color.blue(),
        fields=[
            ("Typ", ticket_type, True),
            ("Status", "🟢 Offen", True)
        ],
        footer="Filpo01 DC Bot • Ticket System"
    )

def automod_embed(
    action: str,
    user: discord.Member,
    channel: discord.TextChannel,
    matched_word: str,
    message_content: str
) -> discord.Embed:
    return create_embed(
        title=f"🛡️ AutoMod: {action.capitalize()}",
        description=f"Ausgelöst in {channel.mention} von {user.mention}",
        color=discord.Color.red(),
        fields=[
            ("Grund", f"||{matched_word}||", True),
            ("Aktion", action, True),
            ("Nachricht", message_content[:1000], False)
        ],
        footer=f"User ID: {user.id} • Channel ID: {channel.id}"
    )
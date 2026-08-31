import re
import discord
from typing import Optional

def parse_role_ids(role_ids_str: str) -> list[int]:
    ids = []
    for part in role_ids_str.split(","):
        part = part.strip()
        if part.isdigit():
            ids.append(int(part))
        else:
            match = re.match(r"<@&(\d+)>", part)
            if match:
                ids.append(int(match.group(1)))
    return ids

def format_role_mentions(role_ids: list[int], guild: discord.Guild) -> str:
    mentions = []
    for rid in role_ids:
        role = guild.get_role(rid)
        if role:
            mentions.append(role.mention)
        else:
            mentions.append(f"<@&{rid}> (nicht gefunden)")
    return ", ".join(mentions) if mentions else "Keine"

def validate_message_content(content: str, max_length: int = 2000) -> tuple[bool, str]:
    if not content or not content.strip():
        return False, "Nachricht darf nicht leer sein"
    if len(content) > max_length:
        return False, f"Nachricht zu lang (max {max_length} Zeichen)"
    return True, ""

def parse_duration(duration_str: str) -> Optional[int]:
    duration_str = duration_str.lower().strip()
    match = re.match(r"^(\d+)([smhd])?$", duration_str)
    if not match:
        return None
    value = int(match.group(1))
    unit = match.group(2) or "m"
    multipliers = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    return value * multipliers.get(unit, 60)

def sanitize_dropdown_name(name: str) -> str:
    return re.sub(r"[^\w\s\-_]", "", name.strip())[:50]

def parse_dropdown_options(options_str: str) -> list[str]:
    options = []
    for opt in options_str.split(","):
        opt = opt.strip()
        if opt:
            options.append(opt[:100])
    return options[:25]

def format_hello_message(template: str, user: discord.Member, guild: discord.Guild) -> str:
    return template.format(
        user=user.mention,
        username=user.name,
        server=guild.name,
        count=guild.member_count,
        user_id=user.id
    )
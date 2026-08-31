import discord
from config.settings import settings
from config.storage import config_db

def _matches_ids(user: discord.User | discord.Member, ids: list[int]) -> bool:
    if user.id in ids:
        return True
    if isinstance(user, discord.Member):
        return any(role.id in ids for role in user.roles)
    return False

async def is_authorized(interaction: discord.Interaction) -> bool:
    user = interaction.user

    if is_owner(user) or is_dev(user):
        return True

    if not user.guild:
        return False

    config = await config_db.get()
    admin_roles = config.get("admin_roles", [])

    if any(role.id in admin_roles for role in user.roles):
        return True

    if user.guild_permissions.administrator:
        return True

    return False

def is_owner(user: discord.User | discord.Member) -> bool:
    return _matches_ids(user, settings.OWNER_IDS)

def is_dev(user: discord.User | discord.Member) -> bool:
    return _matches_ids(user, settings.DEV_IDS)

async def require_authorized(interaction: discord.Interaction) -> bool:
    if not await is_authorized(interaction):
        await interaction.response.send_message(
            "❌ Du hast keine Berechtigung für diesen Befehl.", 
            ephemeral=True
        )
        return False
    return True
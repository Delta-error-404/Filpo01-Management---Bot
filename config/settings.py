import os
from dotenv import load_dotenv

load_dotenv()

def parse_ids(env_var: str) -> list[int]:
    value = os.getenv(env_var, "").strip()
    if not value:
        return []
    return [int(x.strip()) for x in value.split(",") if x.strip().isdigit()]

class Settings:
    BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")
    OWNER_IDS: list[int] = parse_ids("OWNER_IDS")
    DEV_IDS: list[int] = parse_ids("DEV_IDS")
    TWITCH_CLIENT_ID: str = os.getenv("TWITCH_CLIENT_ID", "")
    TWITCH_CLIENT_SECRET: str = os.getenv("TWITCH_CLIENT_SECRET", "")
    BOT_STATUS: str = os.getenv("BOT_STATUS", "Filpo01 DC Bot")
    BOT_STATUS_TYPE: str = os.getenv("BOT_STATUS_TYPE", "playing")
    TEST_GUILD_ID: int = int(os.getenv("TEST_GUILD_ID", "0")) if os.getenv("TEST_GUILD_ID", "0").isdigit() else 0

    @classmethod
    def validate(cls) -> list[str]:
        errors = []
        if not cls.BOT_TOKEN:
            errors.append("BOT_TOKEN ist nicht gesetzt")
        if not cls.OWNER_IDS:
            errors.append("OWNER_IDS ist nicht gesetzt (mindestens eine Owner-ID erforderlich)")
        return errors

settings = Settings()
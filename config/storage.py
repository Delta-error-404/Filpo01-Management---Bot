import json
import asyncio
import shutil
import time
from pathlib import Path
from typing import TypeVar, Generic
from copy import deepcopy

DATA_DIR = Path(__file__).parent.parent / "data"
DATA_DIR.mkdir(exist_ok=True)
BACKUP_DIR = DATA_DIR / "backups"
BACKUP_DIR.mkdir(exist_ok=True)

T = TypeVar("T")

class JSONManager(Generic[T]):
    def __init__(self, filename: str, default: T):
        self.filepath = DATA_DIR / filename
        self.default = default
        self._lock = asyncio.Lock()
        self._cache: T | None = None
        self._loaded = False

    async def _load_impl(self) -> T:
        if self._loaded and self._cache is not None:
            return deepcopy(self._cache)

        data = await self._read_with_backup()
        self._cache = data
        self._loaded = True
        return deepcopy(self._cache)

    async def _read_with_backup(self) -> T:
        """Liest die Datei; fallt bei korrupter Datei aufs Backup zurueck."""
        if self.filepath.exists():
            try:
                content = self.filepath.read_text(encoding="utf-8")
                if content.strip():
                    data = json.loads(content)
                    return self._merge_defaults(data, deepcopy(self.default))
            except (json.JSONDecodeError, OSError) as exc:
                # Datei ist korrupt - Backup versuchen
                backup = self._find_latest_backup()
                if backup:
                    try:
                        logger = None
                        content = backup.read_text(encoding="utf-8")
                        print("[STORAGE] %s korrupt, lade Backup %s" % (self.filepath.name, backup.name))
                        if content.strip():
                            data = json.loads(content)
                            return self._merge_defaults(data, deepcopy(self.default))
                    except (json.JSONDecodeError, OSError):
                        pass
                # Kein Backup -> Default
                print("[STORAGE] Kein Backup fuer %s, starte mit Default" % self.filepath.name)
                return deepcopy(self.default)
        return deepcopy(self.default)

    def _find_latest_backup(self):
        pattern = self.filepath.stem + "_*.bak"
        try:
            backups = sorted(BACKUP_DIR.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
            return backups[0] if backups else None
        except OSError:
            return None

    async def load(self) -> T:
        async with self._lock:
            return await self._load_impl()

    def _merge_defaults(self, data: dict, default: T) -> T:
        if isinstance(default, dict) and isinstance(data, dict):
            merged = deepcopy(default)
            for key, value in data.items():
                if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
                    merged[key] = self._merge_defaults(value, merged[key])
                else:
                    merged[key] = value
            return merged
        return data

    async def _save_impl(self, data: T) -> None:
        self._cache = deepcopy(data)
        temp_path = self.filepath.with_suffix(".tmp")
        content = json.dumps(data, indent=2, ensure_ascii=False)

        for attempt in range(5):
            try:
                temp_path.write_text(content, encoding="utf-8")
                temp_path.replace(self.filepath)
                # Backup erstellen (behalte bis zu 8)
                self._write_backup(content)
                return
            except PermissionError:
                await asyncio.sleep(0.5 * (attempt + 1))

        try:
            temp_path.write_text(content, encoding="utf-8")
            temp_path.replace(self.filepath)
            self._write_backup(content)
        except OSError:
            if temp_path.exists():
                temp_path.unlink(missing_ok=True)
            raise

    def _write_backup(self, content: str):
        """Erstellt eine zeitgestempelte Sicherungskopie der Daten."""
        try:
            stamp = time.strftime("%Y%m%d_%H%M%S")
            backup_path = BACKUP_DIR / ("%s_%s.bak" % (self.filepath.stem, stamp))
            if not backup_path.exists():
                backup_path.write_text(content, encoding="utf-8")
            # alte Backups aufraumen (max 8 pro Datei)
            backups = sorted(
                BACKUP_DIR.glob(self.filepath.stem + "_*.bak"),
                key=lambda p: p.stat().st_mtime,
                reverse=True
            )
            for old in backups[8:]:
                old.unlink(missing_ok=True)
        except OSError:
            pass

    async def save(self, data: T) -> None:
        async with self._lock:
            await self._save_impl(data)

    async def modify(self, fn) -> T:
        """Atomisch laden, via fn mutieren und speichern (unter dem Lock).

        fn bekommt die Daten und gibt die neuen Daten zurück (oder None,
        wenn nichts gespeichert werden soll).
        """
        async with self._lock:
            data = await self._load_impl()
            result = fn(data)
            if result is not None:
                data = result
                await self._save_impl(data)
            return deepcopy(data)

    async def get(self) -> T:
        return await self.load()

    async def update(self, **kwargs) -> T:
        data = await self.load()
        if isinstance(data, dict):
            data.update(kwargs)
        await self.save(data)
        return data

    def invalidate_cache(self) -> None:
        self._loaded = False
        self._cache = None


config_db = JSONManager[dict]("config.json", {
    "admin_roles": [],
    "welcome_role_id": None,
    "ticket_show_role_id": None,
    "log_channel_id": None,
    "hello_message": {"text": "Willkommen {user} auf {server}! 🎉", "channel_id": None},
    "reaction_roles": {},
    "voice_creator": {"category_id": None, "channel_id": None, "name_template": "{user} Talk"}
})

tickets_db = JSONManager[dict]("tickets.json", {
    "panels": [],
    "active_tickets": [],
    "counter": 0
})

notifications_db = JSONManager[dict]("notifications.json", {
    "subscriptions": [],
    "twitch_token": {"access_token": "", "expires_at": 0},
    "panel_roles": {}
})

automod_db = JSONManager[dict]("automod.json", {
    "enabled": True,
    "rules": [],
    "whitelist_roles": [],
    "filters": {
        "links": {"enabled": False, "action": "delete", "duration": 10, "allowed_domains": []},
        "invites": {"enabled": False, "action": "delete", "duration": 10},
        "caps": {"enabled": False, "threshold": 70, "min_length": 8, "action": "delete", "duration": 10},
        "spam": {"enabled": False, "max_messages": 5, "seconds": 5, "action": "timeout", "duration": 10},
        "mentions": {"enabled": False, "max_mentions": 5, "action": "timeout", "duration": 5}
    }
})

warns_db = JSONManager[dict]("warns.json", {
    "warns": {}
})

scheduler_db = JSONManager[dict]("scheduler.json", {
    "messages": []
})

stats_db = JSONManager[dict]("stats.json", {
    "counters": []
})

voicecreator_db = JSONManager[dict]("voicecreator.json", {
    "channels": []
})

giveaway_db = JSONManager[dict]("giveaways.json", {
    "giveaways": []
})

leveling_db = JSONManager[dict]("leveling.json", {
    "enabled": True,
    "cooldown_seconds": 60,
    "xp_min": 15,
    "xp_max": 25,
    "announce_channel_id": None,
    "reward_roles": {},
    "users": {}
})

counting_db = JSONManager[dict]("counting.json", {
    "channels": {}
})

embed_templates_db = JSONManager[dict]("embed_templates.json", {
    "templates": []
})

anti_nuke_db = JSONManager[dict]("anti_nuke.json", {
    "enabled": True,
    "max_deletes": 5,
    "delete_window": 10,
    "max_bans": 3,
    "ban_window": 30,
    "max_kicks": 3,
    "kick_window": 30,
    "max_channel_dels": 2,
    "channel_del_window": 30,
    "max_role_dels": 2,
    "role_del_window": 30,
    "max_role_creates": 5,
    "role_create_window": 30,
    "punishment": "ban",
    "excluded_roles": [],
    "log_channel_id": None
})
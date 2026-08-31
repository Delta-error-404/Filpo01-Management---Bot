import discord
from discord.ext import commands
import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path
from config.settings import settings
from config import config_db, tickets_db, notifications_db, automod_db, giveaway_db, warns_db, scheduler_db, stats_db, voicecreator_db, leveling_db, counting_db, anti_nuke_db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s: %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("bot")

class FilpoBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        intents.guilds = True
        intents.reactions = True
        intents.messages = True
        intents.presences = True
        
        super().__init__(
            command_prefix="!",
            intents=intents,
            help_command=None,
            activity=self._get_activity()
        )
        
        self.config = settings
        self.notification_task: asyncio.Task | None = None
        self.twitch_token_task: asyncio.Task | None = None

    def _get_activity(self) -> discord.Activity:
        status_type = settings.BOT_STATUS_TYPE.lower()
        if status_type == "watching":
            return discord.Activity(type=discord.ActivityType.watching, name=settings.BOT_STATUS)
        elif status_type == "listening":
            return discord.Activity(type=discord.ActivityType.listening, name=settings.BOT_STATUS)
        elif status_type == "competing":
            return discord.Activity(type=discord.ActivityType.competing, name=settings.BOT_STATUS)
        return discord.Game(name=settings.BOT_STATUS)

    async def setup_hook(self):
        await self.load_extension("cogs.config")
        await self.load_extension("cogs.welcome")
        await self.load_extension("cogs.reaction_roles")
        await self.load_extension("cogs.tickets")
        await self.load_extension("cogs.notifications")
        await self.load_extension("cogs.automod")
        await self.load_extension("cogs.hello")
        await self.load_extension("cogs.logging")
        await self.load_extension("cogs.voice_creator")
        await self.load_extension("cogs.admin")
        await self.load_extension("cogs.warns")
        await self.load_extension("cogs.scheduler")
        await self.load_extension("cogs.statcounter")
        await self.load_extension("cogs.embed_builder")
        await self.load_extension("cogs.utility")
        await self.load_extension("cogs.moderation")
        await self.load_extension("cogs.giveaway")
        await self.load_extension("cogs.leveling")
        await self.load_extension("cogs.counting")
        await self.load_extension("cogs.tictactoe")
        await self.load_extension("cogs.anti_nuke")
        
        if settings.TEST_GUILD_ID:
            await self._clear_stale_global_commands()
            guild = discord.Object(id=settings.TEST_GUILD_ID)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
            logger.info(f"Slash-Commands für Test-Guild {settings.TEST_GUILD_ID} synchronisiert (sofort sichtbar)")
        else:
            await self.tree.sync()
            logger.info("Slash-Commands global synchronisiert (kann bis zu 1h dauern)")
        
        self.notification_task = self.loop.create_task(self.notification_loop())
        self.twitch_token_task = self.loop.create_task(self.twitch_token_refresh_loop())
        
        await config_db.load()
        await tickets_db.load()
        await notifications_db.load()
        await automod_db.load()
        await warns_db.load()
        await scheduler_db.load()
        await stats_db.load()
        await voicecreator_db.load()
        await giveaway_db.load()
        await leveling_db.load()
        await counting_db.load()
        await anti_nuke_db.load()
        logger.info("Datenbanken geladen")

    async def _clear_stale_global_commands(self):
        app_id = getattr(self.http, "application_id", None) or getattr(self.user, "id", None)
        if not app_id:
            return
        try:
            for cmd in await self.http.get_global_commands(app_id):
                await self.http.delete_global_command(app_id, cmd["id"])
                logger.info(f"Alte globale Slash-Command '{cmd['name']}' entfernt")
        except Exception as e:
            logger.warning(f"Konnte globale Commands nicht bereinigen: {e}")

    async def on_ready(self):
        logger.info(f"Eingeloggt als {self.user} (ID: {self.user.id})")
        logger.info(f"Verbunden mit {len(self.guilds)} Guild(s)")
        
        # Warten bis die Channels aller Guilds im Cache geladen sind,
        # sonst wuerden Tickets faelschlich als geloescht markiert.
        await asyncio.sleep(3)
        await self.sync_tickets_on_startup()

    async def sync_tickets_on_startup(self):
        tickets_data = await tickets_db.get()
        active_tickets = tickets_data.get("active_tickets", [])
        
        valid = []
        stale = []
        for ticket in active_tickets:
            channel_id = ticket.get("channel_id")
            channel = self.get_channel(channel_id)
            if channel:
                valid.append(ticket)
            else:
                # Falls der Channel nicht im Cache ist (evtl. noch ladend),
                # pruefe per API nach bevor wir loeschen.
                try:
                    ch = await self.fetch_channel(channel_id)
                    valid.append(ticket)
                except discord.NotFound:
                    logger.warning(f"Ticket-Channel {channel_id} existiert nicht mehr, entferne aus DB")
                    stale.append(ticket)
                except (discord.Forbidden, discord.HTTPException):
                    # Kein Zugriff -> Ticket behalten, nicht loeschen
                    valid.append(ticket)
        
        if stale:
            data = await tickets_db.get()
            data["active_tickets"] = valid
            await tickets_db.save(data)

    async def notification_loop(self):
        await self.wait_until_ready()
        from cogs.notifications import check_all_notifications
        
        while not self.is_closed():
            try:
                await check_all_notifications(self)
            except Exception as e:
                logger.error(f"Fehler im Notification-Loop: {e}", exc_info=True)
            await asyncio.sleep(60)

    async def twitch_token_refresh_loop(self):
        await self.wait_until_ready()
        from cogs.notifications import refresh_twitch_token
        
        while not self.is_closed():
            try:
                await refresh_twitch_token(self)
            except Exception as e:
                logger.error(f"Fehler beim Twitch Token Refresh: {e}", exc_info=True)
            await asyncio.sleep(3000)

    async def close(self):
        if self.notification_task:
            self.notification_task.cancel()
        if self.twitch_token_task:
            self.twitch_token_task.cancel()
        cog = self.get_cog("NotificationsCog")
        if cog:
            session = getattr(cog, "session", None)
            if session:
                try:
                    await session.close()
                except Exception:
                    pass
        await super().close()

    async def on_app_command_completion(self, interaction: discord.Interaction, command: discord.app_commands.Command):
        if not interaction.guild:
            return
        config = await config_db.get()
        log_channel_id = config.get("log_channel_id")
        if log_channel_id:
            log_channel = self.get_channel(log_channel_id)
            if log_channel:
                embed = discord.Embed(
                    title="📋 Command Log",
                    color=discord.Color.blue(),
                    timestamp=datetime.now(timezone.utc)
                )
                embed.add_field(name="User", value=f"{interaction.user} (`{interaction.user.id}`)", inline=True)
                embed.add_field(name="Command", value=f"/{command.qualified_name}", inline=True)
                embed.add_field(name="Guild", value=f"{interaction.guild.name} (`{interaction.guild.id}`)", inline=True)
                embed.add_field(name="Channel", value=f"{interaction.channel.mention} (`{interaction.channel.id}`)", inline=True)
                try:
                    await log_channel.send(embed=embed)
                except discord.HTTPException:
                    pass

bot = FilpoBot()

def acquire_single_instance_lock() -> bool:
    try:
        import msvcrt
        lock_path = Path(__file__).resolve().parent / "bot.lock"
        lock_file = open(lock_path, "a")
        try:
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            lock_file.close()
            return False
        bot._lock_file = lock_file
        return True
    except ImportError:
        return True

if __name__ == "__main__":
    if not acquire_single_instance_lock():
        print("[LOCK] Der Bot laeuft bereits in einer anderen Instanz. Start abgebrochen.")
        exit(1)

    errors = settings.validate()
    if errors:
        for err in errors:
            logger.error(f"Konfigurationsfehler: {err}")
        exit(1)

    try:
        bot.run(settings.BOT_TOKEN)
    except KeyboardInterrupt:
        logger.info("Bot wird beendet...")
    except Exception as e:
        logger.error(f"Kritischer Fehler: {e}", exc_info=True)
        # Prozess sauber beenden, damit der Host (Wispbyte/Pterodactyl)
        # den Bot automatisch neu startet. Kein manueller Loop-Close noetig.
        raise SystemExit(1)
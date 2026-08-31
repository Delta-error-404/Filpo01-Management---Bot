# Filpo01 DC Bot

Ein modularer Discord-Bot mit Slash-Commands für Server-Management.

## Features

- **⚙️ Config System** - Vollständige Verwaltung über `/config`
- **🎭 Reaction Roles** - Rollen per Reaction vergeben
- **👋 Welcome System** - Welcome Role + Custom Hello Messages
- **🎫 Ticket System** - Dropdown-Panel, private Channels, Close-Button
- **🛡️ AutoMod** - Wortfilter mit Aktionen (Delete, Timeout, Warn, Ban)
- **📢 Notifications** - Twitch/YouTube/TikTok Live/Upload Alerts (RSS/Polling)
- **📋 Command Logging** - Alle Slash-Commands werden geloggt

## Installation

```bash
# 1. Repository klonen
cd Filpo01_DC_Bot

# 2. Virtual Environment erstellen
python -m venv .venv
.venv\Scripts\activate  # Windows
# source .venv/bin/activate  # Linux/Mac

# 3. Dependencies installieren
pip install -r requirements.txt

# 4. Konfiguration
cp .env.example .env
# .env bearbeiten (BOT_TOKEN, OWNER_IDS, etc.)

# 5. Bot starten
python bot.py
```

## Konfiguration (.env)

```env
BOT_TOKEN=dein_bot_token
OWNER_IDS=123456789,987654321
DEV_IDS=111111111
TWITCH_CLIENT_ID=xxx
TWITCH_CLIENT_SECRET=xxx
BOT_STATUS=Filpo01 DC Bot
BOT_STATUS_TYPE=playing
```

## Commands

### `/config` - Bot-Konfiguration
| Command | Beschreibung |
|---------|-------------|
| `/config config_dc_bot role:<ids>` | Admin/Dev-Rollen setzen (komma-getrennt) |
| `/config welcome_role role:<role>` | Welcome-Rolle für neue Member |
| `/config ticket_show role:<role>` | Support-Rolle für Tickets |
| `/config log_channel channel:<channel>` | Log-Channel für Commands |
| `/config hello_message nachricht:<text> channel:<channel>` | Join-Nachricht |
| `/config show` | Aktuelle Config anzeigen |

### `/reactionroles` - Reaction Roles
| Command | Beschreibung |
|---------|-------------|
| `/reactionroles add message_id:<id> emoji:<emoji> role:<role>` | Reaction Role hinzufügen |
| `/reactionroles remove message_id:<id> emoji:<emoji>` | Reaction Role entfernen |
| `/reactionroles list` | Alle anzeigen |

### `/ticket` - Ticket System
| Command | Beschreibung |
|---------|-------------|
| `/ticket create channel:<channel> dropdown:<opt1,opt2,...>` | Ticket-Panel erstellen |
| `/ticket close` | Aktuelles Ticket schließen |
| `/ticket list` | Alle Tickets anzeigen |
| `/config_deletedropdown name:<name>` | Dropdown entfernen |

### `/automod` - AutoMod
| Command | Beschreibung |
|---------|-------------|
| `/automod verboten:<wörter> beschtrafung:<delete|timeout|warn|ban> dauer:<min>` | Regel hinzufügen |
| `/automod_list` | Regeln anzeigen |
| `/automod_remove index:<nr>` | Regel entfernen |

### `/notify` - Benachrichtigungen
| Command | Beschreibung |
|---------|-------------|
| `/notify add art:<twitch|yt|tt> username:<name> channel:<channel>` | Abo hinzufügen |
| `/notify remove id:<id>` | Abo entfernen |
| `/notify list` | Alle Abos anzeigen |
| `/notify test id:<id>` | Test-Nachricht senden |

## Architektur

```
├── bot.py                 # Entry Point
├── config/
│   ├── settings.py        # .env Loader
│   └── storage.py         # JSON Manager (atomic read/write)
├── cogs/                  # Features als Discord.py Cogs
├── services/              # Background Tasks (Notifications)
├── utils/                 # Helpers (Permissions, Embeds, Validators)
└── data/                  # JSON-Datenbanken
```

## Datenbanken (JSON)

- `config.json` - Hauptkonfiguration
- `tickets.json` - Ticket-Panels & aktive Tickets
- `notifications.json` - Abos & Twitch Token
- `automod.json` - Filter-Regeln

## Berechtigungen

Zugriff auf Admin-Commands haben:
- **Owner** (aus `OWNER_IDS`)
- **Developer** (aus `DEV_IDS`)
- **Admin-Rollen** (via `/config config_dc_bot`)
- **Server-Administratoren** (Discord Permission)

## Twitch/YouTube/TikTok Notifications

- **Twitch**: Helix API Polling (alle 60s) - benötigt Client ID/Secret
- **YouTube**: RSS Feed Polling (alle 5min) - Channel-ID nötig
- **TikTok**: RSS Feed Polling (alle 5min) - Username nötig

Keine Webhooks/öffentlicher Server erforderlich!

## Lizenz

MIT
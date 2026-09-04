import asyncio
import io
import json
import logging
import random
from datetime import datetime, timezone
from typing import Optional

import discord
from discord.ext import commands
from discord import app_commands

from config.storage import embed_templates_db
from utils.permissions import is_authorized
from utils.embeds import success_embed, error_embed, info_embed

logger = logging.getLogger("embed_builder")

# ----------------------------- Constants -----------------------------
EMBED_LIMITS = {
    "title": 256,
    "description": 4000,
    "field_name": 256,
    "field_value": 1024,
    "fields": 25,
    "footer": 2048,
    "author": 256,
    "content": 2000,
    "label": 80,
    "dropdown_name": 100,
    "dropdown_option_label": 100,
    "dropdown_option_desc": 100,
    "dropdown_options": 25,
}
COLORS = {
    "blurple": 0x5865F2,
    "rot": 0xFF0000,
    "gruen": 0x00FF00,
    "blau": 0x0000FF,
    "gelb": 0xFFFF00,
    "orange": 0xFFA500,
    "lila": 0x800080,
    "pink": 0xFF69B4,
    "weiss": 0xFFFFFF,
    "schwarz": 0x000000,
    "grau": 0x808080,
    "tuerkis": 0x40E0D0,
}
COLOR_PAIRS = [
    ("Discord Blurple", "blurple"),
    ("Rot", "rot"),
    ("Grün", "gruen"),
    ("Blau", "blau"),
    ("Gelb", "gelb"),
    ("Orange", "orange"),
    ("Lila", "lila"),
    ("Pink", "pink"),
    ("Weiß", "weiss"),
    ("Schwarz", "schwarz"),
    ("Grau", "grau"),
    ("Türkis", "tuerkis"),
    ("Zufällig", "random"),
]
ACTION_TYPES = ("send_message", "add_role", "remove_role", "create_channel", "delete_channel")
DANGEROUS_ACTIONS = {"add_role", "remove_role", "create_channel", "delete_channel"}

# ----------------------------- Helpers -----------------------------
def _valid_url(url: Optional[str]) -> bool:
    if not url:
        return True
    return url.lower().startswith("http://") or url.lower().startswith("https://")


def _parse_hex_color(value: str) -> int:
    value = (value or "").strip()
    if value.startswith("#"):
        value = value[1:]
    if len(value) != 6 or not all(c in "0123456789abcdefABCDEF" for c in value):
        raise ValueError("Ungültige HEX-Farbe. Nutze z. B. #5865F2")
    return int(value, 16)


class _Ctx:
    def __init__(self, guild, user, channel):
        self.author = user
        self.guild = guild
        self.channel = channel


def _replace_vars(text: str, ctx: _Ctx) -> str:
    if not text or "{" not in text:
        return text
    user = getattr(ctx, "author", None)
    guild = getattr(ctx, "guild", None)
    channel = getattr(ctx, "channel", None)
    mapping = {
        "{user}": user.mention if user else "",
        "{user_mention}": user.mention if user else "",
        "{user_name}": user.name if user else "",
        "{user_id}": str(user.id) if user else "",
        "{server}": guild.name if guild else "",
        "{server_name}": guild.name if guild else "",
        "{server_id}": str(guild.id) if guild else "",
        "{member_count}": str(guild.member_count) if guild else "",
        "{channel}": channel.mention if channel else "",
        "{channel_name}": channel.name if channel else "",
        "{channel_id}": str(channel.id) if channel else "",
    }
    for k, v in mapping.items():
        text = text.replace(k, v)
    return text


def _empty_state() -> dict:
    return {
        "content": "",
        "title": "",
        "description": "",
        "url": "",
        "color": 0x5865F2,
        "author_name": "",
        "author_url": "",
        "author_icon": "",
        "footer_text": "",
        "footer_icon": "",
        "thumbnail": "",
        "image": "",
        "timestamp": True,
        "fields": [],
        "buttons": [],
        "dropdowns": [],
        "role_selects": [],
    }


def _state_embed(state: dict) -> discord.Embed:
    e = discord.Embed(
        title="🛠️ Embed Builder",
        description="Wähle eine Option unten aus, um dein Embed anzupassen.",
        color=discord.Color.blurple(),
    )
    e.add_field(
        name="Aktueller Stand",
        value=(
            "**Titel:** %s\n"
            "**Beschreibung:** %s\n"
            "**Felder:** %d / %d\n"
            "**Buttons:** %d · **Dropdowns:** %d · **Rollen-Selects:** %d\n"
            "**Farbe:** `#%06X`"
            % (
                state["title"][:90] or "*leer*",
                (state["description"] or "*leer*")[:90],
                len(state["fields"]),
                EMBED_LIMITS["fields"],
                len(state["buttons"]),
                len(state["dropdowns"]),
                len(state["role_selects"]),
                state["color"],
            )
        ),
        inline=False,
    )
    e.set_footer(text="Dieser Builder gehört dir · läuft nach 10 Min automatisch ab")
    return e


# ----------------------------- Modal -----------------------------
class _Modal(discord.ui.Modal):
    def __init__(self, title: str, custom_id: str, inputs: list[dict], callback):
        super().__init__(title=title, custom_id=custom_id)
        self._inputs = []
        for i in inputs:
            kwargs = {
                "label": i["label"],
                "style": i.get("style", discord.TextStyle.short),
                "required": i.get("required", False),
                "placeholder": i.get("placeholder", ""),
                "default": i.get("default", ""),
            }
            if i.get("min_length") is not None:
                kwargs["min_length"] = i["min_length"]
            if i.get("max_length") is not None:
                kwargs["max_length"] = i["max_length"]
            item = discord.ui.TextInput(custom_id=i["id"], **kwargs)
            self.add_item(item)
            self._inputs.append(item)
        self._cb = callback

    async def on_submit(self, interaction: discord.Interaction):
        values = {item.custom_id: item.value or "" for item in self._inputs}
        try:
            await self._cb(interaction, values)
        except ValueError as e:
            await interaction.response.send_message(embed=error_embed(str(e)), ephemeral=True)
        except Exception as e:
            logger.exception("Modal-Fehler (%s)", self.custom_id)
            try:
                await interaction.response.send_message(
                    embed=error_embed("Es ist ein Fehler aufgetreten: %s" % e),
                    ephemeral=True,
                )
            except Exception:
                pass


# ----------------------------- Builder View -----------------------------
class EmbedBuilderView(discord.ui.View):
    def __init__(self, builder: "BuilderSession"):
        super().__init__(timeout=600)
        self.builder = builder
        self._buttons = {}
        self.message: Optional[discord.Message] = None

    async def _render_new(self):
        """Fallback: if the original message can't be edited, dispatch a new builder message."""
        try:
            it = self.builder.original_interaction
            if it is None:
                return
            # fetch the message we originally created
            msg = self.message or await it.original_response()
            self.message = msg
        except Exception:
            pass

    def _check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.builder.user_id:
            from utils.permissions import is_owner, is_dev
            if not (is_owner(interaction.user) or is_dev(interaction.user)):
                asyncio.create_task(interaction.response.send_message(
                    embed=error_embed("Du kannst diesen Embed Builder nicht bedienen."),
                    ephemeral=True,
                ))
                return False
        return True

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return self._check(interaction)

    async def on_timeout(self):
        self.builder.expire()

    async def update(self, interaction: discord.Interaction, note: str = None):
        await interaction.response.edit_message(
            embed=_state_embed(self.builder.state), view=self,
        )
        if note:
            await interaction.followup.send(embed=info_embed(note), ephemeral=True)

    def build_rows(self) -> list[list[discord.ui.Button]]:
        row1 = [
            self._route_btn("🖊️ Inhalt", "content", discord.ButtonStyle.primary),
            self._route_btn("🎨 Farbe", "design", discord.ButtonStyle.primary),
            self._route_btn("👤 Author", "author", discord.ButtonStyle.primary),
            self._route_btn("🔻 Footer", "footer", discord.ButtonStyle.primary),
            self._route_btn("🖼️ Bilder", "image", discord.ButtonStyle.primary),
        ]
        row2 = [
            self._route_btn("📋 Felder", "fields", discord.ButtonStyle.secondary),
            self._route_btn("🔘 Buttons", "buttons", discord.ButtonStyle.secondary),
            self._route_btn("🔽 Dropdowns", "dropdowns", discord.ButtonStyle.secondary),
            self._route_btn("🎭 Rollen", "roleselect", discord.ButtonStyle.secondary),
        ]
        row3 = [
            self._route_btn("👀 Vorschau", "preview", discord.ButtonStyle.primary),
            self._route_btn("💾 Vorlagen", "templates", discord.ButtonStyle.success),
            self._route_btn("📤 Senden", "send", discord.ButtonStyle.success),
            self._route_btn("↩️ Reset", "reset", discord.ButtonStyle.danger),
            self._route_btn("❌ Schließen", "cancel", discord.ButtonStyle.danger),
        ]
        return [row1, row2, row3]

    def _route_btn(self, label, action, style):
        b = _RouteButton(self, action)
        b.label = label
        b.style = style
        return b


class _RouteButton(discord.ui.Button):
    def __init__(self, view: EmbedBuilderView, action: str):
        super().__init__(custom_id="eb_%s" % action)
        self._bview = view
        self._action = action

    async def callback(self, interaction: discord.Interaction):
        st = self._bview.builder.state
        if self._action == "content":
            m = _Modal(
                "Embed Inhalt", "embed_content",
                [
                    {"id": "title", "label": "Titel", "placeholder": "Max. 256 Zeichen",
                     "default": st["title"], "max_length": EMBED_LIMITS["title"]},
                    {"id": "description", "label": "Beschreibung", "placeholder": "Max. 4000 Zeichen",
                     "style": discord.TextStyle.paragraph, "default": st["description"],
                     "max_length": EMBED_LIMITS["description"]},
                    {"id": "url", "label": "URL (optional)", "default": st["url"], "max_length": 200},
                    {"id": "message", "label": "Nachricht (optional)",
                     "placeholder": "Text vor dem Embed, max. 2000",
                     "style": discord.TextStyle.paragraph, "default": st["content"],
                     "max_length": EMBED_LIMITS["content"]},
                ],
                self._on_content,
            )
            await interaction.response.send_modal(m)
        elif self._action == "design":
            await self._design(interaction)
        elif self._action == "author":
            m = _Modal(
                "Author", "embed_author",
                [
                    {"id": "name", "label": "Author Name", "default": st["author_name"],
                     "max_length": EMBED_LIMITS["author"]},
                    {"id": "url", "label": "Author URL (optional)", "default": st["author_url"],
                     "max_length": 200},
                    {"id": "icon", "label": "Author Icon URL", "default": st["author_icon"],
                     "max_length": 200},
                ],
                self._on_author,
            )
            await interaction.response.send_modal(m)
        elif self._action == "footer":
            m = _Modal(
                "Footer", "embed_footer",
                [
                    {"id": "text", "label": "Footer Text", "default": st["footer_text"],
                     "max_length": EMBED_LIMITS["footer"]},
                    {"id": "icon", "label": "Footer Icon URL", "default": st["footer_icon"],
                     "max_length": 200},
                ],
                self._on_footer,
            )
            await interaction.response.send_modal(m)
        elif self._action == "image":
            m = _Modal(
                "Bilder", "embed_image",
                [
                    {"id": "thumbnail", "label": "Thumbnail URL", "default": st["thumbnail"],
                     "max_length": 200},
                    {"id": "image", "label": "Image URL", "default": st["image"], "max_length": 200},
                ],
                self._on_image,
            )
            await interaction.response.send_modal(m)
        elif self._action == "fields":
            await self._fields(interaction)
        elif self._action == "buttons":
            await self._buttons(interaction)
        elif self._action == "dropdowns":
            await self._dropdowns(interaction)
        elif self._action == "roleselect":
            await self._roleselect(interaction)
        elif self._action == "preview":
            await self._preview(interaction)
        elif self._action == "templates":
            await self._templates(interaction)
        elif self._action == "send":
            await self._send(interaction)
        elif self._action == "reset":
            await self._reset(interaction)
        elif self._action == "cancel":
            self._bview.builder.expire(interaction)

    # ---- content ----
    async def _on_content(self, interaction, values):
        st = self._bview.builder.state
        url = values["url"].strip()
        if url and not _valid_url(url):
            raise ValueError("Die URL muss mit http:// oder https:// beginnen.")
        st["title"] = values["title"].strip()
        st["description"] = values["description"].strip()
        st["url"] = url
        st["content"] = values["message"].strip()
        await self._bview.update(interaction, "Inhalt aktualisiert.")

    # ---- design ----
    async def _design(self, interaction):
        st = self._bview.builder.state
        opts = _color_option_objects(st["color"])
        sel = discord.ui.Select(placeholder="Farbe auswählen", options=opts)
        view = discord.ui.View(timeout=300)
        view.add_item(sel)
        async def on_select(si):
            val = si.data["values"][0]
            conv = dict(COLOR_PAIRS)  # name->value
            if val == "random":
                st["color"] = random.randint(0, 0xFFFFFF)
                await self._bview.update(si, "Zufällige Farbe gewählt.")
            elif val == "custom":
                m = _Modal(
                    "Benutzerdefinierte Farbe", "embed_color_custom",
                    [{"id": "hex", "label": "HEX-Farbe", "required": True,
                      "placeholder": "#5865F2", "max_length": 7}],
                    self._on_custom_color,
                )
                await si.response.send_modal(m)
            else:
                st["color"] = COLORS.get(val, 0x5865F2)
                await self._bview.update(si, "Farbe geändert.")
        sel.callback = on_select
        await interaction.response.send_message(
            embed=info_embed("Wähle eine Farbe für dein Embed."), view=view, ephemeral=True)

    async def _on_custom_color(self, interaction, values):
        self._bview.builder.state["color"] = _parse_hex_color(values["hex"])
        await self._bview.update(interaction, "Farbe geändert.")

    # ---- author ----
    async def _on_author(self, interaction, values):
        st = self._bview.builder.state
        for label, key in (("Author URL", "author_url"), ("Author Icon URL", "author_icon")):
            v = values[key].strip()
            if v and not _valid_url(v):
                raise ValueError(label + " muss mit http:// oder https:// beginnen.")
        st["author_name"] = values["name"].strip()
        st["author_url"] = values["url"].strip()
        st["author_icon"] = values["icon"].strip()
        await self._bview.update(interaction, "Author aktualisiert.")

    # ---- footer ----
    async def _on_footer(self, interaction, values):
        st = self._bview.builder.state
        icon = values["icon"].strip()
        if icon and not _valid_url(icon):
            raise ValueError("Footer Icon URL muss mit http:// oder https:// beginnen.")
        st["footer_text"] = values["text"].strip()
        st["footer_icon"] = icon
        await self._bview.update(interaction, "Footer aktualisiert.")

    # ---- image ----
    async def _on_image(self, interaction, values):
        st = self._bview.builder.state
        for label, key in (("Thumbnail URL", "thumbnail"), ("Image URL", "image")):
            v = values[key].strip()
            if v and not _valid_url(v):
                raise ValueError(label + " muss mit http:// oder https:// beginnen.")
        st["thumbnail"] = values["thumbnail"].strip()
        st["image"] = values["image"].strip()
        await self._bview.update(interaction, "Bilder aktualisiert.")

    # ---- preview ----
    async def _preview(self, interaction):
        ctx = _Ctx(interaction.guild, interaction.user, interaction.channel)
        embed = self._bview.builder.build_embed(ctx)
        content = _replace_vars(self._bview.builder.state["content"], ctx) or None
        await interaction.response.send_message(content=content, embed=embed, ephemeral=True)

    # ---- reset ----
    async def _reset(self, interaction):
        view = _SimpleConfirm(
            self._do_reset, danger=True,
        )
        await interaction.response.send_message(
            embed=info_embed("⚠️ Möchtest du den Builder zurücksetzen?"),
            view=view, ephemeral=True)

    async def _do_reset(self, interaction):
        self._bview.builder.state = _empty_state()
        # Refresh the builder message + the main view
        try:
            await interaction.response.edit_message(
                embed=info_embed("Builder wurde zurückgesetzt."), view=None)
        except Exception:
            pass
        parent = self._bview.builder.view
        if parent is not None:
            try:
                await parent.message.edit(embed=_state_embed(parent.builder.state), view=parent)
            except Exception:
                asyncio.create_task(parent._render_new())

    # ---- fields ----
    async def _fields(self, interaction):
        st = self._bview.builder.state
        opts = [
            discord.SelectOption(label="➕ Feld hinzufügen", value="add",
                                 description="Neues Feld anlegen (max. 25)"),
            discord.SelectOption(label="✏️ Feld bearbeiten", value="edit"),
            discord.SelectOption(label="🗑️ Feld löschen", value="delete"),
        ]
        sel = discord.ui.Select(placeholder="Felder verwalten", options=opts)
        view = discord.ui.View(timeout=300)
        view.add_item(sel)
        async def on_select(si):
            choice = si.data["values"][0]
            if choice == "add":
                if len(st["fields"]) >= EMBED_LIMITS["fields"]:
                    return await si.response.send_message(
                        embed=error_embed("Maximal 25 Felder erlaubt."), ephemeral=True)
                m = _Modal(
                    "Feld hinzufügen", "embed_field_add",
                    [
                        {"id": "name", "label": "Name", "required": True,
                         "max_length": EMBED_LIMITS["field_name"]},
                        {"id": "value", "label": "Wert", "style": discord.TextStyle.paragraph,
                         "required": True, "max_length": EMBED_LIMITS["field_value"]},
                    ],
                    self._on_field_add,
                )
                await si.response.send_modal(m)
            elif choice == "edit":
                await self._field_pick(si, "edit")
            else:
                await self._field_pick(si, "delete")
        sel.callback = on_select
        await interaction.response.send_message(
            embed=info_embed("Felder verwalten"), view=view, ephemeral=True)

    async def _field_pick(self, si, mode):
        st = self._bview.builder.state
        if not st["fields"]:
            return await si.response.send_message(
                embed=error_embed("Es gibt noch keine Felder."), ephemeral=True)
        opts = [discord.SelectOption(label="Feld %d: %s" % (i + 1, f["name"][:40]), value=str(i))
                for i, f in enumerate(st["fields"])]
        sel = discord.ui.Select(placeholder="Feld wählen", options=opts)
        view = discord.ui.View(timeout=300)
        view.add_item(sel)
        async def on_pick(pi):
            idx = int(pi.data["values"][0])
            if mode == "delete":
                removed = st["fields"].pop(idx)
                await self._bview.update(pi, "Feld entfernt: " + removed["name"])
            else:
                f = st["fields"][idx]
                m = _Modal(
                    "Feld bearbeiten", "embed_field_edit",
                    [
                        {"id": "name", "label": "Name", "required": True, "default": f["name"],
                         "max_length": EMBED_LIMITS["field_name"]},
                        {"id": "value", "label": "Wert", "style": discord.TextStyle.paragraph,
                         "required": True, "default": f["value"],
                         "max_length": EMBED_LIMITS["field_value"]},
                    ],
                    lambda i2, v2, idx=idx: self._on_field_edit(i2, v2, idx),
                )
                await pi.response.send_modal(m)
        sel.callback = on_pick
        await si.response.send_message(
            embed=info_embed("Welches Feld?"), view=view, ephemeral=True)

    async def _on_field_add(self, interaction, values):
        self._bview.builder.state["fields"].append({
            "name": values["name"].strip() or "Ohne Name",
            "value": values["value"].strip() or "*leer*",
            "inline": False,
        })
        await self._bview.update(interaction, "Feld hinzugefügt.")

    async def _on_field_edit(self, interaction, values, idx):
        self._bview.builder.state["fields"][idx] = {
            "name": values["name"].strip() or "Ohne Name",
            "value": values["value"].strip() or "*leer*",
            "inline": False,
        }
        await self._bview.update(interaction, "Feld bearbeitet.")

    # ---- buttons ----
    async def _buttons(self, interaction):
        st = self._bview.builder.state
        opts = [
            discord.SelectOption(label="🔗 Link-Button", value="link",
                                 description="Öffnet eine URL"),
            discord.SelectOption(label="🤖 Interaktions-Button", value="interaction",
                                 description="Button mit Aktion"),
            discord.SelectOption(label="🗑️ Button löschen", value="delete"),
        ]
        sel = discord.ui.Select(placeholder="Buttons verwalten", options=opts)
        view = discord.ui.View(timeout=300)
        view.add_item(sel)
        async def on_select(si):
            choice = si.data["values"][0]
            if choice == "link":
                if len(st["buttons"]) >= 5:
                    return await si.response.send_message(
                        embed=error_embed("Maximal 5 Buttons in einer Reihe erlaubt."), ephemeral=True)
                m = _Modal(
                    "Link-Button", "embed_btn_link",
                    [
                        {"id": "label", "label": "Label", "required": True,
                         "max_length": EMBED_LIMITS["label"]},
                        {"id": "emoji", "label": "Emoji (optional)", "max_length": 8},
                        {"id": "url", "label": "URL", "required": True, "max_length": 200},
                    ],
                    self._on_btn_link,
                )
                await si.response.send_modal(m)
            elif choice == "interaction":
                if len(st["buttons"]) >= 5:
                    return await si.response.send_message(
                        embed=error_embed("Maximal 5 Interaktions-Buttons in einer Reihe erlaubt."),
                        ephemeral=True)
                m = _Modal(
                    "Interaktions-Button", "embed_btn_int",
                    [
                        {"id": "label", "label": "Label", "required": True,
                         "max_length": EMBED_LIMITS["label"]},
                        {"id": "emoji", "label": "Emoji (optional)", "max_length": 8},
                        {"id": "action", "label": "Aktion", "required": True,
                         "placeholder": "send_message | add_role | remove_role | create_channel | delete_channel"},
                        {"id": "data", "label": "Aktions-Daten",
                         "style": discord.TextStyle.paragraph,
                         "placeholder": "Nachricht / Rollen-ID / Kanalname"},
                    ],
                    self._on_btn_int,
                )
                await si.response.send_modal(m)
            else:
                if not st["buttons"]:
                    return await si.response.send_message(
                        embed=error_embed("Es gibt noch keine Buttons."), ephemeral=True)
                opts = []
                for i, b in enumerate(st["buttons"]):
                    desc = "Link" if b.get("style") == "link" else "Aktion: " + b.get("action_type", "?")
                    opts.append(discord.SelectOption(
                        label="%d: %s" % (i + 1, b["label"][:36]), value=str(i), description=desc))
                bsel = discord.ui.Select(placeholder="Button wählen", options=opts)
                bview = discord.ui.View(timeout=300)
                bview.add_item(bsel)
                async def on_bdel(bi):
                    idx = int(bi.data["values"][0])
                    removed = st["buttons"].pop(idx)
                    await self._bview.update(bi, "Button entfernt: " + removed["label"])
                bsel.callback = on_bdel
                await si.response.send_message(
                    embed=info_embed("Welchen Button löschen?"), view=bview, ephemeral=True)
        sel.callback = on_select
        await interaction.response.send_message(
            embed=info_embed("Buttons konfigurieren"), view=view, ephemeral=True)

    async def _on_btn_link(self, interaction, values):
        url = values["url"].strip()
        if not _valid_url(url):
            raise ValueError("URL muss mit http:// oder https:// beginnen.")
        self._bview.builder.state["buttons"].append({
            "label": values["label"].strip(),
            "emoji": values["emoji"].strip() or None,
            "style": "link",
            "url": url,
        })
        await self._bview.update(interaction, "Link-Button hinzugefügt.")

    async def _on_btn_int(self, interaction, values):
        action = values["action"].strip().lower()
        data = values["data"].strip()
        if action not in ACTION_TYPES:
            raise ValueError("Ungültige Aktion. Erlaubt: " + ", ".join(ACTION_TYPES))
        if action in ("add_role", "remove_role"):
            if not data.isdigit():
                raise ValueError("Für Rollen-Aktionen wird eine Rollen-ID benötigt.")
            # ensure role exists
            if not interaction.guild.get_role(int(data)):
                raise ValueError("Rolle mit dieser ID wurde auf dem Server nicht gefunden.")
        if action == "create_channel" and not data:
            raise ValueError("Für 'create_channel' wird ein Kanalname benötigt.")
        if action == "delete_channel" and data not in ("", "this"):
            raise ValueError("Für 'delete_channel' lasse data leer oder setze 'this'.")
        self._bview.builder.state["buttons"].append({
            "label": values["label"].strip(),
            "emoji": values["emoji"].strip() or None,
            "style": "primary",
            "action_type": action,
            "action_data": data,
        })
        await self._bview.update(interaction, "Interaktions-Button hinzugefügt.")

    # ---- dropdowns ----
    async def _dropdowns(self, interaction):
        st = self._bview.builder.state
        opts = [
            discord.SelectOption(label="➕ Dropdown hinzufügen", value="add"),
            discord.SelectOption(label="🗑️ Dropdown löschen", value="delete"),
        ]
        sel = discord.ui.Select(placeholder="Dropdowns verwalten", options=opts)
        view = discord.ui.View(timeout=300)
        view.add_item(sel)
        async def on_select(si):
            if si.data["values"][0] == "add":
                if len(st["dropdowns"]) >= 5:
                    return await si.response.send_message(
                        embed=error_embed("Maximal 5 Dropdowns erlaubt."), ephemeral=True)
                m = _Modal(
                    "Dropdown hinzufügen", "embed_dd_add",
                    [
                        {"id": "name", "label": "Placeholder/Name", "required": True,
                         "max_length": EMBED_LIMITS["dropdown_name"]},
                        {"id": "options", "label": "Optionen", "style": discord.TextStyle.paragraph,
                         "required": True,
                         "placeholder": "Label1:Wert1:Aktion1|Label2:Wert2:Aktion2   (Aktion: send_message/add_role/remove_role)"},
                    ],
                    self._on_dd_add,
                )
                await si.response.send_modal(m)
            else:
                if not st["dropdowns"]:
                    return await si.response.send_message(
                        embed=error_embed("Es gibt noch keine Dropdowns."), ephemeral=True)
                opts2 = [discord.SelectOption(label=d["name"][:40], value=str(i))
                         for i, d in enumerate(st["dropdowns"])]
                dsel = discord.ui.Select(placeholder="Dropdown wählen", options=opts2)
                dview = discord.ui.View(timeout=300)
                dview.add_item(dsel)
                async def on_ddel(di):
                    idx = int(di.data["values"][0])
                    removed = st["dropdowns"].pop(idx)
                    await self._bview.update(di, "Dropdown entfernt: " + removed["name"])
                dsel.callback = on_ddel
                await si.response.send_message(
                    embed=info_embed("Welches Dropdown löschen?"), view=dview, ephemeral=True)
        sel.callback = on_select
        await interaction.response.send_message(
            embed=info_embed("Dropdowns konfigurieren"), view=view, ephemeral=True)

    async def _on_dd_add(self, interaction, values):
        options = []
        for token in values["options"].split("|"):
            token = token.strip()
            if not token:
                continue
            parts = token.split(":")
            if len(parts) < 2:
                raise ValueError("Jede Option muss 'Label:Wert:Aktion' sein (getrennt durch |).")
            label = parts[0].strip()
            val = parts[1].strip()
            action = parts[2].strip().lower() if len(parts) > 2 and parts[2].strip() else "send_message"
            if not label or not val:
                raise ValueError("Label und Wert jeder Option dürfen nicht leer sein.")
            if action not in ("send_message", "add_role", "remove_role"):
                raise ValueError("Aktion muss send_message, add_role oder remove_role sein.")
            data = parts[3].strip() if len(parts) > 3 else ""
            options.append({
                "label": label[:100],
                "value": val[:100],
                "description": None,
                "emoji": None,
                "action_type": action,
                "action_data": data,
            })
        if not options:
            raise ValueError("Keine gültigen Optionen angegeben.")
        if len(options) > EMBED_LIMITS["dropdown_options"]:
            raise ValueError("Maximal 25 Optionen pro Dropdown.")
        self._bview.builder.state["dropdowns"].append({
            "name": values["name"].strip()[:100],
            "placeholder": values["name"].strip()[:150],
            "min_values": 1,
            "max_values": 1,
            "options": options,
        })
        await self._bview.update(interaction, "Dropdown hinzugefügt.")

    # ---- role select ----
    async def _roleselect(self, interaction):
        st = self._bview.builder.state
        opts = [
            discord.SelectOption(label="➕ Rollen-Select hinzufügen", value="add"),
            discord.SelectOption(label="🗑️ Rollen-Select löschen", value="delete"),
        ]
        sel = discord.ui.Select(placeholder="Rollen-Select verwalten", options=opts)
        view = discord.ui.View(timeout=300)
        view.add_item(sel)
        async def on_select(si):
            if si.data["values"][0] == "add":
                if len(st["role_selects"]) >= 5:
                    return await si.response.send_message(
                        embed=error_embed("Maximal 5 Rollen-Selects erlaubt."), ephemeral=True)
                # Build role options for the select
                roles = sorted(si.guild.roles, key=lambda r: r.position, reverse=True)
                role_sel = discord.ui.Select(
                    placeholder="Wähle bis zu 25 Rollen (Mehrfachauswahl)",
                    min_values=1, max_values=min(25, len(si.guild.roles) or 1),
                    options=[discord.SelectOption(label=r.name[:100], value=str(r.id))
                             for r in roles[:25]],
                )
                rview = discord.ui.View(timeout=300)
                rview.add_item(role_sel)
                async def on_role_pick(ri):
                    st["role_selects"].append({
                        "placeholder": "Rollen-Select",
                        "mode": "add",
                        "role_ids": [int(x) for x in ri.data["values"]],
                        "min_values": 1,
                        "max_values": len(ri.data["values"]),
                    })
                    await self._bview.update(ri, "Rollen-Select hinzugefügt (%d Rollen)." % len(ri.data["values"]))
                role_sel.callback = on_role_pick
                await si.response.send_message(
                    embed=info_embed("Wähle die Rollen für den Select:"), view=rview, ephemeral=True)
            else:
                if not st["role_selects"]:
                    return await si.response.send_message(
                        embed=error_embed("Es gibt noch keine Rollen-Selects."), ephemeral=True)
                opts2 = [discord.SelectOption(label=s["placeholder"][:40], value=str(i))
                         for i, s in enumerate(st["role_selects"])]
                rsel = discord.ui.Select(placeholder="Rollen-Select wählen", options=opts2)
                rview = discord.ui.View(timeout=300)
                rview.add_item(rsel)
                async def on_rdel(ri):
                    idx = int(ri.data["values"][0])
                    removed = st["role_selects"].pop(idx)
                    await self._bview.update(ri, "Rollen-Select entfernt: " + removed["placeholder"])
                rsel.callback = on_rdel
                await si.response.send_message(
                    embed=info_embed("Welchen Rollen-Select löschen?"), view=rview, ephemeral=True)
        sel.callback = on_select
        await interaction.response.send_message(
            embed=info_embed("Rollen-Select konfigurieren"), view=view, ephemeral=True)

    # ---- templates ----
    async def _templates(self, interaction):
        opts = [
            discord.SelectOption(label="💾 Speichern", value="save"),
            discord.SelectOption(label="📂 Laden", value="load"),
            discord.SelectOption(label="📋 Liste", value="list"),
            discord.SelectOption(label="🗑️ Löschen", value="delete"),
            discord.SelectOption(label="📦 Exportieren", value="export"),
            discord.SelectOption(label="📥 Importieren", value="import"),
        ]
        sel = discord.ui.Select(placeholder="Vorlagen", options=opts)
        view = discord.ui.View(timeout=300)
        view.add_item(sel)
        async def on_select(si):
            choice = si.data["values"][0]
            if choice == "save":
                m = _Modal(
                    "Vorlage speichern", "embed_tpl_save",
                    [{"id": "name", "label": "Name", "required": True, "placeholder": "z. B. welcome",
                      "max_length": 50}],
                    self._on_tpl_save,
                )
                await si.response.send_modal(m)
            elif choice == "list":
                await si.response.send_message(
                    embed=await self._bview.builder.cog.list_templates(si.guild_id), ephemeral=True)
            elif choice == "delete":
                m = _Modal(
                    "Vorlage löschen", "embed_tpl_del",
                    [{"id": "name", "label": "Name", "required": True, "max_length": 50}],
                    self._on_tpl_del,
                )
                await si.response.send_modal(m)
            elif choice == "export":
                await self._bview.builder.export(si)
            elif choice == "import":
                m = _Modal(
                    "Importieren", "embed_tpl_import",
                    [{"id": "json", "label": "Embed-JSON", "style": discord.TextStyle.paragraph,
                      "required": True}],
                    self._on_tpl_import,
                )
                await si.response.send_modal(m)
            else:
                templates = await self._bview.builder.cog.get_templates(si.guild_id, si.user.id)
                if not templates:
                    return await si.response.send_message(
                        embed=error_embed("Keine Vorlagen gefunden."), ephemeral=True)
                tsel = discord.ui.Select(
                    placeholder="Vorlage wählen",
                    options=[discord.SelectOption(label=t["name"][:90], value=t["name"])
                             for t in templates[:25]],
                )
                tview = discord.ui.View(timeout=300)
                tview.add_item(tsel)
                async def on_load(li):
                    name = li.data["values"][0]
                    ok, msg = await self._bview.builder.load_template(name, li.guild_id, li.user_id)
                    if not ok:
                        return await li.response.send_message(embed=error_embed(msg), ephemeral=True)
                    await self._bview.update(li, "Vorlage geladen: " + name)
                tsel.callback = on_load
                await si.response.send_message(
                    embed=info_embed("Vorlage wählen:"), view=tview, ephemeral=True)
        sel.callback = on_select
        await interaction.response.send_message(
            embed=info_embed("Vorlagen-Verwaltung"), view=view, ephemeral=True)

    async def _on_tpl_save(self, interaction, values):
        ok, msg = await self._bview.builder.save_template(
            values["name"].strip(), interaction.guild_id, interaction.user.id)
        if not ok:
            return await interaction.response.send_message(embed=error_embed(msg), ephemeral=True)
        await self._bview.update(interaction, msg)

    async def _on_tpl_del(self, interaction, values):
        ok, msg = await self._bview.builder.cog.delete_template(
            values["name"].strip(), interaction.guild_id, interaction.user.id)
        await interaction.response.send_message(
            embed=success_embed(msg) if ok else error_embed(msg), ephemeral=True)

    async def _on_tpl_import(self, interaction, values):
        ok, msg = await self._bview.builder.import_json(values["json"].strip())
        if not ok:
            return await interaction.response.send_message(embed=error_embed(msg), ephemeral=True)
        await self._bview.update(interaction, msg)

    # ---- send ----
    async def _send(self, interaction):
        opts = [
            discord.SelectOption(label="Aktueller Channel", value="current",
                                 description=interaction.channel.name if interaction.channel else "Hier"),
            discord.SelectOption(label="Anderer Channel", value="other"),
        ]
        sel = discord.ui.Select(placeholder="Ziel wählen", options=opts)
        view = discord.ui.View(timeout=300)
        view.add_item(sel)
        async def on_select(si):
            if si.data["values"][0] == "current":
                await self._bview.builder.deliver(si, si.channel)
            else:
                csel = discord.ui.ChannelSelect(
                    placeholder="Channel wählen",
                    channel_types=[discord.ChannelType.text, discord.ChannelType.news],
                )
                cview = discord.ui.View(timeout=300)
                cview.add_item(csel)
                async def on_chan(ci):
                    ch = ci.guild.get_channel(ci.data["values"][0])
                    await self._bview.builder.deliver(ci, ch)
                csel.callback = on_chan
                await si.response.send_message(
                    embed=info_embed("Zielchannel wählen:"), view=cview, ephemeral=True)
        sel.callback = on_select
        await interaction.response.send_message(
            embed=info_embed("Wohin soll das Embed gesendet werden?"), view=view, ephemeral=True)


class _SimpleConfirm(discord.ui.View):
    def __init__(self, callback, danger: bool = True):
        super().__init__(timeout=60)
        self._cb = callback
        style = discord.ButtonStyle.danger if danger else discord.ButtonStyle.success
        self.add_item(_ConfirmButton(self, style, "Ja"))


class _ConfirmButton(discord.ui.Button):
    def __init__(self, parent: _SimpleConfirm, style, label):
        super().__init__(label=label, style=style)
        self._parent = parent

    async def callback(self, interaction):
        cb = self._parent._cb
        await cb(interaction)


# ----------------------------- Builder Session -----------------------------
class BuilderSession:
    def __init__(self, cog: "EmbedBuilderCog", user_id: int):
        self.cog = cog
        self.user_id = user_id
        self.state = _empty_state()
        self.view: Optional[EmbedBuilderView] = None
        self.expired = False
        self.original_interaction = None

    def expire(self, interaction=None):
        if self.expired:
            return
        self.expired = True
        self.cog.builders.pop(self.user_id, None)
        if interaction:
            asyncio.create_task(self._close(interaction))

    async def _close(self, interaction):
        try:
            await interaction.response.edit_message(
                embed=info_embed("⏱️ Dieser Embed Builder wurde geschlossen."), view=None)
        except Exception:
            pass

    def build_embed(self, ctx: _Ctx) -> discord.Embed:
        st = self.state
        e = discord.Embed(
            title=_replace_vars(st["title"], ctx) or None,
            description=_replace_vars(st["description"], ctx) or None,
            color=st["color"],
            url=st["url"] or None,
        )
        if st["timestamp"]:
            e.timestamp = datetime.now(timezone.utc)
        if st["author_name"]:
            kw = {"name": _replace_vars(st["author_name"], ctx)}
            if st["author_url"]:
                kw["url"] = st["author_url"]
            if st["author_icon"]:
                kw["icon_url"] = st["author_icon"]
            e.set_author(**kw)
        if st["footer_text"]:
            kw = {}
            if st["footer_icon"]:
                kw["icon_url"] = st["footer_icon"]
            e.set_footer(text=_replace_vars(st["footer_text"], ctx), **kw)
        if st["thumbnail"]:
            e.set_thumbnail(url=st["thumbnail"])
        if st["image"]:
            e.set_image(url=st["image"])
        for f in st["fields"]:
            e.add_field(
                name=_replace_vars(f["name"], ctx) or "Ohne Name",
                value=_replace_vars(f["value"], ctx) or "*leer*",
                inline=f.get("inline", False),
            )
        return e

    # ---- deliver ----
    async def deliver(self, interaction, channel):
        if not channel:
            return await interaction.response.send_message(
                embed=error_embed("Channel nicht gefunden."), ephemeral=True)
        guild = channel.guild or interaction.guild
        ctx = _Ctx(guild, interaction.user, channel)
        embed = self.build_embed(ctx)
        content = _replace_vars(self.state["content"], ctx) or None
        view = self.build_outgoing_view(guild)
        try:
            sent = await channel.send(content=content, embed=embed, view=view)
        except discord.Forbidden:
            return await interaction.response.send_message(
                embed=error_embed("Ich habe keine Berechtigung, in diesen Channel zu senden."),
                ephemeral=True)
        except discord.HTTPException as e:
            return await interaction.response.send_message(
                embed=error_embed("Fehler beim Senden: %s" % e), ephemeral=True)
        await interaction.response.send_message(
            embed=success_embed(
                "Embed gesendet in {0} — [Nachricht öffnen]({1})".format(
                    channel.mention, sent.jump_url)),
            ephemeral=True)

    def build_outgoing_view(self, guild) -> Optional[discord.ui.View]:
        st = self.state
        has_actions = (any(b.get("action_type") for b in st["buttons"])
                       or bool(st["dropdowns"]) or bool(st["role_selects"]))
        if not has_actions:
            return None
        view = discord.ui.View(timeout=1800)
        # interaction + link buttons
        row = []
        for b in st["buttons"]:
            if b.get("style") == "link":
                view.add_item(discord.ui.Button(
                    label=b["label"], style=discord.ButtonStyle.link,
                    url=b["url"], emoji=b.get("emoji")))
            else:
                view.add_item(_ActionButton(b, self, guild, len(row)))
                row.append(b)
        # dropdowns with actions
        for d in st["dropdowns"]:
            view.add_item(_ActionDropdown(d, self, guild))
        # role selects
        for rs in st["role_selects"]:
            view.add_item(_RoleSelectMenu(rs, self, guild))
        return view

    # ---- templates ----
    async def save_template(self, name, guild_id, owner_id):
        if not name:
            return False, "Name darf nicht leer sein."
        data = await embed_templates_db.get()
        templates = data.setdefault("templates", [])
        now = datetime.now(timezone.utc).isoformat()
        for t in templates:
            if t.get("name") == name and t.get("guild_id") == guild_id and t.get("owner_id") == owner_id:
                t.update(self.state)
                t["updated_at"] = now
                await embed_templates_db.save(data)
                return True, "Vorlage '%s' aktualisiert." % name
        templates.append({
            "embed_name": name,
            "name": name,
            "guild_id": guild_id,
            "owner_id": owner_id,
            "created_at": now,
            "updated_at": now,
            **self.state,
        })
        await embed_templates_db.save(data)
        return True, "Vorlage '%s' gespeichert." % name

    async def load_template(self, name, guild_id, owner_id):
        data = await embed_templates_db.get()
        for t in data.get("templates", []):
            if (t.get("name") == name and t.get("guild_id") == guild_id
                    and t.get("owner_id") == owner_id):
                fresh = _empty_state()
                for k in fresh:
                    if k in t:
                        fresh[k] = t[k]
                self.state = fresh
                return True, "Vorlage '%s' geladen." % name
        return False, "Vorlage '%s' nicht gefunden." % name

    async def export(self, interaction):
        payload = json.dumps(self.state, ensure_ascii=False, indent=2)
        await interaction.response.send_message(
            file=discord.File(io.BytesIO(payload.encode("utf-8")), filename="embed.json"),
            ephemeral=True)

    async def import_json(self, raw: str):
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return False, "Ungültiges JSON."
        if not isinstance(data, dict):
            return False, "JSON muss ein Objekt sein."
        fresh = _empty_state()
        for k in fresh:
            if k in data and isinstance(data[k], type(fresh[k])):
                fresh[k] = data[k]
        self.state = fresh
        return True, "Embed importiert."


# ----------------------------- Outgoing interactive components -----------------------------
class _ActionButton(discord.ui.Button):
    def __init__(self, data: dict, session: BuilderSession, guild, index: int):
        super().__init__(
            label=data.get("label") or "Button",
            style=discord.ButtonStyle.success,
            emoji=data.get("emoji"),
            custom_id="eb_act_%d_%d" % (index, guild.id),
        )
        self.session = session
        self.action_type = data.get("action_type")
        self.action_data = data.get("action_data", "")

    async def callback(self, interaction: discord.Interaction):
        await run_action(self.action_type, self.action_data, interaction)


class _ActionDropdown(discord.ui.Select):
    def __init__(self, data: dict, session: BuilderSession, guild):
        opts = []
        for o in data.get("options", []):
            kwargs = {"label": o["label"], "value": o["value"]}
            if o.get("description"):
                kwargs["description"] = o["description"]
            if o.get("emoji"):
                kwargs["emoji"] = o["emoji"]
            opts.append(discord.SelectOption(**kwargs))
        super().__init__(
            placeholder=data.get("placeholder", "Auswahl"),
            options=opts,
            min_values=data.get("min_values", 1),
            max_values=data.get("max_values", 1),
            custom_id="eb_dd_%d" % guild.id,
        )
        self.session = session
        self.options_data = data.get("options", [])

    async def callback(self, interaction: discord.Interaction):
        vals = interaction.data["values"]
        for v in vals:
            match = next((o for o in self.options_data if o["value"] == v), None)
            if match:
                await run_action(match.get("action_type", "send_message"),
                                 match.get("action_data", ""), interaction)


class _RoleSelectMenu(discord.ui.Select):
    def __init__(self, data: dict, session: BuilderSession, guild):
        opts = []
        for rid in data.get("role_ids", []):
            role = guild.get_role(rid)
            opts.append(discord.SelectOption(
                label=("🎭 " + role.name) if role else ("Rolle %d" % rid),
                value=str(rid)))
        super().__init__(
            placeholder=data.get("placeholder", "Rolle wählen"),
            options=opts,
            min_values=data.get("min_values", 1),
            max_values=data.get("max_values", 1),
            custom_id="eb_rs_%d" % guild.id,
        )
        self.session = session
        self.mode = data.get("mode", "add")

    async def callback(self, interaction: discord.Interaction):
        member = interaction.user
        if self.mode == "add" and not member.guild_permissions.manage_roles:
            return await interaction.response.send_message(
                embed=error_embed("Du brauchst 'Rollen verwalten', um Rollen über diesen Select zu bekommen."),
                ephemeral=True)
        results = []
        for rid in interaction.data["values"]:
            role = interaction.guild.get_role(int(rid))
            if not role:
                continue
            try:
                if self.mode == "add":
                    await member.add_roles(role)
                    results.append("✅ " + role.name)
                else:
                    await member.remove_roles(role)
                    results.append("♻️ " + role.name)
            except discord.Forbidden:
                results.append("❌ " + role.name + " (keine Rechte)")
        await interaction.response.send_message(
            "\n".join(results) if results else "Keine Aktion ausgeführt.",
            ephemeral=True)


async def run_action(action_type, action_data, interaction):
    guild = interaction.guild
    try:
        if action_type == "send_message":
            ctx = _Ctx(guild, interaction.user, interaction.channel)
            await interaction.response.send_message(
                _replace_vars(action_data, ctx), ephemeral=False)
        elif action_type in ("add_role", "remove_role"):
            role = guild.get_role(int(action_data))
            if not role:
                return await interaction.response.send_message(
                    embed=error_embed("Rolle nicht gefunden."), ephemeral=True)
            if not interaction.user.guild_permissions.manage_roles:
                return await interaction.response.send_message(
                    embed=error_embed("Du brauchst 'Rollen verwalten'."), ephemeral=True)
            try:
                if action_type == "add_role":
                    await interaction.user.add_roles(role)
                    msg = "Rolle %s hinzugefügt." % role.mention
                else:
                    await interaction.user.remove_roles(role)
                    msg = "Rolle %s entfernt." % role.mention
            except discord.Forbidden:
                return await interaction.response.send_message(
                    embed=error_embed("Ich habe keine Berechtigung, diese Rolle zu vergeben."),
                    ephemeral=True)
            await interaction.response.send_message(msg, ephemeral=True)
        elif action_type == "create_channel":
            if not interaction.user.guild_permissions.manage_channels:
                return await interaction.response.send_message(
                    embed=error_embed("Du brauchst 'Kanäle verwalten'."), ephemeral=True)
            try:
                ch = await guild.create_text_channel(name=action_data or "neuer-channel")
            except discord.Forbidden:
                return await interaction.response.send_message(
                    embed=error_embed("Ich habe keine Berechtigung, Kanäle zu erstellen."),
                    ephemeral=True)
            await interaction.response.send_message(
                "Kanal %s erstellt." % ch.mention, ephemeral=True)
        elif action_type == "delete_channel":
            if not interaction.user.guild_permissions.manage_channels:
                return await interaction.response.send_message(
                    embed=error_embed("Du brauchst 'Kanäle verwalten'."), ephemeral=True)
            if interaction.channel and interaction.channel.guild:
                try:
                    await interaction.channel.delete(reason="Embed-Button Aktion")
                except discord.Forbidden:
                    return await interaction.response.send_message(
                        embed=error_embed("Ich habe keine Berechtigung, diesen Kanal zu löschen."),
                        ephemeral=True)
        else:
            await interaction.response.send_message(
                embed=error_embed("Unbekannte Aktion: %s" % action_type), ephemeral=True)
    except Exception as e:
        logger.exception("Action-Fehler (%s)", action_type)
        try:
            await interaction.response.send_message(
                embed=error_embed("Fehler: %s" % e), ephemeral=True)
        except Exception:
            pass


# ----------------------------- Cog -----------------------------
class EmbedBuilderCog(commands.Cog, name="EmbedBuilder"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.builders = {}

    def get_or_create(self, user_id) -> BuilderSession:
        b = self.builders.get(user_id)
        if not b or b.expired:
            b = BuilderSession(self, user_id)
            self.builders[user_id] = b
        return b

    async def get_templates(self, guild_id, owner_id):
        data = await embed_templates_db.get()
        return [t for t in data.get("templates", [])
                if t.get("guild_id") == guild_id and t.get("owner_id") == owner_id]

    async def list_templates(self, guild_id):
        data = await embed_templates_db.get()
        all_t = [t for t in data.get("templates", []) if t.get("guild_id") == guild_id]
        if not all_t:
            return info_embed("Noch keine Vorlagen auf diesem Server gespeichert.")
        lines = [f"**{t['name']}** — von <@{t['owner_id']}>" for t in all_t[:50]]
        return info_embed("\n".join(lines), title="📋 Gespeicherte Vorlagen")

    async def delete_template(self, name, guild_id, owner_id):
        data = await embed_templates_db.get()
        templates = data.setdefault("templates", [])
        for t in templates:
            if (t.get("name") == name and t.get("guild_id") == guild_id
                    and t.get("owner_id") == owner_id):
                templates.remove(t)
                await embed_templates_db.save(data)
                return True, "Vorlage '%s' gelöscht." % name
        return False, "Vorlage '%s' nicht gefunden oder nicht deine." % name

    async def _check_cmd_perm(self, interaction) -> bool:
        if await is_authorized(interaction):
            return True
        await interaction.response.send_message(
            "❌ Du hast keine Berechtigung für diese Aktion.", ephemeral=True)
        return False

    @app_commands.command(name="embed", description="Interaktiven Embed Builder öffnen")
    @app_commands.guild_only()
    async def embed(self, interaction: discord.Interaction):
        if not await self._check_cmd_perm(interaction):
            return
        b = self.get_or_create(interaction.user.id)
        view = EmbedBuilderView(b)
        b.view = view
        b.original_interaction = interaction
        for row in view.build_rows():
            for btn in row:
                view.add_item(btn)
        await interaction.response.send_message(
            embed=_state_embed(b.state), view=view, ephemeral=True)
        try:
            view.message = await interaction.original_response()
        except Exception:
            pass

    @app_commands.command(name="embed_templates", description="Embed-Vorlagen verwalten")
    @app_commands.guild_only()
    @app_commands.describe(action="Aktion", name="Name der Vorlage")
    @app_commands.choices(action=[
        app_commands.Choice(name="Liste", value="list"),
        app_commands.Choice(name="Laden", value="load"),
        app_commands.Choice(name="Löschen", value="delete"),
        app_commands.Choice(name="Speichern (aktueller Stand)", value="save"),
    ])
    async def embed_templates(self, interaction, action: str, name: str = None):
        if not await self._check_cmd_perm(interaction):
            return
        if action == "list":
            await interaction.response.send_message(
                embed=await self.list_templates(interaction.guild_id), ephemeral=True)
        elif action in ("save", "load", "delete"):
            if not name:
                return await interaction.response.send_message(
                    embed=error_embed("Bitte einen Namen angeben."), ephemeral=True)
            b = self.get_or_create(interaction.user.id)
            if action == "load":
                ok, msg = await b.load_template(name, interaction.guild_id, interaction.user.id)
                await interaction.response.send_message(
                    embed=success_embed(msg) if ok else error_embed(msg), ephemeral=True)
            elif action == "delete":
                ok, msg = await self.delete_template(name, interaction.guild_id, interaction.user.id)
                await interaction.response.send_message(
                    embed=success_embed(msg) if ok else error_embed(msg), ephemeral=True)
            else:
                ok, msg = await b.save_template(name, interaction.guild_id, interaction.user.id)
                await interaction.response.send_message(
                    embed=success_embed(msg) if ok else error_embed(msg), ephemeral=True)
        else:
            await interaction.response.send_message(
                embed=error_embed("Unbekannte Aktion."), ephemeral=True)


# ----------------------------- Choices helper -----------------------------
def _color_option_objects(current: int):
    result = []
    for name, val in COLOR_PAIRS:
        if val == "random":
            continue
        default = (COLORS.get(val) == current)
        result.append(discord.SelectOption(label=name, value=val, default=default))
    result.append(discord.SelectOption(label="Zufällig", value="random"))
    result.append(discord.SelectOption(label="Benutzerdefiniert", value="custom"))
    return result


async def setup(bot: commands.Bot):
    await bot.add_cog(EmbedBuilderCog(bot))

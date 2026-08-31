import discord
from discord.ext import commands
from discord import app_commands

from utils import require_authorized, success_embed, error_embed

_COLORS = {
    "blau": discord.Color.blue(),
    "rot": discord.Color.red(),
    "gruen": discord.Color.green(),
    "orange": discord.Color.orange(),
    "pink": discord.Color.pink(),
    "gelb": discord.Color.gold(),
    "lila": discord.Color.purple(),
    "tuerkis": discord.Color.teal(),
    "grau": discord.Color.darker_grey(),
}
_COLOR_CHOICES = [
    app_commands.Choice(name="Blau", value="blau"),
    app_commands.Choice(name="Rot", value="rot"),
    app_commands.Choice(name="Grün", value="gruen"),
    app_commands.Choice(name="Orange", value="orange"),
    app_commands.Choice(name="Pink", value="pink"),
    app_commands.Choice(name="Gelb", value="gelb"),
    app_commands.Choice(name="Lila", value="lila"),
    app_commands.Choice(name="Türkis", value="tuerkis"),
    app_commands.Choice(name="Grau", value="grau"),
]


def _valid_url(url: str) -> bool:
    return url.lower().startswith("http://") or url.lower().startswith("https://")


def _get_color(value: str) -> discord.Color:
    if not value:
        return discord.Color.blue()
    if value.startswith("#"):
        value = value[1:]
    if len(value) == 6:
        try:
            return discord.Color(int(value, 16))
        except ValueError:
            pass
    return _COLORS.get(value, discord.Color.blue())


class EmbedBuilderCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="embed", description="Eine Embed-Nachricht erstellen und senden")
    @app_commands.describe(
        channel="Channel für die Nachricht",
        titel="Titel (max 256 Zeichen)",
        beschreibung="Beschreibung (max 4000 Zeichen)",
        farbe="Farbe des Embeds",
        image="Bild-URL",
        thumbnail="Thumbnail-URL",
        footer="Footer-Text",
        felder="Felder, Format: Name1;Wert1|Name2;Wert2 (max 25)"
    )
    @app_commands.choices(farbe=_COLOR_CHOICES)
    async def embed(self, interaction: discord.Interaction, channel: discord.TextChannel,
                    titel: str, beschreibung: str = "", farbe: str = "blau",
                    image: str = None, thumbnail: str = None, footer: str = None,
                    felder: str = ""):
        if not await require_authorized(interaction):
            return

        titel = titel.strip()
        beschreibung = beschreibung.strip()

        if len(titel) > 256:
            await interaction.response.send_message(embed=error_embed("Titel zu lang (max 256 Zeichen)"), ephemeral=True)
            return
        if len(beschreibung) > 4000:
            await interaction.response.send_message(embed=error_embed("Beschreibung zu lang (max 4000 Zeichen)"), ephemeral=True)
            return
        if not titel and not beschreibung and not felder.strip():
            await interaction.response.send_message(embed=error_embed("Titel oder Beschreibung erforderlich"), ephemeral=True)
            return
        for name, value in (("Bild", image), ("Thumbnail", thumbnail)):
            if value and not _valid_url(value):
                await interaction.response.send_message(embed=error_embed(f"{name}-URL muss mit http:// oder https:// beginnen"), ephemeral=True)
                return

        fields = []
        if felder.strip():
            for part in felder.split("|"):
                part = part.strip()
                if not part:
                    continue
                if ";" in part:
                    fname, _, fvalue = part.partition(";")
                elif ":" in part:
                    fname, _, fvalue = part.partition(":")
                else:
                    fname, fvalue = part, ""
                fname, fvalue = fname.strip(), fvalue.strip()
                if not fname:
                    continue
                if len(fields) >= 25:
                    break
                if len(fname) > 256 or len(fvalue) > 1024:
                    await interaction.response.send_message(
                        embed=error_embed("Feld-Name max 256 und Feld-Wert max 1024 Zeichen"),
                        ephemeral=True
                    )
                    return
                fields.append((fname, fvalue))

        embed = discord.Embed(
            title=titel or None,
            description=beschreibung or None,
            color=_get_color(farbe)
        )
        for fname, fvalue in fields:
            embed.add_field(name=fname, value=fvalue or "*leer*", inline=False)
        if footer:
            embed.set_footer(text=footer)
        if thumbnail:
            embed.set_thumbnail(url=thumbnail)
        if image:
            embed.set_image(url=image)

        try:
            msg = await channel.send(embed=embed)
        except discord.HTTPException:
            await interaction.response.send_message(
                embed=error_embed("Konnte den Embed nicht senden (fehlende Berechtigungen?)"),
                ephemeral=True
            )
            return

        await interaction.response.send_message(
            embed=success_embed(f"Embed gesendet in {channel.mention} — [Nachricht öffnen]({msg.jump_url})"),
            ephemeral=True
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(EmbedBuilderCog(bot))

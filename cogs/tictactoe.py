import discord
from discord.ext import commands
from discord import app_commands
import random

from utils import success_embed, error_embed, info_embed, create_embed

WIN_LINES = [
    (0, 1, 2), (3, 4, 5), (6, 7, 8),
    (0, 3, 6), (1, 4, 7), (2, 5, 8),
    (0, 4, 8), (2, 4, 6)
]


def check_winner(board):
    for a, b, c in WIN_LINES:
        if board[a] and board[a] == board[b] == board[c]:
            return board[a]
    return None


def bot_move(board):
    # 1. Gewinnen wenn moeglich
    for i in range(9):
        if board[i] is None:
            board[i] = "O"
            if check_winner(board) == "O":
                return i
            board[i] = None
    # 2. Blockieren
    for i in range(9):
        if board[i] is None:
            board[i] = "X"
            if check_winner(board) == "X":
                board[i] = None
                return i
            board[i] = None
    # 3. Mitte nehmen
    if board[4] is None:
        return 4
    # 4. Ecke nehmen
    corners = [i for i in [0, 2, 6, 8] if board[i] is None]
    if corners:
        return random.choice(corners)
    # 5. Beliebig
    free = [i for i in range(9) if board[i] is None]
    return random.choice(free) if free else None


class TicTacToeButton(discord.ui.Button):
    def __init__(self, index: int, game: "TicTacToeGame", cog: "TicTacToeCog"):
        self.game = game
        self.index = index
        self.cog = cog
        label = str(index + 1)
        style = discord.ButtonStyle.secondary
        if self.game.board[index] == "X":
            style = discord.ButtonStyle.danger
            label = "X"
        elif self.game.board[index] == "O":
            style = discord.ButtonStyle.primary
            label = "O"
        super().__init__(label=label, style=style, row=index // 3,
                         disabled=self.game.board[index] is not None or self.game.is_over)

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id not in self.game.players:
            await interaction.response.send_message(error_embed("Du spielst nicht mit!"), ephemeral=True)
            return
        if interaction.user.id != self.game.current_player:
            await interaction.response.send_message(error_embed("Du bist am Zug!"), ephemeral=True)
            return

        self.game.board[self.index] = self.game.current_symbol
        self.game.moves += 1

        winner = check_winner(self.game.board)
        if winner:
            self.game.winner = winner
            self.game.is_over = True
        elif self.game.moves == 9:
            self.game.is_over = True

        if not self.game.is_over:
            self.game.current_symbol = "O" if self.game.current_symbol == "X" else "X"
            self.game.current_player = self.game.player_O if self.game.current_symbol == "O" else self.game.player_X

        # Bot-Zug
        if not self.game.is_over and self.game.is_bot and self.game.current_player == self.game.bot_id:
            idx = bot_move(self.game.board)
            if idx is not None:
                self.game.board[idx] = "O"
                self.game.moves += 1
                winner = check_winner(self.game.board)
                if winner:
                    self.game.winner = winner
                    self.game.is_over = True
                elif self.game.moves == 9:
                    self.game.is_over = True
                if not self.game.is_over:
                    self.game.current_symbol = "X"
                    self.game.current_player = self.game.player_X

        if self.game.is_over:
            self.cog._save_stats(self.game)

        await interaction.response.edit_message(embed=self.game.build_embed(), view=self.game.build_view())


class TicTacToeView(discord.ui.View):
    def __init__(self, game: "TicTacToeGame"):
        super().__init__(timeout=300)
        self.game = game

    def build_buttons(self):
        self.clear_items()
        for i in range(9):
            self.add_item(TicTacToeButton(i, self.game, self.cog))

    async def on_timeout(self):
        self.game.is_over = True
        for child in self.children:
            child.disabled = True


class TicTacToeGame:
    def __init__(self, player_X: int, player_O: int, player_X_name: str, player_O_name: str, is_bot: bool = False):
        self.board: list[str | None] = [None] * 9
        self.player_X = player_X
        self.player_O = player_O
        self.player_X_name = player_X_name
        self.player_O_name = player_O_name
        self.current_player = player_X
        self.current_symbol = "X"
        self.moves = 0
        self.winner = None
        self.is_over = False
        self.is_bot = is_bot
        self.bot_id = -1

    @property
    def players(self):
        return {self.player_X, self.player_O}

    def _cell(self, i):
        return {"X": "❌", "O": "⭕", None: "⬛"}[self.board[i]]

    def build_embed(self):
        rows = [
            f"{self._cell(0)} {self._cell(1)} {self._cell(2)}",
            f"{self._cell(3)} {self._cell(4)} {self._cell(5)}",
            f"{self._cell(6)} {self._cell(7)} {self._cell(8)}",
        ]
        desc = "\n".join(rows)

        if self.winner:
            name = self.player_X_name if self.winner == "X" else self.player_O_name
            color = discord.Color.green()
            desc += f"\n\n🏆 **{name}** hat gewonnen!"
        elif self.is_over:
            color = discord.Color.orange()
            desc += "\n\n🤝 **Unentschieden!**"
        else:
            name = self.player_X_name if self.current_player == self.player_X else self.player_O_name
            symbol = "❌" if self.current_symbol == "X" else "⭕"
            color = discord.Color.blue()
            desc += f"\n\n{symbol} **{name}** ist am Zug"

        return create_embed(title="🎮 Tic Tac Toe", description=desc, color=color)

    def build_view(self):
        view = TicTacToeView(self)
        view.cog = self._cog
        view.build_buttons()
        return view


class TicTacToeCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.games: dict[int, TicTacToeGame] = {}
        self.stats: dict[str, dict] = {}  # {user_id: {"wins": 0, "losses": 0, "draws": 0}}

    def _load_stats(self):
        import json, os
        path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "tictactoe_stats.json")
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                self.stats = json.load(f)

    def _save_stats(self, game: TicTacToeGame):
        import json, os
        path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "tictactoe_stats.json")
        for uid in [str(game.player_X), str(game.player_O)]:
            if uid not in self.stats:
                self.stats[uid] = {"wins": 0, "losses": 0, "draws": 0}

        if game.is_bot:
            uid = str(game.player_X)
            if game.winner == "X":
                self.stats[uid]["wins"] += 1
            elif game.winner == "O":
                self.stats[uid]["losses"] += 1
            else:
                self.stats[uid]["draws"] += 1
        else:
            if game.winner:
                winner_uid = str(game.player_X) if game.winner == "X" else str(game.player_O)
                loser_uid = str(game.player_O) if game.winner == "X" else str(game.player_X)
                self.stats[winner_uid]["wins"] += 1
                self.stats[loser_uid]["losses"] += 1
            else:
                for uid in [str(game.player_X), str(game.player_O)]:
                    self.stats[uid]["draws"] += 1

        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.stats, f, indent=2, ensure_ascii=False)

    def cog_load(self):
        self._load_stats()

    ttt_group = app_commands.Group(name="tictactoe", description="Tic Tac Toe spielen")

    @ttt_group.command(name="play", description="Tic Tac Toe starten (gegen Bot oder User)")
    @app_commands.describe(gegner="Gegner erwaehnen (leer = gegen Bot)")
    async def play(self, interaction: discord.Interaction, gegner: discord.Member = None):
        await interaction.response.defer()

        if interaction.user.bot:
            await interaction.followup.send(error_embed("Bots koennen nicht spielen!"), ephemeral=True)
            return
        if gegner and gegner.bot:
            await interaction.followup.send(error_embed("Erwähne keinen Bot als Gegner — der Bot spielt automatisch!"), ephemeral=True)
            return
        if gegner and gegner.id == interaction.user.id:
            await interaction.followup.send(error_embed("Du kannst nicht gegen dich selbst spielen!"), ephemeral=True)
            return
        if interaction.user.id in self.games and not self.games[interaction.user.id].is_over:
            await interaction.followup.send(error_embed("Du hast bereits ein aktives Spiel!"), ephemeral=True)
            return

        is_bot = gegner is None
        if is_bot:
            player_O_id = self.bot.user.id
            player_O_name = "🤖 Bot"
        else:
            player_O_id = gegner.id
            player_O_name = gegner.display_name

        game = TicTacToeGame(
            player_X=interaction.user.id,
            player_O=player_O_id,
            player_X_name=interaction.user.display_name,
            player_O_name=player_O_name,
            is_bot=is_bot,
        )
        game.bot_id = self.bot.user.id
        game._cog = self
        self.games[interaction.user.id] = game
        if gegner:
            self.games[gegner.id] = game

        await interaction.followup.send(embed=game.build_embed(), view=game.build_view())

    @ttt_group.command(name="stats", description="Tic Tac Toe Statistiken anzeigen")
    async def stats(self, interaction: discord.Interaction):
        uid = str(interaction.user.id)
        s = self.stats.get(uid, {"wins": 0, "losses": 0, "draws": 0})
        total = s["wins"] + s["losses"] + s["draws"]
        embed = create_embed(
            title=f"🎮 Tic Tac Toe Stats — {interaction.user.display_name}",
            description=(
                f"🏆 Siege: **{s['wins']}**\n"
                f"❌ Niederlagen: **{s['losses']}**\n"
                f"🤝 Unentschieden: **{s['draws']}**\n"
                f"📊 Spiele gesamt: **{total}**"
            ),
            color=discord.Color.green()
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @ttt_group.command(name="reset", description="Tic Tac Toe Stats zuruecksetzen")
    async def reset_stats(self, interaction: discord.Interaction):
        uid = str(interaction.user.id)
        self.stats[uid] = {"wins": 0, "losses": 0, "draws": 0}
        import json, os
        path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "tictactoe_stats.json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.stats, f, indent=2, ensure_ascii=False)
        await interaction.response.send_message(embed=success_embed("✅ Stats zurueckgesetzt."), ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(TicTacToeCog(bot))

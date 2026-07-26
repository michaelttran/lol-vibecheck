"""The rating prompt — the Discord translation of the desktop popup (popup.py).

Uses DynamicItem so buttons keep working across bot restarts (the desktop
equivalent of "pending games survive a crash"). One click saves and edits the
message. Total interaction < 5 seconds, same as the PRD demands.
"""

import re

import discord

from .config import RATING_SCALE


def rating_embed(game, bot_user=None) -> discord.Embed:
    kda = f"{game['kills']}/{game['deaths']}/{game['assists']}"
    result = "Victory" if game["win"] else "Defeat"
    mins = (game["duration_seconds"] or 0) // 60
    e = discord.Embed(
        title="How was that game?",
        description=(
            f"**{game['champion']}** · {game['queue_type']} · {result}\n"
            f"KDA {kda} · {game['cs']} CS · {mins} min"
        ),
        color=0x57F287 if game["win"] else 0xED4245,
    )
    e.set_footer(text="Winrate is temporary. The vibes are forever.")
    return e


def rated_embed(game, score: int | None) -> discord.Embed:
    if score is None:
        e = discord.Embed(title="Skipped", description="This one never happened. 🫡",
                          color=0x99AAB5)
    else:
        emoji, label = RATING_SCALE[score]
        e = discord.Embed(
            title=f"{emoji} {label}",
            description=f"**{game['champion']}** · {game['queue_type']} — rated {score}/5",
            color=0x5865F2,
        )
    return e


class RateButton(discord.ui.DynamicItem[discord.ui.Button],
                 template=r"vibe:rate:(?P<game_id>\d+):(?P<score>\d)"):
    """Persistent rating button. score 0 = skip."""

    def __init__(self, game_id: int, score: int):
        emoji = RATING_SCALE[score][0] if score else "🙈"
        super().__init__(
            discord.ui.Button(
                emoji=emoji,
                style=discord.ButtonStyle.secondary,
                custom_id=f"vibe:rate:{game_id}:{score}",
            )
        )
        self.game_id, self.score = game_id, score

    @classmethod
    async def from_custom_id(cls, interaction, item, match: re.Match):
        return cls(int(match["game_id"]), int(match["score"]))

    async def callback(self, interaction: discord.Interaction):
        store = interaction.client.store
        game = store.get_game(self.game_id)
        if not game:
            await interaction.response.send_message("Game not found — data was reset?",
                                                    ephemeral=True)
            return
        # Only the player it belongs to can rate it (prompts can land in shared channels).
        player = store.get_player(interaction.user.id)
        if not player or player["puuid"] != game["puuid"]:
            await interaction.response.send_message("Not your game to judge. 👀", ephemeral=True)
            return
        if self.score == 0:
            store.rate_game(self.game_id, skipped=True)
            await interaction.response.edit_message(embed=rated_embed(game, None), view=None)
        else:
            store.rate_game(self.game_id, fun_score=self.score)
            await interaction.response.edit_message(embed=rated_embed(game, self.score), view=None)


def rating_view(game_id: int) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    for score in (1, 2, 3, 4, 5):
        view.add_item(RateButton(game_id, score))
    view.add_item(RateButton(game_id, 0))  # skip
    return view

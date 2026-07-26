"""Insights — the dashboard tabs translated to slash commands.

The Vibe Check -> /vibe        Champions -> /champions    The Squad -> /squad
Context       -> /context      Regret Curve -> /regret    To Rate -> /torate

Every stat states its sample size; anything under 5 games says
"not enough data yet" instead of pretending to be meaningful (PRD F21).
"""

import discord
from discord import app_commands
from discord.ext import commands

from ..config import MIN_SAMPLE_SIZE, RATING_SCALE
from ..views import rating_embed, rating_view

EMBED_COLOR = 0x5865F2


def _stars(avg):
    return f"{avg:.1f}/5" if avg is not None else "—"


def _face(avg):
    if avg is None:
        return "😶"
    return RATING_SCALE[max(1, min(5, round(avg)))][0]


def _need_data(n):
    return f"*not enough data yet ({n}/{MIN_SAMPLE_SIZE} games)*"


class StatsCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def _player_or_nag(self, interaction):
        player = self.bot.store.get_player(interaction.user.id)
        if not player:
            await interaction.response.send_message(
                "You're not linked yet — run `/link` first.", ephemeral=True
            )
        return player

    # ---------- The Vibe Check ----------

    @app_commands.command(description="Your overall vibe: average fun, bangers, and yikes.")
    async def vibe(self, interaction: discord.Interaction):
        player = await self._player_or_nag(interaction)
        if not player:
            return
        store, puuid = self.bot.store, player["puuid"]
        o = store.overall(puuid)
        e = discord.Embed(title=f"The Vibe Check — {player['game_name']}", color=EMBED_COLOR)
        if not o["n"]:
            e.description = "No rated games yet. Go play — I'll ask how it felt."
            await interaction.response.send_message(embed=e)
            return
        e.add_field(name="Average vibe", value=f"{_face(o['avg_fun'])} **{_stars(o['avg_fun'])}** "
                                               f"over {o['n']} rated games", inline=False)
        e.add_field(name="Winrate", value=f"{o['winrate'] * 100:.0f}%", inline=True)

        champs = store.by_dimension(puuid, "g.champion", MIN_SAMPLE_SIZE)
        if champs:
            best, worst = champs[0], champs[-1]
            e.add_field(name="Certified banger",
                        value=f"**{best['dim']}** {_stars(best['avg_fun'])} ({best['n']} games)",
                        inline=True)
            e.add_field(name="Certified yikes",
                        value=f"**{worst['dim']}** {_stars(worst['avg_fun'])} ({worst['n']} games)",
                        inline=True)
        # Copium tracking: fun in losses.
        loss = store.by_dimension(puuid, "g.win")
        loss_row = next((r for r in loss if r["dim"] == 0), None)
        if loss_row and loss_row["n"] >= MIN_SAMPLE_SIZE:
            e.add_field(name="Copium tracking",
                        value=f"Average vibe in **losses**: {_stars(loss_row['avg_fun'])} "
                              f"({loss_row['n']} games)", inline=False)
        e.set_footer(text="Winrate is temporary. The vibes are forever.")
        await interaction.response.send_message(embed=e)

    # ---------- Champions ----------

    @app_commands.command(description="Champion vibe tier list, and the champs you win with but hate.")
    async def champions(self, interaction: discord.Interaction):
        player = await self._player_or_nag(interaction)
        if not player:
            return
        rows = self.bot.store.by_dimension(player["puuid"], "g.champion", MIN_SAMPLE_SIZE)
        e = discord.Embed(title="Champion Vibe Tier List", color=EMBED_COLOR)
        if not rows:
            e.description = _need_data(0)
            await interaction.response.send_message(embed=e)
            return
        lines = [
            f"{_face(r['avg_fun'])} **{r['dim']}** — {_stars(r['avg_fun'])} vibe, "
            f"{r['winrate'] * 100:.0f}% WR ({r['n']} games)"
            for r in rows[:15]
        ]
        e.description = "\n".join(lines)
        # Vibes vs. Win Rate: winning but miserable / fun but losing.
        miserable = [r for r in rows if r["winrate"] >= 0.55 and r["avg_fun"] <= 2.5]
        joy = [r for r in rows if r["winrate"] <= 0.45 and r["avg_fun"] >= 4.0]
        if miserable:
            e.add_field(name="Winning but miserable 🏆😩",
                        value=", ".join(r["dim"] for r in miserable), inline=False)
        if joy:
            e.add_field(name="Losing but thriving 📉😎",
                        value=", ".join(r["dim"] for r in joy), inline=False)
        await interaction.response.send_message(embed=e)

    # ---------- The Squad ----------

    @app_commands.command(description="Do your friends make games better? Compare vibes with a squadmate.")
    @app_commands.describe(member="A server member who also linked their account")
    async def squad(self, interaction: discord.Interaction, member: discord.Member):
        player = await self._player_or_nag(interaction)
        if not player:
            return
        other = self.bot.store.get_player(member.id)
        if not other:
            await interaction.response.send_message(
                f"{member.display_name} hasn't `/link`ed yet — no vibes to compare.", ephemeral=True
            )
            return
        store = self.bot.store
        with_them, without = store.fun_with_teammate(player["puuid"], other["puuid"])
        e = discord.Embed(title=f"The Squad — you & {other['game_name']}", color=EMBED_COLOR)
        if with_them["n"] < MIN_SAMPLE_SIZE:
            e.description = _need_data(with_them["n"])
        else:
            delta = (with_them["avg_fun"] or 0) - (without["avg_fun"] or 0)
            verdict = ("certified vibe enabler ✨" if delta > 0.3
                       else "vibe neutral 😐" if delta > -0.3 else "we need to talk 💀")
            e.add_field(name=f"With {other['game_name']}",
                        value=f"{_stars(with_them['avg_fun'])} ({with_them['n']} games)", inline=True)
            e.add_field(name="Without them",
                        value=f"{_stars(without['avg_fun'])} ({without['n']} games)", inline=True)
            e.add_field(name="Verdict", value=f"{delta:+.1f} squad buff — {verdict}", inline=False)
        # Mutual vibe matrix: same game, both ratings side by side.
        shared = [r for r in store.shared_games(player["puuid"], other["puuid"])
                  if r["fun_a"] and r["fun_b"]][:5]
        if shared:
            lines = [
                f"{r['champ_a']} vs their {r['champ_b']}: "
                f"you {RATING_SCALE[r['fun_a']][0]} {r['fun_a']}/5 · "
                f"them {RATING_SCALE[r['fun_b']][0]} {r['fun_b']}/5"
                for r in shared
            ]
            e.add_field(name="Mutual vibe matrix (argument settled with data)",
                        value="\n".join(lines), inline=False)
        await interaction.response.send_message(embed=e)

    # ---------- Context ----------

    @app_commands.command(description="Vibe by queue, role, hour, or day.")
    @app_commands.choices(dimension=[
        app_commands.Choice(name="Queue", value="g.queue_type"),
        app_commands.Choice(name="Role", value="NULLIF(g.role, '')"),
        app_commands.Choice(name="Hour of day", value="CAST(strftime('%H', g.played_at) AS INT)"),
        app_commands.Choice(name="Day of week", value="strftime('%w', g.played_at)"),
        app_commands.Choice(name="Win / Loss", value="CASE g.win WHEN 1 THEN 'Win' ELSE 'Loss' END"),
    ])
    async def context(self, interaction: discord.Interaction,
                      dimension: app_commands.Choice[str]):
        player = await self._player_or_nag(interaction)
        if not player:
            return
        rows = self.bot.store.by_dimension(player["puuid"], dimension.value, MIN_SAMPLE_SIZE)
        e = discord.Embed(title=f"Context Vibecheck — by {dimension.name.lower()}",
                          color=EMBED_COLOR)
        days = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
        if not rows:
            e.description = _need_data(0)
        else:
            lines = []
            for r in rows:
                label = r["dim"]
                if dimension.name == "Day of week":
                    label = days[int(label)]
                elif dimension.name == "Hour of day":
                    label = f"{label:02d}:00"
                lines.append(f"{_face(r['avg_fun'])} **{label}** — {_stars(r['avg_fun'])} "
                             f"({r['n']} games)")
            e.description = "\n".join(lines)
        await interaction.response.send_message(embed=e)

    # ---------- Regret Curve ----------

    @app_commands.command(description="Which game of the night your vibe falls off a cliff.")
    async def regret(self, interaction: discord.Interaction):
        player = await self._player_or_nag(interaction)
        if not player:
            return
        rows = self.bot.store.by_dimension(player["puuid"], "g.game_index_in_session")
        rows = sorted(rows, key=lambda r: r["dim"])
        e = discord.Embed(title="The Regret Curve", color=EMBED_COLOR)
        eligible = [r for r in rows if r["n"] >= MIN_SAMPLE_SIZE]
        if not eligible:
            e.description = _need_data(sum(r["n"] for r in rows))
        else:
            bar = "█"
            lines = [
                f"Game {r['dim']}: {bar * round((r['avg_fun'] or 0) * 2)} "
                f"{_stars(r['avg_fun'])} ({r['n']})"
                for r in eligible
            ]
            e.description = "\n".join(lines)
            cliff = min(eligible, key=lambda r: r["avg_fun"] or 5)
            e.set_footer(text=f"The data says: maybe log off before game {cliff['dim']}.")
        await interaction.response.send_message(embed=e)

    # ---------- To Rate ----------

    @app_commands.command(description="Games you haven't given a verdict yet.")
    async def torate(self, interaction: discord.Interaction):
        player = await self._player_or_nag(interaction)
        if not player:
            return
        pending = self.bot.store.pending_games(player["puuid"], limit=5)
        if not pending:
            await interaction.response.send_message("All caught up. Vibes fully accounted for. ✅",
                                                    ephemeral=True)
            return
        await interaction.response.send_message(
            f"**{len(pending)}** game(s) awaiting a verdict:", ephemeral=True
        )
        for game in pending:
            await interaction.followup.send(embed=rating_embed(game),
                                            view=rating_view(game["id"]), ephemeral=True)


async def setup(bot):
    await bot.add_cog(StatsCog(bot))

"""Account linking & setup — replaces the desktop install flow.

Instead of downloading an .exe, you /link your Riot ID once.
"""

import discord
from discord import app_commands
from discord.ext import commands

from ..config import PLATFORM_TO_REGION
from ..riot import RiotAPIError

PLATFORM_CHOICES = [
    app_commands.Choice(name=p.upper(), value=p) for p in sorted(PLATFORM_TO_REGION)
][:25]


class LinkCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(description="Link your Riot account. e.g. riot_id: Faker#KR1")
    @app_commands.describe(riot_id="Your Riot ID as GameName#TAG",
                           platform="Your server (NA1, EUW1, KR, ...)")
    @app_commands.choices(platform=PLATFORM_CHOICES)
    async def link(self, interaction: discord.Interaction, riot_id: str, platform: str):
        await interaction.response.defer(ephemeral=True)
        if "#" not in riot_id:
            await interaction.followup.send("Riot ID must look like `GameName#TAG`.")
            return
        game_name, _, tag_line = riot_id.partition("#")
        try:
            puuid, region = await self.bot.riot.resolve_riot_id(
                game_name.strip(), tag_line.strip(), platform
            )
        except RiotAPIError as err:
            await interaction.followup.send(f"Riot API problem: {err}")
            return
        if not puuid:
            await interaction.followup.send(f"No account found for **{riot_id}** — typo in the tag?")
            return
        self.bot.store.link_player(
            interaction.user.id, puuid, game_name.strip(), tag_line.strip(), platform, region
        )
        await interaction.followup.send(
            f"Linked **{game_name}#{tag_line}** ✅\n"
            "When a game ends I'll ask **how it felt** — one tap, that's it. "
            "By default I DM you; use `/setchannel` to get prompts in a channel instead.\n"
            "*The vibes start now. No backfill — winrate is temporary, but so is the past.*"
        )

    @app_commands.command(description="Unlink your Riot account and stop prompts.")
    async def unlink(self, interaction: discord.Interaction):
        self.bot.store.unlink_player(interaction.user.id)
        await interaction.response.send_message("Unlinked. The vibes remain in your heart.",
                                                ephemeral=True)

    @app_commands.command(description="Get your post-game prompts in this channel instead of DMs.")
    async def setchannel(self, interaction: discord.Interaction):
        if not self.bot.store.get_player(interaction.user.id):
            await interaction.response.send_message("Run `/link` first.", ephemeral=True)
            return
        self.bot.store.set_prompt_channel(interaction.user.id, interaction.channel_id)
        await interaction.response.send_message(
            f"Prompts will land in <#{interaction.channel_id}>. Your squad will see your verdicts. 👀"
        )

    @app_commands.command(description="Switch prompts back to DMs.")
    async def setdm(self, interaction: discord.Interaction):
        self.bot.store.set_prompt_channel(interaction.user.id, None)
        await interaction.response.send_message("Prompts moved back to DMs.", ephemeral=True)


async def setup(bot):
    await bot.add_cog(LinkCog(bot))

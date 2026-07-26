"""Background poller — replaces the desktop app's LCU gameflow watcher (app.py loop).

The desktop app got a push event when a game ended; a bot has to poll match-v5.
Same watermark-sweep design as the original F6 requirement: on each pass, find
finished games newer than the stored watermark, import them, prompt for the
newest, and let older ones fall into "To Rate".
"""

import logging
import time

import discord
from discord.ext import tasks

from .config import FIRST_LINK_LOOKBACK_SECONDS, POLL_INTERVAL_SECONDS
from .riot import RiotAPIError, normalize
from .views import rating_embed, rating_view

log = logging.getLogger(__name__)


class Poller:
    def __init__(self, bot):
        self.bot = bot
        self.loop = tasks.loop(seconds=POLL_INTERVAL_SECONDS)(self._tick)

    def start(self):
        self.loop.start()

    async def _tick(self):
        await self.bot.wait_until_ready()
        for player in self.bot.store.all_players():
            try:
                await self._sweep_player(player)
            except RiotAPIError as err:
                log.warning("sweep failed for %s: %s", player["game_name"], err)
            except Exception:  # noqa: BLE001 — one bad player must not kill the loop
                log.exception("unexpected error sweeping %s", player["game_name"])

    async def _sweep_player(self, player):
        riot, store = self.bot.riot, self.bot.store
        # First sweep after linking looks back 3h max — no deep backfill.
        start_time = None
        if not player["last_seen_match_id"]:
            start_time = int(time.time()) - FIRST_LINK_LOOKBACK_SECONDS
        match_ids = await riot.recent_match_ids(
            player["puuid"], player["region"], count=5, start_time=start_time
        )
        if not match_ids:
            return
        # Everything newer than the watermark, oldest first.
        new_ids = []
        for mid in match_ids:
            if mid == player["last_seen_match_id"]:
                break
            new_ids.append(mid)
        new_ids.reverse()

        newest_game_id = None
        for mid in new_ids:
            match = await riot.match_detail(mid, player["region"])
            if not match:
                continue
            game, participants = normalize(match, player["puuid"])
            if not game:
                continue
            game_id = store.insert_game(game, participants)
            # Remakes are recorded but never prompted (PRD F5).
            if game_id and not game["is_remake"]:
                newest_game_id = game_id
        if new_ids:
            store.set_watermark(player["discord_user_id"], match_ids[0])
        # Prompt only for the newest; older imports wait in To Rate (PRD F6).
        if newest_game_id:
            await self._send_prompt(player, newest_game_id)

    async def _send_prompt(self, player, game_id):
        game = self.bot.store.get_game(game_id)
        embed, view = rating_embed(game), rating_view(game_id)
        dest = None
        if player["prompt_channel_id"]:
            dest = self.bot.get_channel(player["prompt_channel_id"])
        if dest is None:
            try:
                dest = await self.bot.fetch_user(player["discord_user_id"])
            except discord.NotFound:
                return
        try:
            content = f"<@{player['discord_user_id']}>" if player["prompt_channel_id"] else None
            await dest.send(content=content, embed=embed, view=view)
        except discord.Forbidden:
            log.warning("cannot DM/post prompt for %s (closed DMs?)", player["game_name"])

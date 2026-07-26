"""Local game-end watcher — the desktop app's gameflow listener, reborn.

Runs alongside the poller when the bot shares a machine with the League client
(macOS or Windows). Watches the gameflow phase; the moment a game ends it pulls
the stats straight from the client and prompts, which is both instant (no ~1-3
min indexing wait) and able to see games the public API hides, ARAM: Mayhem and
custom lobbies included.

Games land in the same table with the same match-id shape as the poller's, so
the two paths dedupe against each other instead of double-prompting.
"""

import logging

from discord.ext import tasks

from .config import LCU_POLL_SECONDS
from .lcu import END_PHASES, LCUClient, normalize_eog, normalize_history, resolve_credentials

log = logging.getLogger(__name__)


def _key(name) -> str:
    """Riot matches IDs ignoring case and spaces — 'Toilet Paper' == 'ToiletPaper'."""
    return (name or "").replace(" ", "").casefold()


class LCUWatcher:
    def __init__(self, bot):
        self.bot = bot
        self.client: LCUClient | None = None
        self._phase = None
        self._last_game_id = None
        self._connected = False
        self._warned_unlinked = False
        self._discovery_skips = 0
        self.loop = tasks.loop(seconds=LCU_POLL_SECONDS)(self._tick)

    def start(self):
        self.loop.start()

    async def close(self):
        if self.client:
            await self.client.close()
            self.client = None

    # ---------- connection ----------

    async def _ensure_client(self) -> bool:
        """Attach to the client if it's running. Silent when League simply isn't open."""
        if self.client:
            return True
        if self._discovery_skips > 0:  # League wasn't up a moment ago; don't rescan every tick
            self._discovery_skips -= 1
            return False
        creds = resolve_credentials()
        if not creds:
            self._discovery_skips = max(1, 60 // max(LCU_POLL_SECONDS, 1))
            if self._connected:  # client just closed
                log.info("League client closed — LCU capture idle.")
                self._connected = False
                self._phase = None
            return False
        self.client = LCUClient(*creds)
        self._connected = True
        log.info("LCU capture attached to the League client on port %s.", creds[0])
        return True

    async def _drop_client(self):
        """Client went away (or the port rotated) — reconnect on a later tick."""
        if self.client:
            await self.client.close()
        self.client = None
        self._phase = None

    # ---------- the loop ----------

    async def _tick(self):
        await self.bot.wait_until_ready()
        try:
            if not await self._ensure_client():
                return
            phase = await self.client.gameflow_phase()
            if phase is None:
                await self._drop_client()
                return
            if phase == self._phase:
                return
            previous, self._phase = self._phase, phase
            # Fire once on the transition into an end-of-game phase.
            if phase in END_PHASES and previous not in END_PHASES:
                await self._capture()
        except Exception:  # noqa: BLE001 — a bad tick must never kill the loop
            log.exception("LCU watcher tick failed")

    def _match_player(self, summoner: dict):
        """Find the linked row for whoever is logged into the client.

        The client doesn't always report the same puuid account-v1 gave us at
        /link time, so fall back to the Riot ID before giving up.
        """
        puuid = summoner.get("puuid") or ""
        if puuid:
            rows = self.bot.store.players_by_puuids([puuid])
            if rows:
                return rows[0]
        name, tag = _key(summoner.get("gameName")), _key(summoner.get("tagLine"))
        if not name:
            return None
        for row in self.bot.store.all_players():
            if _key(row["game_name"]) == name and (not tag or _key(row["tag_line"]) == tag):
                log.info("matched %s#%s by Riot ID (client puuid differs from the linked one)",
                         row["game_name"], row["tag_line"])
                return row
        return None

    async def _capture(self):
        summoner = await self.client.current_summoner() or {}
        puuid = summoner.get("puuid") or ""
        player = self._match_player(summoner)
        if not player:
            if not self._warned_unlinked:
                log.warning(
                    "LCU saw a game for %s#%s (puuid %s...) but no linked player matches. "
                    "Run /link with that exact Riot ID.",
                    summoner.get("gameName") or "?", summoner.get("tagLine") or "?",
                    puuid[:12] or "none",
                )
                self._warned_unlinked = True
            return
        self._warned_unlinked = False

        # The eog block is the richest source but disappears once the player clicks
        # past the post-game screen, so fall back to the client's match history.
        # Games are always filed under the linked puuid, whatever the client reports,
        # or the stats commands would never find them.
        eog = await self.client.eog_stats()
        if eog and eog.get("gameId"):
            game, participants = normalize_eog(
                eog, puuid, player["platform"] or "", player["puuid"]
            )
        else:
            match = await self.client.recent_match()
            if not match:
                log.warning("game ended but the client returned no stats — skipped.")
                return
            game, participants = normalize_history(
                match, puuid, player["platform"] or "", player["puuid"]
            )
        if not game:
            return

        if game["riot_match_id"] == self._last_game_id:
            return
        self._last_game_id = game["riot_match_id"]

        game["champion"] = await self.client.champion_name(game.get("champion_id") or 0)
        game_id = self.bot.store.insert_game(game, participants)
        if not game_id:  # the poller already imported it
            return
        log.info("LCU captured %s (%s, %s)", game["riot_match_id"],
                 game["champion"], game["queue_type"])
        # Remakes are recorded but never prompted (PRD F5).
        if not game["is_remake"]:
            await self.bot.poller.send_prompt(player, game_id)

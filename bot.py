"""VibeCheck.lol, but it lives in your Discord server instead of your system tray."""

import asyncio
import logging

import aiohttp
import discord
from discord.ext import commands

from vibecheck.config import DB_PATH, DISCORD_TOKEN, LCU_ENABLED, RIOT_API_KEY
from vibecheck.lcu_watcher import LCUWatcher
from vibecheck.poller import Poller
from vibecheck.riot import RiotClient
from vibecheck.store import GameStore
from vibecheck.views import RateButton

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("vibecheck")


class VibeCheckBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=discord.Intents.default())
        self.store = GameStore(DB_PATH)
        self.riot: RiotClient | None = None
        self.poller = Poller(self)
        # Local capture is opt-in and only makes sense on the player's own machine.
        self.lcu = LCUWatcher(self) if LCU_ENABLED else None

    async def setup_hook(self):
        self.riot = RiotClient(aiohttp.ClientSession())
        self.add_dynamic_items(RateButton)  # rating buttons survive restarts
        for ext in ("vibecheck.cogs.link", "vibecheck.cogs.stats"):
            await self.load_extension(ext)
        await self.tree.sync()
        self.poller.start()
        if self.lcu:
            self.lcu.start()
            log.info("Local LCU capture enabled — Mayhem and customs included.")
        log.info("VibeCheck is running. Winrate is temporary; the vibes are forever.")

    async def close(self):
        if self.lcu:
            await self.lcu.close()
        if self.riot:
            await self.riot._session.close()
        self.store.close()
        await super().close()


def main():
    if not DISCORD_TOKEN or not RIOT_API_KEY:
        raise SystemExit("Set DISCORD_TOKEN and RIOT_API_KEY (see .env.example).")
    VibeCheckBot().run(DISCORD_TOKEN)


if __name__ == "__main__":
    main()

"""Central config. Everything tunable lives here (mirrors the desktop app's config.py)."""

import os
from pathlib import Path

DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN", "")
RIOT_API_KEY = os.environ.get("RIOT_API_KEY", "")

DB_PATH = Path(os.environ.get("VIBECHECK_DB", "vibecheck.sqlite3"))

# How often the background poller sweeps each linked player's match history.
POLL_INTERVAL_SECONDS = int(os.environ.get("VIBECHECK_POLL_SECONDS", "120"))

# On first link, how far back to import (mirrors the desktop app's "3h max, no deep backfill").
FIRST_LINK_LOOKBACK_SECONDS = 3 * 3600

# A session = games separated by < 1h (PRD F17).
SESSION_GAP_SECONDS = 3600

# Stats floor: anything under this shows "not enough data yet" (PRD F21).
MIN_SAMPLE_SIZE = 5

# The rating scale. Winrate is temporary. The vibes are forever.
RATING_SCALE = {
    1: ("😨", "FF at 15"),
    2: ("🤨", "Who Let Them Cook?"),
    3: ("😐", "Meh"),
    4: ("😎", "We Are So Back"),
    5: ("👑", "Gigachad"),
}

DEFAULT_TAGS = ["int diff", "clutched it", "tilted", "carried", "good squad", "troll in team"]

# Platform (match/summoner data) -> regional routing (account-v1 / match-v5) hosts.
PLATFORM_TO_REGION = {
    "na1": "americas", "br1": "americas", "la1": "americas", "la2": "americas",
    "euw1": "europe", "eun1": "europe", "tr1": "europe", "ru": "europe", "me1": "europe",
    "kr": "asia", "jp1": "asia",
    "oc1": "sea", "sg2": "sea", "tw2": "sea", "vn2": "sea",
}

QUEUE_NAMES = {
    420: "Ranked Solo/Duo", 440: "Ranked Flex", 400: "Normal Draft",
    430: "Normal Blind", 450: "ARAM", 490: "Quickplay", 700: "Clash",
    1700: "Arena", 900: "URF", 1900: "URF", 720: "ARAM Clash",
}


def queue_label(queue_id: int | None, raw_mode: str = "") -> str:
    """Known queue ids get friendly labels; unknown ids keep the raw name (PRD F3b)."""
    if queue_id in QUEUE_NAMES:
        return QUEUE_NAMES[queue_id]
    return raw_mode or f"Queue {queue_id}"

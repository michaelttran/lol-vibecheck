"""Riot public API client — replaces the desktop app's lcu.py.

The LCU local API only exists on the player's PC, so a bot uses account-v1
(Riot ID -> puuid) and match-v5 (match history + details) instead. Parsing is
defensive like the original capture.py: best-effort with fallbacks, raw payload
always preserved, normalize() never raises.
"""

import asyncio
import logging
from datetime import datetime, timezone

import aiohttp

from .config import PLATFORM_TO_REGION, RIOT_API_KEY, queue_label

log = logging.getLogger(__name__)


class RiotAPIError(Exception):
    pass


class RiotClient:
    def __init__(self, session: aiohttp.ClientSession):
        self._session = session

    async def _get(self, host: str, path: str, params: dict | None = None):
        url = f"https://{host}.api.riotgames.com{path}"
        headers = {"X-Riot-Token": RIOT_API_KEY}
        for attempt in range(3):
            async with self._session.get(url, headers=headers, params=params) as resp:
                if resp.status == 429:  # rate limited — respect Retry-After
                    wait = int(resp.headers.get("Retry-After", "2"))
                    log.warning("429 from Riot, sleeping %ss", wait)
                    await asyncio.sleep(wait)
                    continue
                if resp.status == 404:
                    return None
                if resp.status >= 400:
                    raise RiotAPIError(f"{resp.status} on {path}")
                return await resp.json()
        raise RiotAPIError(f"rate-limited out on {path}")

    async def resolve_riot_id(self, game_name: str, tag_line: str, platform: str):
        """Riot ID -> puuid via account-v1 on the platform's regional host."""
        region = PLATFORM_TO_REGION.get(platform)
        if not region:
            raise RiotAPIError(f"unknown platform '{platform}'")
        data = await self._get(
            region, f"/riot/account/v1/accounts/by-riot-id/{game_name}/{tag_line}"
        )
        return (data["puuid"], region) if data else (None, region)

    async def recent_match_ids(self, puuid: str, region: str, count: int = 5,
                               start_time: int | None = None):
        params = {"count": count}
        if start_time:
            params["startTime"] = start_time
        return await self._get(
            region, f"/lol/match/v5/matches/by-puuid/{puuid}/ids", params
        ) or []

    async def match_detail(self, match_id: str, region: str):
        return await self._get(region, f"/lol/match/v5/matches/{match_id}")


def normalize(match: dict, puuid: str) -> tuple[dict | None, list]:
    """Match-v5 payload -> (game record, participants). Never raises."""
    try:
        info = match.get("info", {})
        me = next((p for p in info.get("participants", []) if p.get("puuid") == puuid), None)
        if not me:
            return None, []
        played_at = datetime.fromtimestamp(
            (info.get("gameStartTimestamp") or 0) / 1000, tz=timezone.utc
        ).isoformat()
        game = {
            "puuid": puuid,
            "riot_match_id": match.get("metadata", {}).get("matchId", ""),
            "played_at": played_at,
            "queue_id": info.get("queueId"),
            "queue_type": queue_label(info.get("queueId"), info.get("gameMode", "")),
            "champion": me.get("championName", "?"),
            "role": me.get("teamPosition") or me.get("role") or "",
            "win": int(bool(me.get("win"))),
            "kills": me.get("kills", 0),
            "deaths": me.get("deaths", 0),
            "assists": me.get("assists", 0),
            "cs": (me.get("totalMinionsKilled", 0) + me.get("neutralMinionsKilled", 0)),
            "duration_seconds": info.get("gameDuration", 0),
            "is_remake": int(bool(me.get("gameEndedInEarlySurrender"))),
            "raw_payload": info,
        }
        participants = [
            {
                "puuid": p.get("puuid", ""),
                "riot_id": f"{p.get('riotIdGameName', '?')}#{p.get('riotIdTagline', '')}",
                "same_team": int(p.get("teamId") == me.get("teamId")),
            }
            for p in info.get("participants", [])
            if p.get("puuid") and p.get("puuid") != puuid
        ]
        return game, participants
    except Exception:  # noqa: BLE001 — worst case we skip the game, never crash the poller
        log.exception("normalize failed for match %s", match.get("metadata", {}).get("matchId"))
        return None, []

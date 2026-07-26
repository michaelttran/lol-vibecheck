"""League Client (LCU) API — the local companion path, on macOS *and* Windows.

Why this exists: Riot's public match-v5 API deliberately hides some games.
ARAM: Mayhem returns 403 (developer-relations #1109) and custom lobbies were
walled behind RSO in July 2024, so the poller can never see them — OP.GG can't
either. The League client's own local API does see them, which is exactly what
the original Windows tray app read.

The client runs on both macOS and Windows, and so does this module: credentials
come from the lockfile or the running process's command line, both discovered
per-platform. Parsing is best-effort like riot.py — normalize never raises.
"""

import asyncio
import base64
import json
import logging
import platform
import re
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import aiohttp

from .config import LCU_LOCKFILE, queue_label

log = logging.getLogger(__name__)

# Where the client drops its lockfile on a default install.
_DEFAULT_LOCKFILES = {
    "Darwin": ["/Applications/League of Legends.app/Contents/LoL/lockfile"],
    "Windows": [
        r"C:\Riot Games\League of Legends\lockfile",
        r"C:\Program Files\Riot Games\League of Legends\lockfile",
        r"C:\Program Files (x86)\Riot Games\League of Legends\lockfile",
    ],
}

# Phases that mean "the game just ended, stats are available".
END_PHASES = {"WaitingForStats", "PreEndOfGame", "EndOfGame"}


def _lockfile_candidates():
    if LCU_LOCKFILE:
        yield Path(LCU_LOCKFILE)
    for raw in _DEFAULT_LOCKFILES.get(platform.system(), []):
        yield Path(raw)


def _creds_from_lockfile():
    """lockfile format: LeagueClient:PID:PORT:PASSWORD:https"""
    for path in _lockfile_candidates():
        try:
            parts = path.read_text().strip().split(":")
        except OSError:
            continue
        if len(parts) >= 4:
            return int(parts[2]), parts[3]
    return None


def _client_command_line() -> str:
    """The running LeagueClientUx command line — carries port and auth token."""
    try:
        if platform.system() == "Windows":
            out = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "Get-CimInstance Win32_Process -Filter \"name='LeagueClientUx.exe'\""
                 " | Select-Object -ExpandProperty CommandLine"],
                capture_output=True, text=True, timeout=10,
            )
        else:  # macOS (and Linux under Wine, where the args look the same)
            out = subprocess.run(["ps", "x", "-o", "args="],
                                 capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return ""
    return out.stdout or ""


def _creds_from_process():
    line = _client_command_line()
    if "LeagueClientUx" not in line:
        return None
    port = re.search(r"--app-port=(\d+)", line)
    token = re.search(r"--remoting-auth-token=([\w-]+)", line)
    if port and token:
        return int(port.group(1)), token.group(1)
    return None


def resolve_credentials():
    """(port, password) for the running client, or None if it isn't up."""
    return _creds_from_lockfile() or _creds_from_process()


class LCUClient:
    """Talks to https://127.0.0.1:<port> with the client's self-signed cert."""

    def __init__(self, port: int, password: str):
        self.port, self.password = port, password
        auth = base64.b64encode(f"riot:{password}".encode()).decode()
        self._session = aiohttp.ClientSession(
            base_url=f"https://127.0.0.1:{port}",
            connector=aiohttp.TCPConnector(ssl=False),  # loopback, cert is self-signed
            headers={"Authorization": f"Basic {auth}"},
            timeout=aiohttp.ClientTimeout(total=10),
        )
        self._champions: dict[int, str] = {}

    async def close(self):
        await self._session.close()

    async def _get(self, path: str):
        try:
            async with self._session.get(path) as resp:
                if resp.status >= 400:
                    return None
                return await resp.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError, json.JSONDecodeError):
            return None

    async def gameflow_phase(self):
        """'None' | 'Lobby' | 'ChampSelect' | 'InProgress' | 'EndOfGame' | ..."""
        return await self._get("/lol-gameflow/v1/gameflow-phase")

    async def current_summoner(self):
        return await self._get("/lol-summoner/v1/current-summoner")

    async def eog_stats(self):
        """Stats block for the game that just ended — includes Mayhem and customs."""
        return await self._get("/lol-end-of-game/v1/eog-stats-block")

    async def recent_match(self):
        """Newest game from the client's own match history (fallback path)."""
        data = await self._get(
            "/lol-match-history/v1/products/lol/current-summoner/matches"
            "?begIndex=0&endIndex=1"
        )
        games = ((data or {}).get("games") or {}).get("games") or []
        return games[0] if games else None

    async def champion_name(self, champion_id: int) -> str:
        """championId -> 'Aatrox'. The client ships the asset, so this stays local."""
        if not champion_id:
            return "?"
        if champion_id not in self._champions:
            data = await self._get(f"/lol-game-data/assets/v1/champions/{champion_id}.json")
            self._champions[champion_id] = (data or {}).get("alias") or "?"
        return self._champions[champion_id]


def _riot_id(player: dict) -> str:
    name = player.get("riotIdGameName") or player.get("summonerName") or "?"
    tag = player.get("riotIdTagline") or player.get("riotIdTagLine") or ""
    return f"{name}#{tag}"


def normalize_eog(eog: dict, puuid: str, platform_id: str):
    """eog-stats-block -> (game, participants), matching riot.normalize()'s shape."""
    try:
        game_id = eog.get("gameId")
        if not game_id:
            return None, []
        local = eog.get("localPlayer") or {}
        stats = local.get("stats") or {}
        teams = eog.get("teams") or []
        my_team = next((t for t in teams if t.get("isPlayerTeam")), {})
        # Stat keys are SCREAMING_CASE here, unlike match-v5's camelCase.
        won = bool(my_team.get("isWinningTeam")) or bool(stats.get("WIN"))
        duration = int(eog.get("gameLength") or 0)
        ended = datetime.now(timezone.utc)
        game = {
            "puuid": puuid,
            # Same id shape as match-v5 so a later poll dedupes instead of duplicating.
            "riot_match_id": f"{platform_id.upper()}_{game_id}",
            "played_at": (ended - timedelta(seconds=duration)).isoformat(),
            "queue_id": eog.get("queueId"),
            "queue_type": queue_label(eog.get("queueId"), eog.get("gameMode", "")),
            "champion": "?",  # resolved via LCUClient.champion_name
            "champion_id": local.get("championId") or 0,
            "role": "",  # the eog block carries no position
            "win": int(won),
            "kills": int(stats.get("CHAMPIONS_KILLED") or 0),
            "deaths": int(stats.get("NUM_DEATHS") or 0),
            "assists": int(stats.get("ASSISTS") or 0),
            "cs": int(stats.get("MINIONS_KILLED") or 0)
                  + int(stats.get("NEUTRAL_MINIONS_KILLED") or 0),
            "duration_seconds": duration,
            "is_remake": int(bool(stats.get("GAME_ENDED_IN_EARLY_SURRENDER"))),
            "raw_payload": eog,
        }
        participants = []
        for team in teams:
            same = int(bool(team.get("isPlayerTeam")))
            for p in team.get("players") or []:
                if p.get("puuid") and p["puuid"] != puuid:
                    participants.append(
                        {"puuid": p["puuid"], "riot_id": _riot_id(p), "same_team": same}
                    )
        return game, participants
    except Exception:  # noqa: BLE001 — worst case we skip the game, never kill the watcher
        log.exception("normalize_eog failed")
        return None, []


def normalize_history(match: dict, puuid: str, platform_id: str):
    """LCU match-history game -> (game, participants). Fallback when eog is gone."""
    try:
        game_id = match.get("gameId")
        if not game_id:
            return None, []
        parts = match.get("participants") or []
        idents = match.get("participantIdentities") or []
        mine_idx = next(
            (i for i, ident in enumerate(idents)
             if ((ident.get("player") or {}).get("puuid")) == puuid),
            0,
        )
        me = parts[mine_idx] if mine_idx < len(parts) else (parts[0] if parts else {})
        stats = me.get("stats") or {}
        timeline = me.get("timeline") or {}
        duration = int(match.get("gameDuration") or 0)
        created = match.get("gameCreation")
        played_at = (
            datetime.fromtimestamp(created / 1000, tz=timezone.utc)
            if created else datetime.now(timezone.utc) - timedelta(seconds=duration)
        )
        game = {
            "puuid": puuid,
            "riot_match_id": f"{platform_id.upper()}_{game_id}",
            "played_at": played_at.isoformat(),
            "queue_id": match.get("queueId"),
            "queue_type": queue_label(match.get("queueId"), match.get("gameMode", "")),
            "champion": "?",
            "champion_id": me.get("championId") or 0,
            "role": timeline.get("lane") or timeline.get("role") or "",
            "win": int(bool(stats.get("win"))),
            "kills": int(stats.get("kills") or 0),
            "deaths": int(stats.get("deaths") or 0),
            "assists": int(stats.get("assists") or 0),
            "cs": int(stats.get("totalMinionsKilled") or 0)
                  + int(stats.get("neutralMinionsKilled") or 0),
            "duration_seconds": duration,
            "is_remake": int(bool(stats.get("gameEndedInEarlySurrender"))),
            "raw_payload": match,
        }
        my_team_id = me.get("teamId")
        participants = []
        for i, ident in enumerate(idents):
            player = ident.get("player") or {}
            p_puuid = player.get("puuid")
            if not p_puuid or p_puuid == puuid:
                continue
            team_id = parts[i].get("teamId") if i < len(parts) else None
            name = player.get("gameName") or player.get("summonerName") or "?"
            tag = player.get("tagLine") or ""
            participants.append({
                "puuid": p_puuid,
                "riot_id": f"{name}#{tag}",
                "same_team": int(team_id == my_team_id),
            })
        return game, participants
    except Exception:  # noqa: BLE001
        log.exception("normalize_history failed")
        return None, []

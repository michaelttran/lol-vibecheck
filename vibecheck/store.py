"""The game store: the ONLY module that touches SQLite (same constraint as the desktop app).

Key change from the desktop version: everything is keyed by player. The desktop app
was single-user ("my machine, my games"); the bot serves every linked Discord user.
"""

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from .config import DB_PATH, SESSION_GAP_SECONDS

_SCHEMA = """
CREATE TABLE IF NOT EXISTS players (
    discord_user_id INTEGER PRIMARY KEY,
    puuid TEXT UNIQUE NOT NULL,
    game_name TEXT,
    tag_line TEXT,
    platform TEXT,
    region TEXT,
    linked_at TEXT,
    prompt_channel_id INTEGER,          -- NULL = DM the player
    last_seen_match_id TEXT             -- watermark: newest match already imported
);
CREATE TABLE IF NOT EXISTS games (
    id INTEGER PRIMARY KEY,
    puuid TEXT NOT NULL,
    riot_match_id TEXT NOT NULL,
    played_at TEXT NOT NULL,
    queue_id INTEGER,
    queue_type TEXT,
    champion TEXT,
    role TEXT,
    win INTEGER,
    kills INTEGER, deaths INTEGER, assists INTEGER,
    cs INTEGER,
    duration_seconds INTEGER,
    session_id INTEGER,
    game_index_in_session INTEGER,
    is_remake INTEGER DEFAULT 0,
    raw_payload TEXT,
    UNIQUE (puuid, riot_match_id)
);
CREATE TABLE IF NOT EXISTS ratings (
    game_id INTEGER PRIMARY KEY REFERENCES games(id),
    fun_score INTEGER,
    skipped INTEGER DEFAULT 0,
    rated_at TEXT
);
CREATE TABLE IF NOT EXISTS game_participants (
    game_id INTEGER REFERENCES games(id),
    puuid TEXT,
    riot_id TEXT,
    same_team INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_games_puuid ON games(puuid);
CREATE INDEX IF NOT EXISTS idx_participants_puuid ON game_participants(puuid);
"""

_NOW = lambda: datetime.now(timezone.utc).isoformat()  # noqa: E731


class GameStore:
    def __init__(self, db_path: Path = DB_PATH):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(db_path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        with self._lock, self._db:
            self._db.executescript(_SCHEMA)

    # ---------- players ----------

    def link_player(self, discord_user_id, puuid, game_name, tag_line, platform, region):
        with self._lock, self._db:
            self._db.execute(
                """INSERT INTO players (discord_user_id, puuid, game_name, tag_line,
                                        platform, region, linked_at)
                   VALUES (?,?,?,?,?,?,?)
                   ON CONFLICT(discord_user_id) DO UPDATE SET
                     puuid=excluded.puuid, game_name=excluded.game_name,
                     tag_line=excluded.tag_line, platform=excluded.platform,
                     region=excluded.region, last_seen_match_id=NULL""",
                (discord_user_id, puuid, game_name, tag_line, platform, region, _NOW()),
            )

    def unlink_player(self, discord_user_id):
        with self._lock, self._db:
            self._db.execute("DELETE FROM players WHERE discord_user_id=?", (discord_user_id,))

    def get_player(self, discord_user_id):
        with self._lock:
            return self._db.execute(
                "SELECT * FROM players WHERE discord_user_id=?", (discord_user_id,)
            ).fetchone()

    def all_players(self):
        with self._lock:
            return self._db.execute("SELECT * FROM players").fetchall()

    def players_by_puuids(self, puuids):
        if not puuids:
            return []
        qs = ",".join("?" * len(puuids))
        with self._lock:
            return self._db.execute(
                f"SELECT * FROM players WHERE puuid IN ({qs})", list(puuids)  # noqa: S608
            ).fetchall()

    def set_prompt_channel(self, discord_user_id, channel_id):
        with self._lock, self._db:
            self._db.execute(
                "UPDATE players SET prompt_channel_id=? WHERE discord_user_id=?",
                (channel_id, discord_user_id),
            )

    def set_watermark(self, discord_user_id, match_id):
        with self._lock, self._db:
            self._db.execute(
                "UPDATE players SET last_seen_match_id=? WHERE discord_user_id=?",
                (match_id, discord_user_id),
            )

    # ---------- games ----------

    def insert_game(self, game: dict, participants: list) -> int | None:
        """Insert a captured game; returns row id, or None if already stored."""
        with self._lock, self._db:
            dup = self._db.execute(
                "SELECT id FROM games WHERE puuid=? AND riot_match_id=?",
                (game["puuid"], game["riot_match_id"]),
            ).fetchone()
            if dup:
                return None
            session_id, session_index = self._assign_session(game)
            cur = self._db.execute(
                """INSERT INTO games (puuid, riot_match_id, played_at, queue_id, queue_type,
                     champion, role, win, kills, deaths, assists, cs, duration_seconds,
                     session_id, game_index_in_session, is_remake, raw_payload)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (game["puuid"], game["riot_match_id"], game["played_at"], game["queue_id"],
                 game["queue_type"], game["champion"], game["role"], game["win"],
                 game["kills"], game["deaths"], game["assists"], game["cs"],
                 game["duration_seconds"], session_id, session_index,
                 game.get("is_remake", 0), json.dumps(game.get("raw_payload", {}))),
            )
            game_id = cur.lastrowid
            self._db.executemany(
                "INSERT INTO game_participants (game_id, puuid, riot_id, same_team) VALUES (?,?,?,?)",
                [(game_id, p["puuid"], p["riot_id"], p["same_team"]) for p in participants],
            )
            return game_id

    def _assign_session(self, game):
        """A session = games separated by < 1h. Computed per player at insert (PRD F17)."""
        prev = self._db.execute(
            "SELECT played_at, duration_seconds, session_id, game_index_in_session "
            "FROM games WHERE puuid=? ORDER BY played_at DESC LIMIT 1",
            (game["puuid"],),
        ).fetchone()
        if prev:
            prev_end = datetime.fromisoformat(prev["played_at"]).timestamp() + (
                prev["duration_seconds"] or 0
            )
            gap = datetime.fromisoformat(game["played_at"]).timestamp() - prev_end
            if 0 <= gap < SESSION_GAP_SECONDS:
                return prev["session_id"], prev["game_index_in_session"] + 1
        row = self._db.execute(
            "SELECT COALESCE(MAX(session_id), 0) + 1 FROM games WHERE puuid=?",
            (game["puuid"],),
        ).fetchone()
        return row[0], 1

    def get_game(self, game_id):
        with self._lock:
            return self._db.execute("SELECT * FROM games WHERE id=?", (game_id,)).fetchone()

    # ---------- ratings ----------

    def rate_game(self, game_id, fun_score=None, skipped=False):
        with self._lock, self._db:
            self._db.execute(
                """INSERT INTO ratings (game_id, fun_score, skipped, rated_at)
                   VALUES (?,?,?,?)
                   ON CONFLICT(game_id) DO UPDATE SET
                     fun_score=excluded.fun_score, skipped=excluded.skipped,
                     rated_at=excluded.rated_at""",
                (game_id, fun_score, int(skipped), _NOW()),
            )

    def pending_games(self, puuid, limit=10):
        with self._lock:
            return self._db.execute(
                """SELECT g.* FROM games g LEFT JOIN ratings r ON r.game_id = g.id
                   WHERE g.puuid=? AND g.is_remake=0 AND r.game_id IS NULL
                   ORDER BY g.played_at DESC LIMIT ?""",
                (puuid, limit),
            ).fetchall()

    # ---------- stats queries (all rated, non-skipped, non-remake games) ----------

    _RATED = """FROM games g JOIN ratings r ON r.game_id = g.id
                WHERE g.puuid=? AND r.skipped=0 AND r.fun_score IS NOT NULL AND g.is_remake=0"""

    def _q(self, sql, params):
        with self._lock:
            return self._db.execute(sql, params).fetchall()

    def overall(self, puuid):
        rows = self._q(
            f"SELECT AVG(r.fun_score) avg_fun, COUNT(*) n, AVG(g.win) winrate {self._RATED}",  # noqa: S608
            (puuid,),
        )
        return rows[0]

    def by_dimension(self, puuid, dim_sql, min_n=1):
        """Generic group-by used by champions / queue / role / hour / session index."""
        return self._q(
            f"""SELECT {dim_sql} dim, AVG(r.fun_score) avg_fun, COUNT(*) n, AVG(g.win) winrate
                {self._RATED} GROUP BY dim HAVING n >= ? ORDER BY avg_fun DESC""",  # noqa: S608
            (puuid, min_n),
        )

    def shared_games(self, puuid_a, puuid_b):
        """Mutual vibe matrix: games both players were in, with both ratings when present."""
        return self._q(
            """SELECT ga.riot_match_id, ga.champion champ_a, gb.champion champ_b,
                      ga.played_at, ra.fun_score fun_a, rb.fun_score fun_b
               FROM games ga
               JOIN games gb ON gb.riot_match_id = ga.riot_match_id AND gb.puuid = ?
               LEFT JOIN ratings ra ON ra.game_id = ga.id AND ra.skipped=0
               LEFT JOIN ratings rb ON rb.game_id = gb.id AND rb.skipped=0
               WHERE ga.puuid = ? ORDER BY ga.played_at DESC""",
            (puuid_b, puuid_a),
        )

    def fun_with_teammate(self, puuid, teammate_puuid):
        """Avg fun in games where a given puuid was on your team, vs. without them."""
        with_rows = self._q(
            f"""SELECT AVG(r.fun_score) avg_fun, COUNT(*) n {self._RATED}
                AND g.id IN (SELECT game_id FROM game_participants
                             WHERE puuid=? AND same_team=1)""",  # noqa: S608
            (puuid, teammate_puuid),
        )
        without_rows = self._q(
            f"""SELECT AVG(r.fun_score) avg_fun, COUNT(*) n {self._RATED}
                AND g.id NOT IN (SELECT game_id FROM game_participants
                                 WHERE puuid=? AND same_team=1)""",  # noqa: S608
            (puuid, teammate_puuid),
        )
        return with_rows[0], without_rows[0]

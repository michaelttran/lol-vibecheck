# VibeCheck.lol — Discord Edition

**Winrate is temporary. The vibes are forever.** ✨📊

The post-game League companion, translated from a Windows tray app into a Discord bot.
When a linked player's game ends, the bot asks one question — **"How was that game?"** —
they tap one of five faces, and over time it shows which champions, teammates, and
situations they genuinely enjoy, as opposed to the ones they merely win with.

| 😨 | 🤨 | 😐 | 😎 | 👑 |
|----|----|----|----|----|
| FF at 15 | Who Let Them Cook? | Meh | We Are So Back | Gigachad |

Runs on **macOS, Windows, and Linux** — it's plain Python, and nothing in it is
platform-specific.

## How the translation works

The desktop app read the League *client's* local LCU API — which only exists on the
player's PC — so the bot uses the **Riot public API** instead:

| Desktop app | Discord bot |
|---|---|
| LCU gameflow push event on game end | Poller sweeps match-v5 every 2 min (+ optional LCU capture, below) |
| Always-on-top popup with 5 faces | Message with 5 persistent buttons (DM or channel) |
| Local web dashboard tabs | Slash commands (`/vibe`, `/champions`, `/squad`, …) |
| Supabase sync for squad features | Squad = server members who also ran `/link` |
| `%LOCALAPPDATA%` SQLite | One SQLite file next to the bot |

Preserved behavior from the original PRD: remakes recorded but never prompted (F5),
watermark sweep so crashes/downtime never lose games (F6), missed prompts fall into
**To Rate** (F11), a skip option (F12), sessions = games < 1h apart (F17), and every
stat states its sample size with a 5-game floor (F21).

## Two ways games get captured

Pick one or run both — they write to the same table and dedupe against each other.

| | **Public API** (default) | **Local capture** (`VIBECHECK_LCU=1`) |
|---|---|---|
| Where the bot runs | Anywhere (server, Pi, VPS) | Same machine as your League client |
| Prompt latency | ~1–3 min after game end | Seconds |
| Serves other people | Yes, everyone who `/link`s | Only the account logged into that client |
| ARAM: Mayhem | ❌ Riot returns 403 | ✅ |
| Custom lobbies | ❌ RSO-only since 2024 | ✅ |
| Needs a Riot API key | Yes | Only for `/link` |

If you mostly play **ARAM: Mayhem or customs, you need local capture** — those games
are invisible to the public API by Riot's deliberate choice, not a bug in this bot.
See [Known limits](#notes--limits).

## Requirements

- **Python 3.10+** (the code uses `X | None` type syntax). Check with `python3 --version`.
  On macOS, `brew install python` if you're older.
- A Discord bot token and a Riot API key (both free — steps below).

## Setup

### 1. Discord app

1. Create one at https://discord.com/developers/applications → **New Application**.
2. **Bot** → **Reset Token** → copy it. No privileged intents needed — leave
   Presence/Members/Message Content off; every command here is a slash command.
3. **OAuth2 → URL Generator** → scopes **`bot`** + **`applications.commands`**,
   permissions **Send Messages** + **Embed Links**. Open the generated URL and
   pick your server.

### 2. Riot API key

Grab one at https://developer.riotgames.com. **Development keys expire every 24
hours** — you'll regenerate and restart daily. Apply for a personal key for
anything long-running.

### 3. Install and run

**macOS / Linux**

```bash
git clone https://github.com/michaelttran/lol-vibecheck.git
cd lol-vibecheck
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env          # then fill in DISCORD_TOKEN and RIOT_API_KEY
export $(grep -v '^#' .env | xargs)
python3 bot.py
```

**Windows (PowerShell)**

```powershell
git clone https://github.com/michaelttran/lol-vibecheck.git
cd lol-vibecheck
py -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt

Copy-Item .env.example .env   # then fill in DISCORD_TOKEN and RIOT_API_KEY
Get-Content .env | Where-Object { $_ -match '=' -and $_ -notmatch '^\s*#' } | ForEach-Object {
    $k, $v = $_ -split '=', 2
    Set-Item -Path "Env:$k" -Value $v
}
py bot.py
```

> ⚠️ **The env vars are per-terminal.** `export` / `Set-Item` only affects the shell
> you ran it in. Open a new tab and `RIOT_API_KEY` is empty again — which shows up
> as a **401** from Riot and a bot that silently imports nothing. Re-run the env
> line in every terminal, including when you restart the bot.

You should see `VibeCheck is running.` in the log. Slash commands can take a minute
to appear in Discord the first time.

Then in Discord: `/link` with your Riot ID as `GameName#TAG` (spaces are fine —
`Toilet Paper#8112`) and your platform. Prompts arrive by **DM by default**; if your
server blocks DMs from members, run `/setchannel` instead.

## Local capture (macOS + Windows)

Enable it when the bot runs on the same machine you play on:

```bash
VIBECHECK_LCU=1
```

The watcher attaches to the running League client, notices the moment a game ends,
and pulls the stats from the client itself — so prompts are instant and **ARAM:
Mayhem and custom games get captured**, which the public API will not do.

It's fully cross-platform. Credentials are discovered automatically from the
client's lockfile, falling back to the running process's command line:

- **macOS** — `/Applications/League of Legends.app/Contents/LoL/lockfile`
- **Windows** — `C:\Riot Games\League of Legends\lockfile`

For a non-default install, set `VIBECHECK_LCU_LOCKFILE` to the full path.

Notes: the account has to be `/link`ed like any other; the watcher idles silently
when League isn't running and reattaches on its own when you launch it; and games
it captures use the same match-id format as the poller's, so if both paths see a
game you're only prompted once.

## Commands

| Command | What it does |
|---|---|
| `/link riot_id platform` | Link your Riot ID (e.g. `Faker#KR1`, `KR`). Prompts start immediately — no backfill. |
| `/setchannel` / `/setdm` | Prompts in a channel (squad sees your verdicts 👀) or privately in DMs. |
| `/vibe` | Average vibe, certified bangers & yikes, copium tracking. |
| `/champions` | The Champion Vibe Tier List, plus "winning but miserable" / "losing but thriving". |
| `/squad @member` | Squad buff vs. a friend, and the mutual vibe matrix — the argument settled with data. |
| `/context` | Vibe by queue, role, hour, day, or win/loss. |
| `/regret` | The Regret Curve: which game of the night your vibe falls off a cliff. |
| `/torate` | Rate the games you missed. |
| `/unlink` | Stop everything. The vibes remain in your heart. |

## Troubleshooting

Everything the bot knows lives in `vibecheck.sqlite3` next to `bot.py`, so start there:

```bash
sqlite3 vibecheck.sqlite3 "SELECT game_name, tag_line, platform, region, last_seen_match_id FROM players;"
sqlite3 vibecheck.sqlite3 "SELECT COUNT(*) FROM games;"
```

**No prompts, and `/torate` is empty too.** The poller isn't importing. Check the
bot's terminal — it logs every failure. Then verify Riot directly (with the env var
set in *that* terminal):

```bash
curl -s -H "X-Riot-Token: $RIOT_API_KEY" \
  "https://americas.api.riotgames.com/riot/account/v1/accounts/by-riot-id/YourName/TAG"
```

| Symptom | Cause | Fix |
|---|---|---|
| `401` from Riot | **No key was sent** — the var is empty in this shell | Re-run the env line in this terminal |
| `403` from Riot | Key sent but **expired** (dev keys last 24h) | Regenerate, update `.env`, restart the bot |
| `404` from Riot | Riot ID typo, or wrong regional host | `americas` NA/BR/LA, `europe` EUW/EUNE/TR/RU, `asia` KR/JP, `sea` OC/SG/TW/VN |
| `last_seen_match_id` empty, no errors | No sweep has succeeded yet | Usually the key; see above |
| `/torate` **has** games but no DM arrived | Discord is blocking DMs | Allow DMs for that server, or `/setchannel` |
| Games older than 3h never import | By design — first sweep looks back 3h only | Play a new game; no deep backfill |
| Matchmade games import, Mayhem/customs don't | Riot hides those from the API | Enable local capture (`VIBECHECK_LCU=1`) |

Two log lines at startup are **harmless**: `PyNaCl is not installed` (voice, unused)
and `Privileged message content intent is missing` (slash commands don't need it).

Riot IDs are matched ignoring spaces, so `Toilet Paper#8112` and `ToiletPaper#8112`
resolve to the same account — a link that "looks wrong" usually isn't the problem.

## Notes & limits

- **ARAM: Mayhem and custom games are invisible to the public API.** Riot returns
  403 for Mayhem matches ([developer-relations #1109](https://github.com/RiotGames/developer-relations/issues/1109),
  [#1154](https://github.com/RiotGames/developer-relations/issues/1154)) to stop
  aggregators from solving the meta, and custom lobbies have needed RSO since July
  2024. OP.GG can't show them either. Local capture is the only way to record them.
- **Polling latency:** prompts arrive ~1–3 minutes after game end on the public API
  path — not the ~10s the desktop push event gave. Local capture restores that.
- **Rate limits:** a dev key allows 100 requests / 2 min. The default 2-minute poll
  interval comfortably handles ~15–20 linked players; raise `VIBECHECK_POLL_SECONDS`
  or get a production key beyond that.
- **Premade detection:** the public API doesn't expose lobby premades, so `/squad`
  counts *any* game where you were both on the same team.
- **ARAM has no roles**, so the role breakdown in `/context` stays empty for
  ARAM-only players. Every other breakdown works.
- **Keep the bot running** — it can't catch a game end while it's down. On macOS,
  `caffeinate -i python3 bot.py` stops the machine sleeping out from under it.
- Not endorsed by Riot Games. Riot Games and all associated properties are trademarks
  of Riot Games, Inc.

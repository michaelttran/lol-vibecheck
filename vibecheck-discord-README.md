# VibeCheck.lol — Discord Edition

**Winrate is temporary. The vibes are forever.** ✨📊

The post-game League companion, translated from a Windows tray app into a Discord bot.
When a linked player's game ends, the bot asks one question — **"How was that game?"** —
they tap one of five faces, and over time it shows which champions, teammates, and
situations they genuinely enjoy, as opposed to the ones they merely win with.

| 😨 | 🤨 | 😐 | 😎 | 👑 |
|----|----|----|----|----|
| FF at 15 | Who Let Them Cook? | Meh | We Are So Back | Gigachad |

## How the translation works

The desktop app read the League *client's* local LCU API — which only exists on the
player's PC — so the bot uses the **Riot public API** instead:

| Desktop app | Discord bot |
|---|---|
| LCU gameflow push event on game end | Background poller sweeps match-v5 every 2 min |
| Always-on-top popup with 5 faces | Message with 5 persistent buttons (DM or channel) |
| Local web dashboard tabs | Slash commands (`/vibe`, `/champions`, `/squad`, …) |
| Supabase sync for squad features | Squad = server members who also ran `/link` |
| `%LOCALAPPDATA%` SQLite | One SQLite file next to the bot |

Preserved behavior from the original PRD: remakes recorded but never prompted (F5),
watermark sweep so crashes/downtime never lose games (F6), missed prompts fall into
**To Rate** (F11), a skip option (F12), sessions = games < 1h apart (F17), and every
stat states its sample size with a 5-game floor (F21).

## Setup

1. **Discord app** — create one at https://discord.com/developers/applications,
   add a bot, copy the token, and invite it with the `bot` + `applications.commands`
   scopes (Send Messages permission is enough).
2. **Riot API key** — grab one at https://developer.riotgames.com. Dev keys expire
   every 24h; apply for a personal key for anything long-running.
3. Run it:

```bash
pip install -r requirements.txt
cp .env.example .env   # fill in DISCORD_TOKEN and RIOT_API_KEY
export $(grep -v '^#' .env | xargs)
python bot.py
```

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

## Notes & limits

- **Polling latency:** prompts arrive ~1–3 minutes after game end (Riot indexes the
  match, then the next poller tick catches it) — not the ~10s the desktop push event gave.
- **Rate limits:** a dev key allows 100 requests / 2 min. The default 2-minute poll
  interval comfortably handles ~15–20 linked players; raise `VIBECHECK_POLL_SECONDS`
  or get a production key beyond that.
- **Premade detection:** the public API doesn't expose lobby premades, so `/squad`
  counts *any* game where you were both on the same team.
- Not endorsed by Riot Games. Riot Games and all associated properties are trademarks
  of Riot Games, Inc.

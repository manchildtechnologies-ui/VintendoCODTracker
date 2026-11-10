# Vintendo COD Discord Bot (Phase 3 development copy)

This bot lives alongside the Phase 3 tracker and does not change its executable, configuration, database, roster, or browser extension. Its SQLite database is separate. The tracker remains the supported source for its current local CODMunity workflow, OCR, artwork review, and webhook posts.

## What is implemented

- Defensive typed-response decoder, including the observed `t:10` object and `t:9` array forms.
- Read-only CODMunity POST client with timeout, per-UID cache, and a two-second minimum request spacing.
- Six private-guild slash commands: `/register`, `/stats`, `/recent`, `/leaderboard`, `/compare`, and `/profile`.
- SQLite registration, cached snapshots, recent matches, and persistent duplicate protection.
- Local export fallback under `<CODMUNITY_LOCAL_DATA_ROOT>/<UID>/` using `profile.json`, or the tracker's `local.json` and `matches.json` export shapes.
- Vintendo-branded player card generated locally with Pillow.
- Optional polling. First successful poll seeds saved match history; later polls announce only unseen records. Posting is disabled by default.

Username-to-UID lookup has not been established through a legitimate public feature, so `/register` requires the optional manually supplied UID. Registration is self-declared and does not prove account ownership. Missing fields remain unavailable; commands do not show missing values as zero.

## Live access status

On 2026-11-08, the supplied endpoint was probed independently for both supplied test UIDs, with no browser cookies or authentication tokens. Both requests returned HTTP 403. No protection bypass or browser-session reuse was attempted. The decoder and failure handling are testable offline, but live stats retrieval is not verified. Remote polling will fail safely until the service allows this unauthenticated request. Local exported data can be used meanwhile.

## Setup

1. Copy `.env.example` to `.env` and set `DISCORD_BOT_TOKEN`, `DISCORD_APPLICATION_ID`, and `DISCORD_GUILD_ID`. Keep `.env` private.
2. Invite the bot to the private guild using the `bot` and `applications.commands` scopes. Grant only View Channels, Send Messages, Embed Links, and Attach Files. Set `DISCORD_CHANNEL_ID` only if you want optional match announcements.
3. Double-click `Launch-COD-Discord-Bot.cmd`. It creates an isolated `.venv` and installs only this bot's dependencies on first launch.
4. In Discord, use `/register username:<name> uid:<CODMunity UID>`. You can also configure `CODMUNITY_LOCAL_DATA_ROOT` so local exported profiles take priority over remote retrieval.

`AUTO_POST_MATCHES` is `false` by default. Set it to `true` and set `DISCORD_CHANNEL_ID` to enable announcements. Polling has a 300-second minimum and defaults to 900 seconds. This does not send Victory/Defeat artwork or use player photos; the tracker's existing consent and manual review workflow remains untouched.

## Configuration

See `.env.example`. `BOT_DATABASE` defaults to `data/bot.sqlite3`. Relative paths in `.env` are relative to the bot folder when launched by the included command file. No secret values belong in source control, logs, or reports.

## Tests

From this folder, with Python 3.10 or later:

```powershell
python -m unittest discover -s tests -v
```

These offline tests do not establish live service access.

# SINGLE_EXE_DESIGN — Vintendo COD Tracker Full Clone (Local-Only)

**Target:** one Windows `.exe` (PyInstaller `--onefile --windowed`, entry `launcher.py` → `app.py:main`)
**Mode:** full clone of CODMunity Desktop autotrack behavior **except** OCR stays local.
**Constraints (hard):** Windows-only; no `POST /api/app/desktop-read`; no paid vision / LLM; no Firebase token storage; all OCR = `RapidOCR(use_cls=False)` via existing `CaptureEngine` + `capture_utils.group_ocr_lines_detailed`.

Evidence base: prior result 3 (exe strings + autotrack.log/feed + stats tree), prior result 1 (current tracker wiring), prior result 2 (auth flow + public client).
This doc is the build spec. Nothing here ships until the ordered steps pass the test plan.

---

## 1. What exists today — reuse as-is

| Area | File / Symbol | Keep / Change |
|---|---|---|
| DXGI capture | `capture_engine.py:CaptureEngine` (`grab`, `grab_game_output`, `outputs`, `game_windows`, `game_output`, `select_game_output`), `capture_utils.get_outputs`, `StableDetector(frames=2)` (3 empty → armed → N stable) | **Keep.** Add watcher progress callbacks only. No new capture backend. |
| OCR | `CaptureEngine.recognize_with_tokens` (lazy `RapidOCR(use_cls=False, intra=2, inter=1)`, `RLock`, `group_ocr_lines_detailed`) | **Keep.** No Windows.Media.Ocr, no sidecar. |
| Scoreboard gate | `capture_utils.looks_like_scoreboard` (player col + ≥2 stat headings + ≥4 numeric rows + ≥5 lines) | **Keep** for scoreboard; new gates per parser (§4). |
| SQLite | `engine.py:Store` `DATA/history.sqlite3` — `matches(id,payload,status,created,updated,capture,ocr,players,theme,prompt,image,attempts,error,message_id,artwork,poster,gen_state,gen_attempts)`, `roster`, `meta`, `deliveries(id,status,created,message_id,error)`, `match_aliases(alias,match_id)`, `cinematic_jobs` | **Keep + extend.** New tables (§5) only. `key_for()` / `identity_aliases()` unchanged. |
| Scoreboard parse | `engine.parse_scoreboard` + `_parse_scoreboard_spatial` (Warzone fixed centers score .439 / elim .539 / kills .631 / assists .733 / redeploys .833 / damage .930; MP header-x; 16-row cap; `combat record` filtered as non-player) | **Keep.** 4 new parsers alongside, never inside. |
| CODMunity cache | `codmunity_stats.load_snapshot()` → `meta['codmunity_stats']` (v1, `file_signature` skip); `merged_matches()` (matches.json + saved Warzone `feed.json`); `import_snapshot()` → `Store.add(..., 'baseline')` | **Keep.** Extend file list (§5). |
| Public API | `codmunity_player_stats.CodmunityPublicClient` (`profile_identity` GET `api.codmunity.gg/website/pages/profile/<user>`, `community_distribution` GET `codmunity.gg/api/public/stats/global?game&mode&scope`, 12-pt monotonic KD, `User-Agent: VintendoCODTracker`, 12 s timeout) | **Keep.** `ModularPlayerStatsService` stays unconfigured (`PlayerStatsUnavailable` if called without fetcher). |
| UI loop | `app.py:578` `QTimer(1s).tick` → `tick()` (L3277) → `poll_records()` every `poll_seconds` (`load_matches` + `refresh_codmunity_stats` + `Store.add`) + `capture_once(automatic=True)` every `AUTO_OCR_INTERVAL_SECONDS=30` (Squad Night + `auto_detect` only) → `_crop_scoreboard_region` (warzone `SCOREBOARD_REGION_TEMPLATES` / MP `MULTIPLAYER_REGION_TEMPLATES`, 2560×1440 ref scaled to 1920×1080 / 3840×2160) → auto profile score → `StableDetector.observe()` → `_finish_scoreboard_ocr` → `parse_ocr` → review (`verified` + `ocr_table`) → `save_capture` binds `_latest_codmunity_match()` or creates `capture-<sha256(frame)>` | **Keep UI; move autotrack off the GUI thread** (§3). |
| Build | `build.py` (PyInstaller, `--onefile --windowed --name VintendoCODTracker`, `launcher.py`, `--collect-all rapidocr_onnxruntime/dxcam/playwright`, `--collect-binaries onnxruntime`, `--exclude-module torch/...`) | **Edit** (§7). |

**What is NOT built (local-only):** `airead.rs` equivalent — `POST /api/app/desktop-read`, `aiRead` flag, Tauri commands `autotrack_ai_read`/`autotrack_samples`, `match completed by AI` path. When exe would call AI, this design reads locally and logs `not used: LocalOnly`. No prompt payloads, no `--hidden-import` for cloud OCR.

---

## 2. Target topology (single exe)

```
VintendoCODTracker.exe
 └─ app.py (PySide6, QTimer 1s tick — UI/status only)
     ├─ WatcherThread (QThread, §3) ── CaptureEngine (dxcam DXGI) ── RapidOCR (local)
     │      ├─ gates: looks_like_* per parser (§4)
     │      ├─ parsers: parse_scoreboard / after_action / combat_record / wz_summary / camo / banner / hud
     │      └─ Store (SQLite) ── SyncWorker (outbox, §6) ── Discord webhook (existing send_discord)
     ├─ poll_records (kept, throttled to poll_seconds) ── E.load_matches + refresh_codmunity_stats
     └─ UI review (hotkey Ctrl+F8 / Ctrl+F9, ocr_text + ocr_table + verified checkbox)
```

* One process, one SQLite file, no helper exe. CODMunity structure (`capture.rs`/`ocr.rs`/`airead.rs`/…). Rust `src-tauri/src/tracker/*` maps 1:1 to Python modules in §7 (no Tauri build).

---

## 3. Watcher thread shape

New module `autotrack.py` (GUI thread never captures):

```python
class WatcherState(Enum):  # matches exe log states
    NO_GAME, NO_WINDOW, GAME_BACKGROUND, WATCHING, NOT_CONFIRMED

class WatcherSignals(QObject):
    state_changed = Signal(str)
    draft_ready   = Signal(dict)   # {parser, text, lines, tokens, frame_path, profile}
    log           = Signal(str)    # -> autotrack.log via Store/meta

class AutotrackWatcher(QThread):   # run() loop, ~1 Hz, jitter ±200 ms
    def __init__(self, capture: CaptureEngine, store: Store, settings, parent=None): ...
    def run(self):
        while not self._stop.wait(1.0):
            state = self._classify()          # game_windows() + foreground + Combat Record gate
            if state != WatcherState.WATCHING:  # -> log + retick, no OCR
                emit(state); continue
            frame = capture.grab(selected_output, roi=None)   # full-monitor; crop per parser
            for p in PARSERS:                  # ordered §4
                roi = p.region(frame.shape, profile)
                text, lines, tokens = capture.recognize_with_tokens(crop(frame, roi))
                if p.gate(lines) and p.detector.observe(text, True):
                    emit draft_ready({**p.parse(text, lines, tokens), source: p.name})
        capture.close()
    def stop(self): self._stop.set()
```

Classification (mirrors exe strings, no AI):

* `no_window` — `game_windows()` empty or target below 640×360.
* `game_background` — `is_call_of_duty_window` true but `looks_like_scoreboard` and all parser gates false for ≥3 consecutive ticks.
* `not_confirmed` — Combat Record present but not MW4 lifetime (`NotLifetimeMultiplayer`) or `NoResult` (same vocabulary as `autotrack.log`: `combat record not used: NotLifetimeMultiplayer` / `NoResult`).
* `watching` — game window foreground + at least the scoreboard gate can arm.

Existing `app.py:tick()` keeps driving `poll_records()` and the 30 s auto-OCR call; the watcher replaces `capture_once(automatic=True)` for continuous tracking. Hotkey capture (`capture_once(hotkey=True)`) stays on-demand and bypasses `StableDetector` (review draft immediately). `StableDetector` is instantiated **once per parser** (`frames=2` for scoreboard, `frames=3` for lifetime/camo), never shared.

Thread safety: `CaptureEngine` already serializes with `RLock`; watcher and hotkey paths must never call `grab`/`recognize` concurrently — gate with `capture_busy` (existing flag, shared with watcher via `threading.Lock`).

---

## 4. Parser list (local-only)

Existing `engine.parse_scoreboard` stays the sole scoreboard writer. Add **5 new pure functions** in `engine.py` (small, tested, no UI deps):

| # | Function (new, `engine.py`) | Trigger gate | Reads (mirrors exe `reads.rs`) | Persist |
|---|---|---|---|---|
| P1 | `parse_after_action(text, tokens)` | `after action report` words + result (`Victory`/`Defeat`) + ≥1 stat heading | `army/mode/result/scoreOwn/scoreOpp`, per-player kills — exe: `match read from after action report: win team_deathmatch (30 kills)` | `matches.payload` + `match_aliases` natural key |
| P2 | `parse_combat_record(text, tokens)` | `combat record` + MW4 lifetime marker; rejects `NoResult` / `NotLifetimeMultiplayer` | `kd/wl/games` → exe: `combat record read: kd Some((10051, 9083)), wl Some((187, 278)), games Some(478)` | `lifetime_snapshots` + `feed` record event (§5) |
| P3 | `parse_warzone_summary(text, tokens, profile='warzone')` | `warzone summary|placement` + squad/placement words | placement + squad stats — exe: `ai warzone summary` downgraded to local (`warzone summary read (local)`) | `matches.payload.placement` |
| P4 | `parse_camo_challenge(text, lines)` | `challenge|camo` + weapon class (`assault rifle/smg/…`) | `{weapon, camo, grind, changed:[[mode,class,Weapon-Camo]], read:[lines]}` — exe: `challenge popup: hud read N line(s)` (1071 hits) + `camo page seen`; `unmatched weapon` → `not matched` | `autotrack-feed.json` entry (`state:saved/dismissed/already`, `guessed:false`) |
| P5 | `parse_banner_hud(text, lines)` | HUD banners, `warzone hud read: X squads…` pattern; `banner read` | low-priority PII-free hints (mode/squad count) only; never creates a match | log line only |
| — | (kept) `parse_scoreboard` | `looks_like_scoreboard` + header table | existing Warzone-spatial/MP-header logic, 16-row cap | existing `players`/`ocr` columns |

Ordering rule (from exe logs): **after-action wins over scoreboard of the same game** (`scoreboard of a game already taken from its after action report`). Implement in watcher: if P1 fires, suppress P0 draft for the same `natural` alias within 90 s.

All five must return `[]` / `None` on non-matching input — never fabricate a row. “Not used” reasons reuse exe vocabulary (`NoResult`, `NotLifetimeMultiplayer`, `Missing(columns)`/`Missing(title)`, `OtherMode`) so `autotrack.log` greps stay comparable.

---

## 5. SQLite tables

Reuse `engine.Store` verbatim for `matches/roster/meta/deliveries/match_aliases/cinematic_jobs`. Add in `Store.__init__` (`engine.py`, migration-safe `CREATE TABLE IF NOT EXISTS` + `ALTER TABLE` like existing `artwork/poster/gen_state` backfill):

```sql
-- Lifetime Combat Record snapshots (P2). One row per observed record card.
CREATE TABLE IF NOT EXISTS lifetime_snapshots(
  id TEXT PRIMARY KEY, at REAL, game TEXT, mode TEXT, scope TEXT,
  kd_num INTEGER DEFAULT 0, kd_den INTEGER DEFAULT 0, wins INTEGER DEFAULT 0,
  losses INTEGER DEFAULT 0, games INTEGER DEFAULT 0, raw TEXT DEFAULT '');

-- Camo / challenge detections (P4). Mirrors autotrack-feed.json entries.
CREATE TABLE IF NOT EXISTS camo_detections(
  id TEXT PRIMARY KEY, at REAL, weapon TEXT, camo TEXT, grind TEXT DEFAULT 'mw4',
  state TEXT DEFAULT 'saved',  -- saved|dismissed|already
  changed TEXT DEFAULT '[]',   -- [[mode,class,Weapon-Camo],...]
  read TEXT DEFAULT '[]',      -- OCR lines
  error TEXT DEFAULT '');

-- CODMunity feed mirror: every saved feed event, even when matches.json lacks it.
CREATE TABLE IF NOT EXISTS tracking_feed(
  id INTEGER PRIMARY KEY, at INTEGER, kind TEXT, state TEXT, reason TEXT,
  data TEXT DEFAULT '{}');

-- Sync outbox (offline-first). The Discord poster is the only network writer.
CREATE TABLE IF NOT EXISTS sync_outbox(
  id TEXT PRIMARY KEY, kind TEXT, match_id TEXT DEFAULT '', status TEXT DEFAULT 'pending',
  attempts INTEGER DEFAULT 0, next_at REAL DEFAULT 0, error TEXT DEFAULT '',
  created REAL, updated REAL);

-- File->table migration (extends codmunity_stats.USER_FILES):
-- %LOCALAPPDATA%/CODMunity/stats/u-*/{matches,feed,local,sent,wz-modes,rejected}.json
--   -> meta['codmunity_stats'] snapshot + matches (baseline) + tracking_feed
-- %LOCALAPPDATA%/CODMunity/{autotrack-feed.json, autotrack.log}
--   -> camo_detections + meta['autotrack_log_tail'] (last N lines, never in Discord captions)
```

`Store.add()` / `identity_aliases()` dedup is unchanged — new parsers call the same `add()` so a P1 after-action read and a later `matches.json` poll collapse to one `matches` row.

---

## 6. Sync queue

Existing `deliveries` + `Store.reserve(mid, kind='squad'|'solo')` + `send_discord()` stay. Add outbox dispatch:

* Poller (`poll_records`, `poll_seconds` UI value, default 10 s) and watcher both enqueue `sync_outbox` rows (`kind='discord_match'`, `kind='lifetime_record'`, `kind='camo'`).
* `SyncWorker` (QThread, 5 s tick, exponential backoff 5 s → 5 min, max 10 attempts): `status='pending' AND next_at<=now` → `reserve()` → send → mark `delivered` in `deliveries` + `sync_outbox`, or back off with `error` column. Uses existing `RECAP`/`recap()` — cache detail (`benchmark_lines`, personal rows, bests, leaderboard) stays **out** of the Discord caption per `engine.recap` (L1043) and the Phase3 test.
* CODMunity uploads (`POST /api/app/matches`, `/api/app/stats`, `/api/app/stats/me`, `/api/app/desktop-read`, Firebase pairing) are **explicitly out of scope** (local-only constraint + prior result 2 approval rule). Only unauthenticated public GETs (`CodmunityPublicClient`) are allowed from this exe. If the CODMunity desktop app is installed, its cache is read locally; never POST as it.

---

## 7. Concrete file plan — ordered build steps

**Step 0 — Pin & snapshot (no code yet).**
Lock `requirements-lock.txt` (already present: `PySide6 6.11.1`, `dxcam 0.3.0`, `rapidocr-onnxruntime 1.4.4`, `onnxruntime 1.31.1`, `playwright 1.60.0`); record `build.py` args (`--windowed --onefile`). Save `SINGLE_EXE_DESIGN.md` (this file) as the spec. *Exit:* design committed, no behavior change.

**Step 1 — Store migrations (`engine.py`, `tests/test_core.py`).**
Add the 4 tables above in `Store.__init__` with `CREATE TABLE IF NOT EXISTS`; add `Store.log_autotrack(msg)` (writes `meta['autotrack_log_tail']`, capped 200 lines) and `Store.enqueue_sync(kind, match_id)`. *Exit:* `Store(tmp)` creates new tables; old `history.sqlite3` opens without error.

**Step 2 — Pure parsers P1–P5 (`engine.py` after `parse_scoreboard` L479).**
Implement §4 as side-effect-free functions (+ per-parser `*_gate(lines)` next to `looks_like_scoreboard` in `capture_utils.py`). Copy `STAT_ALIASES` entries needed (`kd/kdratio`, `placement`, `wins/losses` already present; add `games/matches` if absent). *Exit:* unit tests §8 pass on synthetic OCR text; `parse_scoreboard` untouched.

**Step 3 — Watcher (`autotrack.py` NEW, `engine.py` reuse).**
Create `VintendoCODTracker-Phase3-dev/autotrack.py`: `WatcherState`, `AutotrackWatcher` (§3) with injected `CaptureEngine`, `Store`, parser registry, and `Signal`s. Start/stop from `app.py` `start()`/`stop()`/`pause()` (mirror Solo worker lifecycle L3227–3276). Watcher owns all `grab` calls; hotkey path still uses `capture_once(hotkey=True)`. *Exit:* exe boots to `WATCHING` when game runs; `no_window` when closed; no UI freeze during OCR.

**Step 4 — Cache harvest extension (`codmunity_stats.py`).**
Extend `USER_FILES` with `autotrack-feed.json` (beside `stats/` root); extend `load_snapshot()` to fill `tracking_feed` + `camo_detections` via `Store`; extend `merged_matches()` to also merge saved `feed.json` Warzone rows (already) and local `record` events (new, `kind='record', state='saved'`). Keep `never reposts` (`baseline`) invariant. *Exit:* import of real `%LOCALAPPDATA%/CODMunity/stats` yields identical `matches` count + new `tracking_feed` rows; idempotent second import adds 0.

**Step 5 — Sync worker (`autotrack.py` or `sync.py` NEW, `app.py` wiring).**
Add `SyncWorker(QThread)` consuming `sync_outbox` (§6) → `Store.reserve()` → `send_discord()`. Wire `start()` to create it alongside `AutotrackWatcher`; shutdown waits ≤5 s. *Exit:* offline outbox drains on reconnect; no double-post (`deliveries` guard).

**Step 6 — UI wiring (`app.py` EDIT).**
* `tick()` (L3277): keep `poll_records()` throttle; replace `capture_once(automatic=True)` body with watcher start (no 30 s `AUTO_OCR_INTERVAL_SECONDS` grab on GUI thread).
* `ensure_capture()` (L3328): keep for hotkey manual capture; parametrize `StableDetector(frames=2)` (scoreboard) vs `frames=3` (lifetime/camo) via parser.
* `save_capture()` (L3554): auto-bind now prefers watcher `draft_ready` payload (game/mode/result from P1) over `_latest_codmunity_match()`; still creates `capture-<sha256>` fallback. Review UI (`ocr_text`, `ocr_table`, `verified`) unchanged.
* Settings: add `autotrack_enabled` (bool, default true), `autotrack_source={'game'}` (keep `autoTrackSource:game` semantics; ignore `cua-driver`/overlay titles). Never add `aiRead` UI — local-only.

**Step 7 — Single-exe packaging (`build.py` EDIT, `requirements.txt` EDIT if any).**
Add `--hidden-import autotrack` (and `sync` if split); keep `--collect-all rapidocr_onnxruntime/dxcam/playwright`. No Tauri/Rust, no helper exe. *Exit:* `python build.py` inside pinned env produces `VintendoCODTracker.exe` (~13 MB class) that starts, shows dashboard, reaches `WATCHING`, captures a scoreboard draft.

**Step 8 — Cutover.**
Flip `poll_seconds` default only after watcher parity test (§8). Keep QTimer path as fallback for one release (settings flag `watcher_fallback=True`).

---

## 8. Test plan

All tests run via existing `pytest` (`tests/`, Python `unittest` style as in `test_codmunity_stats.py`):

1. **Parser unit tests** (NEW `tests/test_autotrack_parsers.py`): synthetic OCR strings for each of P1–P5 (win/loss after-action, MW4 lifetime card, Warzone summary, challenge/camo popup, HUD banner) + non-matching menu text (must return `[]`/`None`, `not used` vocabulary matches exe: `NoResult`, `NotLifetimeMultiplayer`, `Missing(columns)`). Cover the 604×`not used` / 372×`not matched` buckets seen in exe logs.
2. **Store migration tests** (`tests/test_core.py` append): (`a`) fresh DB creates 4 tables; (`b`) legacy DB opens clean; (`c`) `Store.add` dedup across P1-after-action + `matches.json` poll yields one row (natural alias); (`d`) `enqueue_sync` + `SyncWorker` drain delivers once.
3. **Cache harvest tests** (`tests/test_codmunity_stats.py` append): extend `build_stats_tree` fixture with `autotrack-feed.json` (camo entry) + `feed.json` `record/saved` event; assert `tracking_feed` counts, `camo_detections.state='saved'`, `import_snapshot` still idempotent (`matches_added: 3 → 0` on re-import, `baseline` status).
4. **Watcher state machine tests** (NEW `tests/test_autotrack_watcher.py`): fake `CaptureEngine` (scripted `game_windows`/`grab`/`recognize_with_tokens`); assert `NO_GAME → GAME_BACKGROUND → WATCHING` transitions, P1-before-P0 suppression within 90 s, `StableDetector` arming (3 empty → armed → N repeats).
5. **Public client tests** (existing `tests/test_codmunity_player_stats.py`): unchanged — `CodmunityPublicClient` only; `ModularPlayerStatsService` without fetcher still raises `PlayerStatsUnavailable`.
6. **Exe smoke test**: build with `build.py`, launch, verify dashboard renders, watcher logs `state watching`, hotkey Ctrl+F8 produces a review draft (not auto-saved), `history.sqlite3` gains one row. No network beyond Discord webhook (mocked in tests).

---

## 9. Gaps & risks (unresolved, named)

* **Exact watcher cadence and DXGI ROI rects for non-scoreboard parsers** — recoverable only from exe `reads.rs`/`capture.rs` (strings do not carry rects). Design uses ~1 Hz tick + parameterized ROIs; scoreboard ROIs are known (`SCOREBOARD_REGION_TEMPLATES`, `MULTIPLAYER_REGION_TEMPLATES` from 2560×1440 ref). P1–P5 ROIs need calibration against the same reference image before shipping.
* **`aiRead` default** — `settings.json` in the profiling window lacked the key; no `by AI` log lines observed. Irrelevant here: no AI path is built.
* **HTTP methods/headers for `/api/app/*`** — inferred `POST` from `upload answered` strings; exe decompilation (Ghidra) timed out, so schemas were not extracted. Out of scope by the local-only constraint; only public GETs are used.
* **Poll interval variance** — exe `autotrack.log` shows ~0.3 s bursts and ~10 min gaps across `no_game`; exact cadence unknown. Design picks 1 Hz with jitter; acceptable for OCR-gated writes.

---

## Appendix — evidence map

* Capture/OCR: `capture_engine.py` (dxcam DXGI, `RapidOCR(use_cls=False, 2/1)`, `StableDetector`), `capture_utils.py` (`get_outputs`, `group_ocr_lines_detailed`, `looks_like_scoreboard`).
* Store/dedup: `engine.py:Store` (L657–822), `key_for`/`identity_aliases` (L245–263), `matches/meta/deliveries/match_aliases/cinematic_jobs`.
* Parsers: `engine.parse_scoreboard` (L374–479) + `_parse_scoreboard_spatial` (L311–372); no after-action/combat-record/Warzone-summary/camo parser exists (gap §4 fills).
* Cache: `codmunity_stats.py:load_snapshot/import_snapshot/merged_matches` (baseline, `file_signature`); `codmunity_player_stats.py:CodmunityPublicClient` public GETs only.
* Wiring: `app.py tick/poll_records` (L3277–3327), `ensure_capture/capture_once/_finish_scoreboard_ocr` (L3328–3478), `parse_ocr/save_capture` (L3479–3594), `QTimer(1s)` + `AUTO_OCR_INTERVAL_SECONDS=30`.
* Exe reference (prior result 3): watcher states + DXGI `Desktop Duplication` + `tracker/{capture,ocr,reads,wz,airead}.rs` + `autotrack.log`/`autotrack-feed.json`/`stats/u-*/{matches,feed,local,sent,wz-modes,rejected}.json` shapes; AI path explicitly excluded per prior result 2.

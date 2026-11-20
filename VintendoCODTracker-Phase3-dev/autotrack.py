"""Background autotrack watcher, Phase 1 (local-only).

Reuses the existing :mod:`capture_engine` DXGI capture + local RapidOCR
service; the GUI thread never captures. This module never imports cloud
sync or any ``desktop-read`` path -- persistence goes through
:mod:`engine` ``Store`` only.

The Solo worker imports ``engine`` alone and never this module, so Solo
Ops behaviour is unchanged. Wire one watcher per Squad Night session with::

    from capture_engine import CaptureEngine
    import engine as E
    from autotrack import AutotrackWatcher
    watcher = AutotrackWatcher(capture, store, settings.values,
                               on_state=..., on_draft=..., on_log=...)
    watcher.start()  # daemon thread, ~1 Hz
    ...
    watcher.stop()
"""

from __future__ import annotations

import enum
import threading
import time

import capture_utils
import engine as E

try:  # Importing capture_engine stays dependency-free (no dxcam until CaptureEngine()).
    from capture_engine import StableDetector, is_call_of_duty_window, select_game_output
except Exception:  # pragma: no cover - capture_engine always ships alongside.
    StableDetector = None  # type: ignore[assignment]
    is_call_of_duty_window = None  # type: ignore[assignment]
    select_game_output = None  # type: ignore[assignment]


class WatcherState(enum.Enum):
    """Watcher classification states (exe log vocabulary)."""

    NO_GAME = "no_game"
    GAME_BACKGROUND = "game_background"
    WATCHING = "watching"
    NO_WINDOW = "no_window"
    NOT_CONFIRMED = "not_confirmed"


# Ordered parser registry. After-action (P1) runs before the scoreboard
# (P0) so a single tick can suppress the scoreboard draft for the same
# game, matching the exe rule ``scoreboard of a game already taken from
# its after action report``.
SCOREBOARD_FRAMES = 2
LIFETIME_FRAMES = 3

# ROI calibration against the 2560x1440 reference (design section 9).
# Scoreboard bounds match app.py templates; P1-P5 are centered-panel
# estimates from the 2560x1440 reference layout, scaled by width.
REFERENCE_SIZE = (2560, 1440)
ROI_BOUNDS_2560 = {
    "after_action": (600, 220, 1360, 620),
    "warzone_summary": (750, 180, 1060, 760),
    "scoreboard": (202, 487, 2156, 464),
    "combat_record": (620, 140, 1320, 720),
    "camo_challenge": (820, 60, 920, 380),
    "banner_hud": (850, 110, 860, 200),
}


def scaled_roi(name, frame_width, frame_height):
    """Return (x0, y0, x1, y1) for *name* on a frame of the given size."""
    bounds = ROI_BOUNDS_2560.get(name)
    if bounds is None:
        return None
    scale = float(frame_width) / REFERENCE_SIZE[0]
    x, y, w, h = bounds
    return (int(x * scale + 0.5), int(y * scale + 0.5),
            int((x + w) * scale + 0.5), int((y + h) * scale + 0.5))


def crop_frame(frame, name):
    """Crop an array-like frame to the parser ROI, or None if not croppable."""
    shape = getattr(frame, "shape", None)
    if shape is None or len(shape) < 2:
        return None
    try:
        height, width = int(shape[0]), int(shape[1])
    except (TypeError, ValueError):
        return None
    box = scaled_roi(name, width, height)
    if box is None:
        return None
    x0, y0, x1, y1 = box
    x0 = max(0, min(width - 1, x0)); y0 = max(0, min(height - 1, y0))
    x1 = max(x0 + 1, min(width, x1)); y1 = max(y0 + 1, min(height, y1))
    try:
        return frame[y0:y1, x0:x1].copy()
    except Exception:
        return None


class _ParserSpec:
    def __init__(self, name, gate, parse, frames):
        self.name = name
        self.gate = gate
        self.parse = parse
        self.frames = frames


def _scoreboard_parse(text, lines, tokens, watcher):  # pragma: no cover - thin wrapper
    return E.parse_scoreboard(text, ocr_tokens=tokens, profile=watcher.profile)


def default_parser_specs():
    return [
        _ParserSpec("after_action", capture_utils.looks_like_after_action,
                    lambda text, lines, tokens, w: E.parse_after_action(text, tokens), SCOREBOARD_FRAMES),
        _ParserSpec("warzone_summary", capture_utils.looks_like_warzone_summary,
                    lambda text, lines, tokens, w: E.parse_warzone_summary(text, tokens), SCOREBOARD_FRAMES),
        _ParserSpec("scoreboard", capture_utils.looks_like_scoreboard,
                    lambda text, lines, tokens, w: E.parse_scoreboard(text, ocr_tokens=tokens, profile=w.profile), SCOREBOARD_FRAMES),
        _ParserSpec("combat_record", capture_utils.looks_like_combat_record,
                    lambda text, lines, tokens, w: E.parse_combat_record(text, tokens), LIFETIME_FRAMES),
        _ParserSpec("camo_challenge", capture_utils.looks_like_camo_challenge,
                    lambda text, lines, tokens, w: E.parse_camo_challenge(text, lines), LIFETIME_FRAMES),
        _ParserSpec("banner_hud", capture_utils.looks_like_banner_hud,
                    lambda text, lines, tokens, w: E.parse_banner_hud(text, lines), SCOREBOARD_FRAMES),
    ]


def _usable_window(window):
    try:
        width = int(window.get("width", 1920))
        height = int(window.get("height", 1080))
    except (TypeError, ValueError):
        return False
    return width >= 640 and height >= 360


class AutotrackWatcher(threading.Thread):
    """~1 Hz background watcher. ``step()`` is one tick and is testable
    without starting the thread."""

    def __init__(self, capture, store, settings=None, output=None, profile="auto",
                 interval=1.0, on_state=None, on_draft=None, on_log=None,
                 specs=None, suppression_window=90.0, time_func=None, name="AutotrackWatcher",
                 own_capture=True):
        super().__init__(name=name, daemon=True)
        self._capture = capture
        self._store = store
        self._settings = dict(settings or {})
        self.output = output
        self.profile = profile
        self.interval = max(0.1, float(interval))
        self._on_state = on_state
        self._on_draft = on_draft
        self._on_log = on_log
        self._specs = list(specs) if specs is not None else default_parser_specs()
        self.suppression_window = float(suppression_window)
        self._time = time_func or time.monotonic
        self._stop = threading.Event()
        capture_lock = getattr(capture, "lock", None)
        self._lock = capture_lock if capture_lock is not None else threading.RLock()
        self.state = WatcherState.NO_GAME
        self.busy = False
        self.last_draft = None
        self.last_text = ""
        self._any_gate_true = False
        self._background_ticks = 0
        self._after_action_alias_at = {}
        self._last_after_action_at = None
        self._combat_unconfirmed = False
        self._detectors = {spec.name: self._make_detector(spec.frames) for spec in self._specs}
        self._own_capture = bool(own_capture)

    @staticmethod
    def _make_detector(frames):
        if StableDetector is None:
            raise RuntimeError("capture_engine.StableDetector is unavailable")
        return StableDetector(frames=frames)

    # -- state / logging -------------------------------------------------
    def _set_state(self, state):
        if state is not self.state:
            self.state = state
            try:
                if self._on_state:
                    self._on_state(state.value if isinstance(state, WatcherState) else str(state))
            except Exception:
                pass

    def _log(self, message):
        text = str(message)
        try:
            if self._store is not None and hasattr(self._store, "log_autotrack"):
                self._store.log_autotrack(text)
        except Exception:
            pass
        try:
            if self._on_log:
                self._on_log(text)
        except Exception:
            pass
        return text

    def classify_windows(self, windows):
        """Pure classification for an injected window list (no capture)."""
        windows = list(windows or [])
        usable = [w for w in windows if isinstance(w, dict) and _usable_window(w)]
        if not usable:
            return WatcherState.NO_GAME
        if self._any_gate_true:
            return WatcherState.WATCHING
        return WatcherState.GAME_BACKGROUND

    def classify(self, windows=None):
        if windows is None:
            windows = self._safe_windows()
        state = self.classify_windows(windows)
        if state is not WatcherState.NO_GAME and self._combat_record_unconfirmed:
            return WatcherState.NOT_CONFIRMED
        if state is WatcherState.NO_GAME:
            return WatcherState.NO_GAME
        return state

    @property
    def _combat_record_unconfirmed(self):
        return bool(getattr(self, "_combat_unconfirmed", False))

    def _safe_windows(self):
        try:
            windows = self._capture.game_windows()
        except Exception:
            return []
        return list(windows or [])

    def _select_output(self, windows):
        if self.output is not None:
            return self.output
        try:
            outputs = self._capture.outputs()
        except Exception:
            outputs = []
        if outputs and select_game_output is not None:
            try:
                output, _window = select_game_output(outputs, windows)
                return output
            except Exception:
                pass
        try:
            output, _window = self._capture.game_output()
            return output
        except Exception:
            return None

    def _enabled(self):
        try:
            return bool(self._settings.get("autotrack_enabled", True))
        except Exception:
            return True

    # -- one tick ---------------------------------------------------------
    def step(self):
        if not self._enabled():
            self._set_state(WatcherState.NO_GAME)
            return self.state
        windows = self._safe_windows()
        if not windows:
            self._any_gate_true = False
            self._combat_unconfirmed = False
            self._feed_empty()
            self._set_state(WatcherState.NO_GAME)
            self._log("no_game: no Call of Duty window visible")
            return self.state
        output = self.output if self.output is not None else self._select_output(windows)
        if output is None:
            self._any_gate_true = False
            self._combat_unconfirmed = False
            self._feed_empty()
            self._set_state(WatcherState.GAME_BACKGROUND)
            self._log("game_background: game window present but no capture output mapped")
            return self.state
        self.busy = True
        try:
            with self._lock:
                frame = self._capture.grab(output, None)
                per_spec = self._recognize_per_spec(frame)
        except Exception as exc:
            self._any_gate_true = False
            self._combat_unconfirmed = False
            self._feed_empty()
            self._set_state(WatcherState.GAME_BACKGROUND)
            self._log(f"game_background: capture failed ({type(exc).__name__}: {exc})")
            return self.state
        finally:
            self.busy = False
        # per_spec maps parser name -> (text, lines, tokens) for this tick.
        gates = {}
        for spec in self._specs:
            _text, _lines, _tokens = per_spec.get(spec.name, ("", [], []))
            try:
                gates[spec.name] = bool(spec.gate(_lines))
            except Exception:
                gates[spec.name] = False
        _ctext, _clines, _ctokens = per_spec.get("combat_record", ("", [], []))
        self._combat_unconfirmed = False
        if gates.get("combat_record") and E.parse_combat_record(_ctext, _ctokens) is None:
            reason = E.combat_record_reason(_ctext) or "NoResult"
            self._combat_unconfirmed = True
            self._log(f"combat record not used: {reason}")
        any_true = any(gates.values())
        self._any_gate_true = any_true
        if per_spec:
            self.last_text = next((per_spec[n][0] for n in per_spec if gates.get(n)),
                                  next(iter(per_spec.values()), ("", [], []))[0])
        if not any_true:
            self._background_ticks += 1
            self._feed_empty()
            self._set_state(WatcherState.GAME_BACKGROUND)
            self._log("game_background: no parser gate matched this tick")
            return self.state
        self._background_ticks = 0
        if self._combat_unconfirmed and not any(gates[name] for name in gates if name != "combat_record"):
            self._set_state(WatcherState.NOT_CONFIRMED)
        else:
            self._set_state(WatcherState.WATCHING)
        for spec in self._specs:
            detector = self._detectors[spec.name]
            text, lines, tokens = per_spec.get(spec.name, ("", [], []))
            if not gates.get(spec.name):
                detector.observe(text, False)
                continue
            if not detector.observe(text, True):
                continue
            try:
                parsed = spec.parse(text, lines, tokens, self)
            except Exception as exc:
                self._log(f"{spec.name} parse failed ({type(exc).__name__}: {exc})")
                continue
            if parsed is None:
                continue
            draft = self._build_draft(spec.name, parsed, text, lines, tokens, frame)
            if draft is None:
                continue
            if spec.name == "scoreboard" and self._is_scoreboard_suppressed(draft):
                self._log("scoreboard of a game already taken from its after action report")
                continue
            self._persist(draft)
            self._emit(draft)
        return self.state

    def _recognize_per_spec(self, frame):
        """Recognize once per parser ROI when croppable, else one full-frame read.

        Test fakes return ``object()`` frames (no shape); those take the
        single full-frame path so existing watcher tests keep passing.
        """
        cropped = {}
        croppable = getattr(frame, "shape", None) is not None
        if croppable:
            for spec in self._specs:
                candidate = crop_frame(frame, spec.name)
                if candidate is not None:
                    cropped[spec.name] = candidate
        if not cropped:
            text, lines, tokens = self._capture.recognize_with_tokens(frame)
            text = str(text or "")
            lines = list(lines or [])
            tokens = list(tokens or [])
            return {spec.name: (text, lines, tokens) for spec in self._specs}
        result = {}
        for spec in self._specs:
            source = cropped.get(spec.name, frame)
            text, lines, tokens = self._capture.recognize_with_tokens(source)
            result[spec.name] = (str(text or ""), list(lines or []), list(tokens or []))
        return result

    def _feed_empty(self):
        for detector in self._detectors.values():
            try:
                detector.observe("", False)
            except Exception:
                pass

    def _build_draft(self, name, parsed, text, lines, tokens, frame):
        if name == "scoreboard":
            rows = list(parsed) if isinstance(parsed, list) else []
            if not rows:
                self._log(f"scoreboard not used: Missing(columns)")
                return None
            match = {
                "modeKey": self.profile if self.profile in ("warzone", "multiplayer") else "warzone",
                "result": "match",
                "source": "screen_capture",
                "autotrack_parser": "scoreboard",
                "playedAt": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
            }
            return {"parser": "scoreboard", "text": text, "lines": lines, "tokens": tokens,
                    "frame": frame, "profile": self.profile, "rows": rows, "match": match}
        if not isinstance(parsed, dict):
            return None
        draft = dict(parsed)
        draft.setdefault("text", text)
        draft.setdefault("lines", lines)
        draft.setdefault("tokens", tokens)
        draft["frame"] = frame
        return draft

    def _match_for(self, draft):
        parser = draft.get("parser")
        if parser == "after_action":
            result = str(draft.get("result") or "")
            return {
                "modeKey": draft.get("modeKey") or "multiplayer",
                "result": "win" if result == "Victory" else "loss" if result == "Defeat" else "match",
                "scoreOwn": draft.get("scoreOwn"),
                "scoreOpp": draft.get("scoreOpp"),
                "source": "after_action",
                "playedAt": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
            }
        if parser == "warzone_summary":
            placement = draft.get("placement")
            stats = {"placement": placement} if isinstance(placement, (int, float)) and not isinstance(placement, bool) else {}
            return {
                "modeKey": draft.get("modeKey") or "warzone",
                "game": "warzone",
                "result": "win" if draft.get("result") == "Victory" else "loss" if draft.get("result") == "Defeat" else "match",
                "placement": placement,
                "stats": stats,
                "source": "warzone_summary",
                "playedAt": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
            }
        return None

    def _persist(self, draft):
        if self._store is None:
            return
        parser = draft.get("parser")
        try:
            if parser in ("after_action", "warzone_summary"):
                match = self._match_for(draft)
                if match is None:
                    return
                mid, _added = self._store.add(match, "needs_review")
                rows = draft.get("players") or draft.get("rows") or []
                if rows:
                    self._store.update(mid, ocr=draft.get("text", ""), players=__import__("json").dumps(rows, ensure_ascii=False))
                else:
                    self._store.update(mid, ocr=draft.get("text", ""))
                self._store.enqueue_sync("discord_match", mid)
                self._store.record_tracking_feed("match", "saved", "", {"parser": parser, "match_id": mid})
                draft["match_id"] = mid
                if parser == "after_action":
                    try:
                        aliases = E.identity_aliases(match)
                    except Exception:
                        aliases = []
                    alias = aliases[0] if aliases else E.key_for(match)
                    self._after_action_alias_at[alias] = self._time()
                    self._last_after_action_at = self._time()
            elif parser == "scoreboard":
                match = draft.get("match")
                mid, _added = self._store.add(dict(match), "needs_review")
                self._store.update(mid, ocr=draft.get("text", ""), players=__import__("json").dumps(draft.get("rows", []), ensure_ascii=False))
                self._store.enqueue_sync("discord_match", mid)
                self._store.record_tracking_feed("match", "saved", "", {"parser": "scoreboard", "match_id": mid})
                draft["match_id"] = mid
            elif parser == "combat_record":
                snap_id = self._store.save_lifetime_snapshot({
                    "game": draft.get("game", "mw4"), "mode": draft.get("mode", "multiplayer"),
                    "scope": draft.get("scope", "lifetime"), "kd_num": draft.get("kd_num") or 0,
                    "kd_den": draft.get("kd_den") or 0, "wins": draft.get("wins") or 0,
                    "losses": draft.get("losses") or 0, "games": draft.get("games") or 0,
                    "raw": draft.get("raw") or draft.get("text", ""),
                })
                self._store.record_tracking_feed("record", "saved", "", {"parser": "combat_record", "snapshot_id": snap_id})
                self._store.enqueue_sync("lifetime_record", snap_id)
                draft["snapshot_id"] = snap_id
            elif parser == "camo_challenge":
                det_id = self._store.save_camo_detection({
                    "weapon": draft.get("weapon", ""), "camo": draft.get("camo", ""),
                    "grind": draft.get("grind", "mw4"), "state": "saved",
                    "changed": draft.get("changed", []), "read": draft.get("read", []),
                })
                self._store.record_tracking_feed("camo", "saved", "", {"parser": "camo_challenge", "detection_id": det_id})
                self._store.enqueue_sync("camo", det_id)
                draft["detection_id"] = det_id
            elif parser == "banner_hud":
                pass  # Log only, PII-free hint; never creates a match.
        except Exception as exc:
            self._log(f"autotrack persist failed for {parser} ({type(exc).__name__}: {exc})")

    def _is_scoreboard_suppressed(self, draft):
        match = draft.get("match") or {}
        try:
            aliases = E.identity_aliases(match)
        except Exception:
            aliases = []
        candidates = list(aliases) + [E.key_for(match)]
        now = self._time()
        for alias in candidates:
            at = self._after_action_alias_at.get(alias)
            if at is not None and (now - at) <= self.suppression_window:
                return True
        # Watcher drafts carry no CODMunity playedAt, so a distinct-game
        # natural alias can never be proven; fall back to the 90 s window
        # after any after-action read (design §4 ordering rule).
        if self._last_after_action_at is not None and (now - self._last_after_action_at) <= self.suppression_window:
            return True
        return False

    def _emit(self, draft):
        self.last_draft = draft
        parser = draft.get("parser")
        self._log(f"{parser} read (local)")
        try:
            if self._on_draft:
                self._on_draft(dict(draft))
        except Exception:
            pass

    # -- thread lifecycle --------------------------------------------------
    def run(self):
        while not self._stop.wait(self.interval):
            try:
                self.step()
            except Exception as exc:
                self._log(f"autotrack tick failed ({type(exc).__name__}: {exc})")
        if not self._own_capture:
            return
        try:
            close = getattr(self._capture, "close", None)
            if close is not None:
                close()
        except Exception:
            pass

    def stop(self, timeout=5.0):
        self._stop.set()
        if self is not threading.current_thread() and self.is_alive():
            self.join(timeout=timeout)


class SyncWorker(threading.Thread):
    """Drain ``sync_outbox`` rows (design section 6), local-only.

    ``sender(store, row)`` is injected by the UI layer and decides
    delivery: return True to mark delivered, False to leave pending
    (not ready yet), raise to count a failure. Local kinds
    (``lifetime_record``, ``camo``) are marked delivered without
    network traffic. ``discord_match`` without a sender stays pending.
    Failures back off in memory (5 s doubling to 5 min); 10 failures
    mark the row failed. ``step()`` is one poll and is testable.
    """

    MAX_ATTEMPTS = 10

    def __init__(self, store, sender=None, interval=5.0, on_log=None,
                 time_func=None, name="SyncWorker"):
        super().__init__(name=name, daemon=True)
        self._store = store
        self._sender = sender
        self.interval = max(0.5, float(interval))
        self._on_log = on_log
        self._time = time_func or time.monotonic
        self._stop = threading.Event()
        self._failures = {}
        self._next_at = {}

    def _log(self, message):
        text = str(message)
        try:
            if self._store is not None and hasattr(self._store, "log_autotrack"):
                self._store.log_autotrack(text)
        except Exception:
            pass
        try:
            if self._on_log:
                self._on_log(text)
        except Exception:
            pass

    def step(self):
        """One drain pass. Returns number of rows marked delivered."""
        delivered = 0
        try:
            rows = self._store.pending_sync(limit=50)
        except Exception as exc:
            self._log(f"sync pending read failed ({type(exc).__name__}: {exc})")
            return 0
        now = self._time()
        for row in rows:
            outbox_id = str(row.get("id") or "")
            kind = str(row.get("kind") or "")
            if not outbox_id:
                continue
            if self._next_at.get(outbox_id, 0) > now:
                continue
            if kind != "discord_match":
                try:
                    self._store.mark_sync(outbox_id, "delivered")
                    delivered += 1
                    self._failures.pop(outbox_id, None)
                    self._next_at.pop(outbox_id, None)
                except Exception as exc:
                    self._log(f"sync mark failed {outbox_id} ({exc})")
                continue
            if self._sender is None:
                continue  # Leave pending; no delivery surface configured.
            try:
                sent = bool(self._sender(self._store, dict(row)))
            except Exception as exc:
                count = self._failures.get(outbox_id, 0) + 1
                self._failures[outbox_id] = count
                if count >= self.MAX_ATTEMPTS:
                    try:
                        self._store.mark_sync(outbox_id, "failed", error=str(exc)[:300])
                    except Exception:
                        pass
                    self._log(f"sync failed {outbox_id} after {count} attempts")
                else:
                    self._next_at[outbox_id] = now + min(300.0, 5.0 * (2.0 ** (count - 1)))
                    self._log(f"sync retry {outbox_id} attempt {count} ({exc})")
                continue
            if sent:
                try:
                    self._store.mark_sync(outbox_id, "delivered")
                    delivered += 1
                    self._failures.pop(outbox_id, None)
                    self._next_at.pop(outbox_id, None)
                except Exception as exc:
                    self._log(f"sync mark failed {outbox_id} ({exc})")
            # False means "not ready yet"; stay pending for a later tick.
        return delivered

    def run(self):
        while not self._stop.wait(self.interval):
            try:
                self.step()
            except Exception as exc:
                self._log(f"sync tick failed ({type(exc).__name__}: {exc})")

    def stop(self, timeout=5.0):
        self._stop.set()
        if self is not threading.current_thread() and self.is_alive():
            self.join(timeout=timeout)

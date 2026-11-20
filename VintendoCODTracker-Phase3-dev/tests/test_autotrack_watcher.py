"""Watcher state-machine tests (SINGLE_EXE_DESIGN section 8.4)."""
import threading
import unittest

import engine as E
from autotrack import AutotrackWatcher, WatcherState

COD_WINDOWS = [{"process_name": "cod.exe", "title": "Call of Duty",
                "monitor_handle": 11, "width": 1920, "height": 1080}]
OUTPUTS = [{"device": 0, "output": 0, "width": 1920, "height": 1080, "monitor_handle": 11}]

MENU_TEXT = "MAIN MENU\nSETTINGS\nAUDIO\nVIDEO\nCREDITS"
SCOREBOARD_TEXT = (
    "PLAYER  KILLS  DAMAGE  SCORE\nVintendo  12  3400  4500\n"
    "Concha  8  2000  3200\nAlpha  5  1500  2100\nBravo  4  1200  1800"
)
AFTER_ACTION_TEXT = (
    "AFTER ACTION REPORT\nTEAM DEATHMATCH\nVICTORY\n"
    "YOUR SCORE  100\nENEMY SCORE  88\n"
    "PLAYER  KILLS  DAMAGE\nVintendo  12  3400\nConcha  8  2000"
)


class FakeCapture:
    def __init__(self, windows, text, lines):
        self._windows = windows
        self._text = text
        self._lines = lines
        self.lock = threading.RLock()

    def set_script(self, windows, text):
        self._windows = windows
        self._text = text
        self._lines = [line for line in text.splitlines() if line.strip()]

    def game_windows(self):
        return list(self._windows)

    def outputs(self):
        return list(OUTPUTS)

    def game_output(self):
        from capture_engine import select_game_output
        return select_game_output(self.outputs(), self.game_windows())

    def grab(self, output, roi=None):
        return object()

    def recognize_with_tokens(self, frame):
        return self._text, list(self._lines), []

    def close(self):
        pass


def _store(tmp):
    import tempfile
    from pathlib import Path
    root = Path(tempfile.mkdtemp(prefix="autotrack-"))
    return E.Store(root / "history.sqlite3")


def _arm(watcher, capture, windows, text, repeats=3):
    capture.set_script(windows, text)
    for _ in range(repeats):
        watcher.step()


class WatcherTests(unittest.TestCase):
    def test_no_game_to_game_background_to_watching(self):
        store = _store(self)
        capture = FakeCapture([], MENU_TEXT, MENU_TEXT.splitlines())
        drafts = []
        watcher = AutotrackWatcher(capture, store, {}, on_draft=drafts.append)
        watcher.step()
        self.assertEqual(watcher.state, WatcherState.NO_GAME)
        # Game window present but no gate matches: background, no OCR draft.
        _arm(watcher, capture, COD_WINDOWS, MENU_TEXT, repeats=3)
        self.assertEqual(watcher.state, WatcherState.GAME_BACKGROUND)
        self.assertEqual(drafts, [])
        # Stable scoreboard fires once armed (frames=2).
        capture.set_script(COD_WINDOWS, SCOREBOARD_TEXT)
        watcher.step()
        self.assertEqual(watcher.state, WatcherState.WATCHING)
        watcher.step()
        self.assertTrue(any(d.get("parser") == "scoreboard" for d in drafts))

    def test_after_action_suppresses_scoreboard_within_window(self):
        store = _store(self)
        capture = FakeCapture(COD_WINDOWS, MENU_TEXT, MENU_TEXT.splitlines())
        drafts = []
        clock = [1000.0]
        watcher = AutotrackWatcher(capture, store, {}, on_draft=drafts.append,
                                   time_func=lambda: clock[0], suppression_window=90.0)
        _arm(watcher, capture, COD_WINDOWS, MENU_TEXT, repeats=3)
        capture.set_script(COD_WINDOWS, AFTER_ACTION_TEXT)
        watcher.step()
        watcher.step()  # StableDetector frames=2 fires here.
        self.assertTrue(any(d.get("parser") == "after_action" for d in drafts))
        before = len([d for d in drafts if d.get("parser") == "scoreboard"])
        capture.set_script(COD_WINDOWS, SCOREBOARD_TEXT)
        watcher.step()
        watcher.step()
        suppressed = len([d for d in drafts if d.get("parser") == "scoreboard"]) == before
        self.assertTrue(suppressed)
        self.assertIn("already taken from its after action report",
                      "\n".join(store.autotrack_log_tail()))
        # Past the window the same scoreboard is no longer suppressed.
        # The detector needs 3 empty ticks to re-arm before it can fire.
        clock[0] += 91.0
        _arm(watcher, capture, COD_WINDOWS, MENU_TEXT, repeats=3)
        capture.set_script(COD_WINDOWS, SCOREBOARD_TEXT)
        watcher.step()
        watcher.step()
        self.assertTrue(any(d.get("parser") == "scoreboard" for d in drafts))

    def test_stable_detector_needs_empty_arm_before_firing(self):
        from capture_engine import StableDetector
        detector = StableDetector(frames=2)
        self.assertFalse(any(detector.observe("board", True) for _ in range(5)))
        for _ in range(3):
            detector.observe("", False)
        self.assertFalse(detector.observe("board", True))
        self.assertTrue(detector.observe("board", True))

    def test_watcher_persists_after_action_to_store(self):
        store = _store(self)
        capture = FakeCapture(COD_WINDOWS, MENU_TEXT, MENU_TEXT.splitlines())
        watcher = AutotrackWatcher(capture, store, {})
        _arm(watcher, capture, COD_WINDOWS, MENU_TEXT, repeats=3)
        capture.set_script(COD_WINDOWS, AFTER_ACTION_TEXT)
        watcher.step()
        watcher.step()
        self.assertEqual(watcher.state, WatcherState.WATCHING)
        self.assertTrue(store.history())
        self.assertTrue(store.pending_sync())
        self.assertTrue(store.tracking_feed())


if __name__ == "__main__":
    unittest.main()

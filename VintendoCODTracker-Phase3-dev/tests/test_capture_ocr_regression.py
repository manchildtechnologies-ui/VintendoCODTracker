"""Focused regression tests for the VERIFIED capture/OCR/screen-validation items.

Locks in behaviors confirmed by code inspection so later edits cannot
silently change them. Headless-safe: no real dialogs, no network, local
OCR is faked except where the suite already exercises it for real.
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import capture_utils
import engine as E


def tmp_store(name):
    root = Path(tempfile.mkdtemp(prefix=name))
    return E.Store(root / "history.sqlite3"), root


def box(left, top, right, bottom):
    return (float(left), float(top), float(right), float(bottom))


def word_token(text, cx, cy, width=60, height=25):
    return (text, box(cx - width / 2, cy - height / 2, cx + width / 2, cy + height / 2))


class OcrEngineConfigTests(unittest.TestCase):
    def test_rapidocr_built_without_angle_classifier_and_capped_threads(self):
        import capture_engine

        seen = {}

        class FakeOcr:
            def __init__(self, **kwargs):
                seen.update(kwargs)

            def __call__(self, frame):
                return [], None

        fake_module = type(sys)("rapidocr_onnxruntime")
        fake_module.RapidOCR = FakeOcr
        service = object.__new__(capture_engine.CaptureEngine)
        import threading

        service.lock = threading.RLock()
        service.ocr = None
        with patch.dict(sys.modules, {"rapidocr_onnxruntime": fake_module}):
            service.recognize_with_tokens([[0]])
        self.assertEqual(seen.get("use_cls"), False)
        self.assertEqual(seen.get("intra_op_num_threads"), 2)
        self.assertEqual(seen.get("inter_op_num_threads"), 1)


class CropPreprocessingTests(unittest.TestCase):
    def test_scoreboard_roi_matches_reference_bounds(self):
        self.assertEqual(
            __import__("autotrack").scaled_roi("scoreboard", 2560, 1440),
            (202, 487, 202 + 2156, 487 + 464),
        )

    def test_scoreboard_roi_scales_down_with_width(self):
        self.assertEqual(
            __import__("autotrack").scaled_roi("scoreboard", 1920, 1080),
            (152, 365, 1769, 713),
        )

    def test_unknown_roi_name_is_not_croppable(self):
        import autotrack

        self.assertIsNone(autotrack.scaled_roi("no_such_parser", 2560, 1440))

    def test_crop_frame_returns_roi_copy_only(self):
        import numpy as np
        import autotrack

        frame = np.zeros((1440, 2560, 3), dtype=np.uint8)
        cropped = autotrack.crop_frame(frame, "scoreboard")
        self.assertIsNotNone(cropped)
        self.assertEqual(cropped.shape, (464, 2156, 3))

    def test_crop_frame_rejects_non_arrays_and_unknown_names(self):
        import autotrack

        self.assertIsNone(autotrack.crop_frame(object(), "scoreboard"))
        self.assertIsNone(autotrack.crop_frame(None, "scoreboard"))
        import numpy as np

        self.assertIsNone(
            autotrack.crop_frame(np.zeros((1440, 2560, 3), dtype=np.uint8), "no_such_parser")
        )


class ConfidenceHandlingTests(unittest.TestCase):
    def test_grouped_tokens_drop_ocr_confidence_scores(self):
        result = [
            [[[10, 10], [70, 10], [70, 35], [10, 35]], "Vintendo", 0.42],
            [[[200, 10], [230, 10], [230, 35], [200, 35]], "12", 0.97],
        ]
        text, lines, tokens = capture_utils.group_ocr_lines_detailed(result)
        self.assertEqual(tokens, [("Vintendo", (10.0, 10.0, 70.0, 35.0)),
                                 ("12", (200.0, 10.0, 230.0, 35.0))])
        for token in tokens:
            self.assertEqual(len(token), 2)
        self.assertNotIn("0.42", text)

    def test_spatial_rows_are_never_verified(self):
        tokens = [
            word_token("PLAYER", 100, 100),
            word_token("KILLS", 400, 100),
            word_token("DAMAGE", 600, 100),
            word_token("Vintendo", 100, 200),
            word_token("12", 400, 200),
            word_token("3400", 600, 200),
        ]
        rows = E.parse_scoreboard("", ocr_tokens=tokens)
        self.assertTrue(rows)
        for row in rows:
            self.assertFalse(row["verified"])
            self.assertEqual(row["confidence"], "review_required")
        by_name = {row["name"]: row for row in rows}
        self.assertEqual(by_name["Vintendo"]["stats"], {"kills": 12, "damage": 3400})
        self.assertEqual(by_name["Vintendo"]["source"], "ocr_spatial")


class ScoreboardGateTests(unittest.TestCase):
    GOOD = [
        "PLAYER KILLS DAMAGE SCORE",
        "Vintendo 12 3400 4500",
        "Concha 8 2000 3200",
        "Alpha 5 1500 2100",
        "Bravo 9 2200 3300",
    ]
    MENU = ["MAIN MENU", "SETTINGS", "AUDIO", "CREDITS"]

    def test_scoreboard_gate_arms_on_player_column_and_stats(self):
        self.assertTrue(capture_utils.looks_like_scoreboard(self.GOOD))

    def test_scoreboard_gate_rejects_menu(self):
        self.assertFalse(capture_utils.looks_like_scoreboard(self.MENU))

    def test_scoreboard_gate_needs_player_column(self):
        lines = ["KILLS DAMAGE SCORE", "12 3400 4500", "8 2000 3200",
                 "5 1500 2100", "9 2200 3300", "7 1800 2900"]
        self.assertFalse(capture_utils.looks_like_scoreboard(lines))

    def test_scoreboard_gate_needs_enough_numeric_rows(self):
        lines = self.GOOD[:3]
        self.assertFalse(capture_utils.looks_like_scoreboard(lines))


class ParserValidationTests(unittest.TestCase):
    def test_parser_int_accepts_plain_and_grouped_numbers(self):
        self.assertEqual(E._parser_int("3400"), 3400)
        self.assertEqual(E._parser_int(" 1,200 "), 1200)
        self.assertEqual(E._parser_int("1.25"), 1.25)

    def test_parser_int_rejects_ocr_garbage(self):
        for bad in ("I2", "12a", "", "abc", "12-34"):
            self.assertIsNone(E._parser_int(bad), bad)

    def test_exact_roster_match_scores_one(self):
        roster = [{"name": "Vintendo", "aliases": [], "enabled": True}]
        detail = E.match_player_detail("vintendo", roster)
        self.assertEqual(detail["status"], "matched")
        self.assertEqual(detail["score"], 1.0)


class StoreDedupTests(unittest.TestCase):
    def test_reread_keeps_first_payload(self):
        store, _root = tmp_store("first-payload-")
        first = {"modeKey": "team_deathmatch", "result": "win", "scoreOwn": 100,
                 "scoreOpp": 88, "playedAt": "2026-11-07T10:00:00Z"}
        mid, added = store.add(dict(first), "needs_review")
        self.assertTrue(added)
        reread = dict(first, matchId="codmunity-source-id", extra="polluted")
        mid2, added2 = store.add(reread, "needs_review")
        self.assertEqual(mid2, mid)
        self.assertFalse(added2)
        kept = json.loads(store.get(mid)["payload"])
        self.assertNotIn("matchId", kept)
        self.assertNotIn("extra", kept)
        self.assertEqual(kept["scoreOwn"], 100)


if __name__ == "__main__":
    unittest.main()

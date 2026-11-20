"""Native Sync & Account page: no external app may spawn, outbox drains locally."""
import os
import tempfile
import unittest
from pathlib import Path

import codmunity_camo as C
import engine as E
from autotrack import SyncWorker


def _store():
    return E.Store(Path(tempfile.mkdtemp(prefix="native-sync-")) / "history.sqlite3")


class LinkedAccountTests(unittest.TestCase):
    def test_uid_parsed_from_user_dir(self):
        account = C.linked_account({"user_dir": "C:/x/stats/u-9Vhs7iIIqKQ73vgW557Fw2JZwrx1"})
        self.assertEqual(account["uid"], "9Vhs7iIIqKQ73vgW557Fw2JZwrx1")

    def test_missing_user_dir_yields_no_account(self):
        self.assertEqual(C.linked_account({}), {})
        self.assertEqual(C.linked_account(None), {})
        self.assertEqual(C.linked_account({"user_dir": "C:/x/stats"}), {})


class SyncWorkerTests(unittest.TestCase):
    def test_local_kinds_drain_without_sender(self):
        store = _store()
        store.enqueue_sync("lifetime_record", "snap1")
        store.enqueue_sync("camo", "det1")
        worker = SyncWorker(store, sender=None)
        self.assertEqual(worker.step(), 2)
        self.assertEqual(store.pending_sync(limit=50), [])

    def test_discord_match_stays_pending_without_sender(self):
        store = _store()
        store.enqueue_sync("discord_match", "m1")
        worker = SyncWorker(store, sender=None)
        self.assertEqual(worker.step(), 0)
        self.assertEqual(len(store.pending_sync(limit=50)), 1)

    def test_sender_true_delivers_discord_row(self):
        store = _store()
        store.enqueue_sync("discord_match", "m1")
        worker = SyncWorker(store, sender=lambda s, r: True)
        self.assertEqual(worker.step(), 1)
        self.assertEqual(store.pending_sync(limit=50), [])

    def test_repeated_failure_marks_failed(self):
        store = _store()
        store.enqueue_sync("discord_match", "m1")
        clock = [1000.0]
        calls = []
        def bad_sender(s, r):
            calls.append(1)
            raise RuntimeError("net down")
        worker = SyncWorker(store, sender=bad_sender, time_func=lambda: clock[0])
        for _ in range(10):
            clock[0] += 400.0
            worker.step()
        self.assertEqual(len(calls), 10)
        self.assertEqual(store.pending_sync(limit=50), [])


class NoExternalLaunchTests(unittest.TestCase):
    def test_launchers_never_start_desktop_app(self):
        root = Path(E.ROOT)
        for name in ("Launch-VintendoCODTracker.vbs", "Launch-Phase3-Tracker.vbs",
                     "Launch-VintendoCODTracker-Unified.vbs"):
            text = (root / name).read_text(encoding="utf-8-sig", errors="replace")
            self.assertNotIn("--start-minimized", text, name)
            self.assertNotIn("codmunity-desktop.exe\", 0, False", text, name)


class SyncPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        cls.qt = QApplication.instance() or QApplication([])

    def setUp(self):
        import app
        self.window = app.Window()
        self.window.notice = lambda *args, **kwargs: None
        self.window.ask = lambda *args, **kwargs: False

    def tearDown(self):
        self.window.shutdown()
        self.window.deleteLater()
        self.qt.processEvents()

    def test_sync_page_refreshes(self):
        self.window.refresh_codmunity_sync()
        self.assertIn("Outbox", self.window.sync_state_label.text())
        self.assertIn("App usage", self.window.app_usage_label.text())

    def test_lifetime_button_requires_sign_in(self):
        self.window.notice = lambda message: setattr(self.window, "_noticed", message)
        self.window.upload_lifetime_stats_ui()
        self.assertIn("Sign in", self.window._noticed)
        self.assertTrue(hasattr(self.window, "upload_lifetime_button"))

    def test_upload_button_counts_and_guards(self):
        import tempfile, json
        from pathlib import Path
        import codmunity_player_stats as C
        root = Path(tempfile.mkdtemp(prefix="camo-upload-ui-"))
        (root / "matches.json").write_text(json.dumps([
            {"matchId": "a", "modeKey": "m", "result": "win", "stats": {}},
            {"matchId": "b", "modeKey": "m", "result": "win", "stats": {}, "sent": True}]), encoding="utf-8")
        self.window.store.set_meta("codmunity_stats", {"user_dir": str(root)})
        self.window.refresh_codmunity_sync()
        self.assertIn("(1)", self.window.upload_pending_button.text())
        self.assertFalse(self.window.upload_pending_button.isEnabled())
        self.window.notice = lambda message: setattr(self.window, "_noticed", message)
        self.window.upload_pending_matches_ui()
        self.assertIn("Sign in", self.window._noticed)
        self.window.codmunity_player_service = C.ModularPlayerStatsService(fetcher=lambda uid: {})
        self.window.codmunity_auth = C.CodmunityAuthClient(refresh_token="ref-x")
        self.window.refresh_codmunity_sync()
        self.assertTrue(self.window.upload_pending_button.isEnabled())

    def test_sign_in_wires_configured_service_then_sign_out_clears(self):
        import time as _time
        import app as appmod
        real_client = appmod.codmunity_player_stats.CodmunityAuthClient

        class FakeAuth:
            def __init__(self, *args, **kwargs):
                pass

            def sign_in(self, email, password):
                assert email and password
                return {"email": email, "localId": "uid-x"}

            @property
            def refresh_token(self):
                return "ref-x"

        appmod.codmunity_player_stats.CodmunityAuthClient = FakeAuth
        try:
            self.window.codmunity_email_edit.setText("me@example.com")
            self.window.codmunity_password_edit.setText("pw")
            self.window.codmunity_sign_in()
            end = _time.time() + 10
            while _time.time() < end and not self.window.codmunity_player_service.configured:
                self.qt.processEvents()
                _time.sleep(0.05)
            self.assertTrue(self.window.codmunity_player_service.configured)
            self.assertEqual(self.window.codmunity_email_edit.text(), "me@example.com")
            self.assertEqual(self.window.codmunity_password_edit.text(), "")
            self.window.codmunity_sign_out()
            self.assertFalse(self.window.codmunity_player_service.configured)
            self.assertIn("Not signed in", self.window.codmunity_account_status.text())
        finally:
            appmod.codmunity_player_stats.CodmunityAuthClient = real_client

    def test_export_writes_isolated_file(self):
        import app as appmod
        before = set((appmod.E.DATA / "exports").glob("*.json")) if (appmod.E.DATA / "exports").is_dir() else set()
        self.window.export_matches_json()
        after = set((appmod.E.DATA / "exports").glob("*.json"))
        self.assertTrue(after - before)

    def test_copy_diagnostics_reaches_clipboard(self):
        from PySide6.QtWidgets import QApplication
        self.window.copy_diagnostics()
        self.assertIn("Matches stored:", QApplication.clipboard().text())


if __name__ == "__main__":
    unittest.main()

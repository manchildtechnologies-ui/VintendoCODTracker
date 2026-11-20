"""Regression: tests must not write into the live tracker data dir."""
import os
import tempfile
import unittest
from pathlib import Path

import engine as E


class DataIsolationTests(unittest.TestCase):
    def test_engine_data_is_temp_not_live(self):
        live = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "Vintendo COD Tracker"
        self.assertEqual(E.DATA, Path(os.environ["VINTENDO_DATA_DIR"]))
        self.assertNotEqual(E.DATA, live)
        self.assertIn(str(Path(tempfile.gettempdir())), str(E.DATA))

    def test_default_store_path_is_isolated(self):
        store = E.Store()
        self.assertTrue(str(store.path).startswith(str(E.DATA)))

    def test_window_starts_with_empty_roster(self):
        import os as _os
        _os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        import app
        qt = QApplication.instance() or QApplication([])
        window = app.Window()
        try:
            self.assertEqual(window.store.roster(), [])
        finally:
            window.shutdown()
            window.deleteLater()
            qt.processEvents()


if __name__ == "__main__":
    unittest.main()

"""Tests for the COD Tweaker review-first config editor (logic + nav registration)."""
import os
import shutil
import tempfile
import unittest
from pathlib import Path

import cod_tweaker as CT

SAMPLE = Path(__file__).resolve().parent / "cod_tweaker_sample.cfg"


def make_copy(directory):
    target = Path(directory) / "options.cfg"
    shutil.copy2(SAMPLE, target)
    return target


class ParserSamplesTests(unittest.TestCase):
    def test_parser_samples(self):
        settings = CT.parse_text(SAMPLE.read_text(encoding="utf-8-sig"), source="sample")
        by_key = {item.key: item for item in settings}
        self.assertEqual([item.key for item in settings], [
            "Renderer.Quality@Variant0", "Renderer.ResolutionScale", "Renderer.VSync",
            "System.ThreadCount", "System.MemoryBudget",
            "Hidden.DebugOverlay", "Hidden.FPSCounter", "Hidden.MOTD",
            "Fov@0;24892;45350", "TextChat background opacity@0;54363;47620"])
        # Sections from lone-// plus // Name headers as well as [bracket] headers.
        self.assertEqual(by_key["Renderer.VSync"].section, "Graphics")
        self.assertEqual(by_key["System.ThreadCount"].section, "Performance")
        self.assertEqual(by_key["Hidden.MOTD"].section, "Hidden")
        self.assertEqual(by_key["Fov@0;24892;45350"].section, "Gameplay")
        # Key@variant split, display name, and ValueRef identity.
        quality = by_key["Renderer.Quality@Variant0"]
        self.assertEqual(quality.variant, "Variant0")
        self.assertEqual(quality.name, "Quality")
        self.assertEqual(quality.valueref, "Renderer.Quality@Variant0")
        # Choice from one of ... with a real value; // comments become description.
        self.assertEqual(quality.kind, "choice")
        self.assertEqual(quality.options, ["Low", "Medium", "High"])
        self.assertEqual(quality.original, "High")
        self.assertIn("graphics preset", quality.description.lower())
        # Number with min/max/decimals.
        scale = by_key["Renderer.ResolutionScale"]
        self.assertEqual(scale.kind, "number")
        self.assertEqual((scale.min, scale.max, scale.decimals), (0.5, 2.0, 3))
        self.assertIn("sharper", scale.description.lower())
        # Bool, plain number, unquoted text, and quoted text forms.
        self.assertEqual(by_key["Renderer.VSync"].kind, "bool")
        self.assertEqual(by_key["System.MemoryBudget"].kind, "number")
        self.assertEqual(by_key["Hidden.MOTD"].kind, "quoted")
        self.assertEqual(by_key["Hidden.MOTD"].original, "Welcome, soldier!")
        # Keys with spaces and semicolon ids parse to readable names and numeric variants.
        fov = by_key["Fov@0;24892;45350"]
        self.assertEqual((fov.name, fov.variant), ("Fov", "0"))
        self.assertEqual((fov.min, fov.max, fov.decimals), (60.0, 120.0, 6))
        self.assertIn("1st person field of view", fov.description.lower())
        chat = by_key["TextChat background opacity@0;54363;47620"]
        self.assertEqual((chat.name, chat.variant), ("TextChat background opacity", "0"))
        self.assertIn("text chat background opacity", chat.description.lower())
        # DO-NOT-MODIFY protection flag.
        overlay = by_key["Hidden.DebugOverlay"]
        self.assertTrue(overlay.protected)
        self.assertFalse(by_key["Hidden.FPSCounter"].protected)
        # Categorization mirrors the UI tabs.
        self.assertEqual(CT.category_for("Graphics", False), "Graphics")
        self.assertEqual(CT.category_for("Performance", False), "Performance")
        self.assertEqual(CT.category_for("Graphics", True), "Hidden Settings")
        # Staging then rendering preserves the real on-disk format.
        doc = CT.CODDocument.load(SAMPLE)
        ok, normalized, _ = doc.stage("Fov@0;24892;45350", "100")
        self.assertTrue(ok)
        self.assertEqual(normalized, "100.000000")
        rendered = doc.render_bytes().decode("utf-8")
        self.assertIn("Fov@0;24892;45350 = 100.000000 // 60.000000 to 120.000000", rendered)
        self.assertIn("TextChat background opacity@0;54363;47620 = 0.200000 // 0.000000 to 1.000000", rendered)


class BackupRestoreTests(unittest.TestCase):
    def test_backup_restore(self):
        work = Path(tempfile.mkdtemp(prefix="tweaker-"))
        self.addCleanup(shutil.rmtree, work, True)
        target = make_copy(work)
        store = CT.BackupStore(work / "backups")
        original = target.read_bytes()
        backup = store.backup_file(target)
        self.assertTrue(backup.is_file())
        target.write_bytes(original + b"\nRenderer.VSync false\n")
        safety = store.restore(backup, target)
        self.assertEqual(target.read_bytes(), original)
        # Restore takes a fresh safety backup first.
        self.assertTrue(safety.is_file())
        self.assertGreaterEqual(len(store.list_backups(basename=target.name)), 2)
        # Keep-30: newest 30 per file survive, oldest pruned.
        for _ in range(32):
            store.backup_file(target)
        kept = store.list_backups(basename=target.name)
        self.assertEqual(len(kept), 30)
        # Read-only lock state is preserved across backup and restore.
        CT.set_locked(target, True)
        try:
            self.assertTrue(CT.is_locked(target))
            locked_backup = store.backup_file(target)
            CT.set_locked(target, False)
            target.write_bytes(b"tampered")
            CT.set_locked(target, True)
            store.restore(locked_backup, target)
            self.assertTrue(CT.is_locked(target))
            self.assertEqual(target.read_bytes(), locked_backup.read_bytes())
        finally:
            CT.set_locked(target, False)


class InvalidValuesTests(unittest.TestCase):
    def test_invalid_values(self):
        work = Path(tempfile.mkdtemp(prefix="tweaker-"))
        self.addCleanup(shutil.rmtree, work, True)
        doc = CT.CODDocument.load(make_copy(work))
        # Out-of-range numbers are rejected with an inline error.
        ok, _, error = doc.stage("Renderer.ResolutionScale", "9.5")
        self.assertFalse(ok)
        self.assertIn("maximum", error.lower())
        ok, _, error = doc.stage("Renderer.ResolutionScale", "0.1")
        self.assertFalse(ok)
        self.assertIn("minimum", error.lower())
        # Decimals are normalized (validate + normalize enforces decimals).
        ok, normalized, _ = doc.stage("Renderer.ResolutionScale", "1.23456")
        self.assertTrue(ok)
        self.assertEqual(normalized, "1.235")
        doc.revert_to_original("Renderer.ResolutionScale")
        # Choice and bool rejects; bool spellings normalize.
        ok, _, error = doc.stage("Renderer.Quality@Variant0", "Ultra")
        self.assertFalse(ok)
        self.assertIn("expected one of", error.lower())
        ok, _, error = doc.stage("Renderer.VSync", "maybe")
        self.assertFalse(ok)
        ok, normalized, _ = doc.stage("Renderer.VSync", "YES")
        self.assertTrue(ok)
        self.assertEqual(normalized, "true")
        doc.revert_to_original("Renderer.VSync")
        # Protected settings reject every staged value.
        ok, _, error = doc.stage("Hidden.DebugOverlay", "1")
        self.assertFalse(ok)
        self.assertIn("do not modify", error.lower())
        self.assertIsNone(doc.match(key="Hidden.DebugOverlay").pending)
        # Unknown keys fail closed.
        ok, _, _ = doc.stage("Nope.Missing", "1")
        self.assertFalse(ok)


class ExternalChangeConflictTests(unittest.TestCase):
    def test_external_change_conflicts(self):
        work = Path(tempfile.mkdtemp(prefix="tweaker-"))
        self.addCleanup(shutil.rmtree, work, True)
        target = make_copy(work)
        doc = CT.CODDocument.load(target)
        self.assertFalse(doc.is_stale_on_disk())
        ok, _, _ = doc.stage("Renderer.ResolutionScale", "1.500")
        self.assertTrue(ok)
        # An external edit to a different setting is detected by byte compare.
        raw = target.read_text(encoding="utf-8-sig")
        target.write_text(raw.replace("Renderer.VSync = true", "Renderer.VSync = false"), encoding="utf-8")
        self.assertTrue(doc.is_stale_on_disk())
        # Reload keeps staged edits while picking up the disk value.
        doc.refresh_keep_pending()
        self.assertFalse(doc.is_stale_on_disk())
        self.assertEqual(doc.match(key="Renderer.ResolutionScale").pending, "1.500")
        self.assertEqual(doc.match(key="Renderer.VSync").original, "false")
        # Revert-to-original clears staging; revert-to-disk adopts the disk value.
        self.assertTrue(doc.revert_to_original("Renderer.ResolutionScale"))
        self.assertIsNone(doc.match(key="Renderer.ResolutionScale").pending)
        target.write_text(target.read_text(encoding="utf-8-sig").replace(
            "System.ThreadCount = 8", "System.ThreadCount = 12"), encoding="utf-8")
        ok, _ = doc.revert_to_disk("System.ThreadCount")
        self.assertTrue(ok)
        self.assertEqual(doc.match(key="System.ThreadCount").pending, "12")


class PresetsReviewFirstTests(unittest.TestCase):
    def test_presets_review_first(self):
        work = Path(tempfile.mkdtemp(prefix="tweaker-"))
        self.addCleanup(shutil.rmtree, work, True)
        doc = CT.CODDocument.load(make_copy(work))
        preset = CT.Preset({"Title": "Performance", "Values": [
            {"Name": "Resolution Scale", "Variant": "", "Value": "0.850",
             "Key": "Renderer.ResolutionScale"},
            {"Name": "V Sync", "Variant": "", "Value": "false", "Key": "Renderer.VSync"},
            {"Name": "Debug Overlay", "Variant": "", "Value": "1",
             "Key": "Hidden.DebugOverlay"},
            {"Name": "Absent Setting", "Variant": "", "Value": "x", "Key": "Missing.Key"},
        ]})
        pairs = preset.match_against(doc)
        # Protected entries are skipped, missing entries reported.
        protected = [pair for pair in pairs if pair["setting"] is not None and pair["setting"].protected]
        self.assertTrue(protected)
        self.assertTrue(all(pair["skipped_protected"] for pair in protected))
        missing = [pair for pair in pairs if pair["setting"] is None]
        self.assertEqual(len(missing), 1)
        # Loading/matching stages nothing until confirm.
        self.assertEqual(doc.staged_changes(), [])
        plan = preset.dry_run(doc)
        self.assertEqual([item["action"] for item in plan], ["stage", "stage", "skip", "skip"])
        staged, skipped = preset.confirm(doc, pairs)
        self.assertEqual(len(staged), 2)
        self.assertEqual(len(skipped), 2)
        self.assertIsNone(doc.match(key="Hidden.DebugOverlay").pending)
        self.assertEqual(doc.match(key="Renderer.ResolutionScale").pending, "0.850")


class NavRegistrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        cls.qt = QApplication.instance() or QApplication([])

    def setUp(self):
        import app
        self.window = app.Window()
        self.addCleanup(self.window.shutdown)
        self.addCleanup(self.window.deleteLater)
        self.addCleanup(self.qt.processEvents)

    def test_nav_registration(self):
        import app
        from PySide6.QtCore import Qt
        names = [self.window.nav.item(i).data(Qt.UserRole) for i in range(self.window.nav.count())]
        # True count is 11 existing pages + the new COD Tweaker section.
        self.assertEqual(self.window.nav.count(), 12)
        self.assertIn("COD Tweaker", names)
        self.assertIn("COD Tweaker", app.PAGE_BANNERS)
        self.assertEqual(app.PAGE_BANNERS["COD Tweaker"], "tweaker.png")
        _page, layout = self.window.pages["COD Tweaker"]
        title = layout.itemAt(0).widget()
        self.assertEqual(title.objectName(), "pageBanner")
        self.assertFalse(title.pixmap().isNull())
        tabs = [self.window.tw_tabs.tabText(i) for i in range(self.window.tw_tabs.count())]
        for expected in ("Graphics", "Performance", "Hidden Settings", "Presets", "Compare", "Backup & Restore", "About"):
            self.assertIn(expected, tabs)
        # The read-only default holds: editing starts locked until unlocked.
        self.assertNotIn(self.window.tw_current, self.window.tw_unlocked)


class RowWidgetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        cls.qt = QApplication.instance() or QApplication([])

    def setUp(self):
        import app
        self.window = app.Window()
        self.addCleanup(self.window.shutdown)
        self.addCleanup(self.window.deleteLater)
        self.addCleanup(self.qt.processEvents)

    def test_row_widgets_match_setting_kinds(self):
        import app
        from PySide6.QtWidgets import QComboBox, QLineEdit, QSlider, QWidget
        w = self.window
        bool_widget = w._tw_row_value_widget(CT.CODSetting(key="K", name="N", kind="bool", original="true"), True)
        self.assertIsInstance(bool_widget, QComboBox)
        self.assertEqual([bool_widget.itemText(i) for i in range(bool_widget.count())], ["true", "false"])
        choice_widget = w._tw_row_value_widget(CT.CODSetting(key="K", name="N", kind="choice", options=["Low", "High"], original="High"), True)
        self.assertIsInstance(choice_widget, QComboBox)
        slider_wrap = w._tw_row_value_widget(CT.CODSetting(key="K", name="N", kind="number", min=0, max=1, decimals=6, original="1.0"), True)
        self.assertIsInstance(slider_wrap, QWidget)
        self.assertTrue(slider_wrap.findChild(QSlider) is not None)
        self.assertTrue(slider_wrap.findChild(QLineEdit) is not None)
        plain = w._tw_row_value_widget(CT.CODSetting(key="K", name="N", kind="number", original="5"), True)
        self.assertIsInstance(plain, QLineEdit)
        text_widget = w._tw_row_value_widget(CT.CODSetting(key="K", name="N", kind="text", original="hi"), True)
        self.assertIsInstance(text_widget, QLineEdit)
        locked = w._tw_row_value_widget(CT.CODSetting(key="K", name="N", kind="bool", original="true"), False)
        self.assertFalse(locked.isEnabled())

    def test_settings_table_columns_readable(self):
        from PySide6.QtWidgets import QHeaderView
        w = self.window
        table = w.tw_tabs_state["Graphics"]["table"]
        self.assertEqual(table.columnCount(), 6)
        self.assertEqual(table.horizontalHeader().sectionResizeMode(1), QHeaderView.Fixed)
        self.assertGreaterEqual(table.columnWidth(1), 260)
        self.assertGreaterEqual(table.verticalHeader().defaultSectionSize(), 30)

    def test_pin_toggle(self):
        w = self.window
        self.assertFalse(w.tw_pins.is_pinned("Some.Key"))
        w.toggle_tweaker_pin("Some.Key")
        self.assertTrue(w.tw_pins.is_pinned("Some.Key"))
        w.toggle_tweaker_pin("Some.Key")
        self.assertFalse(w.tw_pins.is_pinned("Some.Key"))


class DescriptionFallbackTests(unittest.TestCase):
    def test_every_setting_gets_a_description(self):
        registry = CT.NotesRegistry()
        bare = CT.CODSetting(key="RendererWorkerCount", name="RendererWorkerCount",
                             kind="number", min=1, max=16)
        note = registry.note_for(bare)
        self.assertTrue(note["note"])
        self.assertIn("Renderer Worker Count", note["note"])
        self.assertIn("1 to 16", note["note"])
        # Real descriptions and shipped notes still win over the fallback.
        described = CT.CODSetting(key="K", name="N", kind="bool", description="Shipped words.")
        self.assertEqual(registry.note_for(described)["note"], "Shipped words.")


class GameGuardTests(unittest.TestCase):
    def _names(self, *names):
        import sys
        from unittest import mock

        class Proc:
            def __init__(self, name):
                self.info = {"name": name}

        fake = mock.Mock()
        fake.process_iter.return_value = [Proc(n) for n in names]
        return mock.patch.dict(sys.modules, {"psutil": fake})

    def test_editors_do_not_trigger(self):
        with self._names("Code.exe", "explorer.exe", "codium.exe"):
            self.assertFalse(CT.is_game_running())
            self.assertEqual(CT.matching_game_process(), "")

    def test_lookalike_tools_do_not_trigger(self):
        with self._names("Code - Insiders.exe", "codex-windows-sandbox-service.exe", "CoDTweaker.exe"):
            self.assertFalse(CT.is_game_running())
            self.assertEqual(CT.matching_game_process(), "")

    def test_cod_and_titles_trigger(self):
        with self._names("cod.exe"):
            self.assertTrue(CT.is_game_running())
            self.assertEqual(CT.matching_game_process(), "cod.exe")
        with self._names("BlackOpsColdWar.exe"):
            self.assertTrue(CT.is_game_running())

    def test_tracker_helpers_excluded(self):
        with self._names("CODMunity Desktop.exe"):
            self.assertFalse(CT.is_game_running())


class GameLabelTests(unittest.TestCase):
    def test_game_title_for(self):
        self.assertEqual(CT.game_title_for("cod26"), "MW4")
        self.assertEqual(CT.game_title_for("COD25"), "COD 25")
        self.assertEqual(CT.game_title_for(""), "")
        self.assertEqual(CT.game_token_for("s.1.0.cod26.txt"), "cod26")
        self.assertEqual(CT.game_token_for("g.bt.cod25.pm.1.0.l.txt0"), "cod25")
        self.assertEqual(CT.game_token_for("settings.cfg"), "")

    def test_find_labels_game_and_group(self):
        work = Path(tempfile.mkdtemp(prefix="tweaker-roots-"))
        self.addCleanup(shutil.rmtree, work, True)
        players = work / "players"
        players.mkdir()
        (players / "s.1.0.cod26.txt").write_text("// Fov test.\nFov = 110 // 60 to 120\n", encoding="utf-8")
        sub = players / "818187987"
        sub.mkdir()
        (sub / "g.bt.cod25.pm.1.0.l.txt0").write_text("// Fov test.\nFov = 110 // 60 to 120\n", encoding="utf-8")
        found = CT.CODTweakerLocator(roots=[work]).find()
        by_name = {item["path"].name: item for item in found}
        self.assertEqual(by_name["s.1.0.cod26.txt"]["game"], "MW4")
        self.assertEqual(by_name["s.1.0.cod26.txt"]["group"], "Game settings")
        self.assertEqual(by_name["g.bt.cod25.pm.1.0.l.txt0"]["game"], "COD 25")
        self.assertEqual(by_name["g.bt.cod25.pm.1.0.l.txt0"]["group"], "Player 818187987")


if __name__ == "__main__":
    unittest.main()

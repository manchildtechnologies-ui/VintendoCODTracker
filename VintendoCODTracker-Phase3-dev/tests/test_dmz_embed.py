"""DMZ Command Center as embedded live site plus DMZ-mode sidebar.

Headless-safe: QWebEngineView is created but never asked to load in tests
(the load call is stubbed). No network, no auth, no threads.
"""
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import app


class DmzWebMapTests(unittest.TestCase):
    def test_all_entries_map_to_codmunity_urls(self):
        self.assertEqual(len(app.DMZ_WEB_PAGES), 16)
        labels = [label for label, _ in app.DMZ_WEB_PAGES]
        self.assertEqual(len(set(labels)), 16)
        for label, url in app.DMZ_WEB_PAGES:
            self.assertTrue(url.startswith("https://codmunity.gg/"), label)

    def test_key_pages_map_to_verified_urls(self):
        by_url = dict(app.DMZ_WEB_PAGES)
        self.assertEqual(by_url["Overview"], "https://codmunity.gg/dmz")
        self.assertEqual(by_url["Map"], "https://codmunity.gg/dmz/map")
        self.assertEqual(by_url["Review pins"], "https://codmunity.gg/dmz/map/review")
        self.assertEqual(by_url["Missions"], "https://codmunity.gg/dmz/missions")
        self.assertEqual(by_url["Trait Planner"], "https://codmunity.gg/dmz/traits/planner")
        self.assertEqual(by_url["Meta"], "https://codmunity.gg/mw4-dmz")
        self.assertEqual(by_url["Beginner guide"], "https://codmunity.gg/dmz/guide")

    def test_unknown_label_falls_back_home(self):
        self.assertEqual(app.Window._dmz_web_url("Nope"), "https://codmunity.gg/dmz")
        self.assertEqual(app.Window._dmz_web_url(None), "https://codmunity.gg/dmz")


class DmzEmbedUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.qt = QApplication.instance() or QApplication([])

    def setUp(self):
        self.window = app.Window()
        self.loaded = []
        if getattr(self.window, "dmz_view", None) is not None:
            self.window.dmz_view.load = lambda url: self.loaded.append(str(url.toString()))

    def tearDown(self):
        self.window.shutdown()
        self.window.deleteLater()
        self.qt.processEvents()

    def test_dmz_mode_swaps_sidebar(self):
        w = self.window
        self.assertFalse(w.nav.isHidden())
        self.assertTrue(w.dmz_nav.isHidden())
        w._dmz_enter_mode()
        self.qt.processEvents()
        self.assertTrue(w.nav.isHidden())
        self.assertFalse(w.dmz_nav.isHidden())
        labels = [w.dmz_nav.item(i).text() for i in range(w.dmz_nav.count())]
        self.assertIn("Overview", labels)
        self.assertIn("Map", labels)
        for gone in ("Beginner guide", "What's New", "News", "Review pins",
                     "How the map works", "Missions", "Traits", "TOOLS",
                     "DATABASE", "GUIDES AND NEWS", "DMZ", "Meta"):
            self.assertNotIn(gone, labels)
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QPushButton
        for i in range(w.nav.count()):
            self.assertEqual(w.nav.item(i).textAlignment(), Qt.AlignCenter)
        for i in range(w.dmz_nav.count()):
            self.assertEqual(w.dmz_nav.item(i).textAlignment(), Qt.AlignCenter)
        rows = [w.dmz_nav.item(i) for i in range(w.dmz_nav.count())]
        self.assertFalse(any(r.data(256) == "__dmz_back__" for r in rows))
        back_btn = w.findChild(QPushButton, "dmzBrowserBack")
        self.assertIsNotNone(back_btn)
        self.assertFalse(back_btn.isHidden())
        self.assertEqual(back_btn.text(), "Back")
        w._dmz_exit_mode()
        self.qt.processEvents()
        self.assertFalse(w.nav.isHidden())
        self.assertTrue(w.dmz_nav.isHidden())

    def test_open_page_loads_live_url_and_shows_browser(self):
        w = self.window
        w._dmz_enter_mode()
        self.qt.processEvents()
        self.assertEqual(self.loaded, ["https://codmunity.gg/dmz"])
        del self.loaded[:]
        w._dmz_open_page("Map")
        self.qt.processEvents()
        self.assertEqual(self.loaded, ["https://codmunity.gg/dmz/map"])
        self.assertEqual(w.stack.currentWidget(), w.pages["DMZ Browser"][0])
        self.assertIn("Map", w.dmz_status.text())

    def test_hide_rail_targets_site_sidebar_only(self):
        scripts = []
        class FakePage:
            def runJavaScript(self, js):
                scripts.append(js)
        class FakeView:
            def page(self):
                return FakePage()
            def load(self, url):
                pass
        w = self.window
        real = w.dmz_view
        w.dmz_view = FakeView()
        try:
            w._dmz_hide_site_rail()
        finally:
            w.dmz_view = real
        self.assertEqual(len(scripts), 1)
        self.assertIn(".cm-side-menu", scripts[0])
        self.assertIn("display", scripts[0])
        self.assertIn("none", scripts[0])
        self.assertIn("hidden", scripts[0])
        self.assertIn("MutationObserver", scripts[0])

    def test_open_page_hides_rail_and_loads_url(self):
        scripts = []
        loaded = []
        class FakePage:
            def runJavaScript(self, js):
                scripts.append(js)
        class FakeView:
            def page(self):
                return FakePage()
            def load(self, url):
                loaded.append(str(url.toString()))
        w = self.window
        real = w.dmz_view
        w.dmz_view = FakeView()
        try:
            w._dmz_open_page("Map")
            self.qt.processEvents()
        finally:
            w.dmz_view = real
        self.assertEqual(loaded, ["https://codmunity.gg/dmz/map"])
        self.assertTrue(any(".cm-side-menu" in js for js in scripts))

    def test_dmz_nav_scrollbar_is_visible_and_offset(self):
        w = self.window
        w._dmz_enter_mode()
        self.qt.processEvents()
        margins = w.dmz_nav.viewportMargins()
        self.assertGreaterEqual(margins.right(), 8)
        sheet = w.dmz_nav.styleSheet()
        self.assertIn("QScrollBar:vertical", sheet)
        self.assertIn("width:12px", sheet)
        self.assertIn(":hover", sheet)

    def test_overlay_hider_blocks_app_promo(self):
        scripts = []
        class FakePage:
            def runJavaScript(self, js):
                scripts.append(js)
        class FakeView:
            def page(self):
                return FakePage()
        w = self.window
        w._dmz_hide_site_rail(FakeView())
        self.assertEqual(len(scripts), 1)
        self.assertIn("much better on the app", scripts[0])
        self.assertIn(".cm-side-menu", scripts[0])
        self.assertIn("MutationObserver", scripts[0])

    def test_site_chrome_hider_covers_rail_and_header(self):
        scripts = []
        class FakePage:
            def runJavaScript(self, js):
                scripts.append(js)
        class FakeView:
            def page(self):
                return FakePage()
        w = self.window
        w._dmz_hide_site_rail(FakeView())
        self.assertEqual(len(scripts), 1)
        for selector in (".cm-side-menu", ".cm-rail", ".cm-rail-space",
                         ".cm-header", ".cm-gt--strip", ".cm-ad-slot",
                         ".cm-publift-slot", ".cm-download-banner",
                         ".cm-toggle-menu-container"):
            self.assertIn(selector, scripts[0])
        self.assertIn("innerHeight", scripts[0])

    def test_failed_page_load_shows_error_status(self):
        w = self.window
        w._dmz_page_loaded(False)
        self.qt.processEvents()
        self.assertIn("failed to load", w.dmz_status.text())

    def test_successful_page_load_rehides_chrome(self):
        scripts = []
        class FakePage:
            def runJavaScript(self, js):
                scripts.append(js)
        class FakeView:
            def page(self):
                return FakePage()
        w = self.window
        w._dmz_page_loaded(True, FakeView())
        self.qt.processEvents()
        self.assertEqual(len(scripts), 1)
        self.assertIn(".cm-rail", scripts[0])

    def test_dmz_row_above_settings_and_selection_matches_page(self):
        from PySide6.QtCore import Qt
        w = self.window
        rows = [(w.nav.item(i).data(Qt.UserRole), w.nav.item(i).isHidden())
                for i in range(w.nav.count())]
        visible = [name for name, hidden in rows if not hidden]
        self.assertLess(visible.index("DMZ Command Center"), visible.index("Settings"))
        for i in range(w.nav.count()):
            name = w.nav.item(i).data(Qt.UserRole)
            # Only visit side-effect-free rows: Scoreboard Capture can open
            # a modal display picker and Camo Tracker kicks off downloads.
            if name not in ("Dashboard", "Settings", "Player Roster"):
                continue
            w.nav.setCurrentRow(i)
            self.qt.processEvents()
            self.assertEqual(w.stack.currentWidget(), w.pages[name][0])

    def test_back_returns_to_main_sidebar(self):
        from PySide6.QtWidgets import QPushButton
        w = self.window
        w._dmz_enter_mode()
        self.qt.processEvents()
        back_btn = w.findChild(QPushButton, "dmzBrowserBack")
        back_btn.click()
        self.qt.processEvents()
        self.assertFalse(w.nav.isHidden())
        self.assertTrue(w.dmz_nav.isHidden())
        self.assertEqual(w.stack.currentWidget(), w.pages["Dashboard"][0])


class DmzAdblockTests(unittest.TestCase):
    def test_known_ad_domains_blocked(self):
        for host in ("doubleclick.net",
                     "pagead2.googlesyndication.com",
                     "tpc.googlesyndication.com",
                     "googleadservices.com",
                     "ads.yahoo.com",
                     "aax.amazon-adsystem.com",
                     "sub.adsrvr.org",
                     "outbrain.com",
                     "taboola.com"):
            self.assertTrue(app._dmz_host_blocked(host), host)

    def test_first_party_and_lookalikes_allowed(self):
        for host in ("codmunity.gg", "assets.codmunity.gg",
                     "CODEMUNITY.GG", "fonts.googleapis.com",
                     "notdoubleclick.net", "", None, "localhost", "127.0.0.1"):
            self.assertFalse(app._dmz_host_blocked(host), repr(host))

    def test_blocklist_entries_are_bare_domains(self):
        for entry in app.DMZ_ADBLOCK_SUFFIXES:
            self.assertTrue(entry and "." in entry and " " not in entry)
            self.assertEqual(entry, entry.lower())
            self.assertFalse(entry.startswith("."))


if __name__ == "__main__":
    unittest.main()

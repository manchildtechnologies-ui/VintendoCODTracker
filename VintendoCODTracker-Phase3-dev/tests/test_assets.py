import unittest
from pathlib import Path
from PIL import Image
import engine as E


class AssetTests(unittest.TestCase):
    def test_tracker_icon_is_valid_multisize_ico(self):
        ico = Path(E.ROOT) / 'tracker.ico'
        self.assertTrue(ico.is_file(), 'tracker.ico must exist for build.py --icon')
        with Image.open(ico) as img:
            sizes = set(img.info.get('sizes') or [img.size])
        self.assertIn((16, 16), sizes)
        self.assertIn((256, 256), sizes)
        self.assertGreaterEqual(len(sizes), 4)

    def test_section_banners_exist_and_load(self):
        import app as app_module

        banners = Path(E.ROOT) / 'assets' / 'banners'
        self.assertEqual(set(app_module.PAGE_BANNERS), {
            'Dashboard', 'Scoreboard Capture', 'Match History', 'Player Roster',
            'AI Studio', 'ScreenShots and Videos', 'Tracker Browser',
            'Settings',
        })
        for name, filename in app_module.PAGE_BANNERS.items():
            path = banners / filename
            self.assertTrue(path.is_file(), f'{name} banner missing: {filename}')
            with Image.open(path) as img:
                self.assertGreater(img.width, img.height, f'{name} banner should be wide')
                self.assertGreaterEqual(img.height, 500)


if __name__ == '__main__':
    unittest.main()

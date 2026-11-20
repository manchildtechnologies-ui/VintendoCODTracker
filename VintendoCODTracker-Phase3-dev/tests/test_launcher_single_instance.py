"""Desktop/dev launchers must not start a second Tracker copy.

The desktop shortcut fires Launch-VintendoCODTracker.vbs once per click and
the exe cold-starts slowly with a hidden window, so extra clicks used to pile
up several VintendoCODTracker.exe processes before the in-app QLocalServer
guard was listening. Both tracker launchers must therefore check for an
already-running instance and quit (after focusing it) before sh.Run starts a
new one.
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DESKTOP_VBS = ROOT / "Launch-VintendoCODTracker.vbs"
DEV_VBS = ROOT / "Launch-Phase3-Tracker.vbs"


def read_vbs(path):
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None


def guard_before_launch(text, marker_pattern, launch_pattern):
    """The process check (and its quit) must sit before the sh.Run launch."""
    marker = re.search(marker_pattern, text, re.IGNORECASE | re.DOTALL)
    launch = re.search(launch_pattern, text, re.IGNORECASE | re.DOTALL)
    if marker is None or launch is None:
        return False
    quit_before_launch = re.search(r"WScript\.Quit", text[:launch.start()],
                                   re.IGNORECASE) is not None
    return marker.start() < launch.start() and quit_before_launch


class LauncherSingleInstanceTests(unittest.TestCase):
    @unittest.skipIf(DESKTOP_VBS.is_file() is False,
                     "desktop launcher VBS is a local-only file")
    def test_desktop_launcher_quits_when_tracker_running(self):
        text = read_vbs(DESKTOP_VBS)
        self.assertIsNotNone(
            re.search(r'trackerExe\s*=\s*root\s*&\s*"\\VintendoCODTracker\.exe"',
                      text, re.IGNORECASE),
            "desktop VBS must default its launch target to VintendoCODTracker.exe")
        self.assertTrue(
            guard_before_launch(text, r"If hasTracker Then",
                                r"sh\.Run.*trackerExe"),
            "desktop VBS must check for VintendoCODTracker.exe and quit "
            "before launching the exe again")
        self.assertIsNotNone(
            re.search(r"AppActivate", text, re.IGNORECASE),
            "desktop VBS should focus the running Tracker window")

    def test_dev_launcher_quits_when_tracker_running(self):
        text = read_vbs(DEV_VBS)
        self.assertIsNotNone(text, "Launch-Phase3-Tracker.vbs must exist")
        self.assertTrue(
            guard_before_launch(text, r"launcher\.py",
                                r"sh\.Run.*pythonw\.exe.*launcher\.py"),
            "dev VBS must check for a running launcher.py and quit before "
            "starting pythonw again")
        self.assertIsNotNone(
            re.search(r"AppActivate", text, re.IGNORECASE),
            "dev VBS should focus the running Tracker window")

    @unittest.skipIf(DESKTOP_VBS.is_file() is False,
                     "desktop launcher VBS is a local-only file")
    def test_desktop_launcher_starts_visible(self):
        text = read_vbs(DESKTOP_VBS)
        self.assertIsNotNone(
            re.search(r"sh\.Run.*trackerExe.*,\s*1,\s*False",
                       text, re.IGNORECASE),
            "desktop VBS must launch the tracker with a normal window so "
            "it appears on screen")
        for line in text.splitlines():
            if re.search(r"sh\.Run", line, re.IGNORECASE):
                self.assertIsNone(
                    re.search(r",\s*0\s*,", line),
                    "desktop VBS must not launch the tracker hidden: hidden starts "
                    "run with no window while tracking")

    def test_dev_launcher_starts_visible(self):
        text = read_vbs(DEV_VBS)
        self.assertIsNotNone(text, "Launch-Phase3-Tracker.vbs must exist")
        self.assertIsNotNone(
            re.search(r"sh\.Run.*pythonw\.exe.*launcher\.py.*,\s*1,\s*False",
                       text, re.IGNORECASE),
            "dev VBS must launch the tracker with a normal window so it "
            "appears on screen")
        self.assertIsNone(
            re.search(r"sh\.Run.*pythonw\.exe.*launcher\.py.*,\s*0,\s*False",
                       text, re.IGNORECASE),
            "dev VBS must not launch the tracker hidden: hidden starts "
            "run with no window while tracking")


if __name__ == "__main__":
    unittest.main()

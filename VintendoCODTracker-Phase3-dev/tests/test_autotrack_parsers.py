"""Parser unit tests (SINGLE_EXE_DESIGN section 8.1): synthetic OCR for P1-P5."""
import unittest

import capture_utils
import engine as E


AFTER_ACTION_WIN = (
    "AFTER ACTION REPORT\nTEAM DEATHMATCH\nVICTORY\n"
    "YOUR SCORE  100\nENEMY SCORE  88\n"
    "PLAYER  KILLS  DAMAGE\nVintendo  12  3400\nConcha  8  2000"
)
AFTER_ACTION_LOSS = (
    "AFTER ACTION REPORT\nKILL CONFIRMED\nDEFEAT\n"
    "YOUR SCORE  45\nENEMY SCORE  75\nPLAYER  KILLS  DEATHS\nVintendo  9  14"
)
COMBAT_LIFETIME = (
    "COMBAT RECORD\nMULTIPLAYER LIFETIME\nTOP STATS\n"
    "K/D  10051 / 9083\nWINS  187 / LOSSES 278\nGAMES  478"
)
COMBAT_WARZONE_ONLY = "COMBAT RECORD\nWARZONE\nRESURGENCE\nK/D  100 / 200\nGAMES  10"
COMBAT_NO_RESULT = "COMBAT RECORD\nMULTIPLAYER LIFETIME\nTOP STATS\n"
WARZONE_SUMMARY_3RD = "WARZONE SUMMARY\nRESURGENCE\n3RD PLACE\nSQUAD TOTAL  12"
WARZONE_SUMMARY_1ST = "WARZONE SUMMARY\nBATTLE ROYALE\n1ST PLACE"
CAMO_POPUP = "CHALLENGE\nASSAULT RIFLE\nCAMO MIDNIGHT\nMCX - MIDNIGHT"
BANNER_HUD = "42 SQUADS REMAINING\nWARZONE RESURGENCE BANNER"
MENU_TEXT = "MAIN MENU\nSETTINGS\nAUDIO\nVIDEO\nCREDITS"


class ParserTests(unittest.TestCase):
    def test_after_action_parses_win(self):
        parsed = E.parse_after_action(AFTER_ACTION_WIN)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["result"], "Victory")
        self.assertEqual(parsed["modeKey"], "team_deathmatch")
        self.assertEqual(parsed["scoreOwn"], 100)
        self.assertEqual(parsed["scoreOpp"], 88)
        self.assertTrue(parsed["players"])

    def test_after_action_parses_loss(self):
        parsed = E.parse_after_action(AFTER_ACTION_LOSS)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["result"], "Defeat")
        self.assertEqual(parsed["modeKey"], "kill_confirmed")

    def test_after_action_rejects_menu(self):
        self.assertIsNone(E.parse_after_action(MENU_TEXT))
        self.assertIsNone(E.parse_after_action(""))
        self.assertIsNone(E.parse_after_action("AFTER ACTION REPORT\nVICTORY"))  # no stat heading

    def test_combat_record_parses_lifetime(self):
        parsed = E.parse_combat_record(COMBAT_LIFETIME)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["scope"], "lifetime")
        self.assertEqual((parsed["kd_num"], parsed["kd_den"]), (10051, 9083))
        self.assertEqual((parsed["wins"], parsed["losses"]), (187, 278))
        self.assertEqual(parsed["games"], 478)
        self.assertIsNone(E.combat_record_reason(COMBAT_LIFETIME))

    def test_combat_record_rejects_non_lifetime(self):
        self.assertIsNone(E.parse_combat_record(COMBAT_WARZONE_ONLY))
        self.assertEqual(E.combat_record_reason(COMBAT_WARZONE_ONLY), "NotLifetimeMultiplayer")

    def test_combat_record_rejects_no_result(self):
        self.assertIsNone(E.parse_combat_record(COMBAT_NO_RESULT))
        self.assertEqual(E.combat_record_reason(COMBAT_NO_RESULT), "NoResult")
        self.assertIsNone(E.parse_combat_record(MENU_TEXT))

    def test_warzone_summary_parses_placement(self):
        third = E.parse_warzone_summary(WARZONE_SUMMARY_3RD)
        self.assertIsNotNone(third)
        self.assertEqual(third["placement"], 3)
        self.assertEqual(third["result"], "Defeat")
        first = E.parse_warzone_summary(WARZONE_SUMMARY_1ST)
        self.assertEqual((first["placement"], first["result"]), (1, "Victory"))

    def test_warzone_summary_rejects_without_placement(self):
        self.assertIsNone(E.parse_warzone_summary("WARZONE SUMMARY\nSQUAD OF 4"))
        self.assertIsNone(E.parse_warzone_summary(MENU_TEXT))

    def test_camo_challenge_parses_weapon(self):
        parsed = E.parse_camo_challenge(CAMO_POPUP)
        self.assertIsNotNone(parsed)
        self.assertIn("assault", parsed["weapon"])
        self.assertEqual(parsed["grind"], "mw4")
        self.assertTrue(parsed["read"])

    def test_camo_challenge_rejects_without_weapon_class(self):
        self.assertIsNone(E.parse_camo_challenge("CHALLENGE\nNO WEAPON HERE"))
        self.assertIsNone(E.parse_camo_challenge(MENU_TEXT))

    def test_banner_hud_parses_squads(self):
        parsed = E.parse_banner_hud(BANNER_HUD)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["squads"], 42)

    def test_banner_hud_rejects_menu(self):
        self.assertIsNone(E.parse_banner_hud(MENU_TEXT))

    def test_non_matching_menu_rejected_by_every_parser(self):
        self.assertIsNone(E.parse_after_action(MENU_TEXT))
        self.assertIsNone(E.parse_combat_record(MENU_TEXT))
        self.assertIsNone(E.parse_warzone_summary(MENU_TEXT))
        self.assertIsNone(E.parse_camo_challenge(MENU_TEXT))
        self.assertIsNone(E.parse_banner_hud(MENU_TEXT))
        self.assertEqual(E.parse_scoreboard(MENU_TEXT), [])

    def test_gates_mirror_parsers(self):
        self.assertTrue(capture_utils.looks_like_after_action(AFTER_ACTION_WIN.splitlines()))
        self.assertTrue(capture_utils.looks_like_combat_record(COMBAT_LIFETIME.splitlines()))
        self.assertTrue(capture_utils.looks_like_warzone_summary(WARZONE_SUMMARY_3RD.splitlines()))
        self.assertTrue(capture_utils.looks_like_camo_challenge(CAMO_POPUP.splitlines()))
        self.assertTrue(capture_utils.looks_like_banner_hud(BANNER_HUD.splitlines()))
        for gate in (capture_utils.looks_like_after_action, capture_utils.looks_like_combat_record,
                     capture_utils.looks_like_warzone_summary, capture_utils.looks_like_camo_challenge,
                     capture_utils.looks_like_banner_hud):
            self.assertFalse(gate(MENU_TEXT.splitlines()), gate.__name__)


if __name__ == "__main__":
    unittest.main()

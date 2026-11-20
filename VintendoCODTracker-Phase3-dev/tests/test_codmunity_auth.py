"""Approved-endpoint auth: sign-in, refresh, authed fetcher (fake opener only)."""
import io
import json
import unittest
from urllib.error import HTTPError

import codmunity_player_stats as C


class _FakeResponse:
    def __init__(self, payload):
        self._body = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _http_error(url, code, message):
    body = json.dumps({"error": {"message": message}}).encode("utf-8")
    return HTTPError(url, code, "error", {}, io.BytesIO(body))


class FakeOpener:
    """Scripted opener: list of payloads or HTTPError per request."""

    def __init__(self, script):
        self.script = list(script)
        self.seen = []

    def __call__(self, request, timeout=None):
        self.seen.append(request)
        action = self.script.pop(0)
        if isinstance(action, Exception):
            raise action
        return _FakeResponse(action)


def _auth_client(script):
    opener = FakeOpener(script)
    return C.CodmunityAuthClient(opener=opener), opener


class AuthClientTests(unittest.TestCase):
    def test_sign_in_posts_credentials_and_stores_tokens(self):
        auth, opener = _auth_client([{"idToken": "id-1", "refreshToken": "ref-1",
                                      "email": "me@example.com", "localId": "uid-9"}])
        info = auth.sign_in("me@example.com", "s3cret")
        self.assertEqual(info, {"email": "me@example.com", "localId": "uid-9"})
        self.assertTrue(auth.signed_in)
        self.assertEqual(auth.refresh_token, "ref-1")
        request = opener.seen[0]
        self.assertIn("signInWithPassword", request.full_url)
        body = json.loads(request.data.decode("utf-8"))
        self.assertEqual(body, {"email": "me@example.com", "password": "s3cret",
                               "returnSecureToken": True})

    def test_sign_in_rejects_blank_inputs(self):
        auth, _ = _auth_client([])
        with self.assertRaises(ValueError):
            auth.sign_in("", "pw")
        with self.assertRaises(ValueError):
            auth.sign_in("me@example.com", "")

    def test_sign_in_surfaces_firebase_message(self):
        url = C.SIGN_IN_API
        auth, _ = _auth_client([_http_error(url, 400, "INVALID_PASSWORD")])
        with self.assertRaises(C.AuthError) as ctx:
            auth.sign_in("me@example.com", "wrong")
        self.assertIn("INVALID_PASSWORD", str(ctx.exception))
        self.assertFalse(auth.signed_in)

    def test_id_token_refreshes_from_stored_token(self):
        auth, opener = _auth_client([{"id_token": "id-2", "refresh_token": "ref-2",
                                      "expires_in": "3600"}])
        auth._refresh_token = "ref-1"
        self.assertEqual(auth.id_token(), "id-2")
        request = opener.seen[0]
        self.assertIn("securetoken", request.full_url)
        self.assertIn("grant_type=refresh_token", request.data.decode("utf-8"))

    def test_id_token_without_session_raises(self):
        auth, _ = _auth_client([])
        with self.assertRaises(C.AuthError):
            auth.id_token()

    def test_sign_out_clears_tokens(self):
        auth, _ = _auth_client([])
        auth._refresh_token = "ref-1"
        auth._id_token = "id-1"
        auth.sign_out()
        self.assertFalse(auth.signed_in)

    def test_authed_get_sends_bearer_and_retries_once_after_401(self):
        url = C.APP_API + "/api/app/stats/me"
        stats = {"stats": {"kills": 10, "deaths": 5, "games_played": 3}}
        auth, opener = _auth_client([_http_error(url, 401, "UNAUTH"),
                                     {"id_token": "id-2", "refresh_token": "ref-2"},
                                     stats])
        auth._refresh_token = "ref-1"
        auth._id_token = "expired"
        result = auth.get_json("/api/app/stats/me")
        self.assertEqual(result, stats)
        first, _, third = opener.seen
        self.assertEqual(first.headers.get("Authorization"), "Bearer expired")
        self.assertEqual(third.headers.get("Authorization"), "Bearer id-2")

    def test_authed_get_surfaces_http_status(self):
        url = C.APP_API + "/api/app/stats/me"
        auth, _ = _auth_client([_http_error(url, 400, "BAD")])
        auth._refresh_token = ""
        auth._id_token = "id-1"
        with self.assertRaises(C.AuthError) as ctx:
            auth.get_json("/api/app/stats/me")
        self.assertIn("400", str(ctx.exception))

    def test_tokens_never_appear_in_errors(self):
        url = C.APP_API + "/api/app/stats/me"
        auth, _ = _auth_client([_http_error(url, 500, "boom")])
        auth._refresh_token = "ref-secret"
        auth._id_token = "id-secret"
        try:
            auth.get_json("/api/app/stats/me")
        except C.AuthError as exc:
            self.assertNotIn("ref-secret", str(exc))
            self.assertNotIn("id-secret", str(exc))
        else:
            self.fail("expected AuthError")


class AuthorizedFetcherTests(unittest.TestCase):
    def _fetcher(self, script):
        auth, opener = _auth_client(script)
        auth._id_token = "id-live"
        return C.build_authorized_fetcher(auth), opener

    def test_maps_stats_and_matches_envelopes(self):
        stats = {"stats": {"kills": 100, "deaths": 50, "games_played": 20},
                 "warzoneLifetime": {"resurgence": {"kd": 2.0}}}
        matches = {"matches": [{"modeKey": "resurgence", "result": "win"}]}
        fetch, opener = self._fetcher([stats, matches])
        result = fetch("uid-9")
        self.assertEqual(result["state"], "live")
        self.assertFalse(result["public"])
        self.assertEqual(result["lifetime"]["kills"], 100)
        self.assertEqual(result["warzoneLifetime"]["resurgence"]["kd"], 2.0)
        self.assertEqual(len(result["matches"]), 1)
        self.assertIn("/api/app/stats/me", opener.seen[0].full_url)
        self.assertIn("/api/app/matches/me", opener.seen[1].full_url)
        self.assertEqual(opener.seen[0].headers.get("Authorization"), "Bearer id-live")

    def test_matches_failure_still_returns_lifetime(self):
        stats = {"stats": {"kills": 5}}
        fetch, _ = self._fetcher([stats, _http_error(C.APP_API + "/m", 500, "x")])
        result = fetch("uid-9")
        self.assertEqual(result["lifetime"]["kills"], 5)
        self.assertNotIn("matches", result)

    def test_fetcher_rejects_blank_uid(self):
        fetch, _ = self._fetcher([])
        with self.assertRaises(ValueError):
            fetch("  ")

    def test_service_retrieve_uses_injected_fetcher(self):
        service = C.ModularPlayerStatsService(fetcher=lambda uid: {"state": "live"})
        self.assertTrue(service.configured)
        self.assertEqual(service.retrieve("uid-9"), {"state": "live"})

    def test_request_password_reset_posts_oob(self):
        auth, opener = _auth_client([{"email": "me@example.com"}])
        C.request_password_reset("me@example.com", opener=opener)
        request = opener.seen[0]
        self.assertIn("sendOobCode", request.full_url)
        body = json.loads(request.data.decode("utf-8"))
        self.assertEqual(body, {"requestType": "PASSWORD_RESET", "email": "me@example.com"})
        with self.assertRaises(ValueError):
            C.request_password_reset("  ")

    def test_send_email_verification_uses_id_token(self):
        auth, opener = _auth_client([{"email": "me@example.com"}])
        auth._id_token = "id-live"
        auth.send_email_verification()
        request = opener.seen[0]
        self.assertIn("sendOobCode", request.full_url)
        body = json.loads(request.data.decode("utf-8"))
        self.assertEqual(body, {"requestType": "VERIFY_EMAIL", "idToken": "id-live"})

    def test_request_password_reset_posts_oob(self):
        auth, opener = _auth_client([{"email": "me@example.com"}])
        C.request_password_reset("me@example.com", opener=opener)
        request = opener.seen[0]
        self.assertIn("sendOobCode", request.full_url)
        body = json.loads(request.data.decode("utf-8"))
        self.assertEqual(body, {"requestType": "PASSWORD_RESET", "email": "me@example.com"})
        with self.assertRaises(ValueError):
            C.request_password_reset("  ")

    def test_send_email_verification_uses_id_token(self):
        auth, opener = _auth_client([{"email": "me@example.com"}])
        auth._id_token = "id-live"
        auth.send_email_verification()
        request = opener.seen[0]
        self.assertIn("sendOobCode", request.full_url)
        body = json.loads(request.data.decode("utf-8"))
        self.assertEqual(body, {"requestType": "VERIFY_EMAIL", "idToken": "id-live"})

    def _stats_dir(self, matches):
        import tempfile
        from pathlib import Path
        root = Path(tempfile.mkdtemp(prefix="camo-upload-"))
        (root / "matches.json").write_text(json.dumps(matches), encoding="utf-8")
        return root

    def test_pending_matches_lists_unsent_only(self):
        root = self._stats_dir([
            {"matchId": "a", "modeKey": "m", "result": "win", "stats": {}, "sent": True},
            {"matchId": "b", "modeKey": "m", "result": "loss", "stats": {}},
            {"modeKey": "m"},
            "junk"])
        pending = C.pending_matches(root)
        self.assertEqual([m["matchId"] for m in pending], ["b"])
        self.assertEqual(C.pending_matches(root / "nope"), [])

    def test_upload_posts_without_sent_and_marks_sent(self):
        root = self._stats_dir([
            {"matchId": "a", "modeKey": "m", "result": "win", "stats": {"kills": 1}, "sent": True},
            {"matchId": "b", "modeKey": "m", "result": "loss", "stats": {"kills": 2}}])
        auth, opener = _auth_client([{"ok": True}])
        auth._id_token = "id-live"
        summary = C.upload_pending_matches(auth, root)
        self.assertEqual(summary, {"uploaded": 1, "skipped": 1, "failed": []})
        request = opener.seen[0]
        self.assertIn("/api/app/matches", request.full_url)
        body = json.loads(request.data.decode("utf-8"))
        self.assertNotIn("sent", body)
        self.assertEqual(body["matchId"], "b")
        saved = json.loads((root / "matches.json").read_text(encoding="utf-8"))
        self.assertTrue(all(m.get("sent") for m in saved if isinstance(m, dict) and m.get("matchId")))

    def test_upload_records_refusal_and_continues(self):
        url = C.APP_API + "/api/app/matches"
        root = self._stats_dir([
            {"matchId": "a", "modeKey": "m", "result": "win", "stats": {}},
            {"matchId": "b", "modeKey": "m", "result": "win", "stats": {}}])
        auth, opener = _auth_client([_http_error(url, 400, "BAD_MATCH"), {"ok": True}])
        auth._id_token = "id-live"
        summary = C.upload_pending_matches(auth, root)
        self.assertEqual(summary["uploaded"], 1)
        self.assertEqual(summary["skipped"], 0)
        self.assertEqual(len(summary["failed"]), 1)
        self.assertEqual(summary["failed"][0]["matchId"], "a")
        saved = json.loads((root / "matches.json").read_text(encoding="utf-8"))
        by_id = {m["matchId"]: m for m in saved if isinstance(m, dict)}
        self.assertNotIn("sent", by_id["a"])
        self.assertTrue(by_id["b"]["sent"])

    def _lifetime_dir(self):
        import tempfile
        from pathlib import Path
        root = Path(tempfile.mkdtemp(prefix="camo-lifetime-"))
        (root / "local.json").write_text(json.dumps({
            "at": "2026-11-18T18:43:17Z", "game": "mw4", "mode": "multiplayer",
            "scope": "lifetime", "stats": {"kills": 10051, "deaths": 9083}}), encoding="utf-8")
        (root / "wz-modes.json").write_text(json.dumps({
            "resurgence": {"at": "2026-11-16", "stats": {"kd": 3.21}},
            "battle_royale": {"at": "2026-11-09", "sent": True, "stats": {"kd": 4.0}}}), encoding="utf-8")
        return root

    def test_lifetime_upload_posts_envelopes_and_marks_sent(self):
        root = self._lifetime_dir()
        auth, opener = _auth_client([{"ok": True}, {"ok": True}])
        auth._id_token = "id-live"
        summary = C.upload_lifetime_snapshot(auth, root)
        self.assertEqual(summary, {"uploaded": 2, "skipped": 1, "failed": []})
        multiplayer, warzone = opener.seen
        self.assertIn("/api/app/stats", multiplayer.full_url)
        body = json.loads(multiplayer.data.decode("utf-8"))
        self.assertEqual(body["game"], "mw4")
        self.assertEqual(body["mode"], "multiplayer")
        self.assertEqual(body["stats"]["kills"], 10051)
        wz_body = json.loads(warzone.data.decode("utf-8"))
        self.assertEqual(wz_body, {"game": "warzone", "mode": "resurgence",
                                  "scope": "lifetime", "at": "2026-11-16",
                                  "stats": {"kd": 3.21}})
        sent = json.loads((root / "sent.json").read_text(encoding="utf-8"))
        self.assertEqual(sent["stats"]["kills"], 10051)
        modes = json.loads((root / "wz-modes.json").read_text(encoding="utf-8"))
        self.assertTrue(modes["resurgence"]["sent"])

    def test_lifetime_refusal_is_recorded_not_sent(self):
        url = C.APP_API + "/api/app/stats"
        root = self._lifetime_dir()
        auth, _ = _auth_client([_http_error(url, 400, "BAD_STATS"), {"ok": True}])
        auth._id_token = "id-live"
        summary = C.upload_lifetime_snapshot(auth, root)
        self.assertEqual(summary["uploaded"], 1)
        self.assertEqual(len(summary["failed"]), 1)
        self.assertEqual(summary["failed"][0]["scope"], "multiplayer")
        self.assertFalse((root / "sent.json").exists())

    def test_lifetime_upload_missing_files_is_noop(self):
        import tempfile
        from pathlib import Path
        auth, opener = _auth_client([])
        summary = C.upload_lifetime_snapshot(auth, Path(tempfile.mkdtemp(prefix="camo-empty-")))
        self.assertEqual(summary, {"uploaded": 0, "skipped": 0, "failed": []})
        self.assertEqual(opener.seen, [])

    def test_upload_missing_or_corrupt_fails_fast(self):
        import tempfile
        from pathlib import Path
        auth, _ = _auth_client([])
        with self.assertRaises(ValueError):
            C.upload_pending_matches(auth, Path(tempfile.mkdtemp()))
        root = Path(tempfile.mkdtemp(prefix="camo-upload-"))
        (root / "matches.json").write_text("not json", encoding="utf-8")
        with self.assertRaises(ValueError):
            C.upload_pending_matches(auth, root)

    def test_set_visibility_posts_public_flag(self):
        auth, opener = _auth_client([{"ok": True}])
        auth._id_token = "id-live"
        result = C.set_visibility(auth, False)
        self.assertEqual(result, {"ok": True})
        request = opener.seen[0]
        self.assertIn("/api/app/stats/visibility", request.full_url)
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(json.loads(request.data.decode("utf-8")), {"public": False})
        self.assertEqual(request.headers.get("Authorization"), "Bearer id-live")


if __name__ == "__main__":
    unittest.main()

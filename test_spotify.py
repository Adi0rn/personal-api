"""Spotify checks use fake credentials, mocked Spotify responses, and temporary files."""

import base64
import hashlib
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import httpx

import spotify_connection as spotify
import spotify_store
from test_tasks import test_server

FAKE_CLIENT = "a" * 32


class SpotifyTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="personal-api-spotify-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        env = self.root / ".env"
        env.write_text("SPOTIFY_CLIENT_ID=" + FAKE_CLIENT, encoding="utf-8")
        for target, value in (("spotify_connection.ENV_FILE", env),
                              ("spotify_store.TOKEN_FILE", self.root / "tokens.bin"),
                              ("tasks.DB_PATH", self.root / "tasks.db"),
                              ("spotify_connection.PENDING", {})):
            patcher = patch(target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        environment = patch.dict("os.environ", {"SPOTIFY_CLIENT_ID": ""})
        environment.start()
        self.addCleanup(environment.stop)
        remote = patch("spotify_connection.spotify_request", side_effect=AssertionError("Unexpected Spotify request"))
        self.remote = remote.start()
        self.addCleanup(remote.stop)
        manager = test_server()
        self.base = manager.__enter__()
        self.addCleanup(manager.__exit__, None, None, None)
        self.client = httpx.Client(base_url=self.base, follow_redirects=False, trust_env=False)
        self.addCleanup(self.client.close)

    def sign_in(self):
        response = self.client.get("/connect/spotify")
        self.assertEqual(response.status_code, 303)
        url = urlsplit(response.headers["location"])
        self.assertEqual(url.netloc, "accounts.spotify.com")
        params = parse_qs(url.query)
        return params["state"][0], params

    def seed_tokens(self, expired=False):
        tokens = {"access_token": "fake-access", "refresh_token": "fake-refresh",
                  "expires_at": time.time() + (-1 if expired else 3600),
                  "scope": spotify.SCOPES, "client_id": FAKE_CLIENT}
        spotify_store.save_tokens(tokens)
        return tokens

    def test_setup_and_disconnected_status_without_credentials(self):
        spotify.ENV_FILE.write_text("SPOTIFY_CLIENT_ID=", encoding="utf-8")
        response = self.client.get("/connect/spotify")
        self.assertEqual(response.status_code, 200)
        self.assertIn(spotify.REDIRECT_URI, response.text)
        status = self.client.get("/spotify/status").json()
        self.assertFalse(status["configured"])
        self.assertFalse(status["connected"])
        self.assertEqual(self.client.get("/spotify/profile").status_code, 503)
        spotify.ENV_FILE.write_text("SPOTIFY_CLIENT_ID=" + FAKE_CLIENT, encoding="utf-8")
        self.assertEqual(self.client.get("/spotify/profile").status_code, 401)
        self.remote.assert_not_called()

    def test_pkce_cookie_state_exchange_encryption_and_replay(self):
        state, params = self.sign_in()
        self.assertEqual(params["redirect_uri"], [spotify.REDIRECT_URI])
        self.assertEqual(params["code_challenge_method"], ["S256"])
        verifier = spotify.PENDING[state]["verifier"]
        expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        self.assertEqual(params["code_challenge"], [expected])
        self.assertEqual(self.client.get("/spotify/callback", params={"state": "wrong", "code": "fake-code"}).status_code, 400)
        with httpx.Client(base_url=self.base, trust_env=False) as other_browser:
            self.assertEqual(other_browser.get("/spotify/callback", params={"state": state, "code": "fake-code"}).status_code, 400)
        self.remote.assert_not_called()
        def token_response(method, url, **kwargs):
            self.assertEqual(method, "POST")
            self.assertEqual(kwargs["data"]["code_verifier"], verifier)
            self.assertNotIn("client_secret", kwargs["data"])
            return httpx.Response(200, json={"access_token": "fake-secret-access", "refresh_token": "fake-secret-refresh",
                                             "expires_in": 3600, "scope": spotify.SCOPES})
        self.remote.side_effect = token_response
        response = self.client.get("/spotify/callback", params={"state": state, "code": "fake-code"})
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/spotify/status")
        self.assertNotIn(b"fake-secret-access", spotify_store.TOKEN_FILE.read_bytes())
        self.assertEqual(spotify_store.load_tokens()["refresh_token"], "fake-secret-refresh")
        status = self.client.get("/spotify/status")
        self.assertTrue(status.json()["connected"])
        self.assertNotIn("fake-secret", status.text)
        self.assertEqual(self.client.get("/spotify/callback", params={"state": state, "code": "fake-code"}).status_code, 400)
        self.assertEqual(self.client.delete("/spotify/connection").status_code, 200)
        self.assertFalse(spotify_store.TOKEN_FILE.exists())

    def test_expired_and_denied_sign_ins(self):
        state, _ = self.sign_in()
        spotify.PENDING[state]["expires_at"] = 0
        self.assertEqual(self.client.get("/spotify/callback", params={"state": state, "code": "fake-code"}).status_code, 400)
        state, _ = self.sign_in()
        self.assertEqual(self.client.get("/spotify/callback", params={"state": state, "error": "access_denied"}).status_code, 400)
        self.assertFalse(spotify_store.TOKEN_FILE.exists())
        self.remote.assert_not_called()

    def test_refresh_preserves_refresh_token_and_reads_playlists(self):
        self.seed_tokens(expired=True)
        def response(method, url, **kwargs):
            if method == "POST":
                self.assertEqual(kwargs["data"]["grant_type"], "refresh_token")
                return httpx.Response(200, json={"access_token": "new-fake-access", "expires_in": 3600})
            self.assertEqual(kwargs["headers"]["Authorization"], "Bearer new-fake-access")
            return httpx.Response(200, json={"items": [{"id": "p1", "name": "Test playlist", "external_urls": {"spotify": "https://open.spotify.com/playlist/p1"}}], "total": 25, "next": "next page"})
        self.remote.side_effect = response
        response = self.client.get("/spotify/playlists?limit=20&offset=0")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["items"][0]["name"], "Test playlist")
        self.assertEqual(response.json()["next_offset"], 20)
        self.assertEqual(spotify_store.load_tokens()["refresh_token"], "fake-refresh")
        self.assertEqual(self.client.get("/spotify/playlists?limit=51").status_code, 422)
        self.assertEqual(self.client.get("/spotify/playlists?offset=-1").status_code, 422)

    def test_401_retry_rate_limit_and_revoked_grant(self):
        self.seed_tokens()
        self.remote.side_effect = [httpx.Response(401), httpx.Response(200, json={"access_token": "new-fake", "expires_in": 3600}),
                                   httpx.Response(200, json={"account_id": "stable-id", "display_name": "Test user"})]
        response = self.client.get("/spotify/profile")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["account_id"], "stable-id")
        self.remote.side_effect = None
        self.remote.return_value = httpx.Response(429, headers={"Retry-After": "12"})
        response = self.client.get("/spotify/profile")
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.headers["Retry-After"], "12")
        self.remote.return_value = httpx.Response(403)
        self.assertEqual(self.client.get("/spotify/profile").status_code, 403)
        self.seed_tokens(expired=True)
        self.remote.return_value = httpx.Response(400, json={"error": "invalid_grant"})
        self.assertEqual(self.client.get("/spotify/profile").status_code, 401)
        self.assertFalse(spotify_store.TOKEN_FILE.exists())

    def test_foreign_origin_and_host_are_rejected(self):
        self.assertEqual(self.client.get("/spotify/status", headers={"Host": "foreign.example"}).status_code, 403)
        self.assertEqual(self.client.delete("/spotify/connection", headers={"Origin": "https://foreign.example"}).status_code, 403)
        self.remote.assert_not_called()


if __name__ == "__main__":
    unittest.main()

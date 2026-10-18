"""Playback commands are simulated; these checks never control real devices."""

import unittest
import httpx

import spotify_store
import test_spotify as fixtures


class PlaybackTests(unittest.TestCase):
    setUp = fixtures.SpotifyTests.setUp
    seed_tokens = fixtures.SpotifyTests.seed_tokens

    def test_reauthorization_required_for_old_grant(self):
        tokens = self.seed_tokens()
        tokens["scope"] = "user-read-private playlist-read-private"
        spotify_store.save_tokens(tokens)
        status = self.client.get("/spotify/status").json()
        self.assertTrue(status["connected"])
        self.assertFalse(status["playback_ready"])
        self.assertEqual(len(status["missing_playback_permissions"]), 3)
        self.assertEqual(self.client.post("/spotify/play", json={}).status_code, 403)
        self.assertEqual(self.client.get("/spotify/currently-playing").status_code, 403)
        self.remote.assert_not_called()

    def test_current_track_episode_and_no_playback(self):
        self.seed_tokens()
        self.remote.side_effect = None
        self.remote.return_value = httpx.Response(204)
        self.assertEqual(self.client.get("/spotify/currently-playing").json(), {"is_playing": False, "item": None, "progress_ms": None})
        for kind in ("track", "episode"):
            self.remote.return_value = httpx.Response(200, json={"is_playing": True, "progress_ms": 123,
                "item": {"id": "item1", "name": "Example", "type": kind, "duration_ms": 1000}})
            response = self.client.get("/spotify/currently-playing")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["item"]["type"], kind)
            self.assertEqual(response.json()["progress_ms"], 123)

    def test_resume_pause_and_playlist_targeting(self):
        self.seed_tokens()
        self.remote.side_effect = None
        self.remote.return_value = httpx.Response(204)
        for path in ("play", "pause"):
            response = self.client.post("/spotify/" + path)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["status"], "requested")
            args, kwargs = self.remote.call_args
            self.assertEqual(args, ("PUT", "https://api.spotify.com/v1/me/player/" + path))
            self.assertEqual(kwargs["params"], {})
        playlist_id = "a" * 22
        for value in (playlist_id, "spotify:playlist:" + playlist_id, "https://open.spotify.com/playlist/" + playlist_id + "?si=example"):
            response = self.client.post("/spotify/play", json={"playlist": value, "device_id": "my-device"})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(self.remote.call_args.kwargs["json"], {"context_uri": "spotify:playlist:" + playlist_id})
            self.assertEqual(self.remote.call_args.kwargs["params"], {"device_id": "my-device"})

    def test_validation_devices_missing_device_and_restrictions(self):
        self.seed_tokens()
        for value in ("invalid", "https://other.example/playlist/" + "a" * 22, "spotify:track:" + "a" * 22):
            self.assertEqual(self.client.post("/spotify/play", json={"playlist": value}).status_code, 422)
        self.assertEqual(self.client.post("/spotify/pause", json={"device_id": " "}).status_code, 422)
        self.assertEqual(self.client.post("/spotify/play", json={"arbitrary": 1}).status_code, 422)
        self.remote.assert_not_called()
        self.remote.side_effect = None
        self.remote.return_value = httpx.Response(200, json={"devices": [{"id": "d1", "name": "Computer", "is_restricted": True}]})
        self.assertTrue(self.client.get("/spotify/devices").json()["devices"][0]["is_restricted"])
        self.remote.return_value = httpx.Response(404)
        self.assertEqual(self.client.post("/spotify/play", json={}).status_code, 409)
        self.remote.return_value = httpx.Response(403)
        self.assertEqual(self.client.post("/spotify/pause", json={}).status_code, 403)
        self.remote.return_value = httpx.Response(429, headers={"Retry-After": "10"})
        response = self.client.post("/spotify/play", json={})
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.headers["Retry-After"], "10")

    def test_successful_plain_text_player_acknowledgements(self):
        self.seed_tokens()
        self.remote.side_effect = None
        for code, body in ((200, "example-command-acknowledgement"), (202, ""), (204, "")):
            self.remote.return_value = httpx.Response(code, text=body)
            for endpoint in ("/spotify/play", "/spotify/pause"):
                response = self.client.post(endpoint, json={})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["status"], "requested")

    def test_playback_retries_expired_access_once(self):
        self.seed_tokens()
        self.remote.side_effect = [httpx.Response(401), httpx.Response(200, json={"access_token": "fake-new", "expires_in": 3600}), httpx.Response(204)]
        self.assertEqual(self.client.post("/spotify/pause", json={}).status_code, 200)
        self.assertEqual(self.remote.call_count, 3)
        self.assertEqual(self.remote.call_args.kwargs["headers"]["Authorization"], "Bearer fake-new")


if __name__ == "__main__":
    unittest.main()

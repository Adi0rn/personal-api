"""Dashboard player heartbeat and explicit-device routing, with fake Spotify."""
import unittest
from unittest.mock import patch
import dashboard_player
import test_features as fixtures
from test_tasks import test_server, request


class DashboardPlayerTests(unittest.TestCase):
    setUp = fixtures.FeatureTests.setUp

    def test_registration_expiry_block_and_disconnect(self):
        old = dashboard_player.STATE
        dashboard_player.STATE = None
        try:
            with test_server() as base:
                self.assertFalse(request(base, "GET", "/spotify/dashboard-player")[1]["ready"])
                self.assertEqual(request(base, "POST", "/spotify/dashboard-player", {"device_id":"browser", "autoplay_blocked":True})[0], 200)
                state = request(base, "GET", "/spotify/dashboard-player")[1]
                self.assertTrue(state["ready"])
                self.assertTrue(state["autoplay_blocked"])
                self.assertEqual(state["device_id"], "browser")
                request(base, "DELETE", "/spotify/dashboard-player", {"device_id":"other"})
                self.assertTrue(request(base, "GET", "/spotify/dashboard-player")[1]["ready"])
                request(base, "DELETE", "/spotify/dashboard-player", {"device_id":"browser"})
                self.assertFalse(request(base, "GET", "/spotify/dashboard-player")[1]["ready"])
                request(base, "POST", "/spotify/dashboard-player", {"device_id":"browser"})
                dashboard_player.STATE["seen"] -= 20
                self.assertTrue(request(base, "GET", "/spotify/dashboard-player")[1]["ready"], "A background timer delay must not create another tab")
                dashboard_player.STATE["seen"] -= dashboard_player.TTL
                self.assertFalse(request(base, "GET", "/spotify/dashboard-player")[1]["ready"])
                self.assertEqual(request(base, "POST", "/spotify/dashboard-player", {"device_id":""})[0], 422)
        finally:
            dashboard_player.STATE = old

    @patch("assistant_bridge.player.play")
    @patch("spotify_songs.spotify.api_get")
    def test_song_command_targets_explicit_dashboard_device(self, search, play):
        search.return_value = {"tracks":{"items":[{"id":"a"*22,"name":"Kiss of Life","artists":[{"name":"Sade"}]}]}}
        with test_server() as base:
            status, body = request(base, "POST", "/assistant/command", {"text":"play Kiss of Life by Sade","device_id":"dashboard-only"})
        self.assertEqual(status, 200)
        self.assertTrue(body["handled"])
        self.assertEqual(play.call_args.args[0].device_id, "dashboard-only")
        self.assertEqual(play.call_args.args[0].track, "spotify:track:"+"a"*22)

    @patch("assistant_bridge.player.transfer")
    @patch("assistant_bridge.player.play")
    def test_default_ready_dashboard_is_started_without_button(self, play, transfer):
        with patch("dashboard_player.STATE", {"device_id":"dashboard-only", "autoplay_blocked":False, "seen":dashboard_player.time.monotonic()}):
            with test_server() as base:
                status, body = request(base,"POST","/assistant/command",{"text":"play music"})
        self.assertEqual(status,200)
        self.assertTrue(body["dashboard_playback"])
        self.assertEqual(play.call_args.args[0].device_id,"dashboard-only")
        self.assertEqual(transfer.call_args.args[0].device_id,"dashboard-only")
        self.assertTrue(transfer.call_args.args[0].play)
        play.assert_called_once()

    @patch("assistant_bridge.player.transfer")
    @patch("assistant_bridge.player.play")
    def test_explicit_other_device_is_not_transferred_to_dashboard(self, play, transfer):
        with patch("dashboard_player.STATE", {"device_id":"dashboard-only", "autoplay_blocked":False, "seen":dashboard_player.time.monotonic()}):
            with test_server() as base:
                self.assertEqual(request(base,"POST","/assistant/command",{"text":"play music", "device_id":"other-device"})[0],200)
        self.assertEqual(play.call_args.args[0].device_id,"other-device")
        transfer.assert_not_called()

    @patch("assistant_bridge.player.transfer")
    @patch("assistant_bridge.player.pause")
    def test_pause_never_gets_automatically_resumed(self, pause, transfer):
        with patch("dashboard_player.STATE", {"device_id":"dashboard-only", "autoplay_blocked":False, "seen":dashboard_player.time.monotonic()}):
            with test_server() as base:
                self.assertEqual(request(base,"POST","/assistant/command",{"text":"pause my music"})[0],200)
        pause.assert_called_once()
        transfer.assert_not_called()

"""Dashboard remains a Spotify remote; no browser token or SDK is exposed."""
from unittest.mock import patch
import httpx
import unittest
import test_spotify as fixtures
import spotify_connection as spotify


class DesktopRemoteTests(unittest.TestCase):
    setUp=fixtures.SpotifyTests.setUp
    seed_tokens=fixtures.SpotifyTests.seed_tokens

    def test_browser_features_removed_and_remote_controls_kept(self):
        self.seed_tokens()
        for route in ('/spotify/browser-token','/spotify/dashboard-player'):
            self.assertEqual(self.client.post(route).status_code,404)
        self.assertEqual(self.client.get('/dashboard/assets/spotify-browser.js').status_code,404)
        html=self.client.get('/dashboard').text
        self.assertNotIn('browser-enable',html)
        self.assertNotIn('browser-play',html)
        self.assertNotIn('spotify-browser.js',html)
        for control in ('play-pause','previous-track','next-track','playlist-select'):
            self.assertIn('id="'+control+'"',html)
        self.assertNotIn('streaming',spotify.SCOPES.split())
        self.assertNotIn('user-read-email',spotify.SCOPES.split())

    @patch.dict('os.environ',{'COMPUTERNAME':'Local PC','SPOTIFY_DESKTOP_DEVICE_NAME':''})
    def test_devices_identify_this_computer(self):
        self.seed_tokens();self.remote.side_effect=None
        self.remote.return_value=httpx.Response(200,json={'devices':[
            {'id':'local','name':'LOCAL PC','type':'Computer'},
            {'id':'browser','name':'PAPI · This browser','type':'Computer'}]})
        devices=self.client.get('/spotify/devices').json()['devices']
        self.assertTrue(devices[0]['is_local'])
        self.assertFalse(devices[1]['is_local'])

    @patch('assistant_bridge.player.transfer')
    @patch('assistant_bridge.player.play')
    def test_assistant_respects_explicit_desktop_and_does_not_transfer_to_browser(self, play, transfer):
        response=self.client.post('/assistant/command',json={'text':'play music','device_id':'desktop'})
        self.assertEqual(response.status_code,200)
        self.assertEqual(play.call_args.args[0].device_id,'desktop')
        transfer.assert_not_called()

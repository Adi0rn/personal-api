"""Browser playback uses fake credentials and never starts real audio."""
import time
import unittest
import httpx
import spotify_connection as spotify
import spotify_store
import test_spotify as fixtures


class BrowserPlaybackTests(unittest.TestCase):
    setUp = fixtures.SpotifyTests.setUp
    seed_tokens = fixtures.SpotifyTests.seed_tokens

    def test_old_grant_keeps_remote_playback_but_requires_streaming_consent(self):
        tokens = self.seed_tokens()
        tokens['scope'] = 'user-read-private playlist-read-private ' + ' '.join(spotify.PLAYBACK_SCOPES)
        spotify_store.save_tokens(tokens)
        status = self.client.get('/spotify/status').json()
        self.assertTrue(status['playback_ready'])
        self.assertFalse(status['browser_playback_ready'])
        response = self.client.post('/spotify/browser-token', headers={'X-PAPI-Player':'1'})
        self.assertEqual(response.status_code, 403)
        self.assertNotIn('fake-access', response.text)
        self.remote.assert_not_called()

    def test_token_is_post_only_local_no_store_and_contains_no_refresh_secret(self):
        self.seed_tokens()
        self.assertEqual(self.client.get('/spotify/browser-token').status_code, 405)
        self.assertEqual(self.client.post('/spotify/browser-token').status_code, 403)
        self.assertEqual(self.client.post('/spotify/browser-token', headers={'Origin':'https://evil.example','X-PAPI-Player':'1'}).status_code, 403)
        response = self.client.post('/spotify/browser-token', headers={'X-PAPI-Player':'1'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(response.json()), {'access_token','expires_in','token_type'})
        self.assertEqual(response.json()['access_token'], 'fake-access')
        self.assertIn('no-store', response.headers['cache-control'])
        self.assertNotIn('fake-refresh', response.text)
        self.assertNotIn('access_token', self.client.get('/spotify/status').json())

    def test_expired_token_refreshes_without_exposing_refresh_token(self):
        self.seed_tokens(expired=True)
        self.remote.side_effect = None
        self.remote.return_value = httpx.Response(200,json={'access_token':'new-fake-access','expires_in':3600})
        response = self.client.post('/spotify/browser-token',headers={'X-PAPI-Player':'1'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['access_token'],'new-fake-access')
        self.assertEqual(self.remote.call_args.kwargs['data']['grant_type'],'refresh_token')

    def test_transfer_targets_one_device_with_explicit_play_and_validates_input(self):
        self.seed_tokens()
        for body in ({}, {'device_id':''}, {'device_id':'device','play':'yes'}, {'device_id':'device','extra':1}):
            self.assertEqual(self.client.post('/spotify/transfer',json=body).status_code,422)
        self.remote.assert_not_called()
        self.remote.side_effect = None
        self.remote.return_value = httpx.Response(204)
        for play in (False, True):
            response = self.client.post('/spotify/transfer',json={'device_id':'browser-device','play':play})
            self.assertEqual(response.status_code,200)
            self.assertEqual(self.remote.call_args.args,('PUT','https://api.spotify.com/v1/me/player'))
            self.assertEqual(self.remote.call_args.kwargs['json'],{'device_ids':['browser-device'],'play':play})

    def test_oauth_requests_sdk_scopes_and_dashboard_has_explicit_controls(self):
        response = self.client.get('/connect/spotify')
        from urllib.parse import parse_qs,urlsplit
        scopes = set(parse_qs(urlsplit(response.headers['location']).query)['scope'][0].split())
        self.assertTrue(spotify.BROWSER_SCOPES.issubset(scopes))
        html = self.client.get('/dashboard').text
        self.assertIn('id="browser-enable"',html)
        self.assertNotIn('src="https://sdk.scdn.co',html)
        self.assertEqual(self.client.get('/dashboard/assets/spotify-browser.js').status_code,200)

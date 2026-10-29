"""Album metadata checks use mock Spotify responses and fake artwork URLs."""
import unittest
import httpx
import test_spotify as fixtures
import spotify_player


class AlbumArtTests(unittest.TestCase):
    setUp=fixtures.SpotifyTests.setUp
    seed_tokens=fixtures.SpotifyTests.seed_tokens

    def test_album_cover_is_returned_for_playing_and_paused_tracks(self):
        self.seed_tokens()
        self.remote.side_effect=None
        for playing in (True,False):
            self.remote.return_value=httpx.Response(200,json={'is_playing':playing,'item':{
                'type':'track','name':'Example','album':{'images':[{'url':'https://i.scdn.co/image/example'}]}}})
            response=self.client.get('/spotify/currently-playing')
            self.assertEqual(response.status_code,200)
            self.assertEqual(response.json()['item']['image_url'],'https://i.scdn.co/image/example')
            self.assertEqual(response.json()['is_playing'],playing)

    def test_episode_art_and_missing_art(self):
        self.assertEqual(spotify_player.artwork_url({'type':'episode','images':[{'url':'https://i.scdn.co/image/podcast'}]}),'https://i.scdn.co/image/podcast')
        self.assertIsNone(spotify_player.artwork_url({'type':'track','album':{'images':[]}}))
        self.assertIsNone(spotify_player.artwork_url({'type':'track'}))

    def test_untrusted_image_sources_are_not_returned(self):
        for url in ('http://i.scdn.co/image/cover','https://example.com/cover','https://i.scdn.co.evil.test/image/cover','data:image/png;base64,abc','https://user:pass@i.scdn.co/image/a'):
            self.assertIsNone(spotify_player.artwork_url({'type':'track','album':{'images':[{'url':url}]}}))

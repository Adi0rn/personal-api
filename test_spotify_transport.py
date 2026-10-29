"""Transport pooling checks; never calls the real Spotify API."""
import unittest
from unittest.mock import Mock, patch
import httpx
from fastapi import HTTPException
import spotify_connection as spotify


class SpotifyTransportTests(unittest.TestCase):
    def setUp(self):
        spotify.close_http_client()
        self.addCleanup(spotify.close_http_client)

    def test_requests_share_one_client_and_close_on_shutdown(self):
        client=Mock(is_closed=False)
        with patch.object(spotify.httpx,'Client',return_value=client) as factory:
            spotify.spotify_request('PUT','https://api.spotify.com/v1/me/player/pause',headers={'Authorization':'Bearer fake'})
            spotify.spotify_request('PUT','https://api.spotify.com/v1/me/player/play',headers={'Authorization':'Bearer fake'})
            factory.assert_called_once()
            self.assertEqual(client.request.call_count,2)
            self.assertFalse(factory.call_args.kwargs['trust_env'])
            spotify.close_http_client()
            client.close.assert_called_once()
            self.assertIsNone(spotify._HTTP_CLIENT)

    def test_network_failure_has_existing_error_and_does_not_retry_commands(self):
        client=Mock(is_closed=False)
        client.request.side_effect=httpx.ConnectError('offline')
        with patch.object(spotify.httpx,'Client',return_value=client):
            with self.assertRaises(HTTPException) as error:
                spotify.spotify_request('PUT','https://api.spotify.com/v1/me/player/play')
            self.assertEqual(error.exception.status_code,502)
            client.request.assert_called_once()

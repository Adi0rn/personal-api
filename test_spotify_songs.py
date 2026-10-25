"""Song playback checks with fake Spotify results; no real account is used."""
import unittest
from unittest.mock import patch
from fastapi import HTTPException
from pydantic import ValidationError
import spotify_songs as songs
import spotify_player as player


TRACK = {"id": "a"*22, "name": "Kiss of Life", "artists": [{"name": "Sade"}]}


class SongTests(unittest.TestCase):
    @patch("spotify_songs.spotify.api_get")
    @patch("spotify_songs.player.player_request")
    def test_phrase_searches_and_plays_track_uri(self, playback, search):
        search.return_value = {"tracks": {"items": [TRACK]}}
        for phrase in ("Open Spotify and play Kiss of Life by Sade", "Play Kiss of Life by Sade on Spotify", "Play the song Kiss of Life by Sade"):
            result = songs.command(phrase)
            self.assertTrue(result["handled"])
            self.assertEqual(result["action"], "song_requested")
            self.assertEqual(playback.call_args.kwargs["payload"], {"uris": ["spotify:track:"+"a"*22]})
        self.assertEqual(search.call_args.kwargs["q"], 'track:"Kiss of Life" artist:"Sade"')

    @patch("spotify_songs.spotify.api_get")
    @patch("spotify_songs.player.play")
    def test_wrong_artist_or_title_never_plays(self, play, search):
        search.return_value = {"tracks": {"items": [{**TRACK, "artists": [{"name": "Someone else"}]}]}}
        with self.assertRaises(HTTPException) as caught:
            songs.play_song(songs.SongRequest(title="Kiss of Life", artist="Sade"))
        self.assertEqual(caught.exception.status_code, 404)
        play.assert_not_called()

    @patch("spotify_songs.spotify.api_get")
    @patch("spotify_songs.player.play")
    def test_empty_and_unplayable_results_do_not_play(self, play, search):
        for items in ([], [{**TRACK, "is_playable": False}], [{**TRACK, "restrictions": {"reason":"market"}}]):
            search.return_value = {"tracks": {"items": items}}
            with self.assertRaises(HTTPException):
                songs.play_song(songs.SongRequest(title="Kiss of Life", artist="Sade"))
        play.assert_not_called()

    def test_track_validation_and_mutual_exclusion(self):
        for value in ("a"*22, "spotify:track:"+"a"*22, "https://open.spotify.com/track/"+"a"*22+"?si=x"):
            self.assertEqual(player.PlayRequest(track=value).track, "spotify:track:"+"a"*22)
        for body in ({"track":"invalid"}, {"track":"https://evil.example/track/"+"a"*22},
                     {"track":"a"*22,"playlist":"b"*22}):
            with self.assertRaises(ValidationError):
                player.PlayRequest(**body)

    @patch("spotify_songs.play_song")
    def test_other_commands_and_playlists_are_not_intercepted(self, play):
        self.assertIsNone(songs.command("play playlist Heart and soul"))
        self.assertIsNone(songs.command("show my tasks"))
        self.assertIsNone(songs.command("open calculator"))
        play.assert_not_called()

    @patch("spotify_player.player_request", side_effect=HTTPException(409, "No available Spotify device."))
    @patch("spotify_songs.spotify.api_get", return_value={"tracks":{"items":[TRACK]}})
    def test_device_error_is_preserved(self, search, play):
        with self.assertRaises(HTTPException) as caught:
            songs.command("play Kiss of Life by Sade")
        self.assertEqual(caught.exception.status_code, 409)

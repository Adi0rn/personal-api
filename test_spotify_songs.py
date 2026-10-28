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

    @patch("spotify_songs.spotify.api_get")
    @patch("spotify_songs.player.play")
    def test_quoted_transcriptions_route_to_the_exact_track(self, play, search):
        search.return_value = {"tracks":{"items":[{**TRACK,"name":"Like a Tattoo"}]}}
        for phrase in ['"Play Like a Tattoo" by Sade.', '“Play Like a Tattoo” by Sade.', 'Play "Like a Tattoo" by "Sade".', '"Play Like a Tattoo by Sade".', 'Please play Like a Tattoo by Sade.']:
            with self.subTest(phrase=phrase):
                reply = songs.command(phrase, device_id="dashboard")
                self.assertTrue(reply["handled"])
                self.assertEqual(search.call_args.kwargs["q"], 'track:"Like a Tattoo" artist:"Sade"')
                self.assertEqual(play.call_args.args[0].device_id,"dashboard")

    @patch("spotify_songs.spotify.api_get")
    @patch("spotify_songs.player.play")
    def test_spoken_and_joined_words_use_fallback_but_not_first_search_result(self, play, search):
        wrong={**TRACK,"id":"b"*22,"name":"Party Monster","artists":[{"name":"The Weeknd"}]}
        right={**TRACK,"id":"c"*22,"name":"The Party & The After Party","artists":[{"name":"The Weeknd"}]}
        search.side_effect=[{"tracks":{"items":[]}},{"tracks":{"items":[wrong,right]}}]
        result=songs.command("Play The Party and the Afterparty by The Weeknd.",device_id="desktop")
        self.assertEqual(result["track_uri"],"spotify:track:"+"c"*22)
        self.assertEqual(search.call_count,2)
        self.assertEqual(search.call_args.kwargs["q"],'track:"Party" artist:"The Weeknd"')
        self.assertEqual(play.call_args.args[0].device_id,"desktop")

    @patch("spotify_songs.spotify.api_get")
    @patch("spotify_songs.player.play")
    def test_fallback_rejects_other_songs_and_covers(self, play, search):
        search.side_effect=[{"tracks":{"items":[]}},{"tracks":{"items":[
            {**TRACK,"name":"Party Monster","artists":[{"name":"The Weeknd"}]},
            {**TRACK,"name":"The Party & The After Party","artists":[{"name":"A Cover Artist"}]}]}}]
        with self.assertRaises(HTTPException):
            songs.command("Play The Party and the Afterparty by The Weeknd.")
        play.assert_not_called()

    def test_ampersands_spacing_and_accents_normalize_consistently(self):
        self.assertEqual(songs.normalized("The Party & The After Party"),songs.normalized("The Party and the Afterparty"))
        self.assertEqual(songs.normalized("Beyoncé"),songs.normalized("Beyonce"))
        self.assertNotEqual(songs.normalized("Party Monster"),songs.normalized("The Party and the Afterparty"))

    @patch("spotify_songs.spotify.api_get")
    @patch("spotify_songs.player.play")
    def test_numeric_transcription_discovers_punctuation_and_skips_remix(self, play, search):
        original={**TRACK,"id":"d"*22,"name":"34+35","artists":[{"name":"Ariana Grande"}]}
        remix={**original,"id":"e"*22,"name":"34+35 Remix (feat. Doja Cat) - Remix"}
        search.side_effect=[{"tracks":{"items":[]}},{"tracks":{"items":[remix,original]}}]
        reply=songs.command("Play 3435 by Ariana Grande.",device_id="desktop")
        self.assertEqual(reply["track_uri"],"spotify:track:"+"d"*22)
        self.assertEqual(search.call_count,2)
        self.assertEqual(search.call_args.kwargs["q"],'track:"34" artist:"Ariana Grande"')
        play.assert_called_once()

    @patch("spotify_songs.spotify.api_get")
    @patch("spotify_songs.player.play")
    def test_numeric_fallback_is_general_and_validates_every_digit_and_artist(self, play, search):
        for spoken,catalog in (("1234","12/34"),("911","9-1-1")):
            with self.subTest(spoken=spoken):
                search.side_effect=[{"tracks":{"items":[]}},{"tracks":{"items":[{**TRACK,"name":catalog}]}}]
                self.assertTrue(songs.command("play "+spoken+" by Sade")["handled"])
        play.reset_mock()
        search.side_effect=[{"tracks":{"items":[]}},{"tracks":{"items":[
            {**TRACK,"name":"34+36","artists":[{"name":"Ariana Grande"}]},
            {**TRACK,"name":"34+35","artists":[{"name":"A Cover Artist"}]}]}}]
        with self.assertRaises(HTTPException):songs.command("play 3435 by Ariana Grande")
        play.assert_not_called()

    @patch("spotify_songs.spotify.api_get")
    @patch("spotify_songs.player.play")
    def test_exact_numeric_title_does_not_do_extra_search(self, play, search):
        search.return_value={"tracks":{"items":[{**TRACK,"name":"3435"}]}}
        songs.command("play 3435 by Sade")
        search.assert_called_once()

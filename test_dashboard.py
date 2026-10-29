"""Dashboard and assistant integration checks, with temporary data and fake Spotify."""

import unittest
from unittest.mock import patch
import httpx

import test_features as fixtures
from test_tasks import request, test_server


class DashboardCommandTests(unittest.TestCase):
    setUp = fixtures.FeatureTests.setUp

    def test_dashboard_and_local_assets(self):
        with test_server() as base, httpx.Client(base_url=base, trust_env=False) as client:
            for path, content in (("/dashboard", 'id="task-list"'), ("/dashboard/assets/app.js", "/assistant/command"), ("/dashboard/assets/style.css", ".sidebar")):
                response = client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertIn(content, response.text)
            self.assertEqual(client.post("/assistant/command", json={"text": "show my tasks"}, headers={"Origin": "https://example.com"}).status_code, 403)

    def test_task_commands_use_the_same_persistent_data(self):
        with test_server() as base:
            status, body = request(base, "POST", "/assistant/command", {"text": "Add task Buy milk"})
            self.assertEqual(status, 200)
            self.assertEqual(body["action"], "task_created")
            task = request(base, "GET", "/tasks")[1][0]
            self.assertEqual(task["title"], "Buy milk")
            listed = request(base, "POST", "/assistant/command", {"text": "show my tasks"})[1]
            self.assertIn("Buy milk", listed["reply"])
            response = request(base, "POST", "/assistant/command", {"text": f"Complete task {task['id']}"})
            self.assertEqual(response[0], 200)
            self.assertTrue(request(base, "GET", "/tasks")[1][0]["completed"])
            self.assertEqual(request(base, "POST", "/assistant/command", {"text": "complete task 9999"})[0], 404)
            self.assertEqual(request(base, "POST", "/assistant/command", {"text": "add task " + "x" * 201})[0], 422)

    def test_projects_and_unknown_commands(self):
        with test_server() as base:
            request(base, "PATCH", "/projects/Voice%20assistant", {"description": "My assistant", "next_steps": ["Connect the API"]})
            result = request(base, "POST", "/assistant/command", {"text": "Project details for voice assistant"})
            self.assertEqual(result[0], 200)
            self.assertIn("Connect the API", result[1]["reply"])
            with patch("assistant_bridge.tasks.create_task") as create:
                unknown = request(base, "POST", "/assistant/command", {"text": "delete all my files"})[1]
                self.assertFalse(unknown["handled"])
                create.assert_not_called()

    def test_spotify_commands_use_allowlisted_actions_and_exact_playlist_names(self):
        with test_server() as base, patch("assistant_bridge.player.pause") as pause, patch("assistant_bridge.player.play") as play, patch("assistant_bridge.spotify.api_get") as playlists, patch("assistant_bridge.player.currently_playing") as current:
            current.return_value = {"is_playing": True, "item": {"name": "Test song", "artists": ["Test artist"]}}
            result = request(base, "POST", "/assistant/command", {"text": "what song is playing"})
            self.assertIn("Test song", result[1]["reply"])
            self.assertEqual(request(base, "POST", "/assistant/command", {"text": "pause my music"})[0], 200)
            pause.assert_called_once_with()
            self.assertEqual(request(base, "POST", "/assistant/command", {"text": "resume my music"})[0], 200)
            playlists.return_value = {"items": [{"id": "a" * 22, "name": "My playlist"}], "next": None}
            result = request(base, "POST", "/assistant/command", {"text": "play playlist My playlist."})
            self.assertEqual(result[0], 200)
            self.assertEqual(play.call_args.args[0].playlist, "spotify:playlist:" + "a" * 22)
            playlists.return_value = {"items": [{"id": "a" * 22, "name": "Same"}, {"id": "b" * 22, "name": "Same"}], "next": None}
            self.assertEqual(request(base, "POST", "/assistant/command", {"text": "play playlist Same"})[0], 409)

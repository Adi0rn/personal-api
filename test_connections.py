"""Connection checks use fake credentials and responses, with temporary encrypted files."""
import time
import unittest
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch
import httpx
from google.auth.exceptions import RefreshError

import connections as app
import connection_store as store
import test_features as fixtures
from test_tasks import request, test_server

CLIENT = "fake-client.apps.googleusercontent.com"


class ConnectionTests(unittest.TestCase):
    def setUp(self):
        fixtures.FeatureTests.setUp(self)
        for target, value in (("connection_store.DIRECTORY", self.db.parent / "connections"), ("connections.PENDING", {})):
            item = patch(target, value)
            item.start()
            self.addCleanup(item.stop)
        item = patch("connections.remote", side_effect=AssertionError("Unexpected real account request"))
        self.remote = item.start()
        self.addCleanup(item.stop)

    def seed_google(self, expired=False, scopes=None):
        store.save("google_config", {"client_id": CLIENT, "client_secret": "fake-client-secret"})
        store.save("google_tokens", {"client_id": CLIENT, "access_token": "fake-access-token", "refresh_token": "fake-refresh",
            "expires_at": time.time() + (-1 if expired else 3600), "scope": scopes if scopes is not None else app.SCOPES})

    def test_disconnected_status_and_setup(self):
        with test_server() as base, httpx.Client(base_url=base, trust_env=False) as client:
            status = client.get("/connections/status").json()
            for service in ("calendar", "gmail", "github"):
                self.assertFalse(status[service]["connected"])
            for route in ("/calendar/events", "/gmail/messages", "/github/repos"):
                self.assertEqual(client.get(route).status_code, 401)
            self.assertIn(app.CALLBACK, client.get("/connections/setup").text)
            self.assertEqual(client.post("/connections/google/config", headers={"Origin": "https://other.example"},json={"client_id": CLIENT, "client_secret": "fake"}).status_code, 403)
            self.remote.assert_not_called()

    def test_google_state_pkce_encryption_and_callback_replay(self):
        with test_server() as base, httpx.Client(base_url=base, trust_env=False) as client:
            response = client.post("/connections/google/config", json={"client_id": CLIENT, "client_secret": "fake-client-secret"})
            self.assertEqual(response.status_code, 200)
            self.assertNotIn("fake-client-secret", response.text)
            self.assertNotIn(b"fake-client-secret", store.path("google_config").read_bytes())
            start = client.get("/connect/google")
            self.assertEqual(start.status_code, 303)
            params = parse_qs(urlsplit(start.headers["location"]).query)
            state = params["state"][0]
            self.assertEqual(params["redirect_uri"], [app.CALLBACK])
            self.assertEqual(params["code_challenge_method"], ["S256"])
            self.assertEqual(client.get("/connections/google/callback", params={"state": "wrong", "code": "fake-code"}).status_code, 400)
            with patch("connections.exchange_google", return_value={"access_token": "fake-access", "refresh_token": "fake-refresh", "expires_at": time.time()+3600, "scope": app.SCOPES}) as exchange:
                response = client.get("/connections/google/callback", params={"state": state, "code": "fake-code"})
                self.assertEqual(response.status_code, 303)
                self.assertEqual(response.headers["location"], "/dashboard#connections")
                exchange.assert_called_once()
                self.assertEqual(client.get("/connections/google/callback", params={"state": state, "code": "fake-code"}).status_code, 400)
            self.assertTrue(client.get("/connections/status").json()["gmail"]["connected"])

    def test_google_expiry_denial_and_partial_permissions(self):
        self.seed_google(scopes=[app.CALENDAR_SCOPE])
        with test_server() as base, httpx.Client(base_url=base, trust_env=False) as client:
            self.assertEqual(client.get("/gmail/messages").status_code, 403)
            for denied in (False, True):
                start = client.get("/connect/google")
                state = parse_qs(urlsplit(start.headers["location"]).query)["state"][0]
                if not denied:
                    app.PENDING[state]["expires"] = 0
                self.assertEqual(client.get("/connections/google/callback", params={"state": state, "error": "access_denied"}).status_code, 400)
            self.remote.assert_not_called()

    def test_calendar_and_gmail_results_are_read_only(self):
        self.seed_google()
        def respond(method, url, **kwargs):
            self.assertEqual(method, "GET")
            if "calendar/v3" in url:
                self.assertEqual(kwargs["params"]["orderBy"], "startTime")
                return httpx.Response(200, json={"items": [{"id": "e1", "summary": "Meeting", "start": {"date": "2026-11-01"}, "end": {"date": "2026-11-02"}}]})
            if url.endswith("/messages"):
                self.assertEqual(kwargs["params"]["q"], "in:inbox is:unread")
                return httpx.Response(200, json={"messages": [{"id": "m1"}]})
            self.assertEqual(kwargs["params"]["format"], "metadata")
            return httpx.Response(200, json={"id": "m1", "labelIds": ["UNREAD"], "payload": {"headers": [{"name": "Subject", "value": "Test message"}, {"name": "From", "value": "example@example.com"}]}})
        self.remote.side_effect = respond
        with test_server() as base:
            self.assertEqual(request(base, "GET", "/calendar/events?days=7&limit=5")[1]["events"][0]["title"], "Meeting")
            self.assertEqual(request(base, "GET", "/gmail/messages?unread=true")[1]["messages"][0]["subject"], "Test message")
            self.assertEqual(request(base, "GET", "/gmail/messages?limit=100")[0], 422)

    def test_refresh_retains_permissions_and_revocation_clears_tokens(self):
        self.seed_google(expired=True)
        with patch("connections.Credentials") as credentials:
            instance = credentials.return_value
            from datetime import datetime, timedelta
            instance.token = "fake-refreshed"
            instance.refresh_token = "fake-refresh"
            instance.expiry = datetime.now() + timedelta(hours=1)
            self.assertEqual(app.google_token(app.CALENDAR_SCOPE), "fake-refreshed")
            self.assertEqual(store.load("google_tokens")["scope"], app.SCOPES)
            self.seed_google(expired=True)
            instance.refresh.side_effect = RefreshError("fake-sensitive-provider-message")
            with self.assertRaises(Exception) as caught:
                app.google_token(app.GMAIL_SCOPE)
            self.assertEqual(caught.exception.status_code, 401)
            self.assertIsNone(store.load("google_tokens"))

    def test_github_validation_and_selected_repository_metadata(self):
        self.remote.side_effect = None
        self.remote.return_value = httpx.Response(200, json={"login": "test-user"})
        with test_server() as base:
            status, body = request(base, "POST", "/connections/github/config", {"token": "github_pat_fake_testing_only"})
            self.assertEqual(status, 200)
            self.assertEqual(body["login"], "test-user")
            self.assertNotIn("github_pat_fake", str(body))
            self.remote.return_value = httpx.Response(200, json=[{"full_name": "test-user/project", "private": True, "html_url": "https://github.com/test-user/project"}])
            response = request(base, "GET", "/github/repos")
            self.assertEqual(response[0], 200)
            self.assertTrue(response[1]["repositories"][0]["private"])
            self.assertEqual(request(base, "DELETE", "/connections/github")[0], 200)
            self.assertFalse(request(base, "GET", "/connections/status")[1]["github"]["connected"])
            self.assertEqual(request(base, "POST", "/connections/github/config", {"token": "ghp_classic_not_supported"})[0], 400)

    def test_rate_limits_and_voice_command_routing(self):
        self.remote.side_effect = None
        self.remote.return_value = httpx.Response(429, headers={"Retry-After": "15"})
        self.seed_google()
        with test_server() as base:
            self.assertEqual(request(base, "GET", "/calendar/events")[0], 429)
            with patch("assistant_bridge.connections.gmail_messages", return_value={"messages": [{"subject": "Test subject", "from": "Test sender"}]}):
                body = request(base, "POST", "/assistant/command", {"text": "show unread emails"})[1]
                self.assertTrue(body["handled"])
                self.assertIn("Test subject", body["reply"])
            with patch("assistant_bridge.connections.github_repos", return_value={"repositories": [{"name": "user/repo"}]}):
                self.assertIn("user/repo", request(base, "POST", "/assistant/command", {"text": "show my repositories"})[1]["reply"])

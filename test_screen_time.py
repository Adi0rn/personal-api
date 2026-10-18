"""Deterministic screen-time checks; no real input tracking or personal database."""
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

import screen_time
from test_tasks import request, test_server


class ScreenTimeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="papi-screen-time-")
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "time.db"
        self.tracker = screen_time.Tracker(self.path, "Asia/Kolkata")
        self.tracker.init()
        self.now = datetime(2026, 10, 18, 10, 0, tzinfo=timezone.utc)

    def sample(self, seconds, idle=0, unlocked=True):
        self.tracker.consume(self.now + timedelta(seconds=seconds), seconds, idle, unlocked)

    def total(self):
        return self.tracker.summary(self.now)["active_seconds"]

    def test_active_time_persists_across_instances(self):
        self.sample(0)
        self.sample(15)
        self.sample(30)
        self.assertEqual(self.total(), 30)
        another = screen_time.Tracker(self.path, "Asia/Kolkata")
        self.assertEqual(another.summary(self.now)["active_seconds"], 30)
        self.assertEqual(len(another.summary(self.now)["last_7_days"]), 7)

    def test_idle_threshold_counts_only_active_part(self):
        self.sample(0, idle=290)
        self.sample(15, idle=305)
        self.sample(30, idle=320)
        self.assertEqual(self.total(), 10)
        self.assertEqual(self.tracker.state, "idle")

    def test_lock_unlock_sleep_and_clock_jumps_do_not_add_gaps(self):
        self.sample(0)
        self.sample(15, unlocked=False)
        self.sample(30)
        self.assertEqual(self.total(), 0)
        self.sample(45)
        self.assertEqual(self.total(), 15)
        self.sample(3645)
        self.assertEqual(self.total(), 15)
        self.tracker.consume(self.now + timedelta(seconds=7200), 3660, 0, True)
        self.assertEqual(self.total(), 15)

    def test_midnight_is_split_in_profile_timezone(self):
        start = datetime(2026, 10, 18, 18, 29, 50, tzinfo=timezone.utc)
        self.tracker.consume(start, 0, 0, True)
        self.tracker.consume(start+timedelta(seconds=15), 15, 0, True)
        with closing(sqlite3.connect(self.path)) as connection:
            totals = dict(connection.execute("SELECT day,active_seconds FROM daily"))
        self.assertEqual(totals, {"2026-10-18": 10, "2026-10-19": 5})

    def test_endpoint_uses_tracker_summary_without_exposing_input_data(self):
        self.sample(0)
        self.sample(15)
        with patch("screen_time.TRACKER", self.tracker), test_server() as base:
            status, body = request(base, "GET", "/stats/screen-time")
            self.assertEqual(status, 200)
            self.assertEqual(body["timezone"], "Asia/Kolkata")
            self.assertEqual(body["idle_after_seconds"], 300)
            self.assertNotIn("keystrokes", body)
            self.assertNotIn("screenshots", body)
            self.assertNotIn("applications", body)

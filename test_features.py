"""HTTP checks use temporary folders and databases, never personal data."""

import sqlite3
from contextlib import closing
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import projects
import tasks
from test_tasks import request, test_server


class FeatureTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="personal-api-features-")
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        self.db = root / "tasks.db"
        self.code = root / "Code"
        self.code.mkdir()
        (self.code / "Voice assistant").mkdir()
        (self.code / ".hidden").mkdir()
        (self.code / "notes.txt").write_text("Not a project")
        for target, value in (("tasks.DB_PATH", self.db), ("projects.PROJECTS_DIR", self.code)):
            patcher = patch(target, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_migration_preserves_legacy_tasks_and_is_repeatable(self):
        with closing(sqlite3.connect(self.db)) as connection, connection:
            connection.execute(
                "CREATE TABLE tasks (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, "
                "completed INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL)"
            )
            connection.execute(
                "INSERT INTO tasks VALUES (7, 'Existing task', 1, '2026-10-01T12:00:00+00:00')"
            )
        expected = {"id": 7, "title": "Existing task", "completed": True,
                    "created_at": "2026-10-01T12:00:00+00:00", "due_date": None, "priority": "medium"}
        for _ in range(2):
            with test_server() as base:
                self.assertEqual(request(base, "GET", "/tasks"), (200, [expected]))

    def test_task_dates_priorities_partial_edits_filters_and_restart(self):
        with test_server() as base:
            status, task = request(base, "POST", "/tasks", {
                "title": "Plan project", "due_date": "2026-11-01", "priority": "high"})
            self.assertEqual(status, 201)
            task_id = task["id"]
            self.assertEqual(task["due_date"], "2026-11-01")
            self.assertEqual(task["priority"], "high")
            self.assertEqual(request(base, "GET", "/tasks?priority=high&completed=false&due_before=2026-11-01")[1], [task])
            self.assertEqual(request(base, "GET", "/tasks?due_before=2026-10-31")[1], [])
            self.assertEqual(request(base, "GET", "/tasks?priority=low")[1], [])
            _, changed = request(base, "PATCH", f"/tasks/{task_id}", {"title": "Updated title"})
            self.assertEqual(changed["title"], "Updated title")
            self.assertEqual(changed["priority"], "high")
            self.assertEqual(changed["due_date"], "2026-11-01")
            _, task = request(base, "PATCH", f"/tasks/{task_id}", {"due_date": None, "priority": "low", "completed": True})
            self.assertIsNone(task["due_date"])
            self.assertTrue(task["completed"])
            self.assertEqual(task["priority"], "low")
        with test_server() as base:
            self.assertEqual(request(base, "GET", "/tasks"), (200, [task]))

    def test_invalid_task_fields_are_rejected(self):
        with test_server() as base:
            for invalid in ({"due_date": "2026-02-30"}, {"due_date": "01-11-2026"},
                            {"due_date": 0}, {"priority": "urgent"}):
                self.assertEqual(request(base, "POST", "/tasks", {"title": "Test", **invalid})[0], 422)
            _, task = request(base, "POST", "/tasks", {"title": "Test"})
            self.assertIsNone(task["due_date"])
            self.assertEqual(task["priority"], "medium")
            for invalid in ({}, {"title": None}, {"priority": None}, {"completed": None},
                            {"due_date": "bad"}, {"priority": "urgent"}):
                self.assertEqual(request(base, "PATCH", f"/tasks/{task['id']}", invalid)[0], 422)
            self.assertEqual(request(base, "GET", "/tasks?priority=urgent")[0], 422)
            self.assertEqual(request(base, "GET", "/tasks?due_before=invalid")[0], 422)

    def test_project_metadata_validation_partial_updates_and_restart(self):
        path = "/projects/Voice%20assistant"
        with test_server() as base:
            status, listing = request(base, "GET", "/projects")
            self.assertEqual(status, 200)
            self.assertEqual(len(listing["projects"]), 1)
            initial = listing["projects"][0]
            self.assertEqual(initial["description"], "")
            self.assertEqual(initial["status"], "unspecified")
            self.assertEqual(initial["next_steps"], [])
            self.assertEqual(request(base, "GET", path), (200, initial))
            status, project = request(base, "PATCH", path, {
                "description": "My assistant", "status": "active", "next_steps": ["  Connect API  "]})
            self.assertEqual(status, 200)
            self.assertEqual(project["next_steps"], ["Connect API"])
            status, project = request(base, "PATCH", path, {"status": "paused"})
            self.assertEqual(status, 200)
            self.assertEqual(project["description"], "My assistant")
            self.assertEqual(project["next_steps"], ["Connect API"])
            self.assertEqual(request(base, "GET", "/projects")[1]["projects"], [project])
            for invalid in ({}, {"status": "unknown"}, {"next_steps": [" "]},
                            {"description": None}, {"next_steps": "a string"}, {"path": "C:\\elsewhere"}):
                self.assertEqual(request(base, "PATCH", path, invalid)[0], 422)
            self.assertEqual(request(base, "GET", "/projects/missing")[0], 404)
            self.assertEqual(request(base, "PATCH", "/projects/missing", {"status": "active"})[0], 404)
            self.assertEqual(request(base, "GET", "/projects/.hidden")[0], 404)
        with test_server() as base:
            self.assertEqual(request(base, "GET", path), (200, project))
            _, cleared = request(base, "PATCH", path, {"description": "", "next_steps": []})
            self.assertEqual(cleared["description"], "")
            self.assertEqual(cleared["next_steps"], [])
            self.assertEqual(cleared["status"], "paused")


if __name__ == "__main__":
    unittest.main()

"""Exercise real HTTP requests against a temporary task database."""

import json
import socket
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from contextlib import contextmanager
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import uvicorn

import main
import tasks


@contextmanager
def test_server():
    disable_tracker = patch.dict("os.environ", {"PAPI_DISABLE_SCREEN_TIME": "1", "PAPI_DISABLE_REMINDERS": "1"})
    disable_tracker.start()
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(main.app, log_level="error", access_log=False))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started:
            if not thread.is_alive() or time.monotonic() > deadline:
                raise RuntimeError("Test server failed to start")
            time.sleep(0.02)
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        listener.close()
        disable_tracker.stop()
        if thread.is_alive():
            raise RuntimeError("Test server failed to stop")


def request(base, method, path, body=None):
    payload = None if body is None else json.dumps(body).encode()
    req = Request(base + path, data=payload, method=method, headers={"Content-Type": "application/json"})
    try:
        response = urlopen(req, timeout=5)
    except HTTPError as error:
        response = error
    with response:
        return response.status, json.load(response)


class TasksHTTPTests(unittest.TestCase):
    def test_create_filter_complete_reopen_validate_and_restart(self):
        original_path = tasks.DB_PATH
        try:
            with tempfile.TemporaryDirectory(prefix="personal-api-tests-") as directory:
                tasks.DB_PATH = Path(directory) / "tasks.db"
                with test_server() as base:
                    self.assertEqual(request(base, "GET", "/tasks"), (200, []))
                    status, task = request(base, "POST", "/tasks", {"title": "  Test my API  "})
                    self.assertEqual(status, 201)
                    self.assertEqual(task["title"], "Test my API")
                    self.assertIs(task["completed"], False)
                    self.assertTrue(task["created_at"])
                    task_id = task["id"]
                    self.assertEqual(request(base, "GET", "/tasks?completed=false")[1], [task])
                    self.assertEqual(request(base, "GET", "/tasks?completed=true")[1], [])
                    for invalid in ({"title": " "}, {"title": "x" * 201}, {}, {"title": "ok", "unknown": 1}):
                        self.assertEqual(request(base, "POST", "/tasks", invalid)[0], 422)
                    self.assertEqual(request(base, "PATCH", f"/tasks/{task_id}", {"completed": "false"})[0], 422)
                    self.assertEqual(request(base, "PATCH", "/tasks/99999", {"completed": True})[0], 404)
                    status, task = request(base, "PATCH", f"/tasks/{task_id}", {"completed": True})
                    self.assertEqual(status, 200)
                    self.assertIs(task["completed"], True)
                    self.assertEqual(request(base, "GET", "/tasks?completed=false")[1], [])
                    self.assertEqual(request(base, "GET", "/tasks?completed=true")[1], [task])
                    status, reopened = request(base, "PATCH", f"/tasks/{task_id}", {"completed": False})
                    self.assertEqual(status, 200)
                    self.assertIs(reopened["completed"], False)
                    _, task = request(base, "PATCH", f"/tasks/{task_id}", {"completed": True})
                    schema = request(base, "GET", "/openapi.json")[1]
                    self.assertIn("/tasks", schema["paths"])
                    self.assertIn("/tasks/{task_id}", schema["paths"])
                with test_server() as base:
                    self.assertEqual(request(base, "GET", "/tasks"), (200, [task]))
        finally:
            tasks.DB_PATH = original_path


if __name__ == "__main__":
    unittest.main()

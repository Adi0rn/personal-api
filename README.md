# Personal API

A local FastAPI backend for a personal assistant: profile information, project details, and persistent tasks.

## Requirements

- Python 3.12 or newer
- Windows for the Spotify connection's encrypted credential storage

## Set up (PowerShell)

From your cloned repository folder:

```powershell
python -m venv .venv
& '.\.venv\Scripts\python.exe' -m pip install -r requirements.txt
Copy-Item -LiteralPath '.\data\profile.example.json' -Destination '.\data\profile.json'
& '.\.venv\Scripts\python.exe' -m uvicorn main:app --host 127.0.0.1 --port 8000 --no-access-log
```

Copy the profile template only on first setup; keep an existing profile. Leave the terminal open while using the server. Press Ctrl+C to stop it.

Open [interactive API docs](http://127.0.0.1:8000/docs).

## Profile, projects, and tasks

| Route | Purpose |
| --- | --- |
| `GET /` | Welcome message |
| `GET /health` | Server health |
| `GET /profile` | Read `data/profile.json` |
| `GET /projects` | List project folders with saved details |
| `GET /projects/{project_name}` | Read one project's details |
| `PATCH /projects/{project_name}` | Update description, status, or next steps |
| `GET /tasks` | List and filter tasks |
| `POST /tasks` | Create a task |
| `PATCH /tasks/{task_id}` | Edit, complete, or reopen a task |

Edit your private `data/profile.json` to set your name, timezone, interests, goals, and preferences. The API rereads it on every request.

Projects are visible folders directly inside this repository's parent directory. Project files are not read or modified. Details are associated with exact folder names; renaming a folder does not automatically transfer saved details.

Example project update (enter an existing folder's name in the docs):

```json
{
  "description": "A backend for my personal assistant",
  "status": "active",
  "next_steps": ["Connect my voice assistant"]
}
```

Project status can be `unspecified`, `planned`, `active`, `paused`, or `completed`. Blank details are used until you supply them.

Example task:

```json
{
  "title": "Finish assignment",
  "due_date": "2026-11-01",
  "priority": "high"
}
```

Due dates use `YYYY-MM-DD` or `null`; priorities are `low`, `medium`, or `high` (default). Use `PATCH /tasks/{task_id}` with `{"completed": true}` to complete a task, or `false` to reopen it. Partial updates preserve omitted fields; `{"due_date": null}` clears a deadline.

Filters can be combined:

- `/tasks?completed=false&priority=high`
- `/tasks?completed=false&due_before=2026-11-01`

`due_before` is inclusive and excludes tasks without deadlines. Results are newest first. Due dates do not trigger notifications.

Tasks and project details persist in `data/tasks.db`. Schema upgrades preserve existing tasks.

## Tests

```powershell
& '.\.venv\Scripts\python.exe' -B -m unittest -v test_tasks test_features
```

Tests use temporary databases and folders, preserving your saved data.

## Private local files

Personal profiles, SQLite databases, `.env` files, credentials, logs, backups, and virtual environments are excluded from Git. Templates contain no account credentials or personal records.

This API is intended for one user on their own computer. It does not implement API authentication. Keep it bound to `127.0.0.1` rather than exposing it to a network or public host.

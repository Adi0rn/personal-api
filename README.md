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

## Spotify connection and playback

The Spotify integration uses Authorization Code with PKCE, with automatic token refresh. It needs a **Client ID**, not a Client Secret.

1. Create or select an app at [Spotify Developer Dashboard](https://developer.spotify.com/dashboard).
2. Add `http://127.0.0.1:8000/spotify/callback` as a redirect URI, keeping any other callbacks your app uses.
3. On first setup, copy `.env.example` to `.env`, then set `SPOTIFY_CLIENT_ID` to your app's Client ID. Preserve an existing `.env`. Changes are read without a restart.
4. Open [Connect Spotify](http://127.0.0.1:8000/connect/spotify) directly in your browser and approve access. Do not use the docs Execute button for sign-in.
5. Check [connection status](http://127.0.0.1:8000/spotify/status). `connected` means credentials are saved; `playback_ready` means all requested playback permissions are present. Neither guarantees an active device or Premium eligibility.

Spotify development-mode eligibility, allowed-user limits, and Premium requirements are governed by [Spotify's current rules](https://developer.spotify.com/documentation/web-api/concepts/quota-modes). Play/pause requires [Spotify Premium](https://developer.spotify.com/documentation/web-api/reference/start-a-users-playback).

| Route | Purpose |
| --- | --- |
| `GET /connect/spotify` | Browser sign-in |
| `GET /spotify/status` | Connection and playback permission status |
| `GET /spotify/profile` | Account identity |
| `GET /spotify/playlists` | Paginated playlists (`limit`, `offset`, and `next_offset`) |
| `GET /spotify/currently-playing` | Current track or episode and progress |
| `GET /spotify/devices` | Available devices, activity, and restrictions |
| `POST /spotify/play` | Resume or play a selected playlist |
| `POST /spotify/pause` | Pause |
| `DELETE /spotify/connection` | Forget credentials locally |

Use the docs to send POST requests. An empty body or `{}` resumes or pauses on the active device. To play a playlist:

```json
{"playlist": "https://open.spotify.com/playlist/YOUR_PLAYLIST_ID"}
```

A 22-character playlist ID or `spotify:playlist:ID` also works. Include `device_id` to target a controllable device from `/spotify/devices`; otherwise Spotify uses the active device. Open Spotify and start a song if no active device is available. Commands return `status: requested`; check currently playing to verify the outcome. The API controls an existing Spotify client; it does not stream audio.

Requested scopes: `user-read-private`, `playlist-read-private`, `user-read-currently-playing`, `user-read-playback-state`, and `user-modify-playback-state`. Reconnect if playback permissions are missing.

Tokens are encrypted with Windows DPAPI for the Windows user running the server and saved in the ignored `data/spotify_tokens.bin` file. Moving the credentials to another Windows account or computer may require reconnecting. Disconnecting removes local credentials; revoke the grant in Spotify account settings to remove access at Spotify.

Run all checks:

```powershell
& '.\.venv\Scripts\python.exe' -B -m unittest -v test_tasks test_features test_spotify test_spotify_player
```

Spotify tests use fake credentials, simulated responses, and temporary storage. Playback checks never change real devices. Regression coverage includes successful plain-text Spotify command acknowledgements.

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

## Dashboard

Open http://127.0.0.1:8000/dashboard while the API is running. The dashboard uses the same profile, tasks, project details, and Spotify connection as the API.

- Create tasks, set due dates and priorities, edit them, and complete or reopen them.
- Edit project descriptions, status, and next steps.
- Read Spotify playback, choose a device or playlist, and send play/pause commands.
- Type supported assistant commands into the command bar. Press / to focus it.

The sidebar switches between Overview, My tasks, Projects, and Music. Data refreshes while the page is visible; errors in one service do not prevent the other sections from loading. Music controls send requests only when explicitly clicked or commanded.

## Voice assistant integration

The distributable adapter and installation instructions are in [integrations/voice-assistant](integrations/voice-assistant/README.md).

The companion voice-assistant project uses a loopback-only HTTP adapter (`personal_api_client.py`) in its existing `PersonalCommands` handler, shared by its overlay and terminal. Restart an already-open overlay once after updating it. Existing microphone, transcription, voice, hotkey, notes, shortcuts, and Gemini settings are preserved.

Supported examples:

- Show my tasks / Show high priority tasks
- Add task Buy milk
- Complete task 3
- Show my projects
- Project details for Personal API / Next steps for Personal API
- What song is playing?
- Pause my music / Resume my music
- Play playlist Heart and soul (exact playlist name, ID, URI, or link)
- Open dashboard (opens the page from the voice assistant)

The shared API route is `POST /assistant/command` with `{"text":"show my tasks"}`. Only recognized explicit commands execute actions. Unknown commands return handled=false. In the companion assistant, unrelated questions continue to Gemini; failures in recognized API commands are reported and do not fall back to the model. Due dates and priorities can be edited in the dashboard; the basic voice add-task command treats its remaining text as the task title.

The companion assistant defaults to `PERSONAL_API_URL=http://127.0.0.1:8000`; it does not need Spotify credentials. The API must be running. It shares data through the API, not by opening its database files. The companion's existing ElevenLabs speech services still process voice input and spoken replies; no cloud LLM is used to execute these commands.

Run dashboard and API checks:

```powershell
& '.\.venv\Scripts\python.exe' -B -m unittest -v test_tasks test_features test_spotify test_spotify_player test_dashboard
```

## Calendar, Gmail, and GitHub connections

Open http://127.0.0.1:8000/connections/setup for exact registration steps and local credential forms. The dashboard's Connections tab displays upcoming primary-calendar events, unread inbox subjects/senders, and recently updated repositories. These integrations are read-only.

Google uses one web OAuth client for Calendar and Gmail. Enable Google Calendar API and Gmail API, configure the consent screen and your test user, add the requested read-only scopes, and register this callback exactly:

```text
http://127.0.0.1:8000/connections/google/callback
```

Enter the client ID and client secret only on PAPI's local setup page, then authorize Google. Requested scopes: calendar.events.readonly and gmail.readonly. Google may impose verification, organization, or testing restrictions; provider authorization must be completed by the user. Expired or revoked refresh tokens require reconnecting.

GitHub uses a fine-grained personal access token. Choose only the repositories you intend to share, leave optional permissions unset, and keep Metadata read-only. Paste it only into the local setup page. The initial features read repository metadata and public event activity; they do not need code write, mail, calendar write, or message-send permissions. Token expiration and provider policy still apply.

Credentials and grants are encrypted with Windows DPAPI under the ignored data/connections directory. No credential values are returned by status endpoints. The app exposes no message bodies or attachments, does not mark mail read, and makes no email/calendar/repository modifications. Disconnecting forgets local tokens; revoke authorization at the provider to revoke remote grants. Keep the server on loopback; it remains a single-user local application.

Routes:

- GET /connections/status
- GET /connections/setup
- POST /connections/google/config (client_id and client_secret)
- GET /connect/google (browser authorization)
- POST /connections/github/config (fine-grained token)
- DELETE /connections/google or /connections/github
- GET /calendar/events?days=7&limit=10 (primary calendar, upcoming interval)
- GET /gmail/messages?unread=true&limit=5 (inbox subjects, senders, dates, and unread flags)
- GET /github/profile
- GET /github/repos?limit=10
- GET /github/activity?limit=10 (public activity)

Voice and dashboard commands: show my calendar; show unread emails; show my emails; show my repositories; show my github activity. Restart an already-open voice overlay to load new supported phrases. Mail commands read subject/sender summaries, not full message bodies. Calendar commands show upcoming events over seven days rather than a local-calendar-day agenda.

Verification:

```powershell
& '.\.venv\Scripts\python.exe' -B -m unittest -v test_tasks test_features test_spotify test_spotify_player test_dashboard test_connections
```

Account operations are mocked in these checks; passing tests does not imply live accounts have been authorized. Setup credentials and user consent are still required.

## Screen time

The dashboard's Screen time card and GET /stats/screen-time show aggregate active computer use in the Windows session running PAPI. Tracking begins when the updated server starts; earlier usage cannot be reconstructed. The server must remain running (one worker). The dashboard can be closed.

The tracker samples Windows input-idle and desktop-lock state every 15 seconds. After 5 minutes without input it stops counting. Locked intervals and long sample gaps such as sleep, shutdown, or server downtime are excluded conservatively. This is an approximate active-use metric, not Windows' historical screen-on total; passive video/music may become idle after five minutes.

Only daily active seconds and tracking metadata are saved to ignored data/screentime.db. No keystrokes, app names, window titles, screenshots, websites, or activity contents are collected. Totals survive restarts and are split at local midnight using the profile timezone chosen on first tracking start. The API also returns seven daily totals and the tracking start time. PAPI_DISABLE_SCREEN_TIME=1 disables the background sampler; automated server fixtures use it so tests never add to personal totals.

Run screen-time checks with:

```powershell
& '.\.venv\Scripts\python.exe' -B -m unittest -v test_screen_time
```

## Windows launcher and sign-in startup

Run `start-papi.ps1` from the cloned project to start the local server and open the
dashboard. Use `-NoBrowser` to start only the server. The launcher reuses a healthy
server and keeps Uvicorn bound to loopback. Its logs remain ignored by Git.

For automatic startup at Windows sign-in, place a shortcut in your user's Startup
folder targeting Windows PowerShell with `-NoProfile -WindowStyle Hidden -File`
followed by the quoted absolute path to `start-papi.ps1`, then `-NoBrowser`.
Startup and desktop shortcuts belong to your Windows account and are not bundled
in the repository. The Python virtual environment must already be installed.

## Daily briefing

`GET /briefing` combines open tasks (including overdue and due-today counts),
remaining primary-calendar events until local midnight, the unread inbox message
count, and up to five recent public GitHub events. GitHub activity is recent,
not restricted to today. Calendar includes ongoing and all-day events; up to
five are displayed, with an indicator when more are available.

The profile timezone determines the date and midnight boundary, including daylight
saving changes. Missing or invalid profile timezones use Asia/Kolkata and report
`timezone_fallback: true`. Task previews show up to five per group; counts cover
all unfinished tasks. Gmail reads only inbox label statistics for this feature.

The overview dashboard loads the briefing and refreshes it every five minutes
while visible. Its Refresh button updates it immediately. Say or type
"Give me my daily briefing" or "What's my day looking like?". Restart an already
running voice overlay to load the new phrases.

Each section reports its own status. Unavailable or unauthorized services are
identified instead of being reported as empty; other sections remain usable.
Briefings use existing read-only connections, require no additional credentials,
perform no actions, use no model, and are not saved to disk. The endpoint returns
`Cache-Control: no-store`.

Checks: `python -B -m unittest -v test_briefing` (temporary data and mocked accounts).

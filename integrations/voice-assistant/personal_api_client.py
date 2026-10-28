"""Loopback-only adapter for explicit Personal API commands. No cloud LLM calls."""

import os
import re
import time
from urllib.parse import urlsplit
import webbrowser


class PersonalAPIError(Exception):
    pass


EXACT = {
    "give me my daily briefing", "daily briefing", "show my briefing", "show my daily briefing", "give me my briefing", "what's my day looking like", "what is my day looking like",
    "show my calendar", "show calendar", "what's on my calendar", "what is on my calendar", "show upcoming events",
    "show my emails", "show my gmail", "show unread emails", "read my unread emails", "read my emails",
    "show my repositories", "show my github repos", "list my repositories", "show my github repositories",
    "show github activity", "show my github activity",
    "show my tasks", "list my tasks", "what are my tasks", "what do i need to do",
    "show high priority tasks", "what are my high priority tasks",
    "show my projects", "list my projects", "what are my projects",
    "what's playing", "what is playing", "what song is playing", "what's playing on spotify", "what is playing on spotify",
    "pause music", "pause my music", "pause spotify", "resume music", "resume my music", "resume spotify", "play music", "play my music",
}
OPEN = {"open dashboard", "open my dashboard", "open personal dashboard", "open personal api"}
PATTERN = re.compile(r"(?:(?:add|create)(?: a)? task\s+.+|(?:complete|finish|mark complete) task \d+[.!?]?|(?:project details for|next steps for)\s+.+|play(?: my)? playlist\s+.+)", re.IGNORECASE)
SONG = re.compile(r"play\s+(?:(?:the\s+)?(?:song|track)\s+)?(.+?)\s+by\s+(.+)", re.IGNORECASE)


def spotify_candidate(text):
    # Transcription can quote the verb with the title: "Play Like a Tattoo" by Sade.
    value = text.strip().translate(str.maketrans({'“': '"', '”': '"'}))
    value = re.sub(r'^"(?=(?:please\s+)?(?:play|open\s+spotify)\b)', '', value, flags=re.IGNORECASE)
    value = re.sub(r'^please\s+(?=(?:play|open\s+spotify)\b)', '', value, flags=re.IGNORECASE)
    value = re.sub(r'^open\s+spotify\s+and\s+', '', value, flags=re.IGNORECASE)
    value = value.rstrip('.!?').strip().rstrip('"').strip()
    value = re.sub(r'\s+on\s+spotify[.!?]*$', '', value, flags=re.IGNORECASE)
    return value.rstrip('.!?').strip()


def song_field(value):
    value = value.strip().strip('"').strip()
    if len(value) > 1 and (value[0], value[-1]) in {("'", "'"), ('‘', '’')}:
        value = value[1:-1].strip()
    return value


def spotify_request(text):
    """Normalize explicit Spotify phrases before generic app opening can claim them."""
    candidate = spotify_candidate(text)
    song = SONG.fullmatch(candidate)
    if song and not re.match(r"play(?: my)? playlist\s", candidate, re.IGNORECASE):
        return "play " + song_field(song.group(1)) + " by " + song_field(song.group(2)), True
    normalized = candidate.casefold().replace("’", "'")
    music_controls = {"pause music", "pause my music", "pause spotify", "resume music", "resume my music", "resume spotify", "play music", "play my music"}
    if candidate != text and (normalized in music_controls or re.fullmatch(r"play(?: my)? playlist\s+.+", candidate, re.IGNORECASE)):
        return candidate, False
    return text, False


def base_url():
    value = os.getenv("PERSONAL_API_URL", "http://127.0.0.1:8000").strip().rstrip("/")
    try:
        parsed = urlsplit(value)
        valid = (parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
                 and parsed.port is not None and not parsed.username and not parsed.password
                 and not parsed.path and not parsed.query and not parsed.fragment)
    except ValueError:
        valid = False
    if not valid:
        raise PersonalAPIError("PERSONAL_API_URL must be a local address such as http://127.0.0.1:8000.")
    return value


def _start_personal_api():
    from pathlib import Path
    import subprocess
    script = Path(__file__).resolve().parent.parent / "Personal API" / "start-papi.ps1"
    if not script.is_file():
        raise PersonalAPIError("Start your Personal API server, then ask for music again.")
    try:
        subprocess.Popen(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script), "-NoBrowser"],
                         cwd=script.parent, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)
    except OSError:
        raise PersonalAPIError("Could not start your Personal API. Start it manually, then try again.") from None


def _launch_spotify(uri="spotify:"):
    if uri != "spotify:" and (not isinstance(uri, str) or not re.fullmatch(r"spotify:(?:track|playlist):[A-Za-z0-9]{22}", uri)):
        raise PersonalAPIError("PAPI returned an invalid Spotify link.")
    if os.name != "nt":
        raise PersonalAPIError("Automatic Spotify desktop launch requires Windows.")
    try:
        os.startfile(uri)
    except OSError:
        raise PersonalAPIError("Could not open the Spotify desktop app. Install it and sign in, then try again.") from None


def _desktop_devices(session, base):
    response = session.get(base + "/spotify/devices", timeout=(2, 20), allow_redirects=False)
    try:
        body = response.json()
    except ValueError:
        raise PersonalAPIError("Spotify returned an unexpected device response.") from None
    if response.status_code != 200:
        detail = body.get("detail") if isinstance(body, dict) else None
        raise PersonalAPIError(detail if isinstance(detail, str) else "Could not find your Spotify desktop player.")
    if not isinstance(body, dict) or not isinstance(body.get("devices"), list):
        raise PersonalAPIError("Spotify returned an unexpected device response.")
    return body["devices"]


def desktop_device(session, base, cancel_event=None):
    import requests
    import socket
    def cancelled():
        if cancel_event is not None and cancel_event.is_set():
            raise PersonalAPIError("Music request cancelled.")
    cancelled()
    _launch_spotify()
    wanted = (os.getenv("SPOTIFY_DESKTOP_DEVICE_NAME") or os.getenv("COMPUTERNAME") or socket.gethostname()).strip().casefold()
    deadline = time.monotonic() + 30
    started_api = False
    while True:
        cancelled()
        try:
            devices = _desktop_devices(session, base)
        except requests.ConnectionError:
            if not started_api:
                _start_personal_api(); started_api = True
            devices = []
        matches = [d for d in devices if isinstance(d, dict)
                   and str(d.get("name", "")).strip().casefold() == wanted
                   and str(d.get("type", "")).casefold() == "computer"
                   and not d.get("is_restricted") and isinstance(d.get("id"), str)
                   and 0 < len(d["id"].strip()) <= 256]
        if len(matches) == 1:
            return matches[0]["id"]
        if len(matches) > 1:
            raise PersonalAPIError("More than one Spotify computer has that name. Give this computer a unique Spotify device name.")
        if time.monotonic() >= deadline:
            raise PersonalAPIError("Spotify opened but this computer is not available yet. Sign in to the desktop app. If its device name differs from the Windows computer name, set SPOTIFY_DESKTOP_DEVICE_NAME in the assistant's environment.")
        time.sleep(0.5)


def handle_api_command(text, cancel_event=None):
    original = re.sub(r"\s+", " ", text).strip()
    original, song_request = spotify_request(original)
    normalized = original.casefold().rstrip(".!?").replace("’", "'")
    if normalized not in EXACT | OPEN and not PATTERN.fullmatch(original) and not song_request:
        return None
    base = base_url()
    if normalized in OPEN:
        try:
            opened = webbrowser.open(base + "/dashboard", new=2)
        except (OSError, webbrowser.Error):
            opened = False
        if not opened:
            raise PersonalAPIError("Could not open the dashboard. Open " + base + "/dashboard in your browser.")
        return "Opened your Personal API dashboard."
    import requests
    try:
        with requests.Session() as session:
            session.trust_env = False
            payload = {"text": original}
            music = song_request or normalized in {"resume music", "resume my music", "resume spotify", "play music", "play my music"} or bool(re.fullmatch(r"play(?: my)? playlist\s+.+", original, re.IGNORECASE))
            if music:
                payload["device_id"] = desktop_device(session, base, cancel_event)
                if cancel_event is not None and cancel_event.is_set():
                    raise PersonalAPIError("Music request cancelled.")
            if cancel_event is not None and cancel_event.is_set():
                raise PersonalAPIError("Request cancelled.")
            response = session.post(base + "/assistant/command", json=payload, timeout=(2, 75) if normalized in {"give me my daily briefing", "daily briefing", "show my briefing", "show my daily briefing", "give me my briefing", "what's my day looking like", "what is my day looking like"} else (2, 40), allow_redirects=False)
    except requests.RequestException:
        raise PersonalAPIError("Cannot reach your Personal API. Start its server, then try again.") from None
    try:
        body = response.json()
    except ValueError:
        raise PersonalAPIError("Your Personal API returned an unexpected response.") from None
    if response.status_code >= 400:
        detail = body.get("detail") if isinstance(body, dict) else None
        raise PersonalAPIError(detail if isinstance(detail, str) else "Your Personal API could not complete that command.")
    if not isinstance(body, dict) or not isinstance(body.get("handled"), bool):
        raise PersonalAPIError("Your Personal API returned an unexpected response.")
    if not body["handled"]:
        if song_request:
            raise PersonalAPIError("Your Personal API does not support individual-song playback yet. It can play playlists, pause, and resume music.")
        raise PersonalAPIError("That Personal API command is not supported yet. Try show my tasks or pause my music.")
    reply = body.get("reply")
    if not isinstance(reply, str) or not reply:
        raise PersonalAPIError("Your Personal API returned no command result.")
    if music and body.get("action") in {"song_requested", "playlist_requested"}:
        uri = body.get("track_uri") or body.get("playlist_uri")
        if uri:
            # A native URI also initializes a newly opened app's playback context.
            _launch_spotify(uri)
    return reply

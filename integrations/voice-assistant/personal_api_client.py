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


def spotify_request(text):
    """Normalize explicit Spotify phrases before generic app opening can claim them."""
    candidate = re.sub(r"^(?:please\s+)?open\s+spotify\s+and\s+", "", text, flags=re.IGNORECASE)
    candidate = re.sub(r"\s+on\s+spotify[.!?]*$", "", candidate, flags=re.IGNORECASE)
    candidate = candidate.rstrip(".!?").strip()
    song = SONG.fullmatch(candidate)
    if song and not re.match(r"play(?: my)? playlist\s", candidate, re.IGNORECASE):
        return "play " + song.group(1).strip() + " by " + song.group(2).strip(), True
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


def _dashboard_state(session, base):
    response = session.get(base + "/spotify/dashboard-player", timeout=(2, 4), allow_redirects=False)
    if response.status_code != 200:
        raise PersonalAPIError("Update or restart your Personal API to enable dashboard playback.")
    try:
        body = response.json()
    except ValueError:
        raise PersonalAPIError("Your dashboard player returned an unexpected response.") from None
    if not isinstance(body, dict) or not isinstance(body.get("ready"), bool):
        raise PersonalAPIError("Your dashboard player returned an unexpected response.")
    return body


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


def dashboard_device(session, base, cancel_event=None):
    import requests
    def cancelled():
        if cancel_event is not None and cancel_event.is_set():
            raise PersonalAPIError("Music request cancelled.")
    cancelled()
    try:
        state = _dashboard_state(session, base)
    except requests.ConnectionError:
        _start_personal_api()
        deadline = time.monotonic() + 12
        while True:
            cancelled()
            try:
                state = _dashboard_state(session, base)
                break
            except requests.ConnectionError:
                if time.monotonic() >= deadline:
                    raise PersonalAPIError("Your Personal API did not start. Check its server logs.") from None
                time.sleep(0.4)
    if not state["ready"]:
        cancelled()
        try:
            opened = webbrowser.open(base + "/dashboard?voice_music=1#music", new=2)
        except (OSError, webbrowser.Error):
            opened = False
        if not opened:
            raise PersonalAPIError("Could not open your dashboard. Open it in your browser and enable its player.")
        status = session.get(base + "/spotify/status", timeout=(2, 4), allow_redirects=False)
        if status.status_code == 200 and not status.json().get("browser_playback_ready"):
            raise PersonalAPIError("I opened your dashboard. Reconnect Spotify there to approve browser streaming access. Spotify Premium is required.")
        deadline = time.monotonic() + 35
        while not state["ready"]:
            cancelled()
            if time.monotonic() >= deadline:
                raise PersonalAPIError("Your dashboard is open. Click Enable browser player or Play here, then ask for music again.")
            time.sleep(0.4)
            state = _dashboard_state(session, base)
    cancelled()
    # A previous autoplay report may be stale after permission changed.
    # Send this explicit request once; report a fresh block after the request.
    device_id = state.get("device_id")
    if not isinstance(device_id, str) or not device_id.strip() or len(device_id) > 256:
        raise PersonalAPIError("Your dashboard player has no valid device. Enable it again in the dashboard.")
    return device_id


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
    autoplay_blocked = False
    try:
        with requests.Session() as session:
            session.trust_env = False
            payload = {"text": original}
            music = song_request or normalized in {"resume music", "resume my music", "resume spotify", "play music", "play my music"} or bool(re.fullmatch(r"play(?: my)? playlist\s+.+", original, re.IGNORECASE))
            if music:
                payload["device_id"] = dashboard_device(session, base, cancel_event)
                if cancel_event is not None and cancel_event.is_set():
                    raise PersonalAPIError("Music request cancelled.")
                # Activate this SDK device before the song/playlist command; do not
                # resume an old track or repeat the requested command as a retry.
                transfer = session.post(base + "/spotify/transfer",
                    json={"device_id": payload["device_id"], "play": False},
                    timeout=(2, 20), allow_redirects=False)
                if transfer.status_code not in (200, 202, 204):
                    raise PersonalAPIError("Could not select your dashboard player. Enable it again, then retry the command.")
            if cancel_event is not None and cancel_event.is_set():
                raise PersonalAPIError("Request cancelled.")
            response = session.post(base + "/assistant/command", json=payload, timeout=(2, 75) if normalized in {"give me my daily briefing", "daily briefing", "show my briefing", "show my daily briefing", "give me my briefing", "what's my day looking like", "what is my day looking like"} else (2, 40), allow_redirects=False)
            if music and response.status_code == 200:
                try:
                    autoplay_blocked = bool(_dashboard_state(session, base).get("autoplay_blocked"))
                except (requests.RequestException, PersonalAPIError):
                    pass  # Never repeat playback just to check its browser status.
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
    if music:
        reply = reply.replace(" on Spotify.", " in your dashboard.")
        if "dashboard" not in reply.casefold():
            reply += " Playback targets your dashboard."
        if autoplay_blocked:
            reply += " Your browser blocked audio; click Play here once or allow Autoplay for this PAPI site to avoid repeated clicks."
    return reply

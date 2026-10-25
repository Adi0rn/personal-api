"""Loopback-only adapter for explicit Personal API commands. No cloud LLM calls."""

import os
import re
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


def handle_api_command(text):
    original = re.sub(r"\s+", " ", text).strip()
    normalized = original.casefold().rstrip(".!?").replace("’", "'")
    if normalized not in EXACT | OPEN and not PATTERN.fullmatch(original):
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
            response = session.post(base + "/assistant/command", json={"text": original}, timeout=(2, 75) if normalized in {"give me my daily briefing", "daily briefing", "show my briefing", "show my daily briefing", "give me my briefing", "what's my day looking like", "what is my day looking like"} else (2, 40), allow_redirects=False)
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
        raise PersonalAPIError("That Personal API command is not supported yet. Try show my tasks or pause my music.")
    reply = body.get("reply")
    if not isinstance(reply, str) or not reply:
        raise PersonalAPIError("Your Personal API returned no command result.")
    return reply

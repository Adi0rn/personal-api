"""Explicit commands shared by the dashboard and the existing voice assistant."""

import re
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, StringConstraints, ValidationError

import projects
import tasks
import spotify_connection as spotify
import spotify_player as player
import connections
import briefing

router = APIRouter(prefix="/assistant", tags=["Assistant"], dependencies=[Depends(spotify.local_request)])


class Command(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]


def result(reply, action="read"):
    return {"handled": True, "reply": reply, "action": action}


def find_playlist(name):
    try:
        return player.PlayRequest(playlist=name)
    except ValidationError:
        pass
    spoken_name = name.rstrip(".!?")
    if spoken_name != name:
        try:
            return player.PlayRequest(playlist=spoken_name)
        except ValidationError:
            pass
    matches = []
    for offset in range(0, 1000, 50):
        page = spotify.api_get("me/playlists", limit=50, offset=offset)
        matches.extend(item for item in page.get("items", [])
                       if item and item.get("name", "").casefold() in {name.casefold(), spoken_name.casefold()})
        if not page.get("next"):
            break
    exact = [item for item in matches if item.get("name", "").casefold() == name.casefold()]
    matches = exact or matches
    if not matches:
        raise HTTPException(404, "Playlist not found by exact name. Use its Spotify link or choose it in the dashboard.")
    if len(matches) > 1:
        raise HTTPException(409, "More than one playlist has that name. Use its Spotify link or choose it in the dashboard.")
    return player.PlayRequest(playlist=matches[0]["id"])


def handle_command(text):
    original = re.sub(r"\s+", " ", text).strip()
    normalized = original.casefold().rstrip(".!?").replace("’", "'")
    if normalized in {"give me my daily briefing", "daily briefing", "show my briefing", "show my daily briefing", "give me my briefing", "what's my day looking like", "what is my day looking like"}:
        return result(briefing.build_briefing()["summary"])
    if normalized in {"show my calendar", "show calendar", "what's on my calendar", "what is on my calendar", "show upcoming events"}:
        events = connections.calendar_events(days=7, limit=5)["events"]
        if not events:
            return result("You have no upcoming events in the next seven days.")
        lines = [event["title"] + " at " + (event["start"].get("dateTime") or event["start"].get("date", "an unspecified time")) for event in events]
        return result("Upcoming calendar events: " + ". ".join(lines) + ".")
    if normalized in {"show my emails", "show my gmail", "show unread emails", "read my unread emails", "read my emails"}:
        unread = "unread" in normalized
        messages = connections.gmail_messages(limit=5, unread=unread)["messages"]
        if not messages:
            return result("No unread inbox messages." if unread else "Your inbox has no messages.")
        lines = [message["subject"] + " from " + message["from"] for message in messages]
        return result("Email subjects: " + ". ".join(lines) + ".")
    if normalized in {"show my repositories", "show my github repos", "list my repositories", "show my github repositories"}:
        repositories = connections.github_repos(limit=5)["repositories"]
        return result("Your recently updated repositories: " + ", ".join(item["name"] for item in repositories) + "." if repositories else "No repositories are available to this connection.")
    if normalized in {"show github activity", "show my github activity"}:
        events = connections.github_activity(limit=5)["events"]
        return result("Recent public GitHub activity: " + ". ".join((item["type"] or "Activity") + " in " + (item["repository"] or "a repository") for item in events) + "." if events else "No recent public GitHub activity.")
    if normalized in {"help", "commands", "what can you do"}:
        return result("Try: give me my daily briefing; show my calendar; show unread emails; show my repositories; show my github activity; show my tasks; show high priority tasks; add task Buy milk; complete task 3; show my projects; project details for Personal API; what's playing; pause my music; resume my music; or play playlist followed by its exact name or link.")
    if normalized in {"show my tasks", "list my tasks", "what are my tasks", "what do i need to do", "show high priority tasks", "what are my high priority tasks"}:
        priority = "high" if "high priority" in normalized else None
        items = tasks.list_tasks(completed=False, priority=priority)
        if not items:
            return result("You have no unfinished high priority tasks." if priority else "You have no unfinished tasks.")
        lines = [f"Task {item['id']}: {item['title']}" + (f", due {item['due_date']}" if item['due_date'] else "") for item in items[:5]]
        return result(f"You have {len(items)} unfinished {'high priority ' if priority else ''}tasks. " + ". ".join(lines) + ".")
    match = re.fullmatch(r"(?:add|create)(?: a)? task\s+(.+)", original, re.IGNORECASE)
    if match:
        task = tasks.create_task(tasks.TaskCreate(title=match.group(1)))
        return result(f"Added task {task['id']}: {task['title']}.", "task_created")
    match = re.fullmatch(r"(?:complete|finish|mark complete) task (\d+)[.!?]?", original, re.IGNORECASE)
    if match:
        task = tasks.update_task(int(match.group(1)), tasks.TaskUpdate(completed=True))
        return result(f"Completed task {task['id']}: {task['title']}.", "task_completed")
    if normalized in {"show my projects", "list my projects", "what are my projects"}:
        items = projects.list_projects()["projects"]
        if not items:
            return result("No project folders were found.")
        names = ", ".join(item.name for item in items[:8])
        return result(f"You have {len(items)} projects: {names}" + (". The dashboard shows the full list." if len(items) > 8 else "."))
    match = re.fullmatch(r"(?:project details for|next steps for)\s+(.+)", original, re.IGNORECASE)
    if match:
        name = match.group(1).rstrip(".!?")
        folder = next((folder for folder in projects.project_folders() if folder.name.casefold() == name.casefold()), None)
        if folder is None:
            raise HTTPException(404, "Project folder not found. Use its name from the dashboard.")
        project = projects.get_project(folder.name)
        description = project.description or "No description saved."
        steps = ". ".join(project.next_steps[:5]) or "No next steps saved"
        return result(f"{project.name}: {description} Next steps: {steps}.")
    if normalized in {"what's playing", "what is playing", "what song is playing", "what's playing on spotify", "what is playing on spotify"}:
        current = player.currently_playing()
        item = current.get("item")
        if not item:
            return result("Spotify has no current song or episode.")
        artists = ", ".join(item.get("artists") or [])
        return result(("Playing " if current["is_playing"] else "Paused on ") + (item.get("name") or "an untitled item") + (" by " + artists if artists else "") + ".")
    if normalized in {"pause music", "pause my music", "pause spotify"}:
        player.pause()
        return result("Sent the pause command to Spotify.", "music_paused")
    if normalized in {"resume music", "resume my music", "resume spotify", "play music", "play my music"}:
        player.play()
        return result("Sent the resume command to Spotify.", "music_resumed")
    match = re.fullmatch(r"play(?: my)? playlist\s+(.+)", original, re.IGNORECASE)
    if match:
        name = match.group(1).strip()
        player.play(find_playlist(name))
        return result("Sent the playlist playback command to Spotify.", "playlist_requested")
    return {"handled": False, "reply": "That command is not supported yet. Try show my tasks, pause my music, or help.", "action": None}


@router.post("/command")
def command(body: Command):
    """Execute only recognized explicit commands; no model or shell executes actions."""
    try:
        return handle_command(body.text)
    except ValidationError:
        raise HTTPException(422, "Check the command details. Task titles must be between 1 and 200 characters.") from None

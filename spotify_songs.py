"""Explicit title/artist search and song playback using the saved Spotify connection."""
import re
import unicodedata
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import StringConstraints

import spotify_connection as spotify
import spotify_player as player

router = APIRouter(prefix="/spotify", tags=["Spotify songs"], dependencies=[Depends(spotify.local_request)])
SongText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


class SongRequest(player.DeviceRequest):
    title: SongText
    artist: SongText


def normalized(value):
    return "".join(c for c in unicodedata.normalize("NFKD", value.casefold())
                   if c.isalnum() and not unicodedata.combining(c))


def search(title, artist):
    # Quote field values; the HTTP client encodes the query string.
    title_query = title.replace('"', " ")
    artist_query = artist.replace('"', " ")
    data = spotify.api_get("search", q=f'track:"{title_query}" artist:"{artist_query}"',
                           type="track", limit=10)
    found = []
    for item in (data.get("tracks") or {}).get("items", []):
        if not isinstance(item, dict):
            continue
        track_id = item.get("id", "")
        if (not isinstance(track_id, str) or not re.fullmatch(r"[A-Za-z0-9]{22}", track_id)
                or item.get("is_playable") is False or item.get("is_local")
                or item.get("restrictions")):
            continue
        found.append({"id": track_id, "uri": "spotify:track:" + track_id,
                      "name": item.get("name") or "",
                      "artists": [a["name"] for a in item.get("artists", [])
                                  if isinstance(a, dict) and isinstance(a.get("name"), str)]})
    return found


@router.get("/search")
def search_songs(title: str = Query(..., min_length=1, max_length=200),
                 artist: str = Query(..., min_length=1, max_length=200)):
    return {"tracks": search(title, artist)}


@router.post("/play-song")
def play_song(body: SongRequest):
    matches = [item for item in search(body.title, body.artist)
               if normalized(item["name"]) == normalized(body.title)
               and any(normalized(name) == normalized(body.artist) for name in item["artists"])]
    if not matches:
        raise HTTPException(404, "No playable song matched that title and artist. Check the spelling or use a Spotify track link with /spotify/play.")
    track = matches[0]
    player.play(player.PlayRequest(track=track["uri"], device_id=body.device_id))
    return {"status": "requested", "action": "song_requested", "track": track}


def command(text, device_id=None):
    candidate = re.sub(r"^(?:please\s+)?open\s+spotify\s+and\s+", "", text, flags=re.IGNORECASE)
    candidate = re.sub(r"\s+on\s+spotify[.!?]*$", "", candidate, flags=re.IGNORECASE).rstrip(".!?").strip()
    if re.match(r"play(?: my)? playlist\s", candidate, re.IGNORECASE):
        return None
    match = re.fullmatch(r"play\s+(?:(?:the\s+)?(?:song|track)\s+)?(.+?)\s+by\s+(.+)", candidate, re.IGNORECASE)
    if not match:
        return None
    result = play_song(SongRequest(title=match.group(1), artist=match.group(2), device_id=device_id))
    track = result["track"]
    return {"handled": True, "action": "song_requested",
            "reply": "Requested " + track["name"] + " by " + ", ".join(track["artists"]) + " on Spotify."}

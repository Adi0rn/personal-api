"""Read Spotify playback and send explicit playback commands."""

import re
from typing import Annotated
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, StringConstraints, field_validator

import spotify_connection as spotify

router = APIRouter(prefix="/spotify", tags=["Spotify playback"], dependencies=[Depends(spotify.local_request)])
DeviceId = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=256)]


class DeviceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    device_id: DeviceId | None = None


class PlayRequest(DeviceRequest):
    playlist: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)] | None = None

    @field_validator("playlist")
    @classmethod
    def playlist_uri(cls, value):
        if value is None:
            return None
        candidate = value
        if value.startswith("spotify:playlist:"):
            candidate = value.removeprefix("spotify:playlist:")
        elif value.startswith("https://"):
            url = urlsplit(value)
            if url.netloc != "open.spotify.com":
                raise ValueError("Use a Spotify playlist ID, URI, or open.spotify.com playlist URL.")
            match = re.fullmatch(r"/(?:intl-[a-zA-Z-]+/)?playlist/([A-Za-z0-9]{22})/?", url.path)
            if not match:
                raise ValueError("Use a playlist link, not an album or track link.")
            candidate = match.group(1)
        if not re.fullmatch(r"[A-Za-z0-9]{22}", candidate):
            raise ValueError("Use a valid Spotify playlist ID, URI, or URL.")
        return "spotify:playlist:" + candidate


def player_request(method, path, scope, *, params=None, payload=None):
    token = spotify.access_token()
    with spotify.LOCK:
        tokens = spotify.read_tokens() or {}
        if scope not in tokens.get("scope", "").split():
            raise HTTPException(403, "Approve playback permissions by opening /connect/spotify again.")
    kwargs = {"params": params or {}}
    if payload is not None:
        kwargs["json"] = payload
    url = "https://api.spotify.com/v1/me/player/" + path
    response = spotify.spotify_request(method, url, headers={"Authorization": "Bearer " + token}, **kwargs)
    if response.status_code == 401:
        token = spotify.access_token(force_refresh=True)
        response = spotify.spotify_request(method, url, headers={"Authorization": "Bearer " + token}, **kwargs)
        if response.status_code == 401:
            raise HTTPException(401, "Spotify authorization expired. Connect again.")
    # Player commands may return a non-JSON acknowledgement on successful 2xx responses.
    if method == "PUT" and 200 <= response.status_code < 300:
        return None
    if response.status_code == 204:
        return None
    if response.status_code == 404:
        raise HTTPException(409, "No available Spotify device. Open Spotify and start a song, then try again or choose a device from /spotify/devices.")
    if response.status_code == 403:
        raise HTTPException(403, "Spotify refused playback access. Check Premium, the selected device's restrictions, and playback permissions.")
    return spotify.check_response(response)


@router.get("/currently-playing")
def currently_playing():
    data = player_request("GET", "currently-playing", "user-read-currently-playing", params={"additional_types": "track,episode"})
    if data is None:
        return {"is_playing": False, "item": None, "progress_ms": None}
    item = data.get("item")
    result = None if not item else {
        "id": item.get("id"), "name": item.get("name"), "type": item.get("type"),
        "artists": [artist.get("name") for artist in item.get("artists", [])],
        "duration_ms": item.get("duration_ms"), "url": (item.get("external_urls") or {}).get("spotify"),
    }
    return {"is_playing": bool(data.get("is_playing")), "item": result, "progress_ms": data.get("progress_ms")}


@router.get("/devices")
def devices():
    data = player_request("GET", "devices", "user-read-playback-state") or {}
    return {"devices": [{"id": device.get("id"), "name": device.get("name"), "type": device.get("type"),
                         "is_active": bool(device.get("is_active")), "is_restricted": bool(device.get("is_restricted"))}
                        for device in data.get("devices", [])]}


@router.post("/play")
def play(body: PlayRequest | None = None):
    """Resume with no body or {}, or play a playlist by ID, URI, or URL."""
    body = body or PlayRequest()
    params = {"device_id": body.device_id} if body.device_id else {}
    payload = {"context_uri": body.playlist} if body.playlist else {}
    player_request("PUT", "play", "user-modify-playback-state", params=params, payload=payload)
    return {"status": "requested", "action": "play", "playlist_uri": body.playlist, "device_id": body.device_id}


@router.post("/pause")
def pause(body: DeviceRequest | None = None):
    body = body or DeviceRequest()
    params = {"device_id": body.device_id} if body.device_id else {}
    player_request("PUT", "pause", "user-modify-playback-state", params=params)
    return {"status": "requested", "action": "pause", "device_id": body.device_id}

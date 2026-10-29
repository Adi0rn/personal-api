"""Read Spotify playback and send explicit playback commands."""

import re
import os
import socket
from typing import Annotated
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, StringConstraints, StrictBool, field_validator, model_validator

import spotify_connection as spotify

router = APIRouter(prefix="/spotify", tags=["Spotify playback"], dependencies=[Depends(spotify.local_request)])
DeviceId = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=256)]


class DeviceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    device_id: DeviceId | None = None


class TransferRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    device_id: DeviceId
    play: StrictBool = False


class PlayRequest(DeviceRequest):
    track: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)] | None = None

    @field_validator("track")
    @classmethod
    def track_uri(cls, value):
        if value is None:
            return None
        candidate = value
        if value.startswith("spotify:track:"):
            candidate = value.removeprefix("spotify:track:")
        elif value.startswith("https://"):
            url = urlsplit(value)
            match = re.fullmatch(r"/(?:intl-[a-zA-Z-]+/)?track/([A-Za-z0-9]{22})/?", url.path)
            if url.netloc != "open.spotify.com" or not match:
                raise ValueError("Use a Spotify track link.")
            candidate = match.group(1)
        if not re.fullmatch(r"[A-Za-z0-9]{22}", candidate):
            raise ValueError("Use a valid Spotify track ID, URI, or URL.")
        return "spotify:track:" + candidate

    @model_validator(mode="after")
    def one_playback_target(self):
        if self.track and self.playlist:
            raise ValueError("Choose a track or a playlist, not both.")
        return self

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
    url = "https://api.spotify.com/v1/me/player" + ("/" + path if path else "")
    response = spotify.spotify_request(method, url, headers={"Authorization": "Bearer " + token}, **kwargs)
    if response.status_code == 401:
        token = spotify.access_token(force_refresh=True)
        response = spotify.spotify_request(method, url, headers={"Authorization": "Bearer " + token}, **kwargs)
        if response.status_code == 401:
            raise HTTPException(401, "Spotify authorization expired. Connect again.")
    # Player commands may return a non-JSON acknowledgement on successful 2xx responses.
    if method in {"PUT", "POST"} and 200 <= response.status_code < 300:
        return None
    if response.status_code == 204:
        return None
    if response.status_code == 404:
        raise HTTPException(409, "No available Spotify device. Open Spotify and start a song, then try again or choose a device from /spotify/devices.")
    if response.status_code == 403:
        raise HTTPException(403, "Spotify refused playback access. Check Premium, the selected device's restrictions, and playback permissions.")
    return spotify.check_response(response)


def artwork_url(item):
    """Use Spotify-hosted album or episode art without fetching the image here."""
    images = ((item.get("album") or {}).get("images") if item.get("type") == "track"
              else item.get("images") or (item.get("show") or {}).get("images")) or []
    for image in images:
        if not isinstance(image, dict) or not isinstance(image.get("url"), str):
            continue
        url = urlsplit(image["url"])
        if (url.scheme == "https" and url.hostname in {
                "i.scdn.co", "mosaic.scdn.co", "image-cdn-ak.spotifycdn.com", "image-cdn-fa.spotifycdn.com"}
                and not url.username and not url.password and url.port in {None, 443}):
            return image["url"]
    return None


@router.get("/currently-playing")
def currently_playing():
    data = player_request("GET", "currently-playing", "user-read-currently-playing", params={"additional_types": "track,episode"})
    if data is None:
        return {"is_playing": False, "item": None, "progress_ms": None}
    item = data.get("item")
    result = None if not item else {
        "id": item.get("id"), "name": item.get("name"), "type": item.get("type"),
        "artists": [artist.get("name") for artist in item.get("artists", [])],
        "image_url": artwork_url(item),
        "duration_ms": item.get("duration_ms"), "url": (item.get("external_urls") or {}).get("spotify"),
    }
    return {"is_playing": bool(data.get("is_playing")), "item": result, "progress_ms": data.get("progress_ms")}


@router.get("/devices")
def devices():
    data = player_request("GET", "devices", "user-read-playback-state") or {}
    return {"devices": [{"id": device.get("id"), "name": device.get("name"), "type": device.get("type"),
                         "is_local": str(device.get("name", "")).casefold() == (os.getenv("SPOTIFY_DESKTOP_DEVICE_NAME") or os.getenv("COMPUTERNAME") or socket.gethostname()).casefold(),
                         "is_active": bool(device.get("is_active")), "is_restricted": bool(device.get("is_restricted"))}
                        for device in data.get("devices", [])]}


@router.post("/transfer")
def transfer(body: TransferRequest):
    player_request("PUT", "", "user-modify-playback-state",
                   payload={"device_ids": [body.device_id], "play": body.play})
    return {"status": "requested", "action": "transfer", "device_id": body.device_id}


@router.post("/play")
def play(body: PlayRequest | None = None):
    """Resume with no body or {}, or play one track or playlist by ID, URI, or URL."""
    body = body or PlayRequest()
    params = {"device_id": body.device_id} if body.device_id else {}
    payload = {"uris": [body.track]} if body.track else ({"context_uri": body.playlist} if body.playlist else {})
    player_request("PUT", "play", "user-modify-playback-state", params=params, payload=payload)
    return {"status": "requested", "action": "play", "playlist_uri": body.playlist, "track_uri": body.track, "device_id": body.device_id}


@router.post("/next")
def next_track(body: DeviceRequest | None = None):
    body = body or DeviceRequest()
    player_request("POST", "next", "user-modify-playback-state",
                   params={"device_id": body.device_id} if body.device_id else {})
    return {"status": "requested", "action": "next", "device_id": body.device_id}


@router.post("/previous")
def previous_track(body: DeviceRequest | None = None):
    body = body or DeviceRequest()
    player_request("POST", "previous", "user-modify-playback-state",
                   params={"device_id": body.device_id} if body.device_id else {})
    return {"status": "requested", "action": "previous", "device_id": body.device_id}


@router.post("/pause")
def pause(body: DeviceRequest | None = None):
    body = body or DeviceRequest()
    params = {"device_id": body.device_id} if body.device_id else {}
    player_request("PUT", "pause", "user-modify-playback-state", params=params)
    return {"status": "requested", "action": "pause", "device_id": body.device_id}

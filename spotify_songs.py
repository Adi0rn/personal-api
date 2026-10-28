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
    return "".join(c for c in unicodedata.normalize("NFKD", value.casefold().replace("&", " and "))
                   if c.isalnum() and not unicodedata.combining(c))


def playable_tracks(data):
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


def title_and_artist_match(item, title, artist):
    return (normalized(item["name"]) == normalized(title)
            and any(normalized(name) == normalized(artist) for name in item["artists"]))


def search(title, artist):
    title_query = title.replace('"', ' ')
    artist_query = artist.replace('"', ' ')
    strict = f'track:"{title_query}" artist:"{artist_query}"'
    found = playable_tracks(spotify.api_get('search', q=strict, type='track', limit=10))
    if any(title_and_artist_match(item, title, artist) for item in found):
        return found
    # Phrase searches can miss spoken "and"/"&" and joined words. Search a
    # title keyword, then validate the full normalized title AND artist below.
    words = re.findall(r"[^\W_]+", title_query, flags=re.UNICODE)
    keyword = next((word for word in words if word.casefold() not in {'the', 'a', 'an', 'and', 'of', 'to', 'in', 'on', 'by'}), None)
    fallback_keywords = [keyword] if keyword and len(words) > 1 else []
    digits = normalized(title)
    if digits.isdecimal() and 2 <= len(digits) <= 8:
        # Spoken punctuation-free numbers (3435) may be catalogued as 34+35,
        # 34/35, etc. Discover candidates by numeric prefix, never by a
        # hard-coded song alias. Full title/artist validation still applies.
        numeric_prefix = digits[:max(1, len(digits)//2)]
        if numeric_prefix not in fallback_keywords:
            fallback_keywords.append(numeric_prefix)
    for fallback in fallback_keywords:
        loose = f'track:"{fallback}" artist:"{artist_query}"'
        if loose != strict:
            extra = playable_tracks(spotify.api_get('search', q=loose, type='track', limit=10))
            seen = {item['id'] for item in found}
            for item in extra:
                if item['id'] not in seen:
                    found.append(item); seen.add(item['id'])
            if any(title_and_artist_match(item, title, artist) for item in found):
                break
    return found


@router.get("/search")
def search_songs(title: str = Query(..., min_length=1, max_length=200),
                 artist: str = Query(..., min_length=1, max_length=200)):
    return {"tracks": search(title, artist)}


@router.post("/play-song")
def play_song(body: SongRequest):
    matches = [item for item in search(body.title, body.artist)
               if title_and_artist_match(item, body.title, body.artist)]
    if not matches:
        raise HTTPException(404, "I couldn't find a playable match for that song and artist. Try saying the title and artist again.")
    track = matches[0]
    player.play(player.PlayRequest(track=track["uri"], device_id=body.device_id))
    return {"status": "requested", "action": "song_requested", "track": track}


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


def command(text, device_id=None):
    candidate = spotify_candidate(text)
    if re.match(r"play(?: my)? playlist\s", candidate, re.IGNORECASE):
        return None
    match = re.fullmatch(r"play\s+(?:(?:the\s+)?(?:song|track)\s+)?(.+?)\s+by\s+(.+)", candidate, re.IGNORECASE)
    if not match:
        return None
    result = play_song(SongRequest(title=song_field(match.group(1)), artist=song_field(match.group(2)), device_id=device_id))
    track = result["track"]
    return {"handled": True, "action": "song_requested", "track_uri": track["uri"],
            "reply": "Requested " + track["name"] + " by " + ", ".join(track["artists"]) + " on Spotify."}

"""Spotify sign-in with PKCE, automatic token refresh, and account endpoints."""

import base64
import hashlib
import os
from pathlib import Path
import re
import secrets
from threading import RLock
import time
from urllib.parse import urlencode

from dotenv import dotenv_values
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
import httpx

import spotify_store

ENV_FILE = Path(__file__).resolve().parent / ".env"
REDIRECT_URI = "http://127.0.0.1:8000/spotify/callback"
PLAYBACK_SCOPES = {"user-read-currently-playing", "user-read-playback-state", "user-modify-playback-state"}
SCOPES = "user-read-private playlist-read-private " + " ".join(sorted(PLAYBACK_SCOPES))
COOKIE = "spotify_oauth_state"
LOCK = RLock()
PENDING = {}


def local_request(request: Request):
    if request.url.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise HTTPException(403, "Spotify endpoints are available only on this computer.")
    origin = request.headers.get("origin")
    if origin and origin != str(request.base_url).rstrip("/"):
        raise HTTPException(403, "Open the API directly to use Spotify.")


router = APIRouter(tags=["Spotify"], dependencies=[Depends(local_request)])


def client_id(required=True):
    value = (os.environ.get("SPOTIFY_CLIENT_ID") or dotenv_values(ENV_FILE).get("SPOTIFY_CLIENT_ID") or "").strip()
    if not re.fullmatch(r"[0-9a-fA-F]{32}", value):
        if required:
            raise HTTPException(503, "Add your Spotify Client ID to .env, then open /connect/spotify again.")
        return None
    return value


def read_tokens():
    try:
        return spotify_store.load_tokens()
    except (OSError, ValueError):
        raise HTTPException(500, "Cannot read saved Spotify credentials. Disconnect and sign in again.") from None


def write_tokens(tokens):
    try:
        spotify_store.save_tokens(tokens)
    except OSError:
        raise HTTPException(500, "Cannot save Spotify credentials on this computer.") from None


def spotify_request(method, url, **kwargs):
    try:
        with httpx.Client(timeout=15, trust_env=False) as client:
            return client.request(method, url, **kwargs)
    except httpx.RequestError:
        raise HTTPException(502, "Cannot reach Spotify. Check your internet connection and try again.") from None


def check_response(response):
    if response.status_code == 429:
        retry = response.headers.get("Retry-After", "")
        headers = {"Retry-After": retry} if retry.isdigit() else None
        raise HTTPException(429, "Spotify rate limit reached. Try again later.", headers=headers)
    if response.status_code == 403:
        raise HTTPException(403, "Spotify refused access. Check your app's allowed users, permissions, and Premium eligibility.")
    if response.status_code >= 400:
        raise HTTPException(502, "Spotify could not complete the request. Check your app settings and try connecting again.")
    try:
        result = response.json()
        if not isinstance(result, dict):
            raise ValueError()
        return result
    except ValueError:
        raise HTTPException(502, "Spotify returned an unexpected response.") from None


def exchange_token(payload, previous=None):
    response = spotify_request("POST", "https://accounts.spotify.com/api/token", data=payload)
    if response.status_code in (400, 401):
        try:
            invalid = response.json().get("error") == "invalid_grant"
        except ValueError:
            invalid = False
        if invalid:
            if previous is not None:
                spotify_store.delete_tokens()
            raise HTTPException(401, "Spotify sign-in expired or was revoked. Open /connect/spotify to sign in again.")
    data = check_response(response)
    previous = previous or {}
    try:
        token = data["access_token"]
        expires_in = int(data["expires_in"])
        refresh = data.get("refresh_token") or previous.get("refresh_token")
        if not isinstance(token, str) or not token or expires_in <= 0 or not refresh:
            raise ValueError()
    except (KeyError, ValueError, TypeError):
        raise HTTPException(502, "Spotify returned incomplete sign-in credentials.") from None
    result = {"access_token": token, "refresh_token": refresh, "expires_at": time.time() + expires_in,
              "scope": data.get("scope", previous.get("scope", "")), "client_id": payload["client_id"]}
    write_tokens(result)
    return result


def access_token(force_refresh=False):
    with LOCK:
        configured = client_id()
        tokens = read_tokens()
        if not tokens or tokens.get("client_id") != configured:
            raise HTTPException(401, "Spotify is not connected. Open /connect/spotify to sign in.")
        if force_refresh or tokens.get("expires_at", 0) <= time.time() + 60:
            refresh = tokens.get("refresh_token")
            if not refresh:
                raise HTTPException(401, "Open /connect/spotify to sign in again.")
            tokens = exchange_token({"grant_type": "refresh_token", "refresh_token": refresh,
                                     "client_id": configured}, tokens)
        return tokens["access_token"]


def api_get(path, **params):
    token = access_token()
    url = "https://api.spotify.com/v1/" + path
    response = spotify_request("GET", url, headers={"Authorization": "Bearer " + token}, params=params)
    if response.status_code == 401:
        token = access_token(force_refresh=True)
        response = spotify_request("GET", url, headers={"Authorization": "Bearer " + token}, params=params)
        if response.status_code == 401:
            raise HTTPException(401, "Spotify authorization expired. Connect again.")
    return check_response(response)


@router.get("/connect/spotify", response_class=HTMLResponse)
def connect_spotify():
    """Open this address in a browser to sign in (not through Try it out)."""
    configured = client_id(required=False)
    if not configured:
        return HTMLResponse('''<!doctype html><html lang="en"><meta charset="utf-8"><title>Connect Spotify</title>
        <h1>Connect Spotify</h1><ol>
        <li>Create an app at <a href="https://developer.spotify.com/dashboard">Spotify Developer Dashboard</a>.</li>
        <li>Register this redirect URI: <code>http://127.0.0.1:8000/spotify/callback</code>.</li>
        <li>In this project's <code>.env</code> file, set <code>SPOTIFY_CLIENT_ID=your_client_id</code>.
        No Client Secret is needed.</li><li>Save the file, then reload this page to sign in.</li></ol>
        <p>Spotify currently requires the app owner to have Premium. Add your account to the app's allowed users if needed.</p></html>''',
        headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})
    state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    with LOCK:
        for old in list(PENDING):
            if PENDING[old]["expires_at"] <= time.time():
                PENDING.pop(old)
        if len(PENDING) >= 100:
            raise HTTPException(429, "Too many pending sign-ins. Try again later.")
        PENDING[state] = {"verifier": verifier, "client_id": configured, "expires_at": time.time() + 600}
    parameters = {"response_type": "code", "client_id": configured, "redirect_uri": REDIRECT_URI,
                  "state": state, "scope": SCOPES, "code_challenge_method": "S256", "code_challenge": challenge}
    response = RedirectResponse("https://accounts.spotify.com/authorize?" + urlencode(parameters), status_code=303)
    response.set_cookie(COOKIE, state, max_age=600, httponly=True, samesite="lax", path="/spotify/callback")
    response.headers["Cache-Control"] = "no-store"
    return response


@router.get("/spotify/callback", include_in_schema=False)
def spotify_callback(request: Request, state: str = "", code: str = "", error: str = ""):
    browser_state = request.cookies.get(COOKIE, "")
    if not state or not browser_state or not secrets.compare_digest(state, browser_state):
        raise HTTPException(400, "Invalid Spotify sign-in session. Start again at /connect/spotify.")
    with LOCK:
        pending = PENDING.pop(state, None)
        if not pending or pending["expires_at"] <= time.time():
            raise HTTPException(400, "Spotify sign-in session expired. Start again.")
        if error:
            raise HTTPException(400, "Spotify access was not granted. You can try connecting again.")
        if not code:
            raise HTTPException(400, "Spotify did not return an authorization code.")
        if pending["client_id"] != client_id():
            raise HTTPException(400, "Spotify settings changed during sign-in. Start again.")
        exchange_token({"grant_type": "authorization_code", "client_id": pending["client_id"],
                        "redirect_uri": REDIRECT_URI, "code": code, "code_verifier": pending["verifier"]})
    response = RedirectResponse("/spotify/status", status_code=303)
    response.delete_cookie(COOKIE, path="/spotify/callback")
    response.headers.update({"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})
    return response


@router.get("/spotify/status")
def spotify_status():
    configured = client_id(required=False)
    with LOCK:
        tokens = read_tokens()
    return {"configured": bool(configured), "connected": bool(tokens and configured and tokens.get("client_id") == configured),
            "connect_url": "/connect/spotify", "redirect_uri": REDIRECT_URI,
            "playback_ready": bool(tokens and configured and tokens.get("client_id") == configured and PLAYBACK_SCOPES.issubset(set(tokens.get("scope", "").split()))),
            "missing_playback_permissions": sorted(PLAYBACK_SCOPES - set((tokens or {}).get("scope", "").split()))}


@router.get("/spotify/profile")
def spotify_profile():
    data = api_get("me")
    return {"account_id": data.get("account_id"), "id": data.get("id"), "display_name": data.get("display_name"),
            "url": (data.get("external_urls") or {}).get("spotify")}


@router.get("/spotify/playlists")
def spotify_playlists(limit: int = Query(20, ge=1, le=50), offset: int = Query(0, ge=0, le=100000)):
    data = api_get("me/playlists", limit=limit, offset=offset)
    items = [{"id": item.get("id"), "name": item.get("name"),
              "url": (item.get("external_urls") or {}).get("spotify")}
             for item in data.get("items", []) if item is not None]
    return {"items": items, "total": data.get("total"), "limit": limit, "offset": offset,
            "next_offset": offset + limit if data.get("next") else None}


@router.delete("/spotify/connection")
def disconnect_spotify():
    """Forget saved tokens locally. Spotify account settings can revoke the grant."""
    with LOCK:
        spotify_store.delete_tokens()
        PENDING.clear()
    return {"connected": False}

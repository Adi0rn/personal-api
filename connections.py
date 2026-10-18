"""Local Google OAuth and read-only Calendar, Gmail, and GitHub integrations."""
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import RLock
import time
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict, SecretStr
import httpx
from google_auth_oauthlib.flow import Flow
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request as GoogleRequest
from google.auth.exceptions import RefreshError, TransportError

import connection_store as store
from spotify_connection import local_request

router = APIRouter(tags=["Connections"], dependencies=[Depends(local_request)])
BASE = Path(__file__).resolve().parent
CALLBACK = "http://127.0.0.1:8000/connections/google/callback"
CALENDAR_SCOPE = "https://www.googleapis.com/auth/calendar.events.readonly"
GMAIL_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
SCOPES = [CALENDAR_SCOPE, GMAIL_SCOPE]
LOCK = RLock()
PENDING = {}
COOKIE = "papi_google_state"


class GoogleConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    client_id: str
    client_secret: SecretStr


class GitHubConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: SecretStr


def remote(method, url, **kwargs):
    try:
        with httpx.Client(timeout=15, trust_env=False) as client:
            return client.request(method, url, **kwargs)
    except httpx.RequestError:
        raise HTTPException(502, "Could not reach the service. Check your internet connection.") from None


def checked(response):
    if response.status_code == 401:
        raise HTTPException(401, "This connection needs authorization again. Open Connections setup.")
    if response.status_code == 403:
        if response.headers.get("x-ratelimit-remaining") == "0":
            raise HTTPException(429, "GitHub's rate limit has been reached. Try later.")
        raise HTTPException(403, "Access was refused. Check enabled APIs, granted permissions, and allowed accounts or repositories.")
    if response.status_code == 429:
        value = response.headers.get("Retry-After", "")
        raise HTTPException(429, "The service's rate limit has been reached. Try later.", headers={"Retry-After": value} if value.isdigit() else None)
    if response.status_code >= 400:
        raise HTTPException(502, "The connected service could not complete this request.")
    try:
        data = response.json()
        if not isinstance(data, (dict, list)):
            raise ValueError()
        return data
    except ValueError:
        raise HTTPException(502, "The service returned an unexpected response.") from None


def scopes_of(tokens):
    value = (tokens or {}).get("scope", [])
    return set(value.split() if isinstance(value, str) else value)


@router.get("/connections/setup", include_in_schema=False)
def setup():
    return FileResponse(BASE / "dashboard" / "connections-setup.html", headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})


@router.get("/connections/status")
def status():
    with LOCK:
        config = store.load("google_config")
        google = store.load("google_tokens")
        github = store.load("github_tokens")
    same_client = bool(config and google and google.get("client_id") == config.get("client_id"))
    scopes = scopes_of(google) if same_client else set()
    return {"calendar": {"configured": bool(config), "connected": CALENDAR_SCOPE in scopes},
            "gmail": {"configured": bool(config), "connected": GMAIL_SCOPE in scopes},
            "github": {"connected": bool(github), "login": (github or {}).get("login")},
            "setup_url": "/connections/setup", "google_callback": CALLBACK}


@router.post("/connections/google/config")
def configure_google(body: GoogleConfig):
    client = body.client_id.strip()
    secret = body.client_secret.get_secret_value().strip()
    if not client.endswith(".apps.googleusercontent.com") or len(client) > 300 or not secret or len(secret) > 1000:
        raise HTTPException(400, "Enter the Client ID and Client Secret from your Google web OAuth client.")
    with LOCK:
        previous = store.load("google_config")
        store.save("google_config", {"client_id": client, "client_secret": secret})
        if previous and previous.get("client_id") != client:
            store.delete("google_tokens")
    return {"configured": True, "connect_url": "/connect/google"}


@router.get("/connect/google")
def connect_google():
    with LOCK:
        config = store.load("google_config")
        if not config:
            return RedirectResponse("/connections/setup", status_code=303)
        now = time.time()
        for old in list(PENDING):
            if PENDING[old]["expires"] <= now:
                PENDING.pop(old)
        if len(PENDING) >= 50:
            raise HTTPException(429, "Too many pending sign-ins. Try again later.")
        state = secrets.token_urlsafe(32)
        flow = Flow.from_client_config({"web": {**config, "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token"}}, scopes=SCOPES, state=state,
            redirect_uri=CALLBACK, autogenerate_code_verifier=True)
        url, _ = flow.authorization_url(access_type="offline", prompt="consent")
        PENDING[state] = {"flow": flow, "client_id": config["client_id"], "expires": now + 600}
    response = RedirectResponse(url, status_code=303)
    response.set_cookie(COOKIE, state, max_age=600, httponly=True, samesite="lax", path="/connections/google/callback")
    response.headers.update({"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})
    return response


def exchange_google(flow, code):
    try:
        flow.fetch_token(code=code, timeout=15)
        tokens = dict(flow.oauth2session.token)
        if not tokens.get("access_token") or not tokens.get("refresh_token"):
            raise ValueError()
        return tokens
    except Exception:
        raise HTTPException(401, "Google sign-in did not complete. Check the callback, test users, and consent settings, then reconnect.") from None


@router.get("/connections/google/callback", include_in_schema=False)
def google_callback(request: Request, state: str = "", code: str = "", error: str = ""):
    browser_state = request.cookies.get(COOKIE, "")
    if not state or not browser_state or not secrets.compare_digest(state, browser_state):
        raise HTTPException(400, "Invalid Google sign-in session. Start again from Connections setup.")
    with LOCK:
        pending = PENDING.pop(state, None)
        config = store.load("google_config")
        if not pending or pending["expires"] <= time.time() or not config or pending["client_id"] != config["client_id"]:
            raise HTTPException(400, "Google sign-in session expired or settings changed. Start again.")
        if error or not code:
            raise HTTPException(400, "Google access was not granted. No connection was saved.")
        tokens = exchange_google(pending["flow"], code)
        tokens["client_id"] = config["client_id"]
        # Store exactly what Google granted; individual cards reflect partial permission grants.
        store.save("google_tokens", tokens)
    response = RedirectResponse("/dashboard#connections", status_code=303)
    response.delete_cookie(COOKIE, path="/connections/google/callback")
    response.headers.update({"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})
    return response


def google_token(required_scope, force=False):
    with LOCK:
        config, tokens = store.load("google_config"), store.load("google_tokens")
        if not config or not tokens or config["client_id"] != tokens.get("client_id"):
            raise HTTPException(401, "Connect Google from Connections setup first.")
        if required_scope not in scopes_of(tokens):
            raise HTTPException(403, "Reconnect Google and approve access to this service.")
        if force or float(tokens.get("expires_at", 0)) <= time.time() + 60:
            credentials = Credentials(tokens["access_token"], refresh_token=tokens.get("refresh_token"),
                token_uri="https://oauth2.googleapis.com/token", client_id=config["client_id"],
                client_secret=config["client_secret"], scopes=list(scopes_of(tokens)))
            try:
                credentials.refresh(lambda *args, **kwargs: GoogleRequest()(*args, **{**kwargs, "timeout": 15}))
            except RefreshError:
                store.delete("google_tokens")
                raise HTTPException(401, "Google access expired or was revoked. Reconnect from Connections setup.") from None
            except TransportError:
                raise HTTPException(502, "Cannot reach Google to refresh the connection. Try again.") from None
            tokens.update(access_token=credentials.token,
                expires_at=credentials.expiry.replace(tzinfo=timezone.utc).timestamp(),
                refresh_token=credentials.refresh_token)
            store.save("google_tokens", tokens)
        return tokens["access_token"]


def google_get(url, scope, params=None):
    token = google_token(scope)
    response = remote("GET", url, headers={"Authorization": "Bearer " + token}, params=params or {})
    if response.status_code == 401:
        token = google_token(scope, force=True)
        response = remote("GET", url, headers={"Authorization": "Bearer " + token}, params=params or {})
    return checked(response)


@router.get("/calendar/events")
def calendar_events(days: int = Query(7, ge=1, le=90), limit: int = Query(10, ge=1, le=50)):
    now = datetime.now(timezone.utc)
    data = google_get("https://www.googleapis.com/calendar/v3/calendars/primary/events", CALENDAR_SCOPE,
        {"timeMin": now.isoformat(), "timeMax": (now + timedelta(days=days)).isoformat(),
         "singleEvents": "true", "orderBy": "startTime", "maxResults": limit})
    return {"events": [{"id": item.get("id"), "title": item.get("summary", "Untitled event"),
        "start": item.get("start", {}), "end": item.get("end", {}), "url": item.get("htmlLink")}
        for item in data.get("items", []) if item.get("status") != "cancelled"], "days": days}


@router.get("/gmail/messages")
def gmail_messages(limit: int = Query(5, ge=1, le=20), unread: bool = False):
    query = "in:inbox is:unread" if unread else "in:inbox"
    page = google_get("https://gmail.googleapis.com/gmail/v1/users/me/messages", GMAIL_SCOPE,
                      {"q": query, "maxResults": limit})
    messages = []
    for item in page.get("messages", []):
        data = google_get("https://gmail.googleapis.com/gmail/v1/users/me/messages/" + quote(item["id"], safe=""),
            GMAIL_SCOPE, {"format": "metadata", "metadataHeaders": ["Subject", "From", "Date"]})
        headers = {entry["name"].lower(): entry["value"] for entry in data.get("payload", {}).get("headers", [])}
        messages.append({"id": data.get("id"), "subject": headers.get("subject", "(No subject)"),
            "from": headers.get("from", ""), "date": headers.get("date", ""),
            "unread": "UNREAD" in data.get("labelIds", [])})
    return {"messages": messages}


def github_get(path, params=None, token=None):
    if token is None:
        saved = store.load("github_tokens")
        if not saved:
            raise HTTPException(401, "Connect GitHub from Connections setup first.")
        token = saved["access_token"]
    return checked(remote("GET", "https://api.github.com/" + path,
        headers={"Authorization": "Bearer " + token, "Accept": "application/vnd.github+json",
                 "X-GitHub-Api-Version": "2022-11-28"}, params=params or {}))


@router.post("/connections/github/config")
def configure_github(body: GitHubConfig):
    token = body.token.get_secret_value().strip()
    if not token.startswith("github_pat_") or len(token) > 512:
        raise HTTPException(400, "Use a fine-grained GitHub personal access token with selected repositories and read-only metadata access.")
    user = github_get("user", token=token)
    if not user.get("login"):
        raise HTTPException(502, "GitHub returned no account identity.")
    with LOCK:
        store.save("github_tokens", {"access_token": token, "login": user["login"]})
    return {"connected": True, "login": user["login"]}


@router.get("/github/profile")
def github_profile():
    user = github_get("user")
    return {"login": user.get("login"), "name": user.get("name"), "url": user.get("html_url")}


@router.get("/github/repos")
def github_repos(limit: int = Query(10, ge=1, le=50)):
    data = github_get("user/repos", {"per_page": limit, "sort": "updated", "visibility": "all"})
    return {"repositories": [{"name": item.get("full_name"), "description": item.get("description"),
        "private": item.get("private"), "url": item.get("html_url"), "updated_at": item.get("updated_at")}
        for item in data]}


@router.get("/github/activity")
def github_activity(limit: int = Query(10, ge=1, le=30)):
    profile = github_profile()
    events = github_get("users/" + quote(profile["login"], safe="") + "/events/public", {"per_page": limit})
    return {"events": [{"type": item.get("type"), "repository": item.get("repo", {}).get("name"),
                        "created_at": item.get("created_at")} for item in events], "visibility": "public"}


@router.delete("/connections/{provider}")
def disconnect(provider: str):
    with LOCK:
        if provider == "google":
            store.delete("google_tokens")
            PENDING.clear()
        elif provider == "github":
            store.delete("github_tokens")
        else:
            raise HTTPException(404, "Unknown connection.")
    return {"connected": False}

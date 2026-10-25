import json
import os
import screen_time
import briefing
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from tasks import init_db, router as tasks_router
from projects import router as projects_router
from spotify_connection import router as spotify_router
from spotify_player import router as spotify_player_router
from spotify_songs import router as spotify_songs_router
from dashboard_player import router as dashboard_player_router
from assistant_bridge import router as assistant_router
from connections import router as connections_router

BASE_DIR = Path(__file__).resolve().parent
PROFILE_FILE = BASE_DIR / "data" / "profile.json"

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    track_screen_time = os.getenv("PAPI_DISABLE_SCREEN_TIME") != "1"
    if track_screen_time:
        screen_time.TRACKER.start()
    try:
        yield
    finally:
        if track_screen_time:
            screen_time.TRACKER.stop()


app = FastAPI(title="Personal API", version="0.7.0", lifespan=lifespan)
app.include_router(tasks_router)
app.include_router(projects_router)
app.include_router(spotify_router)
app.include_router(spotify_player_router)
app.include_router(spotify_songs_router)
app.include_router(dashboard_player_router)
app.include_router(assistant_router)
app.include_router(connections_router)
app.include_router(screen_time.router)
app.include_router(briefing.router)


@app.get("/")
def root() -> dict[str, str]:
    return {"message": "Welcome to Adi's Personal API", "docs": "/docs"}


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/profile")
def profile() -> dict[str, Any]:
    """Read your latest profile from data/profile.json on every request."""
    try:
        data = json.loads(PROFILE_FILE.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise HTTPException(
            status_code=500,
            detail="Cannot read profile. Check that data/profile.json exists and contains valid JSON.",
        ) from exc
    if not isinstance(data, dict):
        raise HTTPException(status_code=500, detail="data/profile.json must contain a JSON object.")
    return data

app.mount("/dashboard/assets", StaticFiles(directory=BASE_DIR / "dashboard"), name="dashboard-assets")


@app.get("/dashboard", include_in_schema=False)
@app.get("/dashboard/", include_in_schema=False)
def dashboard():
    return FileResponse(BASE_DIR / "dashboard" / "index.html", headers={"Cache-Control": "no-cache"})

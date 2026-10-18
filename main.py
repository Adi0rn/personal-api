import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException

from tasks import init_db, router as tasks_router
from projects import router as projects_router

BASE_DIR = Path(__file__).resolve().parent
PROFILE_FILE = BASE_DIR / "data" / "profile.json"

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="Personal API", version="0.4.0", lifespan=lifespan)
app.include_router(tasks_router)
app.include_router(projects_router)


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

"""Live dashboard-player registration; no account secrets or persistent requests."""
from threading import RLock
import time
from fastapi import APIRouter, Depends
from pydantic import StrictBool
import spotify_connection as spotify
import spotify_player as player

router = APIRouter(prefix="/spotify", tags=["Dashboard player"], dependencies=[Depends(spotify.local_request)])
LOCK = RLock()
STATE = None
# Background browsers may throttle timers to once a minute.
TTL = 90.0


class Registration(player.DeviceRequest):
    device_id: player.DeviceId
    autoplay_blocked: StrictBool = False


@router.get("/dashboard-player")
def status():
    with LOCK:
        if STATE is None or time.monotonic() - STATE["seen"] > TTL:
            return {"ready": False, "device_id": None, "autoplay_blocked": False}
        return {"ready": True, "device_id": STATE["device_id"],
                "autoplay_blocked": STATE["autoplay_blocked"]}


@router.post("/dashboard-player")
def register(body: Registration):
    global STATE
    with LOCK:
        STATE = {"device_id": body.device_id, "autoplay_blocked": body.autoplay_blocked,
                 "seen": time.monotonic()}
    return {"registered": True}


@router.delete("/dashboard-player")
def unregister(body: Registration):
    global STATE
    with LOCK:
        if STATE and STATE["device_id"] == body.device_id:
            STATE = None
    return {"registered": False}

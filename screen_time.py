"""Local aggregate active-use time; no keystrokes, app names, or screenshots are stored."""
import ctypes
from ctypes import wintypes
from contextlib import closing
from datetime import datetime, timedelta, timezone, time as clock_time
import json
import os
from pathlib import Path
import sqlite3
from threading import Event, RLock, Thread
import time
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends
from spotify_connection import local_request

BASE = Path(__file__).resolve().parent
DB_PATH = BASE / "data" / "screentime.db"
INTERVAL = 15
IDLE_SECONDS = 300
MAX_GAP = 45
router = APIRouter(prefix="/stats", tags=["Statistics"], dependencies=[Depends(local_request)])


class LastInput(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]


class WindowsActivity:
    def __init__(self):
        if os.name != "nt":
            raise OSError("Windows session required.")
        self.user = ctypes.WinDLL("user32", use_last_error=True)
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.user.GetLastInputInfo.argtypes = [ctypes.POINTER(LastInput)]
        self.user.GetLastInputInfo.restype = wintypes.BOOL
        self.kernel.GetTickCount64.restype = ctypes.c_ulonglong
        self.user.OpenInputDesktop.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        self.user.OpenInputDesktop.restype = wintypes.HANDLE
        self.user.GetUserObjectInformationW.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
        self.user.GetUserObjectInformationW.restype = wintypes.BOOL
        self.user.CloseDesktop.argtypes = [wintypes.HANDLE]
        self.user.CloseDesktop.restype = wintypes.BOOL

    def sample(self):
        info = LastInput(ctypes.sizeof(LastInput), 0)
        if not self.user.GetLastInputInfo(ctypes.byref(info)):
            raise OSError("Input idle information is unavailable.")
        idle = ((self.kernel.GetTickCount64() - info.dwTime) & 0xFFFFFFFF) / 1000
        desktop = self.user.OpenInputDesktop(0, False, 0x0001)
        if not desktop:
            return idle, False
        try:
            name = ctypes.create_unicode_buffer(256)
            needed = wintypes.DWORD()
            if not self.user.GetUserObjectInformationW(desktop, 2, name, ctypes.sizeof(name), ctypes.byref(needed)):
                raise OSError("Desktop state is unavailable.")
            return idle, name.value.casefold() == "default"
        finally:
            self.user.CloseDesktop(desktop)


def split_days(start, end, zone):
    while start < end:
        day = start.astimezone(zone).date()
        midnight = datetime.combine(day + timedelta(days=1), clock_time.min, tzinfo=zone).astimezone(timezone.utc)
        stop = min(end, midnight)
        yield day.isoformat(), (stop - start).total_seconds()
        start = stop


class Tracker:
    def __init__(self, path=None, zone=None):
        self.path = path
        self.zone_name = zone
        self.lock = RLock()
        self.event = Event()
        self.thread = None
        self.previous = None
        self.state = "starting"
        self.sampled_at = None

    def init(self):
        path = self.path or DB_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        requested = self.zone_name
        if not requested:
            try:
                requested = json.loads((BASE / "data" / "profile.json").read_text(encoding="utf-8-sig")).get("timezone", "Asia/Kolkata")
                ZoneInfo(requested)
            except (OSError, ValueError, TypeError, KeyError):
                requested = "Asia/Kolkata"
        with closing(sqlite3.connect(path)) as connection, connection:
            connection.execute("CREATE TABLE IF NOT EXISTS daily (day TEXT PRIMARY KEY, active_seconds REAL NOT NULL DEFAULT 0)")
            connection.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            connection.execute("INSERT OR IGNORE INTO meta VALUES ('tracking_since', ?)", (datetime.now(timezone.utc).isoformat(),))
            connection.execute("INSERT OR IGNORE INTO meta VALUES ('timezone', ?)", (requested,))
            self.zone_name = connection.execute("SELECT value FROM meta WHERE key='timezone'").fetchone()[0]
        self.zone = ZoneInfo(self.zone_name)

    def consume(self, now, monotonic, idle, unlocked):
        with self.lock:
            previous = self.previous
            self.previous = (now, monotonic, unlocked)
            self.sampled_at = now.isoformat()
            self.state = "locked" if not unlocked else "idle" if idle >= IDLE_SECONDS else "active"
            if previous is None:
                return
            start, old_monotonic, was_unlocked = previous
            elapsed = monotonic - old_monotonic
            if not unlocked or not was_unlocked or not 0 < elapsed <= MAX_GAP:
                return
            if abs((now - start).total_seconds() - elapsed) > 2:
                return
            # If the idle threshold was crossed during this interval, only count its active beginning.
            active = min(elapsed, max(0, IDLE_SECONDS - idle + elapsed))
            if active <= 0:
                return
            with closing(sqlite3.connect(self.path or DB_PATH, timeout=5)) as connection, connection:
                for day, seconds in split_days(start, start + timedelta(seconds=active), self.zone):
                    connection.execute("INSERT INTO daily VALUES (?, ?) ON CONFLICT(day) DO UPDATE SET active_seconds=active_seconds+excluded.active_seconds", (day, seconds))

    def run(self):
        try:
            activity = WindowsActivity()
        except OSError:
            self.state = "unavailable"
            return
        while not self.event.is_set():
            try:
                idle, unlocked = activity.sample()
                self.consume(datetime.now(timezone.utc), time.monotonic(), idle, unlocked)
            except (OSError, sqlite3.Error):
                with self.lock:
                    self.state = "unavailable"
                    self.previous = None
            if self.event.wait(INTERVAL):
                break

    def start(self):
        with self.lock:
            if self.thread and self.thread.is_alive():
                return
            self.init()
            self.previous = None
            self.event.clear()
            self.thread = Thread(target=self.run, name="papi-screen-time", daemon=True)
            self.thread.start()

    def stop(self):
        self.event.set()
        if self.thread:
            self.thread.join(timeout=3)
        self.state = "stopped"

    def summary(self, now=None):
        with self.lock:
            if not hasattr(self, "zone"):
                self.init()
            day = (now or datetime.now(timezone.utc)).astimezone(self.zone).date()
            dates = [(day - timedelta(days=offset)).isoformat() for offset in range(6, -1, -1)]
            with closing(sqlite3.connect(self.path or DB_PATH, timeout=5)) as connection:
                rows = dict(connection.execute("SELECT day, active_seconds FROM daily WHERE day BETWEEN ? AND ?", (dates[0], dates[-1])))
                since = connection.execute("SELECT value FROM meta WHERE key='tracking_since'").fetchone()[0]
            return {"date": day.isoformat(), "timezone": self.zone_name,
                    "active_seconds": round(rows.get(dates[-1], 0), 1),
                    "last_7_days": [{"date": item, "active_seconds": round(rows.get(item, 0), 1)} for item in dates],
                    "status": self.state, "tracking_since": since, "sampled_at": self.sampled_at,
                    "idle_after_seconds": IDLE_SECONDS, "sample_interval_seconds": INTERVAL,
                    "scope": "Active use in this Windows session while PAPI is running. Earlier usage is not included."}


TRACKER = Tracker()


@router.get("/screen-time")
def screen_time():
    return TRACKER.summary()

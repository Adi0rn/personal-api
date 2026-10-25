"""Durable local reminders; one scheduler worker, read-only calendar access."""
from datetime import datetime, timedelta, timezone
import json
import logging
import os
from threading import Event, RLock, Thread
import time
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, StringConstraints, StrictBool, Field, field_validator

import connections
from desktop_notify import notify
from spotify_connection import local_request
import tasks

router = APIRouter(prefix='/reminders', tags=['Reminders'], dependencies=[Depends(local_request)])
LOCK = RLock()
UTC = timezone.utc


def iso(value):
    return value.astimezone(UTC).isoformat()


def init_db():
    with tasks.database() as db:
        db.execute('''CREATE TABLE IF NOT EXISTS reminders (
            id INTEGER PRIMARY KEY, kind TEXT NOT NULL, source_id TEXT UNIQUE,
            task_id INTEGER, title TEXT NOT NULL, due_at TEXT NOT NULL,
            state TEXT NOT NULL DEFAULT 'pending', message TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL)''')
        db.execute('CREATE INDEX IF NOT EXISTS reminders_due ON reminders(state,due_at)')
        db.execute('CREATE TABLE IF NOT EXISTS reminder_settings (id INTEGER PRIMARY KEY CHECK(id=1), calendar_enabled INTEGER NOT NULL, lead_minutes INTEGER NOT NULL)')
        db.execute('INSERT OR IGNORE INTO reminder_settings VALUES (1,1,10)')
        # A crash during submission is ambiguous. Do not submit the same toast twice.
        db.execute("UPDATE reminders SET state='failed',message='Interrupted during notification submission; delivery is unknown.' WHERE state='dispatching'")


class CreateReminder(BaseModel):
    model_config = ConfigDict(extra='forbid')
    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
    due_at: datetime
    task_id: int | None = Field(None, ge=1)

    @field_validator('due_at')
    @classmethod
    def aware(cls, value):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError('Include a timezone offset with the reminder time.')
        return value


class Settings(BaseModel):
    model_config = ConfigDict(extra='forbid')
    calendar_enabled: StrictBool
    lead_minutes: int = Field(10, ge=0, le=120, strict=True)


@router.get('/settings')
def settings():
    with tasks.database() as db:
        row = db.execute('SELECT * FROM reminder_settings WHERE id=1').fetchone()
    return {'calendar_enabled': bool(row['calendar_enabled']), 'lead_minutes': row['lead_minutes'],
            'calendar_error': WORKER.calendar_error, 'calendar_checked_at': WORKER.calendar_checked_at,
            'desktop_supported': os.name == 'nt'}


@router.put('/settings')
def save_settings(body: Settings):
    with LOCK, tasks.database() as db:
        db.execute('UPDATE reminder_settings SET calendar_enabled=?,lead_minutes=? WHERE id=1', (int(body.calendar_enabled), body.lead_minutes))
        # Rebuild pending calendar reminders on the next successful sync.
        db.execute("UPDATE reminders SET state='cancelled' WHERE kind='meeting' AND state='pending'")
    WORKER.next_calendar = 0
    return settings()


@router.get('')
def list_reminders():
    with tasks.database() as db:
        rows = db.execute("SELECT * FROM reminders WHERE state NOT IN ('cancelled','dismissed') ORDER BY CASE WHEN state='pending' THEN 0 ELSE 1 END,CASE WHEN state='pending' THEN due_at END,updated_at DESC LIMIT 100").fetchall()
    return {'reminders': [dict(row) for row in rows]}


@router.post('', status_code=201)
def create_reminder(body: CreateReminder):
    now = datetime.now(UTC)
    if body.due_at <= now:
        raise HTTPException(422, 'Choose a reminder time in the future.')
    with LOCK, tasks.database() as db:
        if body.task_id:
            task = db.execute('SELECT completed FROM tasks WHERE id=?', (body.task_id,)).fetchone()
            if not task or task['completed']:
                raise HTTPException(422, 'Choose an unfinished task.')
        count = db.execute("SELECT count(*) FROM reminders WHERE state='pending' AND kind!='meeting'").fetchone()[0]
        if count >= 500:
            raise HTTPException(409, 'There are already 500 pending reminders.')
        row = db.execute('INSERT INTO reminders(kind,task_id,title,due_at,updated_at) VALUES (?,?,?,?,?) RETURNING *',
            ('task' if body.task_id else 'custom', body.task_id, body.title, iso(body.due_at), iso(now))).fetchone()
    return dict(row)


@router.delete('/{reminder_id}')
def cancel_reminder(reminder_id: int):
    with LOCK, tasks.database() as db:
        row = db.execute("UPDATE reminders SET state='dismissed' WHERE id=? AND state!='dispatching' RETURNING id", (reminder_id,)).fetchone()
        if not row:
            raise HTTPException(409, 'Reminder was not found or is already being submitted.')
    return {'cancelled': True}


@router.post('/test')
def test_notification():
    try:
        notify('PAPI reminder test', 'Desktop reminders are ready. You can close the dashboard; keep PAPI running.')
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from None
    return {'submitted': True, 'message': 'Submitted to Windows. Do not disturb or notification settings may hide the banner.'}


def dispatch(now=None):
    now = now or datetime.now(UTC)
    with LOCK, tasks.database() as db:
        # Completing a task cancels its pending reminders, including future ones.
        db.execute("UPDATE reminders SET state='cancelled' WHERE state='pending' AND task_id IN (SELECT id FROM tasks WHERE completed=1)")
        db.execute("UPDATE reminders SET state='missed',message='PAPI was unavailable at the reminder time.' WHERE state='pending' AND due_at<?", (iso(now-timedelta(minutes=15)),))
        rows = db.execute("SELECT * FROM reminders WHERE state='pending' AND due_at<=? ORDER BY due_at LIMIT 10", (iso(now),)).fetchall()
    for row in rows:
        with LOCK, tasks.database() as db:
            claimed = db.execute("UPDATE reminders SET state='dispatching' WHERE id=? AND state='pending'", (row['id'],)).rowcount
        if not claimed:
            continue
        state, message = 'submitted', ''
        try:
            notify('PAPI · ' + ('Meeting' if row['kind']=='meeting' else 'Reminder'), row['title'])
        except Exception:
            state, message = 'failed', 'Windows could not accept the notification. Use Test notification to check your settings.'
        with tasks.database() as db:
            db.execute('UPDATE reminders SET state=?,message=?,updated_at=? WHERE id=?', (state, message, iso(now), row['id']))


def sync_calendar(now=None):
    now = now or datetime.now(UTC)
    options = settings()
    if not options['calendar_enabled']:
        return
    items, page = [], None
    for _ in range(5):
        params = {'timeMin': iso(now), 'timeMax': iso(now+timedelta(days=1)), 'singleEvents':'true', 'orderBy':'startTime', 'maxResults':100}
        if page:
            params['pageToken'] = page
        data = connections.google_get('https://www.googleapis.com/calendar/v3/calendars/primary/events', connections.CALENDAR_SCOPE, params)
        items.extend(data.get('items', [])); page = data.get('nextPageToken')
        if not page:
            break
    if page:
        raise RuntimeError('Too many calendar events to sync safely.')
    seen = set()
    with LOCK, tasks.database() as db:
        # Settings may have changed while the request was in progress.
        current = db.execute('SELECT * FROM reminder_settings WHERE id=1').fetchone()
        if not current['calendar_enabled'] or current['lead_minutes'] != options['lead_minutes']:
            return
        for item in items:
            if item.get('status') == 'cancelled' or not item.get('id') or not item.get('start', {}).get('dateTime'):
                continue
            if any(a.get('self') and a.get('responseStatus') == 'declined' for a in item.get('attendees', [])):
                continue
            start = datetime.fromisoformat(item['start']['dateTime'].replace('Z','+00:00'))
            if start.tzinfo is None or start <= now:
                continue
            source = 'calendar:' + item['id']; seen.add(source)
            due = start-timedelta(minutes=options['lead_minutes'])
            title = (item.get('summary') or 'Untitled meeting')[:200]
            # The date/time appears in Windows notifications using the profile zone.
            from briefing import profile_zone
            title += ' · ' + start.astimezone(profile_zone()[0]).strftime('%a %d %b, %I:%M %p')
            db.execute('''INSERT INTO reminders(kind,source_id,title,due_at,updated_at) VALUES ('meeting',?,?,?,?)
                ON CONFLICT(source_id) DO UPDATE SET title=excluded.title,due_at=excluded.due_at,updated_at=excluded.updated_at,
                state=CASE WHEN reminders.state='cancelled' THEN 'pending' ELSE reminders.state END
                WHERE reminders.state IN ('pending','cancelled')''', (source,title,iso(due),iso(now)))
        pending = db.execute("SELECT id,source_id FROM reminders WHERE kind='meeting' AND state='pending'").fetchall()
        for row in pending:
            if row['source_id'] not in seen:
                db.execute("UPDATE reminders SET state='cancelled' WHERE id=?", (row['id'],))


class ReminderWorker:
    def __init__(self):
        self.stop_event = Event(); self.thread = None; self.calendar_thread = None
        self.next_calendar = 0; self.calendar_error = ''; self.calendar_checked_at = None

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.stop_event.clear()
        self.thread = Thread(target=self.run, name='papi-reminders', daemon=True); self.thread.start()
        self.calendar_thread = Thread(target=self.calendar_loop, name='papi-meeting-sync', daemon=True); self.calendar_thread.start()

    def stop(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=3)
        if self.calendar_thread:
            self.calendar_thread.join(timeout=3)

    def run(self):
        while not self.stop_event.is_set():
            try:
                dispatch()
            except Exception:
                logging.getLogger('papi.reminders').error('Reminder check failed; retrying on the next tick.')
            self.stop_event.wait(10)

    def calendar_loop(self):
        while not self.stop_event.is_set():
            if time.monotonic() >= self.next_calendar:
                self.next_calendar = time.monotonic()+60
                try:
                    sync_calendar(); self.calendar_error = ''
                except HTTPException as exc:
                    self.calendar_error = 'Reconnect Google for meeting reminders.' if exc.status_code in (401,403) else 'Calendar unavailable. Meeting reminders cannot be refreshed.'
                except Exception:
                    self.calendar_error = 'Calendar unavailable. Meeting reminders cannot be refreshed.'
                self.calendar_checked_at = iso(datetime.now(UTC))
            self.stop_event.wait(5)


WORKER = ReminderWorker()

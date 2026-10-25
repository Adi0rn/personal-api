"""Read-only daily overview assembled without a model or persistent copies."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, time, timedelta, timezone
import json
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, Response

import connections
from spotify_connection import local_request
import tasks

PROFILE_FILE = Path(__file__).resolve().parent / 'data/profile.json'
router = APIRouter(tags=['Briefing'], dependencies=[Depends(local_request)])


def profile_zone():
    try:
        return ZoneInfo(json.loads(PROFILE_FILE.read_text(encoding='utf-8-sig'))['timezone']), False
    except (OSError, ValueError, KeyError, TypeError, ZoneInfoNotFoundError):
        return ZoneInfo('Asia/Kolkata'), True


def task_summary(day):
    items = tasks.list_tasks(completed=False)
    overdue = sorted((x for x in items if x['due_date'] and x['due_date'] < day), key=lambda x: (x['due_date'], x['id']))
    due = [x for x in items if x['due_date'] == day]
    return {'open_count': len(items), 'overdue_count': len(overdue), 'due_today_count': len(due),
            'high_priority_count': sum(x['priority'] == 'high' for x in items),
            'overdue': overdue[:5], 'due_today': due[:5]}


def calendar_summary(now, end):
    data = connections.google_get('https://www.googleapis.com/calendar/v3/calendars/primary/events',
        connections.CALENDAR_SCOPE, {'timeMin': now.isoformat(), 'timeMax': end.isoformat(),
            'singleEvents': 'true', 'orderBy': 'startTime', 'maxResults': 6})
    items = [x for x in data.get('items', []) if x.get('status') != 'cancelled']
    return {'events': [{'title': x.get('summary') or 'Untitled event', 'start': x.get('start', {}),
                        'end': x.get('end', {})} for x in items[:5]],
            'has_more': bool(data.get('nextPageToken')) or len(items) > 5}


def gmail_summary():
    # Label statistics give the inbox unread count, unlike a limited message page.
    data = connections.google_get('https://gmail.googleapis.com/gmail/v1/users/me/labels/INBOX', connections.GMAIL_SCOPE)
    count = data.get('messagesUnread')
    if type(count) is not int or count < 0:
        raise HTTPException(502, 'Gmail returned no unread count.')
    return {'unread_count': count}


def section(read):
    try:
        return {'status': 'ok', **read()}
    except HTTPException as exc:
        status = 'needs_attention' if exc.status_code in (401, 403) else 'unavailable'
        message = 'Connect or reconnect this service in Connections setup.' if status == 'needs_attention' else 'Temporarily unavailable. Try refreshing later.'
        return {'status': status, 'message': message}
    except Exception:
        # Isolate failures and never expose exception text that could contain credentials.
        return {'status': 'unavailable', 'message': 'Temporarily unavailable. Try refreshing later.'}


def event_time(event, zone):
    start = event.get('start', {})
    if start.get('date'):
        return 'all day'
    try:
        value = datetime.fromisoformat(start['dateTime'].replace('Z', '+00:00'))
        if value.tzinfo is None:
            value = value.replace(tzinfo=ZoneInfo(start.get('timeZone', zone.key)))
        return value.astimezone(zone).strftime('%I:%M %p').lstrip('0')
    except (KeyError, TypeError, ValueError, ZoneInfoNotFoundError):
        return 'time unspecified'


def spoken_summary(data, zone):
    lines = []
    todo = data['tasks']
    if todo['status'] == 'ok':
        lines.append(f"You have {todo['open_count']} open tasks: {todo['overdue_count']} overdue and {todo['due_today_count']} due today.")
        if todo['overdue']:
            lines.append('Oldest overdue: ' + todo['overdue'][0]['title'] + '.')
    else:
        lines.append('Tasks are unavailable.')
    calendar = data['calendar']
    if calendar['status'] == 'ok':
        if calendar['events']:
            lines.append('Remaining today: ' + '; '.join(x['title'] + ' (' + event_time(x, zone) + ')' for x in calendar['events']) + '.')
            if calendar['has_more']:
                lines.append('More events are available in Calendar.')
        else:
            lines.append('No remaining calendar events today.')
    else:
        lines.append('Calendar needs connection setup.' if calendar['status'] == 'needs_attention' else 'Calendar is unavailable.')
    mail = data['gmail']
    lines.append(f"You have {mail['unread_count']} unread inbox messages." if mail['status'] == 'ok' else
                 'Gmail needs connection setup.' if mail['status'] == 'needs_attention' else 'Gmail is unavailable.')
    github = data['github']
    if github['status'] == 'ok':
        events = github['events']
        lines.append('Latest public GitHub activity: ' + (events[0].get('type') or 'Activity') + ' in ' + (events[0].get('repository') or 'a repository') + '.' if events else 'No recent public GitHub activity.')
    else:
        lines.append('GitHub needs connection setup.' if github['status'] == 'needs_attention' else 'GitHub is unavailable.')
    return ' '.join(lines)


def build_briefing(now=None):
    zone, fallback = profile_zone()
    now = (now or datetime.now(timezone.utc)).astimezone(zone)
    end = datetime.combine(now.date() + timedelta(days=1), time.min, tzinfo=zone)
    readers = {'tasks': lambda: task_summary(now.date().isoformat()),
               'calendar': lambda: calendar_summary(now, end), 'gmail': gmail_summary,
               'github': lambda: connections.github_activity(limit=5)}
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {key: pool.submit(section, read) for key, read in readers.items()}
        data = {key: future.result() for key, future in futures.items()}
    data.update(date=now.date().isoformat(), timezone=zone.key, timezone_fallback=fallback,
                generated_at=now.isoformat(), calendar_until=end.isoformat(),
                partial=any(x['status'] != 'ok' for x in data.values()))
    data['summary'] = spoken_summary(data, zone)
    return data


@router.get('/briefing')
def daily_briefing(response: Response):
    response.headers['Cache-Control'] = 'no-store'
    return build_briefing()

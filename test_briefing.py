"""Briefing checks use temporary data and mocked connections only."""
from datetime import datetime
import json
import unittest
from unittest.mock import patch

from fastapi import HTTPException
import httpx

import briefing
import tasks
import test_features as fixtures
from test_tasks import request, test_server


class BriefingTests(unittest.TestCase):
    def setUp(self):
        fixtures.FeatureTests.setUp(self)
        self.profile = self.db.parent / 'profile.json'
        self.profile.write_text(json.dumps({'timezone': 'Asia/Kolkata'}))
        for target, value in [('briefing.PROFILE_FILE', self.profile)]:
            p = patch(target, value); p.start(); self.addCleanup(p.stop)
        p = patch('connections.google_get', side_effect=self.google)
        self.google_get = p.start(); self.addCleanup(p.stop)
        p = patch('connections.github_activity', return_value={'events': [], 'visibility': 'public'})
        self.github = p.start(); self.addCleanup(p.stop)
        p = patch('connections.remote', side_effect=AssertionError('Unexpected real account request'))
        p.start(); self.addCleanup(p.stop)

    def google(self, url, scope, params=None):
        if url.endswith('/labels/INBOX'):
            return {'messagesUnread': 42}
        return {'items': [{'summary': 'Planning', 'start': {'dateTime': '2026-10-19T10:00:00+05:30'}}]}

    def test_local_day_tasks_counts_and_exact_inbox_statistic(self):
        tasks.init_db()
        for title, due in [('Overdue', '2026-10-18'), ('Today', '2026-10-19'), ('Tomorrow', '2026-10-20')]:
            tasks.create_task(tasks.TaskCreate(title=title, due_date=due))
        done = tasks.create_task(tasks.TaskCreate(title='Done', due_date='2026-10-01'))
        tasks.update_task(done['id'], tasks.TaskUpdate(completed=True))
        data = briefing.build_briefing(datetime.fromisoformat('2026-10-18T20:00:00+00:00'))
        self.assertEqual(data['date'], '2026-10-19')
        self.assertEqual(data['tasks']['overdue_count'], 1)
        self.assertEqual(data['tasks']['due_today_count'], 1)
        self.assertEqual(data['tasks']['open_count'], 3)
        self.assertEqual(data['gmail']['unread_count'], 42)
        self.assertIn('42 unread inbox messages', data['summary'])
        self.assertIn('10:00 AM', data['summary'])
        self.assertFalse(data['partial'])
        call = next(c for c in self.google_get.call_args_list if c.args[0].endswith('/events'))
        self.assertEqual(call.args[2]['timeMax'], '2026-10-20T00:00:00+05:30')

    def test_service_failures_keep_tasks_and_do_not_claim_zero(self):
        tasks.init_db()
        self.google_get.side_effect = HTTPException(401, 'sensitive provider detail')
        self.github.side_effect = RuntimeError('private-token-in-exception')
        data = briefing.build_briefing()
        self.assertTrue(data['partial'])
        self.assertEqual(data['tasks']['status'], 'ok')
        self.assertEqual(data['calendar']['status'], 'needs_attention')
        self.assertEqual(data['github']['status'], 'unavailable')
        self.assertNotIn('unread_count', data['gmail'])
        self.assertNotIn('private-token', json.dumps(data))
        self.assertNotIn('sensitive provider', json.dumps(data))
        self.assertNotIn('0 unread', data['summary'])

    def test_dst_day_and_all_day_events(self):
        tasks.init_db()
        self.profile.write_text(json.dumps({'timezone': 'America/New_York'}))
        def google(url, scope, params=None):
            return {'messagesUnread': 0} if url.endswith('/INBOX') else {'items': [{'summary': 'Day off', 'start': {'date': '2026-03-08'}}], 'nextPageToken': 'more'}
        self.google_get.side_effect = google
        data = briefing.build_briefing(datetime.fromisoformat('2026-03-08T05:30:00+00:00'))
        self.assertEqual(data['calendar_until'], '2026-03-09T00:00:00-04:00')
        self.assertIn('Day off (all day)', data['summary'])
        self.assertTrue(data['calendar']['has_more'])

    def test_bad_profile_falls_back_and_missing_count_is_unavailable(self):
        tasks.init_db()
        self.profile.write_text('{')
        self.google_get.return_value = {}
        self.google_get.side_effect = None
        data = briefing.build_briefing()
        self.assertTrue(data['timezone_fallback'])
        self.assertEqual(data['gmail']['status'], 'unavailable')
        self.assertNotIn('unread_count', data['gmail'])

    def test_http_voice_and_dashboard(self):
        with test_server() as base, httpx.Client(base_url=base, trust_env=False) as client:
            response = client.get('/briefing')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers['cache-control'], 'no-store')
            self.assertEqual(client.get('/briefing', headers={'Origin': 'https://other.example'}).status_code, 403)
            for phrase in ('Give me my daily briefing!', "What's my day looking like?", 'What’s my day looking like?'):
                reply = request(base, 'POST', '/assistant/command', {'text': phrase})
                self.assertEqual(reply[0], 200)
                self.assertTrue(reply[1]['handled'])
                self.assertIn('42 unread inbox messages', reply[1]['reply'])
            self.assertIn('id="briefing-content"', client.get('/dashboard').text)
            self.assertEqual(client.get('/dashboard/assets/briefing.js').status_code, 200)

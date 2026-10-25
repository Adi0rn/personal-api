"""Temporary reminder data and mocked desktop/calendar services."""
import base64
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, Mock
import unittest
from xml.etree import ElementTree

import httpx
import desktop_notify
import reminders
import tasks
import test_features as fixtures
from test_tasks import test_server, request


class ReminderTests(unittest.TestCase):
    def setUp(self):
        fixtures.FeatureTests.setUp(self)
        tasks.init_db(); reminders.init_db()
        p=patch('reminders.notify', side_effect=AssertionError('Unexpected desktop notification'))
        self.notify=p.start();self.addCleanup(p.stop)
        p=patch('connections.google_get', side_effect=AssertionError('Unexpected real Google request'))
        self.google=p.start();self.addCleanup(p.stop)
        self.now=datetime.now(timezone.utc)

    def add(self, title='Remember', task_id=None):
        return reminders.create_reminder(reminders.CreateReminder(title=title,due_at=self.now+timedelta(minutes=1),task_id=task_id))

    def test_durable_claim_and_no_duplicate_notification(self):
        row=self.add()
        reminders.init_db()  # A normal restart preserves pending reminders.
        self.notify.side_effect=None
        reminders.dispatch(self.now+timedelta(minutes=2))
        reminders.dispatch(self.now+timedelta(minutes=3))
        self.notify.assert_called_once()
        self.assertEqual(reminders.list_reminders()['reminders'][0]['state'],'submitted')

    def test_completed_tasks_cancel_future_reminders_and_missed_reminders_do_not_flood(self):
        task=tasks.create_task(tasks.TaskCreate(title='Finish'))
        self.add(task_id=task['id'])
        tasks.update_task(task['id'],tasks.TaskUpdate(completed=True))
        reminders.dispatch(self.now)
        self.assertEqual(reminders.list_reminders()['reminders'],[])
        self.add()
        reminders.dispatch(self.now+timedelta(hours=1))
        self.notify.assert_not_called()
        self.assertEqual(reminders.list_reminders()['reminders'][0]['state'],'missed')

    def test_failed_notification_and_ambiguous_interrupted_submission_are_not_retried(self):
        row=self.add()
        self.notify.side_effect=RuntimeError('Private exception')
        reminders.dispatch(self.now+timedelta(minutes=2))
        self.assertEqual(reminders.list_reminders()['reminders'][0]['state'],'failed')
        self.assertNotIn('Private',reminders.list_reminders()['reminders'][0]['message'])
        row=self.add('Other')
        with tasks.database() as db:
            db.execute("UPDATE reminders SET state='dispatching' WHERE id=?",(row['id'],))
        reminders.init_db()
        reminders.dispatch(self.now+timedelta(minutes=3))
        self.assertEqual(self.notify.call_count,1)

    def test_meeting_reschedule_cancel_decline_and_all_day(self):
        start=self.now+timedelta(minutes=30)
        self.google.side_effect=None
        self.google.return_value={'items':[
            {'id':'meeting','summary':'Meeting','start':{'dateTime':start.isoformat()}},
            {'id':'allday','start':{'date':self.now.date().isoformat()}},
            {'id':'declined','start':{'dateTime':start.isoformat()},'attendees':[{'self':True,'responseStatus':'declined'}]}]}
        reminders.sync_calendar(self.now)
        first=reminders.list_reminders()['reminders']
        self.assertEqual(len(first),1)
        self.assertEqual(first[0]['due_at'],reminders.iso(start-timedelta(minutes=10)))
        self.google.return_value['items'][0]['start']['dateTime']=(start+timedelta(hours=1)).isoformat()
        reminders.sync_calendar(self.now)
        self.assertEqual(reminders.list_reminders()['reminders'][0]['id'],first[0]['id'])
        self.assertNotEqual(reminders.list_reminders()['reminders'][0]['due_at'],first[0]['due_at'])
        reminders.cancel_reminder(first[0]['id'])
        reminders.sync_calendar(self.now)
        self.assertEqual(reminders.list_reminders()['reminders'],[],'Dismissed meeting must not reappear during sync')
        self.google.return_value={'items':[{'id':'new','start':{'dateTime':start.isoformat()}}]}
        reminders.sync_calendar(self.now)
        self.google.return_value={'items':[]}
        reminders.sync_calendar(self.now)
        self.assertEqual(reminders.list_reminders()['reminders'],[])

    def test_meeting_pagination_and_failed_sync_preserve_known_reminders(self):
        event={'id':'meeting','start':{'dateTime':(self.now+timedelta(hours=1)).isoformat()}}
        self.google.side_effect=[{'items':[],'nextPageToken':'next'},{'items':[event]}]
        reminders.sync_calendar(self.now)
        self.assertEqual(self.google.call_args.args[2]['pageToken'],'next')
        self.assertEqual(len(reminders.list_reminders()['reminders']),1)
        self.google.side_effect=RuntimeError('Offline')
        with self.assertRaises(RuntimeError): reminders.sync_calendar(self.now)
        self.assertEqual(len(reminders.list_reminders()['reminders']),1)

    def test_http_validation_settings_cancellation_and_notification_test(self):
        with test_server() as base, httpx.Client(base_url=base,trust_env=False) as client:
            self.assertEqual(client.post('/reminders',json={'title':'X','due_at':'2030-01-01T10:00'}).status_code,422)
            self.assertEqual(client.post('/reminders',json={'title':'X','due_at':'2020-01-01T10:00Z'}).status_code,422)
            self.assertEqual(client.post('/reminders',json={'title':'X','due_at':(self.now+timedelta(hours=1)).isoformat(),'task_id':999}).status_code,422)
            payload={'title':'X','due_at':(self.now+timedelta(hours=1)).isoformat()}
            self.assertEqual(client.post('/reminders',json=payload,headers={'Origin':'https://evil.example'}).status_code,403)
            response=client.post('/reminders',json=payload)
            self.assertEqual(response.status_code,201)
            self.assertEqual(client.delete('/reminders/'+str(response.json()['id'])).status_code,200)
            self.assertEqual(client.get('/reminders').json()['reminders'],[])
            self.assertEqual(client.put('/reminders/settings',json={'calendar_enabled':False,'lead_minutes':15}).status_code,200)
            self.assertFalse(client.get('/reminders/settings').json()['calendar_enabled'])
            self.notify.side_effect=None
            self.assertTrue(client.post('/reminders/test').json()['submitted'])
            self.assertIn('id="reminder-dialog"',client.get('/dashboard').text)

    @patch('desktop_notify.subprocess.run')
    def test_native_toast_escapes_content_as_xml_not_shell_code(self, run):
        run.return_value=Mock(returncode=0)
        desktop_notify.notify('PAPI', 'Test <video> & $(unexpected)')
        command=base64.b64decode(run.call_args.args[0][-1]).decode('utf-16-le')
        import re
        payload=re.search("FromBase64String\\('([^']+)'\\)",command).group(1)
        tree=ElementTree.fromstring(base64.b64decode(payload))
        self.assertEqual(tree.findall('.//text')[1].text,'Test <video> & $(unexpected)')
        self.assertNotIn('$(unexpected)',command)

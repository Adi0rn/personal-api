import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import tasks
from test_tasks import test_server, request


class SavedLinkTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        patcher = patch.object(tasks, 'DB_PATH', Path(temp.name)/'tasks.db')
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_create_filter_edit_delete_and_restart(self):
        with test_server() as base:
            status, item = request(base,'POST','/links',{'url':'https://example.com/article','title':'Research','tags':[' Study ','STUDY','videos']})
            self.assertEqual(status,201)
            self.assertEqual(item['tags'],['study','videos'])
            link_id = item['id']
            self.assertFalse(item['is_read'])
            for query in ('q=research','q=example.com','tag=STUDY','is_read=false'):
                self.assertEqual(len(request(base,'GET','/links?'+query)[1]),1)
            self.assertEqual(request(base,'GET','/links?q=missing')[1],[])
            self.assertEqual(request(base,'PATCH',f'/links/{link_id}',{'title':'New title','is_read':True,'tags':['Books']})[0],200)
            self.assertEqual(request(base,'GET','/links?is_read=false')[1],[])
        with test_server() as base:
            item = request(base,'GET','/links?is_read=true')[1][0]
            self.assertEqual(item['title'],'New title')
            self.assertEqual(item['tags'],['books'])
            import httpx
            self.assertEqual(httpx.delete(base+f'/links/{link_id}',trust_env=False).status_code,204)
            self.assertEqual(request(base,'GET','/links')[1],[])
            self.assertEqual(request(base,'PATCH',f'/links/{link_id}',{'is_read':False})[0],404)

    def test_validation_duplicates_and_literal_search(self):
        with test_server() as base:
            status,item = request(base,'POST','/links',{'url':'https://example.com'})
            self.assertEqual(status,201)
            self.assertEqual(item['title'],'example.com')
            self.assertEqual(request(base,'POST','/links',{'url':'https://example.com/'})[0],409)
            for body in ({'url':'javascript:alert(1)'},{'url':'file:///C:/private'}, {'url':'https://user:secret@example.com'},
                         {'url':'https://example.org','tags':['a'*31]}, {'url':'https://example.org','tags':[str(i) for i in range(11)]}):
                self.assertEqual(request(base,'POST','/links',body)[0],422)
            for body in ({},{'title':None},{'is_read':'false'},{'tags':None},{'url':'https://other.com'}):
                self.assertEqual(request(base,'PATCH',f"/links/{item['id']}",body)[0],422)
            self.assertEqual(request(base,'GET','/links?q=%25')[1],[])
            self.assertEqual(len(request(base,'GET','/links')[1]),1)

    def test_local_only_and_dashboard(self):
        import httpx
        with test_server() as base:
            self.assertEqual(httpx.get(base+'/links',headers={'Host':'evil.example'},trust_env=False).status_code,403)
            self.assertEqual(httpx.post(base+'/links',json={'url':'https://example.com'},headers={'Origin':'https://evil.example'},trust_env=False).status_code,403)
            html = httpx.get(base+'/dashboard',trust_env=False).text
            for item in ('data-view="links"','id="link-form"','id="link-search"','saved-links.js'):
                self.assertIn(item,html)

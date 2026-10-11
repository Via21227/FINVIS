"""Real loopback D1 routes, isolated P2 store, fixed Candidate, no inference."""
import http.client
import json
from threading import Thread
import unittest
from urllib.parse import quote

from finvis.application import DemoApplication
from tests.http_support import DemoServer
from tests.d1_process_host import TEXT, output
from tests.test_agent_understanding import SequenceProvider
from tests.test_decision_baseline import component, context


class D1HTTPTests(unittest.TestCase):
    def setUp(self):
        self.server=DemoServer(('127.0.0.1',0),DemoApplication(agent_provider=SequenceProvider([output()])))
        self.thread=Thread(target=self.server.serve_forever,daemon=True);self.thread.start()

    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join()

    def call(self,path,payload=None,authorized=True):
        c=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=5)
        headers={'Content-Type':'application/json'}
        if authorized:headers.update(Origin=self.server.origin,**{'X-FINVIS-Token':self.server.csrf})
        c.request('POST' if payload is not None else 'GET',path,json.dumps(payload) if payload is not None else None,headers)
        r=c.getresponse();result=(r.status,json.loads(r.read()));c.close();return result

    def test_full_review_baseline_isolation_stale_version_and_csrf(self):
        self.assertEqual(self.call('/api/user/baseline?decision_id=missing')[1],{'baseline':None})
        self.assertEqual(self.call('/api/user/baseline',{},False)[0],403)
        w=self.server.ensure_user_workflow()
        env=w.interpret(TEXT);view=w.prepare(env['id'])
        p=view['proposal']['id'];v=view['preview'];grant=w.authorize(p,v['id'],v['digest'])
        w.apply(p,v['id'],v['digest'],grant['authorization']['id'])
        home=self.call('/api/user/home')[1];demo=self.server.app.view()
        decision=home['home']['projection']['decisions'][0]['ref']
        request=dict(decision_ref=decision,expected_version=0,context=context('training'),components=[component()],
                     dependency_refs=[],scope_complete=False,buffer=None)
        code,out=self.call('/api/user/baseline',request);self.assertEqual(code,200,out)
        self.assertEqual(out['baseline']['recommended_budget']['status'],'policy_required')
        query='/api/user/baseline?decision_id='+quote(decision['ref']['id'],safe='')
        self.assertEqual(self.call(query)[1]['baseline']['ref'],out['baseline']['ref'])
        self.assertEqual(self.call('/api/user/baseline',request)[0],400)
        self.assertEqual(self.call('/api/user/baseline?unexpected=x')[0],400)
        self.assertEqual(self.call('/api/user/home')[1],home);self.assertEqual(self.server.app.view(),demo)
        self.assertEqual(self.call('/api/user/history')[1]['history']['current_revision'],1)


if __name__=='__main__':unittest.main()

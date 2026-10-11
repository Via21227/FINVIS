"""Real HTTP boundary/restart acceptance using only isolated OS temporary data."""
import tempfile,unittest,json
from pathlib import Path
from threading import Thread
from tests import test_d1_http
from tests.lifecycle_process_host import start

class LifecycleHTTPTests(unittest.TestCase):
    call=test_d1_http.D1HTTPTests.call
    def setUp(self):
        self.root=Path(tempfile.mkdtemp(prefix='finvis-lifecycle-http-')).resolve();self.boot()
    def boot(self):
        self.server=start(self.root);self.thread=Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join()
    def test_real_routes_authorization_receipt_and_restart(self):
        demo=self.server.app.view()
        code,r=self.call('/api/user/interpret',{'text':'机票买好了，1844。'});self.assertEqual(code,200,r);r=r['routing']
        self.assertEqual(r['question']['key'],'source')
        for key,value in [('source','position/daily'),('coverage','apply_to_prior_balance')]:
            code,r=self.call('/api/user/lifecycle/answer',dict(proposal_id=r['id'],key=key,value=value));self.assertEqual(code,200,r)
        code,v=self.call('/api/user/lifecycle/prepare',dict(proposal_id=r['id']));self.assertEqual(code,200,v)
        args=dict(preview_id=v['preview']['id'],digest=v['preview']['digest'])
        code,g=self.call('/api/user/lifecycle/authorize',args);self.assertEqual(code,200,g)
        args['authorization_id']=g['authorization']['id'];code,done=self.call('/api/user/lifecycle/apply',args);self.assertEqual(code,200,done)
        home=self.call('/api/user/home')[1]['home'];self.assertEqual(next(i for i in home['lifecycle']['items'] if i['key']=='plan:expense/flight')['summary']['status'],'completed')
        self.assertEqual(self.server.app.view(),demo)
        self.tearDown();self.boot()
        code,again=self.call('/api/user/lifecycle/apply',args);self.assertEqual(code,200,again)
        self.assertEqual(again['operation']['receipt'],done['operation']['receipt'])
        self.assertEqual(self.call('/api/user/home')[1]['home']['projection'],home['projection'])
        self.assertEqual(self.call('/api/user/home')[1]['home']['lifecycle']['items'],home['lifecycle']['items'])
    def test_csrf_bad_fields_and_no_direct_settlement_payload(self):
        for path in ('answer','prepare','authorize','apply','cancel'):
            self.assertEqual(self.call('/api/user/lifecycle/'+path,{},False)[0],403)
        before=self.call('/api/user/home')[1]
        self.assertEqual(self.call('/api/user/lifecycle/prepare',{'operations':[{'type':'settle'}]})[0],400)
        self.assertEqual(self.call('/api/user/lifecycle/apply',{'preview_id':'fake','digest':'fake','authorization_id':'fake'})[0],409)
        self.assertEqual(self.call('/api/user/home')[1],before)
    def test_stop_requires_new_report(self):
        _,r=self.call('/api/user/interpret',{'text':'地铁花了42。','request_id':'qa-stop'})
        self.assertEqual(self.call('/api/user/interpret/stop',{'request_id':'qa-stop'})[1]['status'],'cancelled')
        self.assertEqual(self.call('/api/user/lifecycle/answer',{'proposal_id':r['routing']['id'],'key':'source','value':'position/daily'})[0],409)
    def test_closed_preference_route_never_changes_world(self):
        before=self.call('/api/user/home')[1]
        code,r=self.call('/api/user/interpret',{'text':'交通费别显示进度了，只显示剩多少钱。'})
        self.assertEqual(code,200);self.assertEqual(r['routing']['stage'],'display')
        self.assertEqual(self.call('/api/user/home')[1],before)

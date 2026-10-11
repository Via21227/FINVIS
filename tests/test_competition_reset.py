"""Explicit reset gates, archive recovery and isolation; OS-temporary data only."""
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread, Event
from unittest.mock import patch
import http.client
import json
import os
import time
import unittest

from finvis.application import DemoApplication
from finvis.competition_launcher import Registry
from finvis.competition_reset import recovery_guard
from finvis.i3_workflow import I3Error
from finvis.persistent_world import StorageLocation, StorageUnavailable
from finvis.user_runtime import UserRuntime
from finvis.web import DemoServer
from finvis.world_store import current_assets
from tests.test_agent_understanding import SequenceProvider
from tests.test_competition_environment import provider
from tests.test_interaction_simplification import commit
from tests.test_future_experience import price

class ResetTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory();self.root=Path(self.temp.name).resolve()
        self.registry=Registry(self.root/'competition');self.value=self.registry.choose()
        self.base,self.loc=self.registry.paths(self.value)
        self.hosts=[];self.s=self.host()
    def tearDown(self):
        for s,t in self.hosts:
            s.shutdown();s.server_close();t.join()
        self.temp.cleanup()
    def host(self,loc=None,identity=None,normal=False):
        s=DemoServer(('127.0.0.1',0),DemoApplication(agent_provider=provider()),
            user_location=loc or self.loc,competition_id=None if normal else identity or self.value['id'])
        t=Thread(target=s.serve_forever,daemon=True);t.start();self.hosts.append((s,t));return s
    def seed(self,s=None):
        s=s or self.s;w=s.ensure_user_workflow();env=w.interpret('Bank balance EUR 3000.')
        receipt=commit(w,w.prepare(env['id']))
        p=s.user_runtime.future_experience.prepare(dict(text='Synthetic future hotel',title='Hotel',amount={**price(1200,'EUR'),'precision':'exact'}))
        s.user_runtime.future_experience.confirm({'proposal_id':p['proposal_id']})
        return receipt
    def post(self,action,payload=None,s=None,headers=None):
        s=s or self.s;c=http.client.HTTPConnection('127.0.0.1',s.server_port,timeout=5)
        h={'Origin':s.origin,'Content-Type':'application/json','X-FINVIS-Token':s.csrf}
        if s.competition_id:h['X-FINVIS-Competition-Generation']=s.competition_reset.generation
        h.update(headers or {})
        path=action if action.startswith('/') else '/api/competition/reset/'+action
        c.request('POST',path,json.dumps(payload or {}),h);r=c.getresponse();result=r.status,json.loads(r.read());c.close();return result
    def prepare(self):
        status,p=self.post('prepare');self.assertEqual(status,200);return p['confirmation_id']
    def confirmed(self):
        identity=self.prepare();status,p=self.post('confirm',{'confirmation_id':identity,'phrase':'CLEAR'})
        self.assertEqual(status,200);return {'confirmation_id':identity,'authorization':p['authorization']}
    def clear(self):
        status,result=self.post('apply',self.confirmed());self.assertEqual(status,200,result);return result
    def test_archive_empty_restart_original_receipts_and_future_preserved(self):
        receipt=self.seed();oldtoken=self.s.csrf;result=self.clear()
        self.assertEqual(self.s.user_runtime.state,'first_run');self.assertIsNone(self.s.user_workflow)
        self.assertFalse(self.loc.database.exists());self.assertFalse((self.base/'world-required').exists())
        self.assertNotEqual(oldtoken,self.s.csrf)
        backup=self.base/'backups'/('clear-'+result['generation']);self.assertTrue((backup/'world-required').exists())
        old=UserRuntime(SequenceProvider([]),StorageLocation.temporary(backup/'state'/'UserWorld'))
        try:
            self.assertEqual(current_assets(old.workflow.store.snapshot).positions[0].balance.amount.minor_units,300000)
            self.assertEqual(len(old.workflow.store.snapshot.receipts),1)
            self.assertTrue(old.future_experience.listing()['records'])
        finally:old.close()
        self.s.shutdown();self.s.server_close();self.hosts[0][1].join();self.hosts=[]
        reopened=self.host();self.assertEqual(reopened.user_runtime.state,'first_run')
        self.assertEqual(reopened.competition_reset.generation,result['generation'])
        self.seed(reopened);self.assertEqual(len(reopened.user_workflow.store.snapshot.receipts),1)
    def test_normal_host_has_no_reset_api_or_bootstrap(self):
        n=self.host(StorageLocation.temporary(self.root/'personal'/'UserWorld'),normal=True)
        self.seed(n);revision=n.user_workflow.store.snapshot.revision
        for action in ['prepare','confirm','apply']:self.assertEqual(self.post(action,s=n)[0],404)
        c=http.client.HTTPConnection('127.0.0.1',n.server_port);c.request('GET','/');r=c.getresponse();self.assertNotIn(b'competition.js',r.read());c.close()
        self.assertEqual(n.user_workflow.store.snapshot.revision,revision)
    def test_other_instance_and_personal_byte_identical(self):
        other=self.registry.choose(new=True);b,loc=self.registry.paths(other);o=self.host(loc,other['id'])
        n=self.host(StorageLocation.temporary(self.root/'personal'/'UserWorld'),normal=True)
        self.seed(o);self.seed(n);self.seed()
        # Closed synthetic stores provide stable persisted byte witnesses.
        o.user_runtime.close();n.user_runtime.close()
        before={p:p.read_bytes() for path in [b/'state',self.root/'personal'] for p in path.rglob('*') if p.is_file()}
        self.clear();self.assertTrue(before);self.assertTrue(all(p.read_bytes()==v for p,v in before.items()))
    def test_prepare_and_cancel_are_nondestructive(self):
        self.seed();self.prepare();self.assertEqual(self.s.user_workflow.store.snapshot.revision,1)
        self.assertTrue(self.loc.database.exists());self.assertFalse((self.base/'competition-reset.json').exists())
    def test_apply_before_phrase_rejected(self):
        identity=self.prepare();self.assertEqual(self.post('apply',{'confirmation_id':identity,'authorization':'fake'})[0],409)
    def test_wrong_phrase_and_instance_payload_rejected(self):
        identity=self.prepare()
        self.assertEqual(self.post('confirm',{'confirmation_id':identity,'phrase':'clear'})[0],409)
        self.assertEqual(self.post('prepare',{'instance':'b'*32})[0],400)
        self.assertEqual(self.post('prepare',{'path':'/private/tmp/other'})[0],400)
    def test_wrong_token_origin_and_generation_rejected(self):
        for h in [{'X-FINVIS-Token':'bad'},{'Origin':'https://untrusted.example'},{'X-FINVIS-Competition-Generation':'old'}]:
            self.assertIn(self.post('prepare',headers=h)[0],[403,409])
        self.assertFalse((self.base/'competition-reset.json').exists())
    def test_confirmation_id_and_grant_bound(self):
        grant=self.confirmed();grant['authorization']='bad';self.assertEqual(self.post('apply',grant)[0],409)
    def test_expired_confirmation_rejected(self):
        grant=self.confirmed();self.s.competition_reset.pending['expires']=time.monotonic()-1
        self.assertEqual(self.post('apply',grant)[0],409)
    def test_any_mutating_activity_invalidates_confirmation(self):
        grant=self.confirmed();self.post('/api/user/cancel',{})
        self.assertEqual(self.post('apply',grant)[0],409)
    def test_busy_get_or_inference_fences_clear(self):
        grant=self.confirmed()
        for path,method in [('/api/user/home','GET'),('/api/user/interpret','POST'),('/api/ai/credential','POST')]:
            active=self.s.competition_request_begin(path,method,'initial')
            self.assertEqual(self.post('apply',grant)[0],409)
            self.s.competition_request_end(active)
    def test_actual_overlapping_status_request_blocks_clear(self):
        entered=Event();release=Event();results=[]
        original=self.s.app.local_ai_status
        def slow_status():
            entered.set();release.wait(4);return original()
        def request():
            c=http.client.HTTPConnection('127.0.0.1',self.s.server_port,timeout=5)
            c.request('GET','/api/local-ai/status');r=c.getresponse();r.read();results.append(r.status);c.close()
        with patch.object(self.s.app,'local_ai_status',slow_status):
            worker=Thread(target=request);worker.start()
            try:
                self.assertTrue(entered.wait(2));self.assertEqual(self.post('prepare')[0],409)
                self.assertFalse((self.base/'competition-reset.json').exists())
            finally:release.set();worker.join(5)
        self.assertEqual(results,[200]);self.clear()

    def test_duplicate_apply_and_stale_financial_request_rejected(self):
        grant=self.confirmed();oldgen=self.s.competition_reset.generation;oldtoken=self.s.csrf
        self.assertEqual(self.post('apply',grant)[0],200)
        self.assertEqual(self.post('apply',grant)[0],409)
        # Even a fresh CSRF token cannot rehabilitate an old page's generation.
        self.assertEqual(self.post('/api/user/interpret',{'text':'Bank balance EUR 3000.'},headers={'X-FINVIS-Competition-Generation':oldgen})[0],409)
        self.assertFalse(self.loc.database.exists())
    def test_two_explicit_clears_archive_separately(self):
        one=self.clear();two=self.clear();self.assertNotEqual(one['generation'],two['generation'])
        self.assertEqual(len(list((self.base/'backups').glob('clear-*'))),2)
    def test_symlink_or_hardlink_inside_state_fails_closed(self):
        target=self.root/'outside';target.write_text('synthetic outside');target.chmod(0o600)
        alias=self.base/'state'/'alias';alias.symlink_to(target)
        self.assertEqual(self.post('apply',self.confirmed())[0],503);self.assertEqual(target.read_text(),'synthetic outside')
        alias.rename(self.root/'saved-alias');os.link(target,alias)
        self.assertEqual(self.post('apply',self.confirmed())[0],503);self.assertEqual(target.read_text(),'synthetic outside')
    def test_interruption_at_each_stage_requires_manual_recovery(self):
        for checkpoint in ['journal','state_archived','marker_archived','empty_state']:
            value=self.registry.choose(new=True);base,loc=self.registry.paths(value);s=self.host(loc,value['id'])
            self.seed(s);token=s.csrf;gen=s.competition_reset.generation
            p=s.competition_reset.action('prepare',{},token,gen)
            q=s.competition_reset.action('confirm',{'confirmation_id':p['confirmation_id'],'phrase':'CLEAR'},token,gen)
            def fail(stage):
                if stage==checkpoint:raise OSError('Synthetic interrupted clear')
            with patch.object(s.competition_reset,'_checkpoint',fail):
                with self.assertRaises(I3Error):s.competition_reset.action('apply',{'confirmation_id':p['confirmation_id'],'authorization':q['authorization']},token,gen)
            self.assertEqual(s.user_runtime.state,'recovery_required')
            with self.assertRaises(StorageUnavailable):recovery_guard(loc,value['id'])
            with self.assertRaises(StorageUnavailable):DemoServer(('127.0.0.1',0),DemoApplication(agent_provider=provider()),user_location=loc,competition_id=value['id'])
    def test_backup_ancestor_alias_rejected_on_restart(self):
        result=self.clear();backups=self.base/'backups';backups.rename(self.base/'saved-backups')
        backups.symlink_to(self.base/'saved-backups',target_is_directory=True)
        with self.assertRaises(StorageUnavailable):recovery_guard(self.loc,self.value['id'])
    def test_reset_does_not_touch_provider_or_credentials(self):
        manager=self.s.app.local_ai;agent=self.s.app.agent.provider;self.clear()
        self.assertIs(self.s.app.local_ai,manager);self.assertIs(self.s.app.agent.provider,agent)

if __name__=='__main__':unittest.main()

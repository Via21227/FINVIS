"""Competition host isolation. Synthetic data, offline providers, no User storage."""
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
import http.client
import json
import os
import socket
import subprocess
import sys
import time
import unittest
from unittest.mock import patch

from finvis.competition_launcher import Registry, compatible_health, browser_environment, write_json
from finvis.persistent_world import StorageLocation, competition_directory, production_directory
from finvis.user_runtime import UserRuntime
from finvis.application import DemoApplication
from finvis.web import DemoServer
from finvis.world_store import current_assets
from tests.test_agent_understanding import SequenceProvider
from tests.test_generalization_gate import candidate, fact
from tests.test_interaction_simplification import commit

ROOT=Path(__file__).resolve().parents[1]

def provider():
    return SequenceProvider([candidate([fact('bank','current_asset','Bank',3000,'EUR',account='Bank')],currency='EUR')])

class CompetitionTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory(); self.root=Path(self.temp.name).resolve()/'competition'; self.registry=Registry(self.root)
    def tearDown(self): self.temp.cleanup()
    def test_first_empty_no_world_created(self):
        value=self.registry.choose(); directory,loc=self.registry.paths(value)
        runtime=UserRuntime(SequenceProvider([]),loc)
        try:
            self.assertEqual(runtime.state,'first_run');self.assertIsNone(runtime.workflow);self.assertFalse(loc.database.exists())
            self.assertEqual(self.registry.choose(),value)
            self.assertEqual(set(p.name for p in directory.iterdir()),{'instance.json','state','browser','logs','backups','tmp'})
        finally: runtime.close()
    def test_two_instances_distinct_origin_storage_and_new_preserves_old(self):
        a=self.registry.choose(); da,la=self.registry.paths(a); (da/'browser'/'sentinel').write_text('synthetic preference')
        b=self.registry.choose(new=True); db,lb=self.registry.paths(b)
        self.assertNotEqual(a['port'],b['port']);self.assertNotEqual(la.directory,lb.directory)
        self.assertEqual((da/'browser'/'sentinel').read_text(),'synthetic preference');self.assertFalse((db/'browser'/'sentinel').exists())
        self.assertEqual(self.registry.choose(identity=a['id']),a)
    def test_review_authorize_apply_restart_and_idempotency(self):
        a=self.registry.choose(); _,loc=self.registry.paths(a); runtime=UserRuntime(provider(),loc)
        try:
            w=runtime.ensure(); env=w.interpret('Bank balance EUR 3000.');view=w.prepare(env['id'])
            self.assertEqual(view['stage'],'preview'); self.assertEqual(w.store.snapshot.revision,0)
            self.assertEqual(len(current_assets(w.store.snapshot).positions),0)
            grant=w.authorize(view['proposal']['id'],view['preview']['id'],view['preview']['digest'])
            auth=grant['authorization']['id']
            receipt=w.apply(view['proposal']['id'],view['preview']['id'],view['preview']['digest'],auth)
            self.assertEqual(w.store.snapshot.revision,1)
            retry=w.apply(view['proposal']['id'],view['preview']['id'],view['preview']['digest'],auth)
            self.assertEqual(retry['receipt'],receipt['receipt'])
            self.assertEqual(w.store.snapshot.revision,1)
        finally: runtime.close()
        resumed=UserRuntime(SequenceProvider([]),loc)
        try:
            self.assertEqual(resumed.state,'ready');self.assertEqual(resumed.workflow.store.snapshot.revision,1)
            self.assertEqual(current_assets(resumed.workflow.store.snapshot).positions[0].balance.amount.minor_units,300000)
            self.assertEqual(len(resumed.workflow.store.snapshot.receipts),1)
            b=self.registry.choose(new=True); _,other=self.registry.paths(b)
            r=UserRuntime(SequenceProvider([]),other)
            try:self.assertEqual(r.state,'first_run');self.assertFalse(other.database.exists())
            finally:r.close()
        finally: resumed.close()
    def test_production_path_unchanged_and_competition_restricted(self):
        identity='a'*32;loc=StorageLocation.competition(identity)
        self.assertEqual(loc.directory,competition_directory()/'instances'/identity/'state'/'UserWorld')
        self.assertEqual(StorageLocation.production().directory,production_directory())
        with self.assertRaises(ValueError):StorageLocation(production_directory(),competition_id=identity)
        with self.assertRaises(ValueError):StorageLocation.competition('../escape')
        with self.assertRaises(ValueError):StorageLocation(self.root)
    def test_test_root_cannot_point_to_persistent_storage(self):
        with self.assertRaises(ValueError):Registry(Path.home()/'Library'/'Application Support'/'FINVIS')
    def test_missing_current_never_auto_resets(self):
        a=self.registry.choose(); (self.root/'current.json').rename(self.root/'current.saved')
        with self.assertRaises(ValueError):self.registry.choose()
        self.assertEqual(self.registry.choose(identity=a['id']),a)
    def test_missing_selected_instance_does_not_create_new(self):
        write_json(self.root/'current.json',{'instance':'b'*32})
        with self.assertRaises(FileNotFoundError):self.registry.choose()
        self.assertEqual(list((self.root/'instances').iterdir()),[])
    def test_symlink_rejected(self):
        alias=Path(self.temp.name).resolve()/'alias';alias.symlink_to(self.root,target_is_directory=True)
        with self.assertRaises(ValueError):Registry(alias)
    def test_unsafe_permissions_rejected(self):
        a=self.registry.choose(); p=self.root/'instances'/a['id']/'instance.json';p.chmod(0o644)
        with self.assertRaises(ValueError):self.registry.load(a['id'])
    def test_browser_does_not_inherit_credentials_or_provider_config(self):
        with patch.dict(os.environ,{'OPENAI_API_KEY':'synthetic-secret','FINVIS_OPENAI_ENABLED':'1','OTHER_SECRET':'test'}):
            env=browser_environment(self.root)
        self.assertNotIn('OPENAI_API_KEY',env);self.assertNotIn('FINVIS_OPENAI_ENABLED',env);self.assertNotIn('OTHER_SECRET',env)
        self.assertEqual(env['TMPDIR'],str(self.root/'tmp'))
    def test_host_empty_identity_legacy_block_and_default_unchanged(self):
        a=self.registry.choose();_,loc=self.registry.paths(a)
        s=DemoServer(('127.0.0.1',0),DemoApplication(agent_provider=SequenceProvider([])),user_location=loc,competition_id=a['id'])
        t=Thread(target=s.serve_forever,daemon=True);t.start()
        def request(path,method='GET'):
            c=http.client.HTTPConnection('127.0.0.1',s.server_port,timeout=3)
            c.request(method,path,headers={'Origin':s.origin,'X-FINVIS-Token':s.csrf});r=c.getresponse();out=(r.status,r.read());c.close();return out
        try:
            health=json.loads(request('/api/health')[1]);self.assertTrue(compatible_health(a,loc,health))
            self.assertFalse(compatible_health({**a,'id':'b'*32},loc,health))
            for path in ('/','/mobile'):
                html=request(path)[1];self.assertIn(b'/competition.js',html);self.assertIn(a['id'].encode(),html)
            self.assertEqual(request('/api/command','POST')[0],403)
            self.assertEqual(s.user_runtime.state,'first_run');self.assertFalse(loc.database.exists())
            self.assertEqual(request('/api/user/home')[0],200)
        finally:s.shutdown();s.server_close();t.join()
    def test_host_cannot_attach_production_location(self):
        with self.assertRaises(ValueError):DemoServer(user_location=StorageLocation.production(),competition_id='a'*32)
    def test_launcher_process_ready_reuse_stop_restart(self):
        command=[sys.executable,'-B','-m','finvis.competition_launcher','--test-root',str(self.root),'--no-browser']
        process=subprocess.Popen(command,cwd=ROOT,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        try:
            from finvis.competition_launcher import existing_health
            health=None
            for _ in range(100):
                if (self.root/'current.json').exists():
                    a=self.registry.choose();health=existing_health(a['port'])
                    if health:break
                if process.poll() is not None: self.fail(process.communicate())
                time.sleep(.05)
            self.assertIsNotNone(health)
            again=subprocess.run(command,cwd=ROOT,capture_output=True,text=True,timeout=10)
            self.assertEqual(again.returncode,0,again.stdout+again.stderr);self.assertIn('已运行',again.stdout)
        finally:process.terminate();process.communicate(timeout=10)
        self.assertEqual(self.registry.choose(),a)
    def test_conflicting_service_not_killed_or_reassigned(self):
        a=self.registry.choose()
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',a['port']));sock.listen()
            result=subprocess.run([sys.executable,'-B','-m','finvis.competition_launcher','--test-root',str(self.root),'--no-browser'],cwd=ROOT,capture_output=True,text=True,timeout=8)
            self.assertEqual(result.returncode,1)
            self.assertEqual(self.registry.choose()['port'],a['port'])
            self.assertEqual(sock.getsockname()[1],a['port'])
    def test_missing_state_directory_fails_closed(self):
        a=self.registry.choose(); directory,_=self.registry.paths(a)
        (directory/'state').rename(directory/'state.saved')
        with self.assertRaises(ValueError):self.registry.paths(a)
        self.assertFalse((directory/'state').exists())
    def test_initialized_state_missing_fails_closed(self):
        from finvis.persistent_world import competition_world_guard, StorageIntegrityError
        a=self.registry.choose();directory,loc=self.registry.paths(a)
        competition_world_guard(loc,initializing=True)
        with self.assertRaises(StorageIntegrityError):competition_world_guard(loc)
        with self.assertRaises(StorageIntegrityError):DemoServer(('127.0.0.1',0),DemoApplication(agent_provider=SequenceProvider([])),user_location=loc,competition_id=a['id'])
    def test_normal_synthetic_runtime_does_not_share_competition_state(self):
        a=self.registry.choose();_,loc=self.registry.paths(a)
        normal_location=StorageLocation.temporary(Path(self.temp.name).resolve()/'normal'/'UserWorld')
        normal=UserRuntime(provider(),normal_location); competition=UserRuntime(SequenceProvider([]),loc)
        try:
            w=normal.ensure(); view=w.prepare(w.interpret('Bank balance EUR 3000.')['id']); commit(w,view)
            before=normal_location.database.read_bytes()
            competition.ensure()
            self.assertEqual(competition.workflow.store.snapshot.revision,0)
            self.assertEqual(len(current_assets(competition.workflow.store.snapshot).positions),0)
            self.assertEqual(normal_location.database.read_bytes(),before)
        finally:normal.close();competition.close()
    def test_normal_host_has_no_competition_bootstrap(self):
        a=self.registry.choose();_,loc=self.registry.paths(a)
        s=DemoServer(('127.0.0.1',0),DemoApplication(agent_provider=SequenceProvider([])),user_location=loc)
        t=Thread(target=s.serve_forever,daemon=True);t.start()
        try:
            c=http.client.HTTPConnection('127.0.0.1',s.server_port);c.request('GET','/');r=c.getresponse();self.assertNotIn(b'/competition.js',r.read());c.close()
        finally:s.shutdown();s.server_close();t.join()
    def test_original_launcher_hash_unchanged(self):
        import hashlib
        hashes=json.loads((ROOT/'verification/competition-environment-01/baseline.json').read_text())
        self.assertEqual(hashlib.sha256((ROOT/'启动 FINVIS.command').read_bytes()).hexdigest(),hashes['启动 FINVIS.command'])

if __name__=='__main__':unittest.main()

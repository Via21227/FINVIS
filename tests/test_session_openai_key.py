"""Credential boundary tests: synthetic values, fake transport, isolated stores."""
import http.client,json,os,threading,unittest
from unittest.mock import patch
from finvis.agent_providers import AgentProviderConfig,ProviderError
from finvis.application import DemoApplication
from finvis.openai_provider import OpenAIProvider
from finvis.provider_selection import ProviderSelection
from tests.test_production_openai import manager,FakeTransport
from tests.test_local_ai_connection import connection,model
from tests.http_support import DemoServer
KEY='sk-synthetic-session-key-never-valid'
OTHER='sk-synthetic-replacement-never-valid'

def empty():
 p=OpenAIProvider(AgentProviderConfig(),api_key='',transport=FakeTransport());p.enabled=False
 return ProviderSelection(local=connection([model('local')]),cloud=p)

class SessionKeyTests(unittest.TestCase):
 def test_explicit_configuration_no_network_or_environment_mutation(self):
  m=empty();before=dict(os.environ);r=m.session_credential('connect',KEY,True)
  self.assertEqual(r['state'],'cloud_configured');self.assertEqual(r['credential_source'],'session')
  self.assertEqual(m.cloud.transport.calls,[]);self.assertEqual(dict(os.environ),before)
  self.assertNotIn(KEY,json.dumps(r));self.assertNotIn(KEY,json.dumps(m.status()))
 def test_consent_required(self):
  m=empty()
  for ack in [False,None,'true',1]:
   with self.assertRaises(ProviderError):m.session_credential('connect',KEY,ack)
  self.assertEqual(m.cloud.api_key,'')
 def test_invalid_input_preserves_previous_credential(self):
  m=empty();m.session_credential('connect',KEY,True);token=m.consent_token
  for key in ['',None,12,[],{},'short','a'*513,'a'*18+'\n','a'*18+' ','a'*18+'\x7f','密钥'*15]:
   with self.subTest(type=type(key).__name__):
    with self.assertRaises(ProviderError) as e:m.session_credential('connect',key,True)
    self.assertEqual(e.exception.category,'credentials_rejected');self.assertEqual(m.cloud.api_key,KEY);self.assertEqual(m.consent_token,token)
 def test_replace_invalidates_old_consent(self):
  m=empty();old=m.session_credential('connect',KEY,True)['cloud_consent_token'];new=m.session_credential('connect',OTHER,True)['cloud_consent_token']
  self.assertNotEqual(old,new)
  with self.assertRaises(ProviderError):m.authorize_request(old)
  m.authorize_request(new);m.end_request();self.assertEqual(m.cloud.api_key,OTHER)
 def test_disconnect_no_environment_fallback(self):
  m=manager();m.select_provider('openai',True);m.session_credential('connect',KEY,True);old=m.consent_token
  m.session_credential('disconnect');self.assertEqual(m.cloud.api_key,'');self.assertFalse(m.cloud.enabled)
  with self.assertRaises(ProviderError):m.authorize_request(old)
  self.assertEqual(m.selected_provider,'openai');self.assertEqual(m.status()['state'],'missing_api_key')
 def test_busy_rejects_connect_disconnect(self):
  m=empty();m.session_credential('connect',KEY,True);token=m.consent_token;m.authorize_request(token)
  for action in ['connect','disconnect']:
   with self.assertRaises(ProviderError) as e:m.session_credential(action,OTHER,True)
   self.assertEqual(e.exception.category,'provider_busy');self.assertEqual(m.cloud.api_key,KEY);self.assertEqual(token,m.consent_token)
  m.end_request()
 def test_new_process_configuration_does_not_inherit_session(self):
  with patch.dict(os.environ,{},clear=True):
   a=empty();a.session_credential('connect',KEY,True);b=ProviderSelection(local=connection([]))
   self.assertFalse(b.cloud.api_key);self.assertFalse(b.cloud.enabled);self.assertIsNone(b.consent_token)
 def test_environment_compatibility_and_restart(self):
  with patch.dict(os.environ,{'OPENAI_API_KEY':KEY,'FINVIS_OPENAI_ENABLED':'1'},clear=True):
   m=ProviderSelection(local=connection([]));self.assertEqual(m.credential_source,'environment');self.assertEqual(m.selected_provider,'lmstudio')
   m.select_provider('openai',True);self.assertTrue(m.status()['ready']);m.session_credential('disconnect')
   self.assertFalse(m.cloud.api_key);self.assertEqual(os.environ['OPENAI_API_KEY'],KEY)
   n=ProviderSelection(local=connection([]));self.assertEqual(n.cloud.api_key,KEY);self.assertFalse(n.cloud_ack)
 def test_independent_servers_do_not_share_key_or_consent(self):
  a=empty();b=empty();a.session_credential('connect',KEY,True)
  self.assertFalse(b.cloud.api_key);self.assertIsNone(b.consent_token)
 def test_rejected_remote_key_has_no_retry_or_echo(self):
  m=empty();m.session_credential('connect',KEY,True);m.cloud.transport.error='credentials_rejected'
  with self.assertRaises(ProviderError) as e:m.cloud.complete('synthetic','synthetic')
  self.assertNotIn(KEY,str(e.exception));self.assertEqual(len(m.cloud.transport.calls),1)

class CredentialHTTPTests(unittest.TestCase):
 def setUp(self):
  self.m=empty();self.server=DemoServer(app=DemoApplication(agent_provider=self.m.provider,local_ai=self.m));self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
 def tearDown(self):
  self.server.shutdown();self.server.server_close();self.thread.join()
 def request(self,payload=None,headers=None,method='POST',path='/api/ai/credential'):
  h={'Origin':self.server.origin,'X-FINVIS-Token':self.server.csrf,'Content-Type':'application/json'};h.update(headers or {})
  c=http.client.HTTPConnection('127.0.0.1',self.server.server_port);c.request(method,path,json.dumps(payload) if payload is not None else None,h);r=c.getresponse();v=(r.status,r.read(),dict(r.getheaders()));c.close();return v
 def connect(self,headers=None):return self.request(dict(action='connect',api_key=KEY,cloud_ack=True),headers)
 def test_exact_host_origin_token(self):
  for h in [{'Host':'evil.test'},{'Origin':''},{'Origin':'null'},{'Origin':'http://evil.test'},{'X-FINVIS-Token':''},{'X-FINVIS-Token':'wrong'},{'Origin':'http://127.0.0.1:9'}]:
   with self.subTest(headers=h):self.assertEqual(self.connect(h)[0],403);self.assertFalse(self.m.cloud.api_key)
 def test_exact_payload_and_size(self):
  for body in [{},{'action':'disconnect','api_key':KEY},{'action':'connect','api_key':KEY,'cloud_ack':'yes'},{'action':'connect','api_key':KEY,'cloud_ack':True,'remember':True},{'action':'other'},{'action':'connect','api_key':'a'*5000,'cloud_ack':True}]:
   self.assertEqual(self.request(body)[0],400)
  self.assertEqual(self.connect({'Content-Type':'text/plain'})[0],400)
 def test_session_lifecycle_no_financial_initialization_or_secret_response(self):
  status,raw,headers=self.connect();self.assertEqual(status,200);self.assertNotIn(KEY.encode(),raw);self.assertEqual(headers['Cache-Control'],'no-store')
  self.assertFalse(self.server.user_runtime.location.database.exists());self.assertEqual(self.m.cloud.transport.calls,[])
  status,raw,_=self.request(method='GET',path='/api/local-ai/status');self.assertNotIn(KEY.encode(),raw);self.assertNotIn(b'cloud_consent_token',raw)
  self.assertEqual(self.request({'action':'disconnect'})[0],200);self.assertFalse(self.m.cloud.api_key)
 def test_get_cannot_manage_credentials(self):self.assertEqual(self.request(method='GET')[0],404)
 def test_http_invalid_key_no_echo(self):
  status,raw,_=self.request(dict(action='connect',api_key='\n'+KEY,cloud_ack=True));self.assertEqual(status,409);self.assertNotIn(KEY.encode(),raw);self.assertFalse(self.m.cloud.api_key)

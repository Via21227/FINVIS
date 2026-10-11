"""Offline synthetic production-provider acceptance. Never connects to OpenAI."""
import json,os,threading,unittest
from unittest.mock import patch
from finvis.agent_providers import AgentProviderConfig,ProviderError
from finvis.openai_provider import OpenAIProvider
from finvis.openai_transport import Transport,decode,MODEL,INPUT_CAP,secret_free
from finvis.provider_selection import ProviderSelection
from finvis.agent_understanding import AgentUnderstandingService
from tests.test_local_ai_connection import connection,model
from tests.test_compound_future import bundle,claim
from finvis.compound_payments import interpret_bundle,schema
from tests.test_payment_meaning import price_case


def response(value):
 return dict(status='completed',model=MODEL,output=[dict(type='message',role='assistant',status='completed',content=[dict(type='output_text',text=json.dumps(value))])],usage=dict(input_tokens=2700,output_tokens=300))

class FakeTransport:
 def __init__(self,value=None):self.value=value or response({});self.calls=[];self.tokens=2700;self.error=None
 def __call__(self,method,path,body,key,timeout):
  self.calls.append((method,path,body,timeout))
  if self.error:raise ProviderError(self.error,'private detail must not escape')
  return dict(input_tokens=self.tokens) if path.endswith('input_tokens') else self.value

def cloud(transport=None):
 p=OpenAIProvider(AgentProviderConfig(),api_key='synthetic-key',transport=transport or FakeTransport());p.enabled=True;return p

def manager(p=None):return ProviderSelection(config=AgentProviderConfig(),local=connection([model('local')]),cloud=p or cloud())

class ProductionOpenAITests(unittest.TestCase):
 def test_request_matches_evaluated_format(self):
  t=FakeTransport();p=cloud(t);p.complete_explanation('frozen prompt','synthetic input',schema())
  self.assertEqual(len(t.calls),2);body=t.calls[-1][2]
  self.assertEqual(body,dict(model=MODEL,instructions='frozen prompt',input='synthetic input',text={'format':{'type':'json_schema','name':'finvis_compound_meaning','strict':True,'schema':schema()}},max_output_tokens=1536,reasoning={'effort':'low'},store=False,service_tier='default'))
 def test_missing_key_no_network(self):
  p=cloud();p.api_key=''
  with self.assertRaisesRegex(ProviderError,'unavailable'):p.complete('a','b')
  self.assertEqual(p.transport.calls,[])
 def test_disabled_no_network(self):
  p=cloud();p.enabled=False
  with self.assertRaises(ProviderError) as e:p.complete('a','b')
  self.assertEqual(e.exception.category,'cloud_disabled');self.assertFalse(p.transport.calls)
 def test_invalid_key_not_in_error(self):
  p=cloud();p.api_key='secret\nheader'
  with self.assertRaises(ProviderError) as e:p.complete('a','b')
  self.assertNotIn(p.api_key,str(e.exception));self.assertFalse(p.transport.calls)
 def test_only_approved_model(self):
  p=cloud();p.model='other'
  with self.assertRaises(ProviderError) as e:p.complete('a','b')
  self.assertEqual(e.exception.category,'model_unavailable')
 def test_input_cap_before_inference(self):
  t=FakeTransport();t.tokens=INPUT_CAP+1;p=cloud(t)
  with self.assertRaises(ProviderError) as e:p.complete('a','b')
  self.assertEqual(e.exception.category,'input_token_limit');self.assertEqual(len(t.calls),1)
 def test_invalid_count_no_inference(self):
  for n in [None,True,-1,0,'100']:
   t=FakeTransport();t.tokens=n
   with self.assertRaises(ProviderError):cloud(t).complete('a','b')
   self.assertEqual(len(t.calls),1)
 def test_one_attempt_for_every_transport_error(self):
  for code in ['credentials_rejected','access_denied','rate_limited','insufficient_credits','timeout','transport_interrupted','model_unavailable']:
   t=FakeTransport();t.error=code
   with self.assertRaises(ProviderError) as e:cloud(t).complete('a','b')
   self.assertEqual(e.exception.category,code);self.assertEqual(len(t.calls),1)
 def test_invalid_candidate_never_enters_paid_repair_loop(self):
  t=FakeTransport(response({'invalid':True}));service=AgentUnderstandingService(cloud(t))
  with self.assertRaises(ProviderError):service.interpret('Synthetic input','g',0)
  self.assertEqual(len(t.calls),2);self.assertIsNone(service.candidate)
 def test_incomplete_response_not_parsed(self):
  x=response({});x['status']='incomplete'
  with self.assertRaises(ProviderError) as e:decode(x)
  self.assertEqual(e.exception.category,'truncated_response')
 def test_refusal(self):
  x=response({});x['output'][0]['content']=[dict(type='refusal',refusal='no')]
  with self.assertRaises(ProviderError) as e:decode(x)
  self.assertEqual(e.exception.category,'provider_refusal')
 def test_invalid_response_shapes(self):
  for x in [None,{},dict(status='completed',output=None),dict(status='completed',output=[{}]),dict(status='completed',output=[])]:
   with self.assertRaises(ProviderError):decode(x)
 def test_native_currency_and_unknown_pass_through_unchanged(self):
  b=bundle(claim('lamp','lamp','40',currency='EUR',source='I might buy a lamp for EUR 40.'))
  p=cloud(FakeTransport(response(dict(version=2,meaning=b,payments=[]))))
  actual,_=interpret_bundle(p,'I might buy a lamp for EUR 40.')
  self.assertEqual(actual['claims'][0]['money'],b['claims'][0]['money'])
 def test_payment_question_and_free_text_remain_deterministic(self):
  for zh in [False,True]:
   text,b=price_case(zh);b['ambiguities']=[];t=FakeTransport(response(dict(version=2,meaning=b,payments=[])))
   actual,meta=interpret_bundle(cloud(t),text)
   self.assertTrue(actual['ambiguities']);q=actual['ambiguities'][0]
   answer='这是包含定金的总价。' if zh else "That's the full price."
   result,_=interpret_bundle(cloud(t),text,prior=actual,answer={'ambiguity_id':q['id'],'text':answer},prior_ledger=meta['evidence_ledger'])
   self.assertEqual(len(t.calls),2);self.assertTrue(result.get('payment_resolutions'))
 def test_secret_output_rejected(self):
  with self.assertRaises(ProviderError):secret_free({'a':'synthetic-key'},'synthetic-key')
 def test_secret_in_input_rejected_without_request(self):
  p=cloud()
  with self.assertRaises(ProviderError):p.complete('a','synthetic-key')
  self.assertFalse(p.transport.calls)
 def test_cloud_defaults_disabled(self):
  with patch.dict(os.environ,{},clear=True):self.assertFalse(OpenAIProvider(AgentProviderConfig()).enabled)
 def test_default_local_and_refresh_never_contacts_cloud(self):
  m=manager();self.assertEqual(m.refresh()['provider_id'],'lmstudio');self.assertFalse(m.cloud.transport.calls)
 def test_cloud_selection_needs_explicit_ack(self):
  m=manager()
  with self.assertRaises(ProviderError):m.select_provider('openai')
  self.assertEqual(m.selected_provider,'lmstudio')
 def test_cloud_selection_does_not_call_api(self):
  m=manager();s=m.select_provider('openai',True)
  self.assertEqual(s['state'],'cloud_configured');self.assertTrue(s['cloud_consent_token']);self.assertFalse(m.cloud.transport.calls)
  self.assertNotIn('cloud_consent_token',m.refresh());self.assertNotIn('synthetic-key',json.dumps(s))
 def test_ack_required_per_tab(self):
  m=manager();s=m.select_provider('openai',True)
  for token in [None,'wrong']:
   with self.assertRaises(ProviderError):m.authorize_request(token)
  m.authorize_request(s['cloud_consent_token']);self.assertTrue(m.refresh()['busy']);m.end_request()
 def test_switch_blocked_during_call(self):
  m=manager();s=m.select_provider('openai',True);m.authorize_request(s['cloud_consent_token'])
  with self.assertRaises(ProviderError):m.select_provider('lmstudio')
  with self.assertRaises(ProviderError):m.authorize_request(s['cloud_consent_token'])
  m.end_request();m.select_provider('lmstudio');self.assertEqual(m.refresh()['selected_model'],'local')
 def test_stable_reference_and_no_fallback(self):
  m=manager();router=m.provider;m.select_provider('openai',True);m.cloud.transport.error='timeout'
  with self.assertRaises(ProviderError):router.complete('a','b')
  self.assertIs(router,m.provider);self.assertEqual(m.selected_provider,'openai')
 def test_restart_requires_new_ack(self):
  m=manager();old=m.select_provider('openai',True)['cloud_consent_token'];fresh=manager()
  self.assertEqual(fresh.selected_provider,'lmstudio');self.assertIsNone(fresh.consent_token)
  fresh.select_provider('openai',True)
  with self.assertRaises(ProviderError):fresh.authorize_request(old)
 def test_model_preference_preserved(self):
  m=manager();m.select('local');m.select_provider('openai',True);m.select_provider('lmstudio')
  self.assertEqual(m.refresh()['selected_model'],'local')
 def test_environment_openai_does_not_skip_ack(self):
  m=ProviderSelection(config=AgentProviderConfig(provider='openai'),local=connection([model('local')]),cloud=cloud())
  self.assertEqual(m.refresh()['state'],'cloud_consent_required')
  with self.assertRaises(ProviderError):m.provider.complete('a','b')

class HttpErrorTests(unittest.TestCase):
 def test_status_mapping_and_fixed_tls_host(self):
  for status,code,raw in [(401,'credentials_rejected',b'private'),(403,'access_denied',b'private'),(404,'model_unavailable',b''),(429,'rate_limited',b''),(429,'insufficient_credits',b'{"error":{"code":"insufficient_quota"}}'),(500,'provider_server_error',b'private'),(302,'provider_http_error',b'')]:
   with patch('finvis.openai_transport.http.client.HTTPSConnection') as c:
    res=c.return_value.getresponse.return_value;res.status=status;res.read.return_value=raw
    with self.assertRaises(ProviderError) as e:Transport()('POST','/v1/responses',{},'dummy',1)
    self.assertEqual(e.exception.category,code);self.assertNotIn('private',str(e.exception));c.assert_called_once_with('api.openai.com',timeout=1)
 def test_nonallowlisted_endpoint_blocked(self):
  with patch('finvis.openai_transport.http.client.HTTPSConnection') as c:
   with self.assertRaises(ProviderError):Transport()('GET','https://evil.invalid',{},'dummy',1)
   c.assert_not_called()
 def test_malformed_json_and_duplicate_keys(self):
  for raw in [b'not-json',b'{"x":1,"x":2}']:
   with patch('finvis.openai_transport.http.client.HTTPSConnection') as c:
    res=c.return_value.getresponse.return_value;res.status=200;res.read.return_value=raw
    with self.assertRaises(ProviderError) as e:Transport()('POST','/v1/responses',{},'dummy',1)
    self.assertEqual(e.exception.category,'malformed_response')

class PrivacyAndRecoveryTests(unittest.TestCase):
 def test_cloud_context_drops_unmentioned_records(self):
  from types import SimpleNamespace as N
  from finvis.compound_workflow import CompoundWorkflow
  from unittest.mock import patch
  refs=[N(record_kind='account',ref=N(id=x)) for x in ['Harbor','Unrelated account']]
  records={x.ref.id:N(label=x.ref.id) for x in refs}
  snapshot=N(world=N(members=refs),get=lambda x:records[x.ref.id])
  w=N(authority=N(read=lambda:N(snapshot=snapshot)),understanding=N(_provider=lambda:N(cloud=True)))
  with patch('finvis.compound_workflow._wire',return_value={'test':'reference'}):
   result=CompoundWorkflow(w)._context('I paid EUR 3 from Harbor.')
   self.assertEqual([x['label'] for x in result],['Harbor'])
   self.assertEqual(CompoundWorkflow(w)._context('I paid EUR 3 for lunch.'),[])
   self.assertNotIn('balance',json.dumps(result))
 def test_cancelled_and_stale_workflow_still_use_existing_rules(self):
  # Production transport -> actual coordinator; no model/cloud network.
  from pathlib import Path
  import tempfile
  from finvis.user_runtime import UserRuntime,runtime_location
  from finvis.i3_workflow import I3Error
  b=bundle(claim('lamp','lamp','40',currency='EUR',source='I might buy a lamp for EUR 40.'))
  m=manager(cloud(FakeTransport(response(dict(version=2,meaning=b,payments=[])))));m.select_provider('openai',True)
  rt=UserRuntime(m.provider,runtime_location(Path(tempfile.mkdtemp(prefix='finvis-openai-test-')).resolve()))
  try:
   w=rt.ensure();out=w.compound.interpret('I might buy a lamp for EUR 40.')['routing']
   self.assertEqual(w.store.snapshot.revision,0)
   w.stop_interpretation(out['bundle_id'])
   with self.assertRaises(I3Error):w.compound.future(dict(bundle_id=out['bundle_id'],revision=out['revision']))
   self.assertEqual(w.store.snapshot.revision,0)
  finally:rt.close()
 def test_runtime_and_demo_share_same_router_without_database_open(self):
  from finvis.application import DemoApplication
  from finvis.user_runtime import UserRuntime,runtime_location
  from pathlib import Path
  import tempfile
  m=manager();app=DemoApplication(local_ai=m)
  rt=UserRuntime(app.agent.provider,runtime_location(Path(tempfile.mkdtemp(prefix='finvis-openai-test-')).resolve()))
  try:
   self.assertIs(rt.provider,m.provider);self.assertFalse(rt.location.database.exists())
   m.select_provider('openai',True);self.assertEqual(rt.provider.name,'openai');self.assertFalse(rt.location.database.exists())
  finally:rt.close()

class CrossProviderPrivacyTests(unittest.TestCase):
 def test_local_only_reservation_rejects_cloud(self):
  m=manager();m.select_provider('openai',True)
  with self.assertRaises(ProviderError) as e:m.begin_local_request()
  self.assertEqual(e.exception.category,'local_only');self.assertFalse(m.cloud.transport.calls);self.assertFalse(m.refresh()['busy'])
 def test_local_only_reservation_prevents_switch(self):
  m=manager();m.begin_local_request()
  try:
   with self.assertRaises(ProviderError):m.select_provider('openai',True)
   self.assertEqual(m.selected_provider,'lmstudio')
  finally:m.end_request()
 def test_context_word_edges_and_answer_reference(self):
  from types import SimpleNamespace as N
  from finvis.compound_workflow import CompoundWorkflow
  w=N(understanding=N(_provider=lambda:N(cloud=True)))
  c=CompoundWorkflow(w);rows=[dict(label=x) for x in ['A','Harbor','Secret','房租']]
  self.assertEqual(c._provider_context('I paid from Harbor.',rows),[dict(label='Harbor')])
  self.assertEqual(c._provider_context('房租已付。',rows),[dict(label='房租')])
  self.assertEqual(c._provider_context('Which account?\nA',rows),[dict(label='A')])
 def test_initial_context_built_after_provider_reserved(self):
  from pathlib import Path
  import tempfile
  from finvis.user_runtime import UserRuntime,runtime_location
  m=manager();rt=UserRuntime(m.provider,runtime_location(Path(tempfile.mkdtemp(prefix='finvis-openai-test-')).resolve()))
  try:
   w=rt.ensure();observed=[]
   def reserve(run):
    token=m.select_provider('openai',True)['cloud_consent_token'];m.authorize_request(token)
    try:return run()
    finally:m.end_request()
   def context(text):observed.append(m.provider.cloud);return []
   with patch.object(w.compound,'_context',side_effect=context),patch('finvis.compound_workflow.interpret_bundle',side_effect=ProviderError('test_stop','synthetic')):
    with self.assertRaises(ProviderError):w.compound.interpret('Synthetic EUR 30 choice.',provider_call=reserve)
   self.assertEqual(observed,[True]);self.assertEqual(w.store.snapshot.revision,0)
  finally:rt.close()
 def test_local_pending_context_rechecked_on_cloud_answer(self):
  from pathlib import Path
  import tempfile
  from finvis.user_runtime import UserRuntime,runtime_location
  text,b=price_case(False);t=FakeTransport(response(dict(version=2,meaning=b,payments=[])));m=manager(cloud(t));m.select_provider('openai',True)
  rt=UserRuntime(m.provider,runtime_location(Path(tempfile.mkdtemp(prefix='finvis-openai-test-')).resolve()))
  try:
   w=rt.ensure();out=w.compound.interpret(text)['routing'];pending=w.compound.pending[out['bundle_id']]
   pending['context']=[dict(id='unrelated',label='Unrelated private account',kind='account',ref={})]
   m.select_provider('lmstudio');q=w.compound._question(pending);seen=[]
   def reserve(run):
    token=m.select_provider('openai',True)['cloud_consent_token'];m.authorize_request(token)
    try:return run()
    finally:m.end_request()
   def capture(provider,source,context,**kw):
    seen.append((context,kw['prior'],source));raise ProviderError('test_stop','synthetic')
   with patch('finvis.compound_workflow.interpret_bundle',side_effect=capture):
    with self.assertRaises(ProviderError):w.compound.answer(dict(bundle_id=out['bundle_id'],revision=out['revision'],ambiguity_id=q['id'],answer='The total price.'),reserve)
   self.assertEqual(seen[0][0],[]);self.assertEqual(seen[0][1],pending['bundle']);self.assertEqual(seen[0][2],text);self.assertEqual(w.store.snapshot.revision,0)
  finally:rt.close()

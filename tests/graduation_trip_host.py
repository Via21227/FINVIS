"""Isolated typed synthetic prerequisites; no model response fixtures.

Initial financial seed explicitly uses store preview/authorize/execute. This is
NOT a verified consumer natural-language intake and must stay PARTIAL in report.
Future is created through existing structured production prepare/confirm APIs.
"""
from pathlib import Path
from dataclasses import replace
import json,signal,sys
from finvis.application import DemoApplication
from finvis.agent_providers import ProviderError
from finvis.web import DemoServer
from finvis.user_runtime import runtime_location
from finvis.world_commands import PutRecord,FinancialWorldCommand
from finvis.domain import Currency,Ref
from finvis.future_experience import wire_ref
from tests.test_world_assets import account,position,exact
from tests.test_persistent_world import changes
from tests.test_future_experience import price

class NoInference:
 name='offline-no-inference'
 def complete(self,*a):raise ProviderError('offline_acceptance_gate','Real model authorization pending')
 def complete_explanation(self,*a):raise ProviderError('offline_acceptance_gate','Real model authorization pending')

class OfflineApplication(DemoApplication):
 def __init__(self):super().__init__(agent_provider=NoInference())
 def local_ai_status(self):
  return dict(provider='Offline acceptance',connection='unavailable',endpoint=None,selected_model=None,models=[],loaded_model_ids=[],state='no_model_loaded',ready=False,requests_enabled=False,busy=False,last_successful_check=None,error_category=None)

def start(root,lang='en',port=0,seed=True):
 root=Path(root).resolve()
 if not root.name.startswith('finvis-graduation-'):raise ValueError('Dedicated synthetic root required')
 s=DemoServer(('127.0.0.1',port),OfflineApplication(),user_location=runtime_location(root));w=s.ensure_user_workflow();store=w.store
 if seed and not store.snapshot.revision:
  a=replace(account('bank','银行' if lang=='zh-CN' else 'Bank'),owner_id=w.context.owner_id,world_id=w.context.world_id)
  p=replace(position('bank',a,exact(3000,Currency.EUR),Currency.EUR),owner_id=a.owner_id,world_id=a.world_id)
  c=changes(store,[PutRecord('bank/account',a),PutRecord('bank/position',p)],'explicit-synthetic-bank')
  before=store.snapshot.revision;v=store.preview(a.owner_id,c,Ref('trip/bank-preview',1));assert store.snapshot.revision==before
  g=store.authorize(a.owner_id,v,Ref('trip/bank-authorization',1),c.created_at);assert store.snapshot.revision==before
  cmd=FinancialWorldCommand('trip/bank-command',a.owner_id,c,v.ref,v.digest,g.ref,c.created_at)
  receipt=store.execute(cmd);assert store.execute(cmd)==receipt
  (root/'initial-review.json').write_text(json.dumps(dict(synthetic=True,model_response_fixture=False,consumer_intake_verified=False,typed_fixture='EUR3000 Bank BEFORE flight payment',preview_digest=v.digest,revision_before=before,revision_after=store.snapshot.revision,duplicate_same_receipt=True),indent=2)+'\n')
 return s

def labels(lang):
 return dict(trip='毕业旅行 · 11月' if lang=='zh-CN' else 'Graduation trip · November',flight='机票' if lang=='zh-CN' else 'Flight',a='航班 A' if lang=='zh-CN' else 'Flight A',b='航班 B' if lang=='zh-CN' else 'Flight B',hotel='酒店' if lang=='zh-CN' else 'Hotel',camera='相机' if lang=='zh-CN' else 'Camera',reimbursement='报销（待到账）' if lang=='zh-CN' else 'Reimbursement (not received)')

def build_trip(x,lang='en'):
 L=labels(lang);refs={};opts={}
 def keep(key,amount=None,parent=None,label=None,when=None,direction=None,node=None):
  title=L[key];d=dict(text='Explicit synthetic graduation trip in November 2026: '+title,title=title)
  if amount is not None:d['amount']={**price(amount,'EUR'),'precision':'exact'}
  if parent:d['parent']=parent
  if label:d['label']=label
  if when:d['money_timing_text']=when
  if direction:d['direction']=direction
  if node:d['node']=node;d.pop('title')
  p=x.prepare(d);x.confirm(dict(proposal_id=p['proposal_id']))
  rows=x.listing()['records'];n=node or next(r['ref'] for r in rows if r['ref']['kind']=='node' and r.get('title')==title)
  o=next((r['ref'] for r in rows if r['ref']['kind']=='option' and r.get('label')==(label or title)),None)
  return n,o
 refs['trip'],_=keep('trip')
 refs['flight'],opts['a']=keep('flight',900,refs['trip'],L['a'],'2026-10')
 _,opts['b']=keep('flight',1400,label=L['b'],node=refs['flight'],when='unspecified')
 refs['hotel'],opts['hotel']=keep('hotel',1200,refs['trip'],when='2026-11')
 refs['camera'],opts['camera']=keep('camera',600,refs['trip'],when='2026-12')
 refs['reimbursement'],opts['reimbursement']=keep('reimbursement',1000,when='2026-12',direction='INFLOW')
 return dict(roots=[refs['trip'],refs['reimbursement']],options=[opts[k] for k in ('a','hotel','camera','reimbursement')]),refs,opts

if __name__=='__main__':
 s=start(sys.argv[1],sys.argv[2] if len(sys.argv)>2 else 'en',int(sys.argv[3]) if len(sys.argv)>3 else 0)
 if '--story' in sys.argv and not s.user_runtime.future_experience.listing()['records']:build_trip(s.user_runtime.future_experience,sys.argv[2])
 signal.signal(signal.SIGTERM,lambda *_:(_ for _ in ()).throw(KeyboardInterrupt()))
 print(json.dumps(dict(url=s.origin,root=str(Path(sys.argv[1]).resolve()),fixture='Typed synthetic bank via preview/authorization; no model responses; initial consumer intake NOT verified')),flush=True)
 try:s.serve_forever(poll_interval=.05)
 except KeyboardInterrupt:pass
 finally:s.server_close()

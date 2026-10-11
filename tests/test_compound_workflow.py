"""Real coordinators and temporary durable stores; fake semantics disclosed."""
import json
from pathlib import Path
import tempfile
from copy import deepcopy
from threading import Thread, Event
import unittest
from finvis.user_runtime import UserRuntime, runtime_location
from finvis.i3_workflow import I3Error
from finvis.agent_providers import ProviderError
from finvis.world_history_replay import replay_history
from tests.test_compound_future import claim,bundle
from tests.test_interaction_simplification import commit
from tests.test_generalization_gate import candidate,fact

class BundleProvider:
 name='synthetic_compound'
 def __init__(self,values):self.values=list(values);self.calls=0
 def complete_explanation(self,prompt,text,schema):
  self.calls+=1;return json.dumps(self.values.pop(0),ensure_ascii=False),dict(provider=self.name,model='fixture-not-live',latency_ms=0)
 def complete(self,prompt,text):return self.complete_explanation(prompt,text,None)

def sample():
 return bundle(claim('cash','Harbor','2400',currency='EUR',kind='current_asset',source='Harbor balance EUR 2400'),
  claim('course','Course','450',currency='EUR',source='I am considering Course EUR 450'))

def original(b):return '. '.join(c['source'] for c in b['claims'])+'.'

class CompoundWorkflowTests(unittest.TestCase):
 def boot(self,b=None,root=None):
  self.provider=BundleProvider([b or sample()]);root=root or Path(tempfile.mkdtemp(prefix='finvis-compound-')).resolve()
  rt=UserRuntime(self.provider,runtime_location(root));self.addCleanup(rt.close)
  w=rt.ensure();return w,rt,root
 def understand(self,w,b=None):
  b=b or sample();return w.interpret(original(b),route=True)['routing']
 def payload(self,v,**extra):return dict(bundle_id=v['bundle_id'],revision=v['revision'],**extra)
 def test_router_invokes_bundle_before_reality_cue(self):
  b=bundle(claim('paid','Repair','18',currency='EUR',kind='actual_expense',source='I paid EUR 18 for Repair'),claim('later','Paint','80',currency='EUR',source='I am considering Paint EUR 80'))
  w,_,_=self.boot(b);v=self.understand(w,b)
  self.assertEqual(self.provider.calls,1);self.assertEqual(len(v['claims']),2);self.assertEqual(w.store.snapshot.revision,0)
  self.assertEqual(v['question']['field'],'account');self.assertTrue(v['future_available'])
 def test_current_fact_and_future_separate_review_not_auto_apply(self):
  b=sample();b['claims'][0]['account']='Harbor';w,_,_=self.boot(b);v=self.understand(w,b)
  before=w.store.snapshot;out=w.compound.stage(self.payload(v,claim_id='cash'))
  self.assertEqual(out['review']['stage'],'preview',out);self.assertIs(w.store.snapshot,before)
  done=commit(w,out['review']);self.assertEqual(done['receipt_revision'],1)
  view=w.compound.view(dict(bundle_id=v['bundle_id']));self.assertEqual(view['claims'][0]['state'],'complete');self.assertTrue(view['future_available'])
  trial=w.compound.future(self.payload(view))['trial'];self.assertEqual(trial['stage'],'trial');self.assertEqual(w.store.snapshot.revision,1)
 def test_same_stage_retry_returns_same_preview(self):
  b=sample();b['claims'][0]['account']='Harbor';w,_,_=self.boot(b);v=self.understand(w,b);p=self.payload(v,claim_id='cash')
  a=w.compound.stage(p);c=w.compound.stage(p);self.assertEqual(a['review']['preview'],c['review']['preview'])
 def test_committed_retry_one_receipt(self):
  b=sample();b['claims'][0]['account']='Harbor';w,_,_=self.boot(b);v=self.understand(w,b);p=self.payload(v,claim_id='cash')
  out=w.compound.stage(p);commit(w,out['review']);before=w.store.snapshot
  out=w.compound.stage(p);self.assertIn('operation',out);self.assertIs(w.store.snapshot,before)
 def test_stop_ready_compound_rejects_followup(self):
  w,_,_=self.boot();v=self.understand(w);w.stop_interpretation(v['bundle_id'])
  with self.assertRaisesRegex(I3Error,'compound_expired'):w.compound.future(self.payload(v))
 def test_cancel_keeps_original_without_financial_effect(self):
  w,_,_=self.boot();v=self.understand(w);s=w.store.snapshot;w.compound.cancel(dict(bundle_id=v['bundle_id']))
  self.assertIs(w.store.snapshot,s)
  with self.assertRaises(I3Error):w.compound.view(dict(bundle_id=v['bundle_id']))
 def test_restart_expires_pending_but_recovers_committed(self):
  b=sample();b['claims'][0]['account']='Harbor';w,rt,root=self.boot(b);v=self.understand(w,b)
  out=w.compound.stage(self.payload(v,claim_id='cash'));a=w.authorize(out['review']['proposal']['id'],out['review']['preview']['id'],out['review']['preview']['digest'])
  p=dict(proposal_id=out['review']['proposal']['id'],preview_id=out['review']['preview']['id'],digest=out['review']['preview']['digest'],authorization_id=a['authorization']['id'])
  first=w.apply(**p);rt.close();w2,_,_=self.boot(root=root)
  self.assertEqual(w2.apply(**p)['receipt'],first['receipt']);self.assertEqual(w2.store.snapshot.revision,1)
  self.assertEqual(replay_history(w2.store.export_replay()),w2.store.snapshot)
  with self.assertRaisesRegex(I3Error,'compound_expired'):w2.compound.view(dict(bundle_id=v['bundle_id']))
 def test_unconsumed_grant_does_not_survive_restart(self):
  b=sample();b['claims'][0]['account']='Harbor';w,rt,root=self.boot(b);v=self.understand(w,b);out=w.compound.stage(self.payload(v,claim_id='cash'))['review'];a=w.authorize(out['proposal']['id'],out['preview']['id'],out['preview']['digest']);rt.close();w2,_,_=self.boot(root=root)
  with self.assertRaises(I3Error):w2.apply(out['proposal']['id'],out['preview']['id'],out['preview']['digest'],a['authorization']['id'])
  self.assertEqual(w2.store.snapshot.revision,0)
 def test_question_answer_updates_same_bundle_without_model(self):
  b=bundle(claim('paid','Repair','18',currency='EUR',kind='actual_expense',source='I paid EUR 18 for Repair'),claim('later','Paint','80',currency='EUR',source='I am considering Paint EUR 80'))
  w,_,_=self.boot(b);v=self.understand(w,b);out=w.compound.answer(self.payload(v,ambiguity_id=v['question']['id'],answer='Harbor'))['compound']
  self.assertEqual(out['bundle_id'],v['bundle_id']);self.assertIsNone(out['question']);self.assertEqual(out['revision'],2);self.assertEqual(out['source'],v['source']);self.assertEqual(self.provider.calls,1)
  with self.assertRaisesRegex(I3Error,'compound_stale'):w.compound.answer(self.payload(v,ambiguity_id=v['question']['id'],answer='Harbor'))
 def test_new_simple_interpretation_supersedes_bundle(self):
  w,_,_=self.boot();v=self.understand(w);self.provider.values.append(candidate([fact('x','current_asset','Elm',23,'EUR',account='Elm')],'EUR'))
  w.interpret('Elm balance EUR 23.',route=True)
  with self.assertRaisesRegex(I3Error,'compound_expired'):w.compound.view(dict(bundle_id=v['bundle_id']))
 def test_no_concurrent_stages(self):
  b=sample();b['claims'][0]['account']='Harbor';b['claims'].append(claim('other','Elm','20',currency='EUR',kind='current_asset',source='Elm balance EUR 20'));b['claims'][-1]['account']='Elm'
  w,_,_=self.boot(b);v=self.understand(w,b);w.compound.stage(self.payload(v,claim_id='cash'))
  with self.assertRaisesRegex(I3Error,'compound_review_active'):w.compound.stage(self.payload(v,claim_id='other'))
 def test_context_bounded_without_balances(self):
  w,_,_=self.boot();self.assertEqual(w.compound._context('anything'),[])
 def test_no_client_payload_can_authorize(self):
  w,_,_=self.boot();v=self.understand(w)
  with self.assertRaises(I3Error):w.compound.stage(self.payload(v,claim_id='cash',authorization=True))
  self.assertEqual(w.store.snapshot.revision,0)

 # Lifecycle tests exercise real receipts and the existing current-balance gate.
 def seed_plan(self,w):
  from tests.test_agent_understanding import SequenceProvider
  fs=[fact('cash','current_asset','Harbor',2400,'EUR',account='Harbor'),fact('cost','planned_expense','Course',500,'EUR')]
  w.understanding.provider=SequenceProvider([candidate(fs,'EUR')])
  env=w.interpret('Harbor balance EUR 2400. I plan to buy Course EUR 500.',route=False)
  commit(w,w.prepare(env['id']))
  w.understanding.provider=self.provider
 def actual_bundle(self,account=True,kind='actual_expense',value='94'):
  c=claim('paid','Course',value,currency='EUR',kind=kind,source=('I received a refund of EUR '+value+' for Course' if kind=='refund' else 'I paid EUR '+value+' for Course')+(' from Harbor' if account else ''))
  c['account']='Harbor' if account else None
  return bundle(c,claim('later','Materials','80',currency='EUR',source='I am considering Materials EUR 80'))
 def lifecycle_questions(self,w,r):
  seen=[]
  while r['stage']=='question':
   q=r['question'];key=q['key'];seen.append(key)
   value='yes' if key in ('same_plan','same_event') else 'apply_to_prior_balance' if key=='coverage' else 'none' if key=='earmark' else q['options'][0]['value']
   r=w.plan_reality.answer(dict(proposal_id=r['id'],key=key,value=value))
  return r,seen
 def balance_preview(self,w,v):
  r=w.compound.stage(self.payload(v,claim_id='paid'))['routing']
  r,seen=self.lifecycle_questions(w,r);self.assertEqual(r['stage'],'blocked',r)
  return w.plan_reality.review_balance(dict(proposal_id=r['id'],amount=None)),r
 def grant_lifecycle(self,w,v):
  a=w.plan_reality.authorize(dict(preview_id=v['preview']['id'],digest=v['preview']['digest']))
  return dict(preview_id=v['preview']['id'],digest=v['preview']['digest'],authorization_id=a['authorization']['id'])
 def test_bundle_cancel_revokes_child_lifecycle_grant(self):
  b=self.actual_bundle();w,_,_=self.boot(b);self.seed_plan(w);v=self.understand(w,b)
  review,_=self.balance_preview(w,v);args=self.grant_lifecycle(w,review);before=w.store.snapshot
  w.compound.cancel(dict(bundle_id=v['bundle_id']))
  with self.assertRaises(I3Error):w.plan_reality.apply(args)
  self.assertIs(w.store.snapshot,before)
 def test_stop_bundle_revokes_child_lifecycle_grant(self):
  b=self.actual_bundle();w,_,_=self.boot(b);self.seed_plan(w);v=self.understand(w,b)
  review,_=self.balance_preview(w,v);args=self.grant_lifecycle(w,review);before=w.store.snapshot
  w.stop_interpretation(v['bundle_id'])
  with self.assertRaises(I3Error):w.plan_reality.apply(args)
  self.assertIs(w.store.snapshot,before)
 def test_simple_supersession_revokes_child_lifecycle_grant(self):
  b=self.actual_bundle();w,_,_=self.boot(b);self.seed_plan(w);v=self.understand(w,b)
  review,_=self.balance_preview(w,v);args=self.grant_lifecycle(w,review);before=w.store.snapshot
  self.provider.values.append(candidate([fact('x','current_asset','Elm',23,'EUR',account='Elm')],'EUR'))
  w.interpret('Elm balance EUR 23.',route=True)
  with self.assertRaises(I3Error):w.plan_reality.apply(args)
  self.assertIs(w.store.snapshot,before)
 def test_explicit_cancel_or_stop_revokes_future_draft(self):
  from finvis.future_space import FutureError
  for action in ('cancel','stop'):
   with self.subTest(action=action):
    w,_,_=self.boot();v=self.understand(w);p=w.compound.future(self.payload(v))['trial']
    before=w.compound_if.x.memory.read()[:2];before=(before[0].records,before[1])
    if action=='cancel':w.compound.cancel(dict(bundle_id=v['bundle_id']))
    else:w.stop_interpretation(v['bundle_id'])
    with self.assertRaises(FutureError):w.compound_if.keep(dict(proposal_id=p['proposal_id']))
    after=w.compound_if.x.memory.read();self.assertEqual((after[0].records,after[1]),before)
 def test_balance_receipt_rebases_bundle_without_completing_payment(self):
  from tests.test_settlement_expansion import live
  b=self.actual_bundle();w,_,_=self.boot(b);self.seed_plan(w);v=self.understand(w,b)
  review,_=self.balance_preview(w,v);result=w.plan_reality.apply(self.grant_lifecycle(w,review))
  self.assertEqual(result['action'],'balance_review');self.assertEqual(w.store.snapshot.revision,2)
  current=w.compound.view(dict(bundle_id=v['bundle_id']))
  self.assertEqual(current['claims'][0]['state'],'staged')
  future=w.compound.future(self.payload(current))['trial'];self.assertEqual(future['stage'],'trial')
  r,seen=self.lifecycle_questions(w,result['resume']);self.assertEqual(seen,['coverage'])
  payment=w.plan_reality.prepare(dict(proposal_id=r['id']));args=self.grant_lifecycle(w,payment)
  done=w.plan_reality.apply(args);self.assertEqual(done['action'],'actual');self.assertEqual(w.store.snapshot.revision,3)
  self.assertEqual(live(w.store,'asset_position').balance.amount.minor_units,230600)
  current=w.compound.view(dict(bundle_id=v['bundle_id']));self.assertEqual(current['claims'][0]['state'],'complete')
  second=w.compound.stage(self.payload(current,claim_id='paid'));self.assertEqual(second['operation']['receipt'],done['operation']['receipt'])
  self.assertEqual(replay_history(w.store.export_replay()),w.store.snapshot)
 def test_owned_receipt_does_not_rebase_across_external_revision(self):
  from unittest.mock import patch
  from tests.test_history_actions import apply as history_apply,request
  b=self.actual_bundle();w,_,_=self.boot(b);self.seed_plan(w);v=self.understand(w,b)
  review,_=self.balance_preview(w,v);args=self.grant_lifecycle(w,review);execute=w._execute_command
  def external_after_owned(operation):
   execute(operation)
   if operation.command.change_set.ref.id.startswith('lifecycle/balance-review/'):
    history_apply(w,request(w,'edit','account','label','Harbor renamed'))
  with patch.object(w,'_execute_command',side_effect=external_after_owned):result=w.plan_reality.apply(args)
  self.assertEqual(result['stage'],'applied');self.assertIsNone(result['resume'])
  before=w.store.snapshot
  with self.assertRaisesRegex(I3Error,'compound_world_changed'):w.compound.future(self.payload(v))
  self.assertIs(w.store.snapshot,before)
 def test_retained_future_retry_after_owned_reality_receipt_is_same_effect(self):
  b=sample();b['claims'][0]['account']='Harbor';w,_,_=self.boot(b);v=self.understand(w,b)
  first=w.compound.future(self.payload(v))['trial'];w.compound_if.keep(dict(proposal_id=first['proposal_id']))
  memory=w.compound_if.x.memory.read()[:2];memory=(memory[0].records,memory[1]);commit(w,w.compound.stage(self.payload(v,claim_id='cash'))['review'])
  second=w.compound.future(self.payload(v))['trial']
  self.assertEqual(second['proposal_id'],first['proposal_id']);after=w.compound_if.x.memory.read();self.assertEqual((after[0].records,after[1]),memory)
 def test_account_clarification_flows_into_lifecycle_without_reasking_source(self):
  b=self.actual_bundle(account=False);w,_,_=self.boot(b);self.seed_plan(w);v=self.understand(w,b)
  v=w.compound.answer(self.payload(v,ambiguity_id=v['question']['id'],answer='Harbor'))['compound']
  r=w.compound.stage(self.payload(v,claim_id='paid'))['routing'];r,seen=self.lifecycle_questions(w,r)
  self.assertEqual(seen,['same_plan']);self.assertEqual(r['account'],'Harbor')
  self.assertIn('Harbor',r['source_text']);self.assertEqual(self.provider.calls,1)
 def test_lifecycle_uses_claim_amount_not_unrelated_future_amount(self):
  b=self.actual_bundle();w,_,_=self.boot(b);self.seed_plan(w);v=self.understand(w,b)
  r=w.compound.stage(self.payload(v,claim_id='paid'))['routing']
  p=w.plan_reality.proposals[r['id']]
  self.assertEqual(p['answers']['amount'],dict(currency='EUR',precision='exact',value='94',range_min=None,range_max=None))
  self.assertNotIn('Materials',r['source_text']);self.assertEqual(w.store.snapshot.revision,1)
 def test_unresolved_relation_blocks_only_related_reality_claims(self):
  from tests.test_compound_future import rel
  for relation in ('conditional_on','alternative','part_of','replacement'):
   with self.subTest(relation=relation):
    a=claim('a','Course deposit','20',currency='EUR',kind='actual_expense',source='I paid EUR 20 Course deposit from Harbor');a['account']='Harbor'
    b=claim('b','Course fee','80',currency='EUR',kind='actual_expense',source='I paid EUR 80 Course fee from Harbor');b['account']='Harbor'
    c=claim('cash','Harbor','2400',currency='EUR',kind='current_asset',source='Harbor balance EUR 2400');c['account']='Harbor'
    relation_source={'conditional_on':'Course deposit depends on Course fee', 'alternative':'Either Course deposit or Course fee', 'part_of':'Course deposit is part of Course fee', 'replacement':'Course deposit replaces Course fee'}[relation]
    a['source']+='; '+relation_source
    meaning=bundle(a,b,c,relationships=[rel(relation,'a','b',relation_source)])
    w,_,_=self.boot(meaning);v=self.understand(w,meaning);before=w.store.snapshot
    with self.assertRaisesRegex(I3Error,'compound_item_unavailable'):w.compound.stage(self.payload(v,claim_id='a'))
    self.assertIs(w.store.snapshot,before)
    self.assertEqual(w.compound.stage(self.payload(v,claim_id='cash'))['review']['stage'],'preview')

 def standalone_bundle(self,kind='actual_expense',value='18',currency='EUR',precision='exact'):
  source=('I received EUR '+value+' for Gift into Harbor' if kind=='actual_inflow' else 'I paid EUR '+value+' for Repair from Harbor') if value is not None else 'I paid an unknown EUR amount for Repair from Harbor'
  c=claim('paid','Gift' if kind=='actual_inflow' else 'Repair',value,currency=currency,kind=kind,source=source,precision=precision);c['account']='Harbor'
  return bundle(c,claim('later','Materials','80',currency='EUR',source='I am considering Materials EUR 80'))
 def test_standalone_expense_and_inflow_recover_balance_without_creating_plan(self):
  from tests.test_settlement_expansion import live
  from finvis.world_planning import OccurrenceKind
  for kind,expected in (('actual_expense',238200),('actual_inflow',241800)):
   with self.subTest(kind=kind):
    b=self.standalone_bundle(kind);w,_,_=self.boot(b);self.seed_plan(w);plans=[r for r in w.store.snapshot.world.members if r.record_kind=='planned_expense']
    v=self.understand(w,b);r=w.compound.stage(self.payload(v,claim_id='paid'))['routing']
    self.assertEqual(r['stage'],'blocked');self.assertEqual(r['reason'],'current_balance_required')
    before=w.store.snapshot;review=w.plan_reality.review_balance(dict(proposal_id=r['id'],amount=None));args=self.grant_lifecycle(w,review)
    self.assertIs(w.store.snapshot,before)
    result=w.plan_reality.apply(args);r,seen=self.lifecycle_questions(w,result['resume']);self.assertEqual(seen,['coverage'])
    payment=w.plan_reality.prepare(dict(proposal_id=r['id']));args=self.grant_lifecycle(w,payment);done=w.plan_reality.apply(args)
    self.assertEqual(done['action'],'inflow' if kind=='actual_inflow' else 'actual')
    self.assertEqual(live(w.store,'asset_position').balance.amount.minor_units,expected)
    actual=live(w.store,'actual_occurrence');self.assertEqual(actual.related_entity_refs,());self.assertIsNone(actual.lineage)
    self.assertEqual(actual.kind,OccurrenceKind.INFLOW if kind=='actual_inflow' else OccurrenceKind.EXPENSE)
    self.assertEqual([r for r in w.store.snapshot.world.members if r.record_kind=='planned_expense'],plans)
    current=w.compound.view(dict(bundle_id=v['bundle_id']));self.assertEqual(current['claims'][0]['state'],'complete')
    self.assertEqual(w.compound.future(self.payload(current))['trial']['stage'],'trial')
    before=w.store.snapshot;self.assertEqual(w.plan_reality.apply(args)['operation']['receipt'],done['operation']['receipt']);self.assertIs(w.store.snapshot,before)
    self.assertEqual(replay_history(w.store.export_replay()),before)
 def test_standalone_unknown_amount_remains_local_question(self):
  b=self.standalone_bundle(value=None,precision='unknown');w,_,_=self.boot(b);self.seed_plan(w);v=self.understand(w,b);before=w.store.snapshot
  r=w.compound.stage(self.payload(v,claim_id='paid'))['routing'];self.assertEqual(r['question']['key'],'amount')
  self.assertNotIn('amount',w.plan_reality.proposals[r['id']]['answers']);self.assertIs(w.store.snapshot,before)
  self.assertEqual(w.compound.future(self.payload(v))['trial']['stage'],'trial')
  r=w.plan_reality.answer(dict(proposal_id=r['id'],key='amount',value=dict(currency='EUR',precision='exact',value='18',range_min=None,range_max=None)))
  self.assertEqual(r['stage'],'blocked');self.assertIs(w.store.snapshot,before)
 def test_missing_currency_never_defaults_or_creates_financial_effect(self):
  b=self.standalone_bundle(currency=None);b['claims'][0]['source']='I paid 18 for Repair from Harbor'
  w,_,_=self.boot(b);self.seed_plan(w);v=self.understand(w,b);before=w.store.snapshot
  with self.assertRaisesRegex(I3Error,'compound_item_unavailable'):w.compound.stage(self.payload(v,claim_id='paid'))
  self.assertIs(w.store.snapshot,before)
  self.assertEqual(w.compound.future(self.payload(v))['trial']['stage'],'trial')
 def test_standalone_payment_receipt_recovers_after_bundle_cancel_and_restart(self):
  from tests.test_plan_reality import execute
  b=self.standalone_bundle();w,rt,root=self.boot(b);self.seed_plan(w);v=self.understand(w,b)
  review,_=self.balance_preview(w,v);result,_=execute(w,review);r,_=self.lifecycle_questions(w,result['resume'])
  payment=w.plan_reality.prepare(dict(proposal_id=r['id']));done,args=execute(w,payment);before=w.store.snapshot
  w.compound.cancel(dict(bundle_id=v['bundle_id']))
  self.assertEqual(w.plan_reality.apply(args)['operation']['receipt'],done['operation']['receipt'])
  rt.close();w,_,_=self.boot(root=root)
  self.assertEqual(w.plan_reality.apply(args)['operation']['receipt'],done['operation']['receipt']);self.assertEqual(w.store.snapshot,before)
  self.assertEqual(replay_history(w.store.export_replay()),before)
 def test_refund_requires_exact_plan_and_prior_payment(self):
  b=self.actual_bundle(kind='refund',value='10');w,_,_=self.boot(b);self.seed_plan(w);v=self.understand(w,b);before=w.store.snapshot
  r=w.compound.stage(self.payload(v,claim_id='paid'))['routing']
  r=w.plan_reality.answer(dict(proposal_id=r['id'],key='same_plan',value='yes'))
  self.assertEqual(r['question']['key'],'payment_missing');self.assertIs(w.store.snapshot,before)
  self.assertEqual(w.compound.future(self.payload(v))['trial']['stage'],'trial')
 def test_refund_does_not_guess_an_unrelated_plan(self):
  b=self.actual_bundle(kind='refund',value='10');b['claims'][0]['label']='Repair';b['claims'][0]['source']='I received a refund of EUR 10 for Repair into Harbor'
  w,_,_=self.boot(b);self.seed_plan(w);v=self.understand(w,b);before=w.store.snapshot
  with self.assertRaisesRegex(I3Error,'compound_item_unavailable'):w.compound.stage(self.payload(v,claim_id='paid'))
  self.assertIs(w.store.snapshot,before);self.assertIsNone(w.plan_reality.pending)
 def test_refund_preserves_amount_and_prior_payment_lineage(self):
  from tests.test_plan_reality import execute
  from tests.test_settlement_expansion import live
  from finvis.world_commands import record_ref
  b=self.actual_bundle();w,_,_=self.boot(b);self.seed_plan(w);v=self.understand(w,b)
  review,_=self.balance_preview(w,v);result,_=execute(w,review);r,_=self.lifecycle_questions(w,result['resume'])
  execute(w,w.plan_reality.prepare(dict(proposal_id=r['id'])));paid=live(w.store,'actual_occurrence')
  refund=self.actual_bundle(kind='refund',value='10');self.provider.values.append(refund);v=self.understand(w,refund)
  r=w.compound.stage(self.payload(v,claim_id='paid'))['routing'];r,seen=self.lifecycle_questions(w,r)
  self.assertEqual(seen,['same_plan','coverage']);self.assertEqual(r['stage'],'ready')
  done,_=execute(w,w.plan_reality.prepare(dict(proposal_id=r['id'])))
  actuals=[w.store.snapshot.get(ref) for ref in w.store.snapshot.world.members if ref.record_kind=='actual_occurrence']
  returned=next(a for a in actuals if a.lineage and a.lineage.refund_of_ref)
  self.assertEqual(returned.actual_amount.amount.minor_units,1000);self.assertEqual(returned.lineage.refund_of_ref,record_ref(paid))
  self.assertEqual(live(w.store,'asset_position').balance.amount.minor_units,231600)
  self.assertEqual(w.compound.stage(self.payload(v,claim_id='paid'))['operation']['receipt'],done['operation']['receipt'])
  self.assertEqual(replay_history(w.store.export_replay()),w.store.snapshot)

 def test_claim_timing_preserves_day_month_and_unresolved_literal(self):
  from datetime import date
  from tests.test_plan_reality import execute
  from tests.test_settlement_expansion import live
  from finvis.world_values import TemporalKind
  for timing,expected in ((dict(text='2026-02-18',iso='2026-02-18',precision='day'),TemporalKind.KNOWN_DATE),
                          (dict(text='2024-02',iso='2024-02',precision='month'),TemporalKind.DATE_WINDOW),
                          (dict(text='last month',iso=None,precision='literal'),TemporalKind.UNKNOWN)):
   with self.subTest(timing=timing):
    b=self.standalone_bundle();b['claims'][0]['source']+=' on '+timing['text'];b['claims'][0]['timing']=timing
    w,_,_=self.boot(b);self.seed_plan(w);v=self.understand(w,b)
    review,_=self.balance_preview(w,v);result,_=execute(w,review);r,_=self.lifecycle_questions(w,result['resume'])
    execute(w,w.plan_reality.prepare(dict(proposal_id=r['id'])))
    occurrence=live(w.store,'actual_occurrence');self.assertEqual(occurrence.timing.kind,expected);self.assertEqual(occurrence.timing.source_text,timing['text'])
    self.assertEqual(occurrence.observation.as_of,occurrence.timing)
    if expected is TemporalKind.KNOWN_DATE:self.assertEqual(occurrence.timing.date_value,date(2026,2,18))
    if expected is TemporalKind.DATE_WINDOW:
     self.assertEqual(occurrence.timing.window_start,date(2024,2,1));self.assertEqual(occurrence.timing.window_end,date(2024,2,29))
    self.assertEqual(replay_history(w.store.export_replay()),w.store.snapshot)

 def ambiguous_future(self):
  a=claim('course','Course',None,currency='EUR',precision='unknown',source='I am considering Course in EUR, amount unknown')
  b=claim('materials','Materials','80',currency='EUR',source='I am considering Materials EUR 80')
  c=claim('transport','Transport','20',currency='EUR',source='I am considering Transport EUR 20')
  question=dict(id='price',claim_ids=['course'],field='money',question='How much is Course?',choices=['EUR 40','EUR 60'],source=a['source'])
  old=bundle(a,b,c,ambiguities=[question]);resolved=deepcopy(old)
  resolved['claims'][0]['money'].update(value='40',precision='exact');resolved['ambiguities']=[]
  return old,resolved
 def test_clarification_expires_old_unretained_future_draft(self):
  from finvis.future_space import FutureError
  b,resolved=self.ambiguous_future();w,_,_=self.boot(b);v=self.understand(w,b);old=w.compound.future(self.payload(v))['trial']
  self.provider.values.append(resolved)
  v=w.compound.answer(self.payload(v,ambiguity_id='price',answer='EUR 40'))['compound']
  self.assertEqual(v['revision'],2)
  with self.assertRaises(FutureError):w.compound_if.keep(dict(proposal_id=old['proposal_id']))
  new=w.compound.future(self.payload(v))['trial'];self.assertNotEqual(old['proposal_id'],new['proposal_id'])
  self.assertEqual(set(new['supported_claim_ids']),{'course','materials','transport'})
  self.assertEqual(w.compound_if.x.memory.read()[1],0)
 def test_retained_partial_then_clarification_preserves_remaining_without_duplicates(self):
  from finvis.future_space import FutureNode
  from finvis.future_experience import ref,wire_ref
  b,resolved=self.ambiguous_future();w,_,_=self.boot(b);v=self.understand(w,b);first=w.compound.future(self.payload(v))['trial']
  catalog,_,_,_=w.compound_if._catalog(first['proposal_id'])
  option=first['claim_options']['materials'];root=wire_ref(catalog.get(catalog.get(ref(option)).variable).node)
  w.compound_if.keep(dict(proposal_id=first['proposal_id'],roots=[root],options=[option],name='Materials only'))
  before=w.compound_if.x.memory.read()[0];self.assertEqual([r.title for r in before.latest.values() if type(r) is FutureNode],['Materials'])
  self.provider.values.append(resolved)
  v=w.compound.answer(self.payload(v,ambiguity_id='price',answer='EUR 40'))['compound']
  second=w.compound.future(self.payload(v))['trial']
  self.assertEqual(set(second['supported_claim_ids']),{'course','transport'})
  w.compound_if.keep(dict(proposal_id=second['proposal_id']))
  after=w.compound_if.x.memory.read()[0];nodes=[r for r in after.latest.values() if type(r) is FutureNode]
  self.assertEqual(sorted(r.title for r in nodes),['Course','Materials','Transport'])
  self.assertEqual(next(r for r in nodes if r.title=='Materials'),next(r for r in before.latest.values() if type(r) is FutureNode))
  self.assertEqual(w.store.snapshot.revision,0)
 def test_local_currency_answer_preserves_amount_and_does_not_call_model(self):
  b=self.standalone_bundle(currency=None);b['claims'][0]['source']='I paid 18 for Repair from Harbor'
  w,_,_=self.boot(b);self.seed_plan(w);v=self.understand(w,b);before=w.store.snapshot
  self.assertEqual(v['question']['field'],'currency')
  with self.assertRaisesRegex(I3Error,'compound_answer_unresolved'):
   w.compound.answer(self.payload(v,ambiguity_id=v['question']['id'],answer='USD'))
  v=w.compound.answer(self.payload(v,ambiguity_id=v['question']['id'],answer='EUR'))['compound']
  self.assertEqual(v['claims'][0]['money'],dict(value='18',lower=None,upper=None,currency='EUR',precision='exact'))
  self.assertEqual(self.provider.calls,1);self.assertIs(w.store.snapshot,before)
  r=w.compound.stage(self.payload(v,claim_id='paid'))['routing'];self.assertEqual(r['stage'],'blocked');self.assertEqual(r['balance']['currency'],'EUR')

 def test_retained_component_does_not_recreate_its_parent_cashflow(self):
  from finvis.future_space import FutureNode,FutureOption
  from finvis.future_experience import ref,wire_ref
  b,_=self.ambiguous_future()
  b['claims'].append(claim('kit','Kit',None,currency='EUR',precision='unknown',source='I am considering Kit in EUR, amount unknown'))
  relationship='Materials is part of Kit';b['claims'][1]['source']+='; '+relationship
  b['relationships']=[dict(kind='part_of',**{'from':'materials','to':'kit'},source=relationship)]
  resolved=deepcopy(b);resolved['claims'][0]['money'].update(value='40',precision='exact');resolved['ambiguities']=[]
  w,_,_=self.boot(b);v=self.understand(w,b);first=w.compound.future(self.payload(v))['trial']
  catalog,_,_,_=w.compound_if._catalog(first['proposal_id'])
  option=first['claim_options']['materials'];child=catalog.get(catalog.get(ref(option)).variable).node
  root=wire_ref(catalog.get(child).parent)
  w.compound_if.keep(dict(proposal_id=first['proposal_id'],roots=[root],options=[option],name='Kit materials'))
  before=w.compound_if.x.memory.read()[0]
  self.assertEqual(sorted(r.title for r in before.latest.values() if type(r) is FutureNode),['Kit','Materials'])
  self.provider.values.append(resolved)
  v=w.compound.answer(self.payload(v,ambiguity_id='price',answer='EUR 40'))['compound']
  second=w.compound.future(self.payload(v))['trial']
  self.assertEqual(set(second['supported_claim_ids']),{'course','transport'})
  self.assertIn('kit',{r['claim_id'] for r in second['blocked']})
  w.compound_if.keep(dict(proposal_id=second['proposal_id']))
  after=w.compound_if.x.memory.read()[0]
  self.assertEqual(sorted(r.title for r in after.latest.values() if type(r) is FutureNode),['Course','Kit','Materials','Transport'])
  self.assertTrue(all(r in after.records for r in before.records))
  options=[r for r in after.latest.values() if type(r) is FutureOption]
  self.assertEqual(len(options),3)
  self.assertEqual(sum(r.effects[0].observed.amount.minor_units for r in options),14000)
  self.assertEqual(w.store.snapshot.revision,0)

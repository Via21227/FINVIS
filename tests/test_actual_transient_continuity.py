"""Visible transient choices participate in actual-report review without writes."""
import unittest
from finvis.if_interaction import IfInteraction
from finvis.future_space import FutureError, FutureNode, FutureOption
from finvis.future_experience import ref
from tests import test_actual_report as report_base
from tests.test_future_experience import price

class ActualTransientContinuityTests(unittest.TestCase):
 keep=report_base.ActualReportAuthorityTests.keep
 options=report_base.ActualReportAuthorityTests.options
 apply=report_base.ActualReportAuthorityTests.apply
 seed_money=report_base.ActualReportAuthorityTests.seed_money
 records=report_base.ActualReportAuthorityTests.records
 source=report_base.ActualReportAuthorityTests.source
 def setUp(self):
  report_base.ActualReportAuthorityTests.setUp(self)
  self.i=IfInteraction(self.x);self.w.compound_if=self.i
 def draft(self,title='oak desk',value=375,previous=None):
  return self.i.propose(dict(text='Considering '+title,title=title,label=title,amount=price(value),**({'proposal_id':previous} if previous else {})))
 def report(self,trial,source=None,**kw):
  return self.a.interpret(dict(text=source or self.source(),proposal_id=trial['proposal_id'],**trial['selection'],**kw))
 def test_transient_named_quote_found_without_keep_or_financial_mutation(self):
  p=self.draft();before=self.store.snapshot;catalog,revision,_=self.m.read()
  r=self.report(p,node=p['selection']['roots'][0]);self.assertEqual(r['reason'],'actual_plan_required');self.assertEqual(r['source_text'],self.source())
  self.assertEqual(len(r['choices']),1);choice=r['choices'][0]
  self.assertEqual(choice['keep_payload'],dict(proposal_id=p['proposal_id']))
  self.assertEqual(choice['keep_scope'],'current_future');self.assertEqual(choice['payload'],p['selection'])
  self.assertEqual((self.m.read()[0].records,self.m.read()[1]),(catalog.records,revision));self.assertIs(self.store.snapshot,before)
  with self.assertRaises(FutureError):self.a.prepare(choice['payload'])
 def test_en_zh_and_other_values_match_exact_existing_choices(self):
  for source,title,value in [(self.source(),'oak desk',375),(self.source(True),'书桌',375),('I ordered the CNY 220 piano lesson option. The actual cost was CNY 235, and I paid it.','piano lesson',220)]:
   p=self.draft(title,value);r=self.report(p,source)
   self.assertEqual(len(r['choices']),1);self.assertIn(title,r['choices'][0]['label']);self.assertEqual(self.store.snapshot.revision,0)
 def test_amount_alone_wrong_quote_currency_and_unrelated_focus_never_bind(self):
  p=self.draft('lamp',375)
  self.assertEqual(self.report(p)['choices'],[])
  p=self.draft(previous=p['proposal_id']);n=p['selection']['roots'][0]
  for source in [self.source().replace('375','376'),self.source().replace('CNY','EUR')]:self.assertEqual(self.report(p,source)['choices'],[])
  self.assertEqual(len(self.report(p,node=n)['choices']),1)
 def test_duplicate_matching_names_remain_explicit_choices(self):
  p=self.draft();p=self.draft(previous=p['proposal_id']);r=self.report(p)
  self.assertEqual(len(r['choices']),2);self.assertNotEqual(r['choices'][0]['payload'],r['choices'][1]['payload']);self.assertEqual(self.store.snapshot.revision,0)
 def test_stale_and_cancelled_trial_cannot_propose_or_keep(self):
  p=self.draft();choice=self.report(p)['choices'][0];self.keep('Unrelated retained',30)
  with self.assertRaisesRegex(FutureError,'future_head_changed'):self.report(p)
  with self.assertRaisesRegex(FutureError,'future_head_changed'):self.i.keep(choice['keep_payload'])
  fresh=self.draft();self.i.cancel(dict(proposal_id=fresh['proposal_id']))
  with self.assertRaisesRegex(FutureError,'future_review_expired'):self.report(fresh)
  self.assertEqual(self.store.snapshot.revision,0)
 def test_explicit_whole_keep_preserves_parent_unselected_alternative_and_unrelated_item(self):
  root=self.draft('Studio setup',500);node=root['selection']['roots'][0]
  p=self.i.propose(dict(kind='structure',text='Setup includes desk and lamp',node=node,proposal_id=root['proposal_id'],parts=[dict(title='oak desk',amount=price(375)),dict(title='lamp',amount=price(90))]))
  c,_,_,_=self.i._catalog(p['proposal_id']);desk=next(x for x in c.latest.values() if type(x) is FutureNode and x.title=='oak desk')
  p=self.i.propose(dict(text='Metal desk is another alternative',node={'kind':'node','id':desk.meta.ref.id,'version':desk.meta.ref.version},label='metal desk',amount=price(450),proposal_id=p['proposal_id']))
  p=self.draft('Independent course',120,p['proposal_id']);choice=self.report(p)['choices'][0]
  before=self.store.snapshot;keep=self.i.keep(choice['keep_payload']);self.assertIs(self.store.snapshot,before)
  c,_,_=self.m.read();titles={x.title for x in c.latest.values() if type(x) is FutureNode};labels={x.label for x in c.latest.values() if type(x) is FutureOption}
  self.assertTrue({'Studio setup','oak desk','lamp','Independent course'}<=titles);self.assertTrue({'oak desk','metal desk'}<=labels)
  self.assertIsNotNone(c.get(desk.meta.ref).parent)
  retry=self.i.keep(choice['keep_payload']);self.assertEqual(retry['acknowledgement'],keep['acknowledgement'])
  preview=self.a.prepare(choice['payload']);self.assertIs(self.store.snapshot,before);self.assertEqual([r['label'] for r in preview['summary'] if r['adopted']],['oak desk'])
  self.apply(preview);self.assertEqual(len(self.records('planned_expense')),1);self.assertEqual(self.records('actual_occurrence'),[])
 def test_retained_target_with_unrelated_pending_items_requires_explicit_keep(self):
  target=self.keep('oak desk',375);p=self.draft('Separate course',80)
  before=self.store.snapshot;choice=self.report(p)['choices'][0]
  self.assertEqual(choice['payload']['roots'],[target]);self.assertEqual(choice['keep_payload'],dict(proposal_id=p['proposal_id']))
  self.assertFalse(any(type(r) is FutureNode and r.title=='Separate course' for r in self.m.read()[0].latest.values()))
  self.i.keep(choice['keep_payload']);self.assertIs(self.store.snapshot,before)
  self.apply(self.a.prepare(choice['payload']))
  self.assertTrue(any(type(r) is FutureNode and r.title=='Separate course' for r in self.m.read()[0].latest.values()))
  self.assertEqual(len(self.records('planned_expense')),1)
 def test_existing_plan_keeps_unrelated_draft_before_actual_apply(self):
  from tests.test_plan_reality import execute
  self.seed_money();n=self.keep('oak desk',375);self.apply(self.a.prepare(dict(roots=[n],options=self.options())))
  p=self.draft('Separate course',80);before=self.store.snapshot
  choice=self.report(p)['choices'][0];self.assertTrue(choice['continue_actual']);self.assertIs(self.store.snapshot,before)
  self.i.keep(choice['keep_payload']);self.assertIs(self.store.snapshot,before)
  result=self.a.interpret(dict(text=self.source()))['routing']
  while result['stage']=='question':
   key=result['question']['key'];value='yes' if key=='same_plan' else 'apply_to_prior_balance' if key=='coverage' else 'none' if key=='earmark' else result['question']['options'][0]['value']
   result=self.w.plan_reality.answer(dict(proposal_id=result['id'],key=key,value=value))
  preview=self.w.plan_reality.prepare(dict(proposal_id=result['id']));self.assertIs(self.store.snapshot,before)
  result,retry=execute(self.w,preview);snapshot=self.store.snapshot
  self.assertEqual(self.w.plan_reality.apply(retry)['operation']['receipt'],result['operation']['receipt']);self.assertIs(self.store.snapshot,snapshot)
  self.assertTrue(any(type(r) is FutureNode and r.title=='Separate course' for r in self.m.read()[0].latest.values()))
 def test_existing_plan_and_payment_gates_follow_explicit_keep(self):
  self.seed_money();previous_actual=self.records('actual_occurrence');p=self.draft();choice=self.report(p)['choices'][0];before=self.store.snapshot
  self.i.keep(choice['keep_payload']);self.assertIs(self.store.snapshot,before)
  again=self.a.interpret(dict(text=self.source()));self.assertNotIn('keep_payload',again['choices'][0])
  self.apply(self.a.prepare(choice['payload']));before=self.store.snapshot
  result=self.a.interpret(dict(text=self.source()))['routing'];self.assertEqual(result['question']['key'],'same_plan');self.assertIs(self.store.snapshot,before)
  result=self.w.plan_reality.answer(dict(proposal_id=result['id'],key='same_plan',value='yes'))
  proposal=self.w.plan_reality.proposals[result['id']]
  self.assertEqual(proposal['answers']['amount']['value'],'389');self.assertEqual(proposal['target']['amount'].amount.minor_units,37500)
  self.assertEqual(proposal['source'],self.source());self.assertEqual(self.records('actual_occurrence'),previous_actual)

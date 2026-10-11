"""Quoted option versus paid price: synthetic closed-action and authority tests."""
import unittest
from copy import deepcopy
from finvis.actual_report import parse_actual_report
from finvis.interaction_meaning import resolve
from finvis.i3_workflow import I3Error
from finvis.world_history_replay import replay_history
from tests import test_future_adoption as base
from tests import test_d1_http

TEXT='I booked the €900 economy flight option. The actual price was €944, and I paid it.'

class ActualReportGrammarTests(unittest.TestCase):
 def test_exact_user_report_preserves_distinct_roles(self):
  r=parse_actual_report(TEXT);self.assertEqual(r['quoted']['value'],'900');self.assertEqual(r['actual']['value'],'944');self.assertEqual(r['subject'],'economy flight');self.assertEqual(r['source_text'],TEXT)
 def test_unseen_en_objects_amounts_and_native_currencies(self):
  for currency in ('CNY','EUR','SGD'):
   for verb in ('purchased','ordered','bought'):
    source=f'I {verb} the {currency} 375.50 oak desk option; final cost came to {currency} 389.20, and I have paid for it.'
    r=parse_actual_report(source);self.assertIsNotNone(r,source);self.assertEqual(r['actual']['value'],'389.2');self.assertEqual(r['quoted']['currency'],currency)
 def test_zh_paraphrases(self):
  for source in ['我买了原估价900欧元的经济舱机票，实际价格944欧元，已经付了。','我购买了报价350新币的书桌，最终花费为370新币，我已付款。','我预订了原价人民币220的课程；实际价钱是人民币240；已付清。']:
   self.assertIsNotNone(parse_actual_report(source),source)
 def test_approximation_preserved(self):
  r=parse_actual_report(TEXT.replace('€900','about EUR 900').replace('€944','approximately EUR 944'))
  self.assertEqual(r['quoted']['precision'],'approximate');self.assertEqual(r['actual']['precision'],'approximate')
 def test_unrelated_negation_conditional_financial_facts_not_dropped(self):
  for source in [TEXT+' I also received EUR 30.',TEXT.replace('I paid it','I have not paid it'),TEXT.replace('I booked','If I booked'),TEXT.replace('economy flight','flight and hotel'),TEXT.replace('economy flight','flight or hotel'),TEXT.replace('actual price','remaining payment'),TEXT.replace('actual price','deposit'),TEXT.replace('€944','USD 944'),TEXT.replace('€944','SGD 944'),TEXT.replace('€944','944'),TEXT.replace('€944','€944 or €955'),TEXT.replace('I paid it','I might pay it'),TEXT.replace('I paid it','I was refunded'),TEXT.replace('economy flight','refundable economy flight'),TEXT.replace(' option','')]:
   with self.subTest(source=source):self.assertIsNone(parse_actual_report(source))
 def test_route_preserves_whole_text_and_does_not_claim_authority(self):
  r=resolve(dict(text=TEXT),dict(records=[]));self.assertEqual(r['layer'],'reality');self.assertTrue(r['bounded_followup']);self.assertFalse(r['financial_authority']);self.assertEqual(r['source_text'],TEXT)

class ActualReportAuthorityTests(unittest.TestCase):
 setUp=base.FutureAdoptionTests.setUp
 keep=base.FutureAdoptionTests.keep
 option=base.FutureAdoptionTests.option
 options=base.FutureAdoptionTests.options
 apply=base.FutureAdoptionTests.apply
 adopt=base.FutureAdoptionTests.adopt
 records=base.FutureAdoptionTests.records
 seed_money=base.FutureAdoptionTests.seed_money
 restart=base.FutureAdoptionTests.restart
 actual=base.FutureAdoptionTests.actual
 def source(self,zh=False):return '我买了原估价人民币375的书桌，实际价格人民币389，已经付款。' if zh else 'I purchased the CNY 375 oak desk option. The final cost was CNY 389, and I paid it.'
 def test_retained_future_requires_explicit_plan_and_correct_option(self):
  n=self.keep('oak desk',375);self.option(n,550,label='metal desk');before=self.store.snapshot
  r=self.a.interpret(dict(text=self.source(),options=[self.options()[-1]]));self.assertEqual(r['stage'],'clarify');self.assertEqual(r['reason'],'actual_plan_required');self.assertEqual(r['source_text'],self.source())
  self.assertEqual(r['choices'][0]['payload']['options'],[self.options()[0]]);self.assertIs(self.store.snapshot,before);self.assertEqual(len(r['choices']),1)
 def test_same_amount_unrelated_label_and_quote_mismatch_do_not_bind(self):
  self.keep('lamp',375)
  for source in [self.source(),self.source().replace('oak desk','lamp').replace('375','399')]:
   r=self.a.interpret(dict(text=source));self.assertEqual(r['choices'],[]);self.assertEqual(self.store.snapshot.revision,0)
 def test_current_selection_and_focus_cannot_override_named_option(self):
  n=self.keep('oak desk',375);other=self.keep('lamp',375)
  r=self.a.interpret(dict(text=self.source(),node=other));self.assertEqual(r['choices'][0]['payload']['roots'],[n])
 def test_explicit_plan_then_existing_reality_gates_en_zh(self):
  for zh in (False,True):
   with self.subTest(zh=zh):
    if zh:self.setUp()
    self.seed_money();n=self.keep('书桌' if zh else 'oak desk',375);self.apply(self.adopt([n],self.options()));before=self.store.snapshot
    v=self.a.interpret(dict(text=self.source(zh)))['routing'];self.assertEqual(v['question']['key'],'same_plan');self.assertIs(self.store.snapshot,before)
    v=self.w.plan_reality.answer(dict(proposal_id=v['id'],key='same_plan',value='yes'));self.assertEqual(v['question']['key'],'source');self.assertIs(self.store.snapshot,before)
    p=self.w.plan_reality.proposals[v['id']];self.assertEqual(p['answers']['amount']['value'],'389');self.assertEqual(p['source'],self.source(zh));self.assertEqual(p['target']['amount'].amount.minor_units,37500)
    result,body=self.actual(n,self.source(zh));summary=self.a.listing()[0]['plan']['summary'];self.assertEqual(summary['net_actual']['value_minor'],38900);self.assertEqual(summary['estimated']['amount']['minor_units'],37500)
    self.assertEqual(self.w.plan_reality.apply(body)['operation']['receipt'],result['operation']['receipt']);snapshot=self.store.snapshot;self.restart();self.assertEqual(self.store.snapshot,snapshot);self.assertEqual(replay_history(self.store.export_replay()),snapshot)
 def test_stale_actual_review_cannot_apply(self):
  self.seed_money();n=self.keep('oak desk',375);self.apply(self.adopt([n],self.options()));r=self.a.interpret(dict(text=self.source()))['routing'];self.a.interpret(dict(text=self.source()))
  with self.assertRaises(I3Error):self.w.plan_reality.answer(dict(proposal_id=r['id'],key='same_plan',value='yes'))
 def test_duplicate_names_require_choice(self):
  self.keep('oak desk',375);self.keep('oak desk',375)
  r=self.a.interpret(dict(text=self.source()));self.assertEqual(len(r['choices']),2);self.assertEqual(self.store.snapshot.revision,0)

class ActualReportHTTPTests(unittest.TestCase):
 setUp=test_d1_http.D1HTTPTests.setUp
 tearDown=test_d1_http.D1HTTPTests.tearDown
 call=test_d1_http.D1HTTPTests.call
 def test_entire_report_uses_real_existing_review_routes(self):
  _,p=self.call('/api/user/future-space/prepare',dict(text='Considering economy flight',title='economy flight',amount=dict(currency='EUR',precision='approximate',value='900',range_min=None,range_max=None)))
  self.call('/api/user/future-space/confirm',dict(proposal_id=p['proposal_id']))
  status,r=self.call('/api/user/if-space/meaning',dict(text=TEXT));self.assertEqual(status,200);self.assertTrue(r['bounded_followup']);self.assertEqual(r['layer'],'reality')
  status,r=self.call('/api/user/future-adoption/interpret',dict(text=TEXT));self.assertEqual(status,200,r);self.assertEqual(r['reason'],'actual_plan_required');self.assertEqual(len(r['choices']),1);self.assertEqual(self.server.user_workflow.store.snapshot.revision,0)
 def test_extra_claim_stays_on_compound_path(self):
  _,r=self.call('/api/user/if-space/meaning',dict(text=TEXT+' I received EUR 60.'));self.assertTrue(r['compound_required'])

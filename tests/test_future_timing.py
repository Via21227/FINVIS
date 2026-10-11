"""Bounded timing, generic synthetic fixtures, no production data/model access."""
import copy,json,unittest
from datetime import date
from dataclasses import replace
from finvis.future_timing import parse_timing,trajectory,timing_wire,overdue
from finvis.future_space import MoneyTiming,FutureError,FutureOption,dumps,loads,canonical
from finvis.future_experience import wire_ref,ref
from finvis.simulation_entry import SimulationEntry
from finvis.simple_surface import SimpleSurface
from finvis.if_interaction import IfInteraction
from finvis.interaction_meaning import resolve
from tests.test_future_space import event
from tests import test_simple_surface as base

TODAY=date(2026,10,7)
def item(value=4000,when='2026-10-20',currency='CNY',typ='cost',name='Workshop'):
 a=None if value is None else dict(lower_minor=value*100,upper_minor=value*100)
 return dict(title=name,label=name,currency=currency,effect_type=typ,time_basis='one_time',amount=a,
   money_timing=timing_wire(parse_timing(when,TODAY)) if when else None)
def run(rows,start=(200000,200000)):
 return trajectory(rows,{'CNY':start} if start else {},TODAY)['currencies'][0]

class TimingPureTests(unittest.TestCase):
 def test_exact_day(self):self.assertEqual(parse_timing('2027-02-09',TODAY).value,'2027-02-09')
 def test_month_end_no_invented_day(self):
  t=parse_timing('2026年10月底',TODAY);self.assertEqual((t.precision,t.value,t.source_text),('month','2026-10','2026年10月底'))
 def test_relative_once_anchor(self):
  t=parse_timing('下个月',TODAY);self.assertEqual((t.value,t.anchor_date),('2026-11','2026-10-07'));self.assertEqual(loads(dumps(t)),t)
 def test_year_boundary(self):self.assertEqual(parse_timing('next month',date(2026,12,1)).value,'2027-01')
 def test_broad_remains_unknown(self):self.assertEqual(parse_timing('回国前',TODAY).precision,'unknown')
 def test_invalid_dates_reject(self):
  for s in ['2026-02-30','2026-13','10/20','2026-1-2-3']:
   with self.subTest(s=s),self.assertRaises(ValueError):parse_timing(s,TODAY)
 def test_mixed_month_never_false_negative(self):
  r=run([item(),item(5000,'2026-10',typ='expected_inflow')]);self.assertEqual(len(r['groups']),1);self.assertEqual(r['groups'][0]['balance']['lower_minor'],300000);self.assertFalse(r['groups'][0]['negative'])
 def test_same_day_no_intraday_order(self):
  r=run([item(),item(5000,typ='expected_inflow')]);self.assertEqual(len(r['groups']),1);self.assertFalse(r['groups'][0]['negative'])
 def test_negative_then_recovery(self):
  r=run([item(),item(5000,'2026-11',typ='expected_inflow')]);self.assertTrue(r['groups'][0]['negative']);self.assertEqual(r['groups'][-1]['balance']['lower_minor'],300000)
 def test_unknown_not_zero_later_partial(self):
  r=run([item(None),item(100,'2026-11')]);self.assertIsNone(r['groups'][0]['items'][0]['amount']);self.assertTrue(r['groups'][-1]['partial']);self.assertEqual(r['groups'][-1]['balance']['lower_minor'],190000)
 def test_undated_not_appended(self):
  r=run([item(100),item(500,None)]);self.assertEqual(len(r['undated']),1);self.assertEqual(r['groups'][-1]['balance']['lower_minor'],190000)
 def test_no_start_signed_changes_only(self):
  g=run([item(50)],None)['groups'][0];self.assertIsNone(g['balance']);self.assertEqual(g['known_change']['lower_minor'],-5000);self.assertFalse(g['negative'])
 def test_native_currencies_separate(self):
  r=trajectory([item(20),item(50,currency='EUR')],{'CNY':(10000,10000)},TODAY);self.assertEqual(len(r['currencies']),2);self.assertIsNone(r['currencies'][1]['groups'][0]['balance']);self.assertNotIn('total',r)
 def test_inflow_conditional(self):self.assertTrue(run([item(50,typ='expected_inflow')])['conditional_inflow'])
 def test_range_not_midpoint(self):
  i=item();i['amount']=dict(lower_minor=100000,upper_minor=300000);g=run([i])['groups'][0];self.assertEqual(g['balance'],dict(lower_minor=-100000,upper_minor=100000));self.assertTrue(g['crosses_zero']);self.assertFalse(g['negative'])
 def test_past_needs_disposition(self):
  r=run([item(80,'2026-09-01'),item(10)]);self.assertEqual(len(r['disposition_required']),1);self.assertIsNone(r['groups'][0]['balance'])
 def test_current_month_not_automatically_overdue(self):self.assertFalse(overdue(parse_timing('2026-10'),TODAY))
 def test_pending_past_remains_undated(self):
  i=item(50,'2026-09-01');i['money_timing']['pending_checked_on']=TODAY.isoformat();r=run([i]);self.assertFalse(r['disposition_required']);self.assertFalse(r['groups']);self.assertEqual(len(r['undated']),1)
 def test_legacy_codec_stays_v1(self):
  rows=event('Garden tools',(90,));old=dumps(rows);self.assertIn('finvis.future-space.v1',old);self.assertNotIn('money_timing',old);self.assertEqual(dumps(loads(old)),old)
 def test_typed_codec_roundtrip(self):
  o=event('Course',(80,))[2];o=replace(o,effects=(replace(o.effects[0],money_timing=parse_timing('2027-02')),));s=dumps(o);self.assertIn('finvis.future-space.v2',s);self.assertEqual(dumps(loads(s)),s)
 def test_new_timing_cannot_claim_old_codec(self):
  d=json.loads(dumps(parse_timing('2027-03')));d['schema_version']='finvis.future-space.v1'
  with self.assertRaises(FutureError):loads(canonical(d))
 def test_metadata_is_never_timing(self):
  rows=event('2027-02 trip',(80,));self.assertIsNone(rows[2].effects[0].money_timing)

class TimingApplicationTests(unittest.TestCase):
 setUp=base.SimpleSurfaceTests.setUp;funds=base.SimpleSurfaceTests.funds;keep=base.SimpleSurfaceTests.keep;option=base.SimpleSurfaceTests.option;options=base.SimpleSurfaceTests.options;restart=base.SimpleSurfaceTests.restart
 def setup_event(self):
  self.funds();self.n=self.keep('Ceramics course',200);self.o=self.options()[0];self.i=self.service.j.i
 def proposal(self,t='2099-11-20',**kw):
  return self.i.propose(dict(kind='timing',text='Ceramics course payment '+t,node=self.n,option=self.o,timing=t,roots=[self.n],options=[self.o],**kw))
 def test_direct_timing_readonly_until_keep(self):
  self.setup_event();before=self.store.snapshot;rev=self.m.read()[1];p=self.proposal();r=self.service.simulate(dict(p['selection'],proposal_id=p['proposal_id']));self.assertEqual(r['timing']['currencies'][0]['groups'][0]['when'],'2099-11-20');self.assertEqual(self.m.read()[1],rev);self.assertEqual(self.store.snapshot,before)
 def test_restart_exact_idempotent_ack(self):
  self.setup_event();p=self.proposal();ack=self.x.confirm({'proposal_id':p['proposal_id']});selection=p['selection'];self.restart();self.service=SimpleSurface(IfInteraction(self.x),self.a);self.assertEqual(self.x.confirm({'proposal_id':p['proposal_id']}),ack);self.assertEqual(self.service.simulate(selection)['timing']['currencies'][0]['groups'][0]['when'],'2099-11-20');self.assertEqual(self.store._db.execute('PRAGMA user_version').fetchone()[0],2)
 def test_natural_and_manual_same_state(self):
  self.setup_event();s=self.x.listing();m=resolve(dict(text='Ceramics course will pay 2099-11-20',context=dict(selected=[self.o])),s);self.assertEqual(m['status'],'supported');self.assertEqual(m['payload']['option'],self.o);p=self.i.propose(dict(m['payload'],roots=[self.n],options=[self.o]));actual=self.service.simulate(dict(p['selection'],proposal_id=p['proposal_id']));q=self.proposal();manual=self.service.simulate(dict(q['selection'],proposal_id=q['proposal_id']));self.assertEqual(actual['items'][0]['money_timing'],manual['items'][0]['money_timing'])
 def test_zh_correction_and_topic_switch(self):
  self.setup_event();other=self.keep('Travel',20);m=resolve(dict(text='Ceramics course 付款改到2099年11月底',context=dict(node=other,selected=[self.o])),self.x.listing());self.assertEqual(m['target'],self.n);self.assertEqual(m['payload']['timing'],'2099年11月底')
 def test_life_date_no_payment_inference(self):
  self.setup_event();m=resolve(dict(text='Ceramics course trip in 2099-11',context=dict(selected=[self.o])),self.x.listing());self.assertNotEqual(m.get('operation'),'timing')
 def test_past_disposition_no_actual(self):
  self.setup_event();before=self.store.snapshot;p=self.proposal('2020-01-01');entry=SimulationEntry(self.i,self.a);r=entry.prepare(dict(p['selection'],proposal_id=p['proposal_id']));self.assertEqual(r['reason'],'money_timing_disposition');self.assertEqual(self.store.snapshot,before)
 def test_pending_confirmation_keeps_original_date(self):
  self.setup_event();p=self.proposal('2020-01-01');self.x.confirm({'proposal_id':p['proposal_id']});self.o=p['selection']['options'][0];q=self.proposal('2020-01-01',disposition='pending');out=SimulationEntry(self.i,self.a).prepare(dict(q['selection'],proposal_id=q['proposal_id']));self.assertEqual(out['stage'],'ready');self.assertEqual(out['simulation']['items'][0]['money_timing']['value'],'2020-01-01');self.assertEqual(out['simulation']['timing']['currencies'][0]['groups'],[])
 def test_immutable_result_after_date_edit(self):
  self.setup_event();p=self.proposal();out=self.service.simulate(dict(p['selection'],proposal_id=p['proposal_id']));frozen=copy.deepcopy(out);self.x.confirm({'proposal_id':p['proposal_id']});self.o=p['selection']['options'][0];q=self.proposal('2099-12');new=self.service.simulate(dict(q['selection'],proposal_id=q['proposal_id']));self.assertEqual(out,frozen);self.assertNotEqual(new['timing'],out['timing'])

 def test_timing_amount_edit_preserves_selected_date(self):
  self.setup_event();p=self.proposal();self.x.confirm({'proposal_id':p['proposal_id']});o=p['selection']['options'][0]
  m=resolve(dict(text='What if Ceramics course costs CNY 250?',context=dict(selected=[o])),self.x.listing());self.assertEqual(m['payload']['timing_source'],o)
  q=self.i.propose(dict(m['payload'],roots=[self.n],options=[o]));out=self.service.simulate(dict(q['selection'],proposal_id=q['proposal_id']));self.assertEqual(out['items'][0]['money_timing']['value'],'2099-11-20');self.assertEqual(out['items'][0]['amount']['lower_minor'],25000)
 def test_create_with_explicit_payment_clause(self):
  m=resolve(dict(text='What if I buy Materials for CNY 300, will pay 2099-11-20'),self.x.listing());self.assertEqual(m['status'],'supported');p=self.service.j.i.propose(m['payload']);o=self.service.simulate(dict(p['selection'],proposal_id=p['proposal_id']));self.assertEqual(o['items'][0]['amount']['lower_minor'],30000);self.assertEqual(o['items'][0]['money_timing']['value'],'2099-11-20')
 def test_new_timing_unknown_retained(self):
  self.setup_event();p=self.proposal('回国前');r=self.service.simulate(dict(p['selection'],proposal_id=p['proposal_id']));self.assertEqual(r['timing']['currencies'][0]['groups'],[]);self.assertEqual(r['items'][0]['money_timing']['source_text'],'回国前')
 def test_timed_future_authorized_actual_restart(self):
  from tests.test_future_adoption import FutureAdoptionTests
  from finvis.world_history_replay import replay_history
  self.setup_event();p=self.proposal();self.x.confirm({'proposal_id':p['proposal_id']});selection=p['selection'];before=self.store.snapshot
  preview=self.a.prepare(selection);self.assertEqual(self.store.snapshot,before)
  result,body=FutureAdoptionTests.apply(self,preview)
  FutureAdoptionTests.actual(self,self.n,'Finally bought it for CNY 210')
  row=self.a.listing()[0];self.assertEqual(row['plan']['summary']['estimated']['amount']['minor_units'],20000);self.assertEqual(row['plan']['summary']['net_actual']['value_minor'],21000)
  self.restart();self.assertEqual(self.a.listing()[0],row);self.assertEqual(replay_history(self.store.export_replay()),self.store.snapshot);self.assertEqual(self.a.apply(body)['operation']['receipt'],result['operation']['receipt'])

 def test_timing_clarifies_invalid_calendar_date(self):
  self.setup_event();p=dict(text='Ceramics course will pay 2099-02-30',context=dict(selected=[self.o]));m=resolve(p,self.x.listing());self.assertEqual(m['question']['key'],'money_date');m=resolve(dict(p,reviewed_timing='2099-02'),self.x.listing());self.assertEqual(m['status'],'supported');self.assertEqual(m['payload']['timing'],'2099-02')
 def test_approximate_start_stays_qualified(self):
  r=trajectory([item(20)],{'CNY':(10000,10000)},TODAY,starting_approximate={'CNY'});self.assertTrue(r['currencies'][0]['groups'][0]['approximate'])

 def test_direct_simulation_withholds_unreviewed_past_balance(self):
  self.setup_event();p=self.proposal('2020-01-01');r=self.service.simulate(dict(p['selection'],proposal_id=p['proposal_id']));self.assertIsNone(r['buckets'][0]['after']);self.assertEqual(r['buckets'][0]['unavailable_reason'],'money_timing_disposition');self.assertIsNotNone(r['buckets'][0]['known_cost'])

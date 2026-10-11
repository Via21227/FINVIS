"""Synthetic entry recovery: no production storage or model calls."""
import unittest
from dataclasses import replace
from finvis.simulation_entry import SimulationEntry
from finvis.simple_surface import SimpleSurface
from finvis.if_interaction import IfInteraction
from finvis.future_space import FutureError
from finvis.future_experience import ref,wire_ref
from finvis.world_commands import record_ref
from finvis.reviewed_proposals import _wire
from tests import test_simple_surface as base
from tests.test_persistent_world import live
from tests.test_history_actions import request,apply,amount

class EntryTests(unittest.TestCase):
    setUp=base.SimpleSurfaceTests.setUp;funds=base.SimpleSurfaceTests.funds;keep=base.SimpleSurfaceTests.keep;option=base.SimpleSurfaceTests.option;options=base.SimpleSurfaceTests.options;restart=base.SimpleSurfaceTests.restart
    def start(self):
        self.funds();d=live(self.store,'world_decision');p=self.x.prepare(dict(text=d.subject,source_ref=_wire(record_ref(d))))
        self.x.confirm({'proposal_id':p['proposal_id']});self.n=next(r['ref'] for r in self.x.listing()['records'] if r.get('title')==d.subject)
        self.p=dict(roots=[self.n],options=self.options());self.entry=SimulationEntry(self.service.j.i,self.a)
        return self.entry.prepare(self.p)
    def change(self,n=4300):return apply(self.w,request(self.w,'edit','world_decision','amount',amount(n,'approximate')))
    def review(self):self.start();self.change();return self.entry.prepare(self.p)
    def confirm(self,q):self.x.confirm({'proposal_id':q['proposal_id']});return self.entry.prepare(q['summary']['selection'])
    def test_normal_ready_no_retention(self):
        a=self.start();rev=self.m.read()[1];self.assertEqual(a['stage'],'ready');self.assertEqual(self.entry.prepare(self.p),a);self.assertEqual(self.m.read()[1],rev)
    def test_reproduce_old_stale_and_review_resumes(self):
        q=self.review();self.assertEqual(self.service.simulate(self.p)['status'],'stale');self.assertEqual(self.x.preview(self.p)['status'],'stale');self.assertEqual(q['stage'],'review');self.assertEqual(self.confirm(q)['stage'],'ready')
    def test_prepare_no_financial_or_future_write(self):
        self.start();self.change();before=self.store.snapshot;rev=self.m.read()[1];q=self.entry.prepare(self.p);self.assertEqual(q['stage'],'review');self.assertEqual(self.store.snapshot,before);self.assertEqual(self.m.read()[1],rev)
    def test_confirmation_preserves_world_history(self):
        self.start();self.change();q=self.entry.prepare(dict(self.p,language='zh-CN'));before=self.store.snapshot;h=self.w.history();self.confirm(q);self.assertEqual(self.store.snapshot,before);self.assertEqual(self.w.history(),h);self.assertTrue(any(r.get('name','').startswith('已核对的选择 · ') for r in self.x.listing()['records']))
    def test_old_reference_and_scenario_remain_stale(self):
        self.start();q=self.x.prepare_scenario(dict(self.p,name='Before'));self.x.confirm({'proposal_id':q['proposal_id']});self.change();q=self.entry.prepare(self.p);self.confirm(q);self.assertRaises(FutureError,self.service.simulate,self.p);old=next(r for r in self.x.listing()['records'] if r.get('name')=='Before');self.assertEqual(old['status'],'stale')
    def test_review_does_not_replace_hypothetical_price(self):
        first=self.start()['simulation']['items'][0]['amount'];self.change();q=self.entry.prepare(self.p);a=self.confirm(q);self.assertEqual(a['simulation']['items'][0]['amount'],first)
    def test_change_again_rejects_confirmation(self):
        q=self.review();self.change(5100)
        with self.assertRaisesRegex(FutureError,'financial_basis_changed'):self.confirm(q)
    def test_future_change_rejects_confirmation(self):
        q=self.review();self.keep('Other class',50)
        with self.assertRaisesRegex(FutureError,'future_head_changed'):self.confirm(q)
    def test_confirm_idempotent_after_restart(self):
        q=self.review();ack=self.x.confirm({'proposal_id':q['proposal_id']});sel=q['summary']['selection'];self.restart();self.assertEqual(self.x.confirm({'proposal_id':q['proposal_id']}),ack);a=SimulationEntry(IfInteraction(self.x),self.a).prepare(sel);self.assertEqual(a['stage'],'ready')
    def test_unconfirmed_restart_requires_fresh_review(self):
        q=self.review();self.restart()
        with self.assertRaisesRegex(FutureError,'future_review_expired'):self.x.confirm({'proposal_id':q['proposal_id']})
        self.assertEqual(SimulationEntry(IfInteraction(self.x),self.a).prepare(self.p)['stage'],'review')
    def test_balance_change_reads_new_money_no_review_if_not_dependency(self):
        self.start();a=self.entry.prepare(self.p)['simulation'];apply(self.w,request(self.w,'edit','asset_position','balance',amount(19000)));b=self.entry.prepare(self.p);self.assertEqual(b['stage'],'ready');self.assertNotEqual(a['buckets'],b['simulation']['buckets'])
    def test_latest_option_requires_review_not_silent_selection(self):
        self.start();c,_,_=self.m.read();old=c.get(ref(self.p['options'][0]));new=replace(old,meta=self.x.meta('option','Updated estimate',old.meta.ref.id,old.meta.ref));self.m.retain(self.m.prepare('update',(new,),'revise'));q=self.entry.prepare(self.p);self.assertEqual(q['stage'],'review');self.assertEqual(self.confirm(q)['stage'],'ready')
    def test_expired_evidence_not_laundered(self):
        self.start();c,_,_=self.m.read();old=c.get(ref(self.n));meta=self.x.meta('node','Expired',old.meta.ref.id,old.meta.ref,old.meta.dependencies);meta=replace(meta,evidence=tuple(replace(e,freshness='stale') for e in meta.evidence));self.m.retain(self.m.prepare('expired',(replace(old,meta=meta),),'revise'));q=self.entry.prepare(self.p);self.assertEqual(q['stage'],'needs_input')
    def test_transient_alternative_preserved_on_source_review(self):
        self.start();self.change();p=self.service.j.i.propose(dict(text='Used estimate',node=self.n,amount=dict(currency='CNY',precision='approximate',value='2300',range_min=None,range_max=None),**self.p));q=self.entry.prepare(dict(p['selection'],proposal_id=p['proposal_id']));a=self.confirm(q);self.assertEqual(a['simulation']['buckets'][0]['known_cost']['lower_minor'],230000)
    def test_closed_fields(self):
        self.start()
        for extras in ({'apply':True},{'authorization_id':'x'},{'language':'x'}):
            with self.assertRaises(FutureError):self.entry.prepare(dict(self.p,**extras))

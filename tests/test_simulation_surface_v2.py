"""Consumer simulation invariants using generic synthetic worlds only."""
import unittest
from dataclasses import replace
from finvis.future_space import FutureNode,FutureError
from finvis.future_experience import wire_ref
from finvis.simulation_entry import SimulationEntry
from tests.test_future_space import event
from tests import test_simulation_entry as entry_base

class SimulationSurfaceV2Tests(unittest.TestCase):
    setUp=entry_base.EntryTests.setUp;funds=entry_base.EntryTests.funds;keep=entry_base.EntryTests.keep;option=entry_base.EntryTests.option;options=entry_base.EntryTests.options;restart=entry_base.EntryTests.restart
    def expired(self,amount=700):
        self.funds();rows=event('Workshop',(amount,),scope=self.m.scope)
        rows=tuple(replace(r,meta=replace(r.meta,evidence=tuple(replace(e,freshness='stale') for e in r.meta.evidence))) if isinstance(r,FutureNode) else r for r in rows)
        self.m.retain(self.m.prepare('expired',rows,'keep'));self.entry=SimulationEntry(self.service.j.i,self.a)
        self.p=dict(roots=[wire_ref(rows[0].meta.ref)],options=[wire_ref(rows[2].meta.ref)])
        return rows
    def test_expired_names_item_and_fact(self):
        self.expired();q=self.entry.prepare(self.p);i=q['items'][0];self.assertEqual(i['title'],'Workshop');self.assertEqual(i['needed_fact'],'estimate_confirmation');self.assertTrue(i['can_confirm_estimate']);self.assertEqual(i['amount']['amount']['minor_units'],70000)
    def test_attestation_needs_explicit_review_and_preserves_old_evidence(self):
        rows=self.expired();before=self.store.snapshot;rev=self.m.read()[1];q=self.entry.prepare(dict(self.p,confirmed_estimates=self.p['roots']));self.assertEqual(q['stage'],'review');self.assertEqual(self.m.read()[1],rev);self.x.confirm({'proposal_id':q['proposal_id']});r=self.entry.prepare(q['summary']['selection']);self.assertEqual(r['stage'],'ready');self.assertEqual(r['simulation']['buckets'][0]['known_cost']['lower_minor'],70000);self.assertEqual(self.m.read()[0].get(rows[0].meta.ref).meta.evidence[0].freshness,'stale');self.assertEqual(self.store.snapshot,before)
    def test_confirmation_cannot_name_unselected_root(self):
        self.expired();other=self.keep('Other',40)
        with self.assertRaises(FutureError):self.entry.prepare(dict(self.p,confirmed_estimates=[other]))
    def test_bad_confirmation_shape(self):
        self.expired()
        for value in ('all',True,{},[None]):
            with self.assertRaises(FutureError):self.entry.prepare(dict(self.p,confirmed_estimates=value))
    def test_no_confirmation_keeps_blocked(self):
        self.expired();self.assertEqual(self.entry.prepare(self.p)['stage'],'needs_input')
    def test_retry_ack_recovered_after_restart(self):
        self.expired();q=self.entry.prepare(dict(self.p,confirmed_estimates=self.p['roots']));ack=self.x.confirm({'proposal_id':q['proposal_id']});self.restart();self.assertEqual(self.x.confirm({'proposal_id':q['proposal_id']}),ack)
    def test_unconsumed_attestation_review_does_not_survive_restart(self):
        self.expired();q=self.entry.prepare(dict(self.p,confirmed_estimates=self.p['roots']));self.restart()
        with self.assertRaises(FutureError):self.x.confirm({'proposal_id':q['proposal_id']})
    def test_excluded_run_is_read_only_no_deletion(self):
        self.expired();n=self.keep('Printing',80);c=self.m.read()[0];opts=[wire_ref(r.meta.ref) for r in c.latest.values() if r.meta.ref.kind=='option' and r.variable.id!=self.p['options'][0]['id'].rsplit('/',1)[0]+'/choice'];opts=[r for r in self.options() if r not in self.p['options']];before=self.m.read()[1];snapshot=self.store.snapshot;out=self.entry.prepare(dict(roots=[n],options=opts));self.assertEqual(out['stage'],'ready');self.assertEqual(self.m.read()[1],before);self.assertEqual(self.store.snapshot,snapshot);self.assertEqual(self.entry.prepare(self.p)['stage'],'needs_input')
    def test_frozen_wire_result_is_independent_from_new_world(self):
        self.expired();n=self.keep('Supplies',80);opts=[r for r in self.options() if r not in self.p['options']];out=self.entry.prepare(dict(roots=[n],options=opts));import copy;old=copy.deepcopy(out)
        from tests.test_history_actions import apply,request,amount
        apply(self.w,request(self.w,'edit','asset_position','balance',amount(22000)));new=self.entry.prepare(dict(roots=[n],options=opts));self.assertEqual(out,old);self.assertNotEqual(new['simulation']['buckets'],old['simulation']['buckets']);self.assertIn('assessment',old['simulation'])

class SimulationMetricPresentationTests(unittest.TestCase):
    def test_numeric_counterexamples(self):
        import subprocess
        from pathlib import Path
        run=subprocess.run(['node','tests/simulation_metrics_test.cjs'],cwd=Path(__file__).resolve().parents[1],text=True,capture_output=True)
        self.assertEqual(run.returncode,0,run.stdout+run.stderr)
        self.assertIn('24 presentation checks passed',run.stdout)

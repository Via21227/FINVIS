"""Synthetic explicit-budget tests; no production DB, model or new policy."""
import unittest
from dataclasses import replace
from finvis.if_interaction import IfInteraction
from finvis.if_judgment import IfJudgment, target_of, PREFIX
from finvis.future_space import FutureOption, FutureError, FutureCatalog
from finvis.future_experience import wire_ref, ref
from finvis.future_memory import FutureMemoryService
from finvis.future_experience import FutureExperience
from finvis.future_adoption import FutureAdoption
from finvis.domain import Currency
from tests import test_future_adoption as base
from tests.test_future_experience import price
from tests.test_future_space import event

class JudgmentTests(unittest.TestCase):
    keep=base.FutureAdoptionTests.keep
    option=base.FutureAdoptionTests.option
    options=base.FutureAdoptionTests.options
    adopt=base.FutureAdoptionTests.adopt
    apply=base.FutureAdoptionTests.apply
    restart=base.FutureAdoptionTests.restart
    records=base.FutureAdoptionTests.records
    seed_money=base.FutureAdoptionTests.seed_money
    actual=base.FutureAdoptionTests.actual
    def setUp(self):
        base.FutureAdoptionTests.setUp(self);self.j=IfJudgment(IfInteraction(self.x),self.a)
    def target(self,n,cap='100',c='EUR',**extra):
        p=self.j.prepare_target(dict(node=n,currency=c,maximum=cap,text='My explicit personal cap '+str(c)+' '+cap,**extra))
        return self.x.confirm({'proposal_id':p['proposal_id']})
    def assess(self,roots,options=None,**kw):return self.j.assess(dict(roots=roots,options=options if options is not None else self.options(),**kw))
    def state(self,n):return self.assess([n])['dimensions'][0]['state']
    def test_above_cap_is_conflict(self):
        n=self.keep('Woodworking',120,'EUR');self.target(n);self.assertEqual(self.state(n),'conflict')
    def test_below_cap_is_within_target(self):
        n=self.keep('Course',80,'EUR');self.target(n);self.assertEqual(self.state(n),'within_target')
    def test_boundary_equal_not_universal_safety(self):
        n=self.keep('Course',100,'EUR');self.target(n);o=self.assess([n]);self.assertEqual(o['dimensions'][0]['state'],'within_target');self.assertNotIn('affordable',str(o))
    def test_missing_target_no_default(self):
        n=self.keep('Course',10,'EUR');self.assertEqual(self.state(n),'unassessed')
    def test_unknown_supporting_evidence_not_a_positive_verdict(self):
        rows=list(event('Certification',(50,60),scope=self.m.scope,currency=Currency.EUR))
        rows=[replace(r,meta=replace(r.meta,evidence=tuple(replace(e,freshness='unknown') for e in r.meta.evidence))) for r in rows]
        self.m.retain(self.m.prepare('unknown-proof',tuple(rows),'keep'));n=wire_ref(rows[0].meta.ref);self.target(n)
        out=self.assess([n],[wire_ref(rows[2].meta.ref)]);self.assertEqual(out['dimensions'][0]['state'],'unassessed')
        self.assertEqual(out['dimensions'][0]['reason'],'supporting_information_unknown')
    def test_unsupported_unselected_alternative_not_consumed(self):
        rows=list(event('Training',(50,60),scope=self.m.scope,currency=Currency.EUR))
        rows[3]=replace(rows[3],meta=replace(rows[3].meta,evidence=tuple(replace(e,freshness='unknown') for e in rows[3].meta.evidence)))
        self.m.retain(self.m.prepare('unknown-other',tuple(rows),'keep'));n=wire_ref(rows[0].meta.ref);self.target(n)
        self.assertEqual(self.assess([n],[wire_ref(rows[2].meta.ref)])['dimensions'][0]['state'],'within_target')

    def test_unknown_selected_amount(self):
        n=self.keep('Renovation',None);p=self.x.prepare(dict(text='Unknown EUR quote',node=n,currency='EUR'));self.x.confirm({'proposal_id':p['proposal_id']});self.target(n);self.assertEqual(self.state(n),'unassessed')
    def test_range_crossing_cap_unassessed(self):
        n=self.keep('Class',50,'EUR');p=self.x.prepare(dict(text='EUR quote',node=n,label='Interval',amount=dict(currency='EUR',precision='range',value=None,range_min='90',range_max='110')));self.x.confirm({'proposal_id':p['proposal_id']});self.target(n)
        o=self.assess([n],[self.options()[-1]]);self.assertEqual(o['dimensions'][0]['reason'],'range_crosses_target')
    def test_range_wholly_above(self):
        n=self.keep('Rent deposit',50,'EUR');p=self.x.prepare(dict(text='EUR quote',node=n,label='Range',amount=dict(currency='EUR',precision='range',value=None,range_min='110',range_max='120')));self.x.confirm({'proposal_id':p['proposal_id']});self.target(n);self.assertEqual(self.assess([n],[self.options()[-1]])['dimensions'][0]['state'],'conflict')
    def test_zero_cap_explicit_not_missing(self):
        n=self.keep('Tool',1,'EUR');self.target(n,'0');self.assertEqual(self.state(n),'conflict')
    def test_cny_sgd_native_targets(self):
        for cur in ('CNY','SGD'):
            n=self.keep('Native '+cur,50,cur);self.target(n,'40',cur);self.assertEqual(self.assess([n],[self.options()[-1]])['dimensions'][0]['state'],'conflict')
    def test_mixed_currency_no_fx(self):
        n=self.keep('Moving',None);self.keep('Truck',70,'EUR',n);self.keep('Service',20,'SGD',n);self.target(n);self.assertEqual(self.assess([n])['dimensions'][0]['reason'],'mixed_currency')
    def test_expected_income_cannot_offset_cost(self):
        n=self.keep('Program',None);self.keep('Course cost',120,'EUR',n);rows=list(event('Grant',(500,0),scope=self.m.scope,control='external_outcome',currency=Currency.EUR));rows[0]=replace(rows[0],node_kind='component',parent=ref(n));self.m.retain(self.m.prepare('grant',tuple(rows),'keep'));self.target(n)
        self.assertEqual(self.assess([n],[self.options()[0],wire_ref(rows[2].meta.ref)])['dimensions'][0]['state'],'conflict')
    def test_recurring_not_added_to_once(self):
        rows=list(event('Subscription',(50,60),scope=self.m.scope,currency=Currency.EUR));rows=[replace(r,effects=tuple(replace(e,time_basis='monthly') for e in r.effects)) if type(r) is FutureOption else r for r in rows];self.m.retain(self.m.prepare('monthly',tuple(rows),'keep'));n=wire_ref(rows[0].meta.ref);self.target(n);self.assertEqual(self.assess([n],[wire_ref(rows[2].meta.ref)])['dimensions'][0]['reason'],'unsupported_time_basis')
    def test_direct_option_alternative_existing_only(self):
        n=self.keep('Class',120,'EUR');self.option(n,80,'EUR','Evening class');self.target(n);o=self.assess([n],[self.options()[0]]);a=o['alternatives'][0];self.assertEqual(a['label'],'Evening class');self.assertEqual(a['dimensions'][0]['state'],'within_target')
    def test_optional_component_exclusion_trial(self):
        n=self.keep('Workshop',None);self.keep('Venue',80,'EUR',n);self.keep('Equipment',40,'EUR',n);self.target(n);o=self.assess([n]);self.assertEqual(o['dimensions'][0]['state'],'conflict');self.assertTrue(any(a['label']=='Without Equipment' and a['dimensions'][0]['state']=='within_target' for a in o['alternatives']))
    def test_trial_same_if_recomputes(self):
        n=self.keep('Class',120,'EUR');self.option(n,80,'EUR','Online class');self.target(n);o=self.assess([n],[self.options()[0]]);a=o['alternatives'][0];new=self.j.assess(a['selection']);self.assertEqual(new['dimensions'][0]['state'],'within_target');self.assertEqual(new['dimensions'][0]['node'],n)
    def test_no_alternative_no_advice(self):
        n=self.keep('Course',120,'EUR');self.target(n);self.assertEqual(self.assess([n])['alternatives'],[])
    def test_repeated_read_no_financial_or_future_write(self):
        n=self.keep('Workshop',100,'EUR');self.target(n);s=self.store.snapshot;rev=self.m.read()[1];a=self.assess([n]);b=self.assess([n]);self.assertEqual(a,b);self.assertEqual(self.store.snapshot,s);self.assertEqual(self.m.read()[1],rev)
    def test_explanation_facts_source(self):
        n=self.keep('Workshop',100,'EUR');self.target(n);o=self.assess([n]);self.assertEqual(o['explanation_facts']['schema'],'finvis.explanation-facts.v0.1');self.assertEqual(o['dimensions'][0]['estimate'],o['explanation_facts']['impact']['buckets'][0]['known_selection_total'][0])
    def test_target_requires_explicit_confirm(self):
        n=self.keep('Class',50,'EUR');p=self.j.prepare_target(dict(node=n,currency='EUR',maximum='40',text='My own cap is EUR40'));self.assertEqual(self.state(n),'unassessed');self.x.confirm({'proposal_id':p['proposal_id']});self.assertEqual(self.state(n),'conflict')
    def test_target_changes_do_not_break_tree(self):
        n=self.keep('Workshop',None);self.keep('Class',50,'EUR',n);self.target(n,'40');self.target(n,'60');self.assertEqual(self.state(n),'within_target');self.assertEqual(len(self.x.listing()['records']),3+1)
    def test_target_remove_preserves_prior_versions(self):
        n=self.keep('Class',50,'EUR');self.target(n);p=self.j.prepare_target(dict(node=n,text='Remove my budget',remove=True));self.x.confirm({'proposal_id':p['proposal_id']});self.assertEqual(self.state(n),'unassessed');c,_,_=self.m.read();self.assertEqual(sum(any(s.startswith(PREFIX) for s in getattr(r,'context',())) for r in c.records),2)
    def test_unchanged_target_rejected_no_extra_revision(self):
        n=self.keep('Class',50,'EUR');self.target(n);rev=self.m.read()[1]
        with self.assertRaisesRegex(FutureError,'future_unchanged'):self.target(n)
        self.assertEqual(self.m.read()[1],rev)
    def test_changed_target_invalidates_signature(self):
        n=self.keep('Class',50,'EUR');self.target(n);a=self.assess([n]);self.target(n,'40');b=self.assess([n],previous_binding=a['binding']);self.assertFalse(b['previous_valid']);self.assertEqual(b['dimensions'][0]['state'],'conflict')
    def test_unrelated_future_change_preserves_relevance(self):
        n=self.keep('Class',50,'EUR');self.target(n);a=self.assess([n]);self.keep('Unrelated class',80,'SGD');b=self.assess([n],[self.options()[0]],previous_binding=a['binding']);self.assertTrue(b['previous_valid']);self.assertNotEqual(a['binding']['future_revision'],b['binding']['future_revision'])
    def test_unrelated_financial_revision_does_not_invalidate(self):
        n=self.keep('Class',50,'EUR');self.target(n);a=self.assess([n]);self.seed_money();b=self.assess([n],previous_binding=a['binding']);self.assertTrue(b['previous_valid']);self.assertNotEqual(a['binding']['world_version']['revision'],b['binding']['world_version']['revision'])
    def test_selected_option_version_stales_old_assessment(self):
        n=self.keep('Class',50,'EUR');self.target(n);a=self.assess([n]);c,_,_=self.m.read();old=c.get(ref(self.options()[0]));new=replace(old,meta=self.x.meta('option','Correct source',old.meta.ref.id,old.meta.ref));self.m.retain(self.m.prepare('correct',(new,),'revise'));b=self.assess([n],previous_binding=a['binding']);self.assertEqual(b['dimensions'][0]['reason'],'inputs_changed');self.assertFalse(b['previous_valid'])
    def test_saved_scenario_binding(self):
        n=self.keep('Class',50,'EUR');self.target(n);p=self.x.prepare_scenario(dict(roots=[n],options=self.options(),name='My class'));self.x.confirm({'proposal_id':p['proposal_id']});s=next(r['ref'] for r in self.x.listing()['records'] if r['ref']['kind']=='scenario');o=self.j.assess(dict(scenario=s));self.assertEqual(o['binding']['scenario_ref'],s)
    def test_transient_proposal_uses_same_overlay(self):
        n=self.keep('Course',80,'EUR');self.target(n);i=self.j.i;trial=i.propose(dict(text='Premium class',node=n,label='Premium',amount=price(120,'EUR'),roots=[n],options=self.options()));o=self.j.assess(dict(proposal_id=trial['proposal_id'],**trial['selection']));self.assertEqual(o['dimensions'][0]['state'],'conflict')
    def test_restart_recovers_target_and_judgment(self):
        n=self.keep('Workshop',50,'EUR');self.target(n);before=self.assess([n]);self.restart();self.j=IfJudgment(IfInteraction(self.x),self.a);after=self.assess([n]);self.assertEqual(before['dimensions'],after['dimensions'])
    def test_target_ack_retry_after_restart(self):
        n=self.keep('Course',50,'EUR');p=self.j.prepare_target(dict(node=n,currency='EUR',maximum='40',text='EUR40 maximum'));body={'proposal_id':p['proposal_id']};a=self.x.confirm(body);self.restart();self.assertEqual(self.x.confirm(body),a)
    def test_eur_plan_not_payment(self):
        n=self.keep('Art class',50,'EUR');self.target(n,'40');self.apply(self.adopt([n],self.options()));self.assertEqual(len(self.records('planned_expense')),1);self.assertEqual(self.records('actual_occurrence'),[])
    def test_changed_adopted_choice_invalidates_live_judgment(self):
        n=self.keep('Training course',50,'EUR');self.option(n,30,'EUR','Home course');opts=self.options();self.target(n)
        self.apply(self.adopt([n],[opts[0]]));before=self.assess([n],[opts[0]])
        self.apply(self.adopt([n],[opts[1]]));after=self.assess([n],[opts[0]],previous_binding=before['binding'])
        self.assertFalse(after['previous_valid']);self.assertEqual(after['dimensions'][0]['reason'],'inputs_changed');self.assertEqual(after['alternatives'],[])
    def test_unrelated_adoption_does_not_invalidate(self):
        n=self.keep('Training course',50,'EUR');option=self.options()[0];self.target(n);before=self.assess([n],[option])
        other=self.keep('Art class',30,'SGD');self.apply(self.adopt([other],[self.options()[-1]]))
        after=self.assess([n],[option],previous_binding=before['binding']);self.assertTrue(after['previous_valid'])

    def test_target_never_changes_financial_receipts(self):
        n=self.keep('Class',50,'EUR');s=self.store.snapshot;self.target(n);self.assertEqual(s,self.store.snapshot);self.assertEqual(self.w.history()['entries'],[])
    def test_target_invalid_minor_precision(self):
        n=self.keep('Class',50,'EUR')
        with self.assertRaises(ValueError):self.target(n,'4.001')
    def test_target_wrong_scope_and_currency_fail(self):
        n=self.keep('Course',None);child=self.keep('Venue',50,'EUR',n)
        with self.assertRaisesRegex(FutureError,'budget_root_required'):self.target(child)
        for bad in ('USD',None,{},[],True):
            with self.subTest(currency=bad),self.assertRaisesRegex(FutureError,'currency_required'):self.target(n,c=bad)
    def test_bad_binding_rejected(self):
        n=self.keep('Class',50,'EUR')
        with self.assertRaises(FutureError):self.assess([n],previous_binding=7)
    def test_large_negative_target_rejected(self):
        n=self.keep('Class',50,'EUR')
        with self.assertRaises(ValueError):self.target(n,'-1')
    def test_english_chinese_identical_financial_meaning(self):
        n=self.keep('Learning budget',120,'EUR');self.target(n);a=self.assess([n],language='en');b=self.assess([n],language='zh-CN');self.assertEqual(a['dimensions'],b['dimensions'])

if __name__=='__main__':unittest.main()

"""Bounded read-only consequences; synthetic worlds only, no provider calls."""
import unittest
from dataclasses import replace
from finvis.simple_surface import SimpleSurface
from finvis.if_interaction import IfInteraction
from finvis.future_space import FutureError, FutureOption
from finvis.future_experience import wire_ref
from finvis.domain import Currency, Money
from finvis.world_values import AmountEstimate
from tests import test_if_judgment as base
from tests.test_future_space import event
from tests.test_future_experience import price
from tests.test_persistent_world import seed, changes, prepare

class SimpleSurfaceTests(unittest.TestCase):
    keep=base.JudgmentTests.keep
    option=base.JudgmentTests.option
    options=base.JudgmentTests.options
    adopt=base.JudgmentTests.adopt
    apply=base.JudgmentTests.apply
    actual=base.JudgmentTests.actual
    records=base.JudgmentTests.records
    restart=base.JudgmentTests.restart
    def setUp(self):
        base.JudgmentTests.setUp(self)
        self.service=SimpleSurface(IfInteraction(self.x),self.a)
    def funds(self,n=17000):
        cmd=seed(self.store)
        ops=tuple(replace(op,record=replace(op.record,balance=AmountEstimate.exact_reported(Money(n*100,op.record.currency)))) if op.record.__class__.__name__=='AssetPosition' else op for op in cmd.change_set.operations)
        self.store.execute(prepare(self.store,changes(self.store,ops,'funded'),'funded'))
    def sim(self,roots,options=None,**kw):
        return self.service.simulate(dict(roots=roots,options=options if options is not None else self.options(),**kw))
    def test_partial_known_34000_less_19200(self):
        self.funds();roots=[self.keep('Rail upgrade',4200),self.keep('Studio equipment',15000),self.keep('Undecided course',None)]
        p=self.x.prepare(dict(node=roots[-1],text='Course amount unknown CNY',currency='CNY'));self.x.confirm({'proposal_id':p['proposal_id']})
        b=self.sim(roots)['buckets'][0]
        self.assertEqual(b['starting']['lower_minor'],3400000);self.assertEqual(b['known_cost']['lower_minor'],1920000);self.assertEqual(b['after']['lower_minor'],1480000);self.assertTrue(b['partial']);self.assertEqual(b['unknown_count'],1)
    def test_known_selected_result_not_full_forecast(self):
        self.funds();n=self.keep('Workshop',200);o=self.sim([n]);self.assertEqual(o['buckets'][0]['after']['lower_minor'],3380000);self.assertTrue(o['omissions']['monthly_cash_flow']);self.assertFalse(o['financial_authority'])
    def test_unknown_has_no_zero_amount(self):
        self.funds();n=self.keep('Course',None);p=self.x.prepare(dict(node=n,text='Unknown CNY',currency='CNY'));self.x.confirm({'proposal_id':p['proposal_id']});o=self.sim([n]);self.assertIsNone(o['items'][0]['amount']);self.assertTrue(o['buckets'][0]['partial']);self.assertEqual(o['buckets'][0]['unknown_count'],1)
    def test_no_starting_money_keeps_cost(self):
        n=self.keep('Workshop',200);b=self.sim([n])['buckets'][0];self.assertIsNone(b['after']);self.assertEqual(b['known_cost']['lower_minor'],20000);self.assertEqual(b['unavailable_reason'],'starting_unknown')
    def test_no_currency_conversion_or_global_total(self):
        self.funds();roots=[self.keep('Native class',200,'CNY'),self.keep('Overseas class',70,'SGD'),self.keep('Print course',40,'EUR')];o=self.sim(roots);self.assertEqual([b['currency'] for b in o['buckets']],['CNY','EUR','SGD']);self.assertIsNone(o['buckets'][1]['after']);self.assertNotIn('total',o)
    def test_existing_compatible_alternative_same_rest(self):
        self.funds();n=self.keep('Rail upgrade',4200);a=self.options()[0];self.option(n,1800,label='Standard seat');b=self.options()[-1];other=self.keep('Equipment',15000);c=self.options()[-1];o=self.sim([n,other],[a,c]);self.assertEqual(o['buckets'][0]['after']['lower_minor'],1480000);alt=o['alternatives'][0];self.assertEqual(alt['result']['buckets'][0]['after']['lower_minor'],1720000);self.assertEqual(alt['cost_difference']['lower_minor'],-240000);self.assertEqual(alt['alternative'],b);self.assertEqual(o['items'][0]['option'],a)
    def test_different_currency_not_comparable(self):
        n=self.keep('Course',100,'CNY');a=self.options()[0];self.option(n,80,'EUR');self.assertEqual(self.sim([n],[a])['alternatives'],[])
    def test_unknown_alternative_no_fabricated_difference(self):
        n=self.keep('Course',100);a=self.options()[0];p=self.x.prepare(dict(node=n,text='Other cost unknown CNY',currency='CNY',label='Other course'));self.x.confirm({'proposal_id':p['proposal_id']});o=self.sim([n],[a]);self.assertIsNone(o['alternatives'][0]['cost_difference'])
    def test_ranges_use_interval_arithmetic(self):
        self.funds();n=self.keep('Class',100);p=self.x.prepare(dict(node=n,text='Range',amount=dict(currency='CNY',precision='range',value=None,range_min='90',range_max='110')));self.x.confirm({'proposal_id':p['proposal_id']});b=self.sim([n],[self.options()[-1]])['buckets'][0];self.assertEqual(b['after'],dict(lower_minor=3389000,upper_minor=3391000))
    def test_external_income_conditional_not_current_assets(self):
        self.funds();rows=event('Research grant',(500,0),scope=self.m.scope,control='external_outcome',currency=Currency.CNY);self.m.retain(self.m.prepare('grant',rows,'keep'));o=self.sim([wire_ref(rows[0].meta.ref)],[wire_ref(rows[2].meta.ref)]);b=o['buckets'][0];self.assertEqual(b['known_inflow']['lower_minor'],50000);self.assertEqual(b['starting']['lower_minor'],3400000)
    def test_recurring_blocks_once_after(self):
        self.funds();rows=event('Membership',(50,60),scope=self.m.scope,currency=Currency.CNY);rows=tuple(replace(r,effects=tuple(replace(e,time_basis='monthly') for e in r.effects)) if type(r) is FutureOption else r for r in rows);self.m.retain(self.m.prepare('monthly',rows,'keep'));b=self.sim([wire_ref(rows[0].meta.ref)],[wire_ref(rows[2].meta.ref)])['buckets'][0];self.assertIsNone(b['after']);self.assertEqual(b['unavailable_reason'],'unsupported_effect')
    def test_parent_child_not_double_counted(self):
        self.funds();p=self.keep('Garden',None);self.keep('Tools',400,parent=p);self.keep('Plants',300,parent=p);self.assertEqual(self.sim([p])['buckets'][0]['known_cost']['lower_minor'],70000)
    def test_repeat_no_financial_future_or_receipt_writes(self):
        self.funds();n=self.keep('Materials',200);snap=self.store.snapshot;future=self.m.read()[1];history=self.w.history();a=self.sim([n]);self.assertEqual(self.sim([n]),a);self.assertEqual(self.store.snapshot,snap);self.assertEqual(self.m.read()[1],future);self.assertEqual(self.w.history(),history)
    def test_transient_override_same_meaning_overlay(self):
        self.funds();n=self.keep('Class',200);i=self.service.j.i;p=i.propose(dict(text='Class now CNY250',node=n,label='Class',amount=price(250),roots=[n],options=self.options()));o=self.sim(p['selection']['roots'],p['selection']['options'],proposal_id=p['proposal_id']);self.assertEqual(o['buckets'][0]['known_cost']['lower_minor'],25000)
    def test_mutually_exclusive_double_selection_rejected(self):
        n=self.keep('Class',100);self.option(n,80)
        with self.assertRaises(FutureError):self.sim([n])
    def test_changed_adoption_stales_previous(self):
        self.funds();n=self.keep('Course',200);a=self.options()[0];self.option(n,100);b=self.options()[-1];self.apply(self.adopt([n],[a]));self.apply(self.adopt([n],[b]));o=self.sim([n],[a]);self.assertEqual(o['status'],'stale');self.assertIsNone(o['buckets'][0]['after']);self.assertEqual(o['alternatives'],[])
    def test_restart_same_read_result(self):
        self.funds();n=self.keep('Course',200);o=self.sim([n]);self.restart();self.service=SimpleSurface(IfInteraction(self.x),self.a);self.assertEqual(o,self.sim([n]))
    def test_bad_request_rejected(self):
        for payload in ({},{'roots':[],'options':[],'apply':True},{'roots':[],'options':[],'language':'other'}):
            with self.assertRaises((FutureError,ValueError)):self.service.simulate(payload)

if __name__=='__main__':unittest.main()

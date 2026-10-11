"""D2 generic hypothetical acceptance, no inference or production storage."""
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import patch

from finvis.decision_baseline import BaselineService
from finvis.domain import Currency, Ref
from finvis.if_lines import IfError, IfLineService, bucket
from finvis.if_interpretation import interpret
from finvis.world_commands import PutRecord
from finvis.world_serialization import dumps_world_mutation
from tests.test_world_application import synthetic, authority
from tests.test_decision_baseline import amount, component, context, external
from tests.test_persistent_world import changes, prepare


def setup_d2():
    target=synthetic((Currency.CNY,Currency.SGD))
    baselines=BaselineService(authority(target))
    ref=next(r for r in target.snapshot.world.members if r.record_kind=='world_decision')
    request=dict(decision_ref={'record_kind':ref.record_kind,'ref':{'id':ref.ref.id,'version':ref.ref.version}},
        expected_version=0,context=context('training'),components=[component(amount('120','range',upper='160'),'course')],
        dependency_refs=[],scope_complete=True,buffer=None)
    request['components'][0]['label']='Course package'
    request['context'].update(duration_days=5,nights=4,party_size=1)
    b=baselines.establish(request)
    return IfLineService(baselines),target,b,request


def make(service,b,source='If I buy a reference book for CNY 30?',**overrides):
    p=service.propose(dict(baseline_ref=b['ref'],text=source))
    draft={**p['draft'],**overrides}
    return service.create(dict(proposal_id=p['id'],clarification=draft))


class IfTests(unittest.TestCase):
    def setUp(self):
        self.s,self.store,self.b,self.request=setup_d2()
        self.before=dumps_world_mutation(self.store.snapshot)

    def tearDown(self):
        # Each read/exploration-only test asserts the whole authoritative snapshot.
        if not getattr(self,'mutates_fixture',False):
            self.assertEqual(dumps_world_mutation(self.store.snapshot),self.before)

    def test_add_independent_not_accumulated(self):
        a=make(self.s,self.b,amount=amount('20','range',upper='40'))
        b=make(self.s,self.b,'If I add a moving service for CNY 80?',amount=amount('80','range',upper='100'))
        self.assertEqual(a['effects']['scenario_cost'][0]['lower_minor'],14000)
        self.assertEqual(b['effects']['scenario_cost'][0]['lower_minor'],20000)
        self.assertEqual(b['effects']['scenario_cost'][0]['upper_minor'],26000)
        self.assertEqual(len(self.s.combinations),0)

    def test_preview_selection_never_creates_or_evaluates_combination(self):
        a=make(self.s,self.b)
        with patch.object(self.s,'evaluate',side_effect=AssertionError('full evaluation on checkbox')):
            for _ in range(3):p=self.s.preview({'line_ids':[a['id']]})
        self.assertEqual(p['status'],'selection_only');self.assertFalse(p['scenario_created'])
        self.assertEqual(self.s.combinations,{})

    def test_confirm_explicit_repeat_idempotent_and_independent(self):
        a=make(self.s,self.b,amount=amount('20','range',upper='40'))
        b=make(self.s,self.b,'If I add a move for SGD 50?',amount=amount('50','approximate','SGD'))
        c=self.s.confirm({'line_ids':[a['id'],b['id']]})
        self.assertEqual(c['status'],'confirmed')
        self.assertEqual(len(c['effects']['scenario_cost']),2)
        self.assertEqual(self.s.confirm({'line_ids':[b['id'],a['id']]})['id'],c['id'])
        self.assertEqual(len(self.s.combinations),1)

    def test_replace_is_substitution_not_addition(self):
        a=make(self.s,self.b,'If I upgrade Course package for CNY 200?',target='component:course')
        e=a['effects'];self.assertEqual(e['scenario_cost'][0]['value_minor'],20000)
        # Approximate quote without bounds minus range cannot become exact delta.
        self.assertEqual(e['cost_delta'][0]['precision'],'unknown')

    def test_replacement_range_conservative_signed_bounds(self):
        a=make(self.s,self.b,'If I replace Course package?',target='component:course',amount=amount('100','range',upper='140'))
        self.assertEqual(a['effects']['cost_delta'][0]['lower_minor'],-6000)
        self.assertEqual(a['effects']['cost_delta'][0]['upper_minor'],2000)
        self.assertEqual(a['effects']['scenario_cost'][0]['upper_minor'],14000)

    def test_remove_cost_does_not_add_negative_asset(self):
        a=make(self.s,self.b,'If I remove Course package?',target='component:course')
        self.assertEqual(a['effects']['scenario_cost'][0]['value_minor'],0)
        self.assertEqual(a['effects']['cost_delta'][0]['lower_minor'],-16000)
        self.assertEqual(a['effects']['expected_inflow_delta'],[])

    def test_expected_inflow_remove_separate_from_cost_cash(self):
        t=next(t for t in self.s.targets(self.s.baseline(self.b['ref'])) if t['kind']=='expected_inflow')
        a=make(self.s,self.b,'如果奖金没有了呢？',target=t['key'])
        self.assertEqual(a['kind'],'remove')
        self.assertEqual(a['effects']['baseline_cost'],a['effects']['scenario_cost'])
        self.assertEqual(a['effects']['cost_delta'][0]['value_minor'],0)
        self.assertTrue(a['effects']['expected_inflow_delta'])

    def test_actual_report_rejected_even_manual_relabel(self):
        p=self.s.propose({'baseline_ref':self.b['ref'],'text':'My company confirmed my bonus is cancelled.'})
        self.assertEqual(p['status'],'not_hypothetical')
        with self.assertRaisesRegex(IfError,'hypothetical_only'):
            self.s.create({'proposal_id':p['id'],'clarification':p['draft']})
        self.assertFalse(self.s.lines)

    def test_ongoing_context_remains_original_source(self):
        source='I am two days into the course. What if I attend an extra session for SGD 38?'
        a=make(self.s,self.b,source)
        self.assertEqual(a['source_text'],source);self.assertEqual(a['observed_amount']['precision'],'exact_reported')
        self.assertEqual(a['effects']['scenario_cost'][1]['precision'],'approximate')

    def test_parameter_no_invented_scaling(self):
        a=make(self.s,self.b,'如果把五天改成七天呢？',target='component:course',amount=amount(precision='unknown'))
        self.assertEqual(a['kind'],'parameter');self.assertEqual(a['parameter']['after'],7)
        self.assertEqual(a['effects']['scenario_cost'][0]['precision'],'unknown')

    def test_parameter_explicit_repriced_component(self):
        a=make(self.s,self.b,'If I change 5 days to 7 days?',target='component:course',amount=amount('210'))
        self.assertEqual(a['effects']['scenario_cost'][0]['value_minor'],21000)
        self.assertEqual(a['effects']['parameters'][0]['before'],5)

    def test_quantity_explicit_user_baseline_assumption(self):
        a=make(self.s,self.b,'If I change quantity?',kind='parameter',target='component:course',
               parameter={'name':'quantity','before':2,'after':3},amount=amount('230'),assumptions=['User assumes two units in the original package; revised total for three'])
        self.assertEqual(a['parameter']['name'],'quantity')

    def test_wrong_parameter_before_blocked(self):
        with self.assertRaisesRegex(IfError,'parameter_before_mismatch'):
            make(self.s,self.b,'If I change 4 days to 9 days?',target='component:course',amount=amount('240'))

    def test_competing_replacements_block_confirm(self):
        a=make(self.s,self.b,'If I upgrade Course package to premium?',target='component:course',amount=amount('250'))
        b=make(self.s,self.b,'If I replace Course package with a private tutor?',target='component:course',amount=amount('300'))
        self.assertIn('competing_target_changes',self.s.preview({'line_ids':[a['id'],b['id']]})['conflicts'])
        with self.assertRaises(IfError):self.s.confirm({'line_ids':[a['id'],b['id']]})
        self.assertFalse(self.s.combinations)

    def test_inflow_removal_dependency_conflict(self):
        t=next(t for t in self.s.targets(self.s.baseline(self.b['ref'])) if t['kind']=='expected_inflow')
        a=make(self.s,self.b,'If I remove an expected receipt?',target=t['key'])
        b=make(self.s,self.b,depends_on=[t['key']])
        self.assertIn('removed_dependency',self.s.preview({'line_ids':[a['id'],b['id']]})['conflicts'])
        with self.assertRaises(IfError):self.s.confirm({'line_ids':[a['id'],b['id']]})

    def test_duplicate_and_empty_selection_refused(self):
        a=make(self.s,self.b)
        for ids in ([],[a['id'],a['id']],['missing']):
            with self.assertRaises(IfError):self.s.confirm({'line_ids':ids})

    def test_same_add_assumption_cannot_double_count(self):
        a=make(self.s,self.b);b=make(self.s,self.b)
        self.assertIn('duplicate_assumption',self.s.preview({'line_ids':[a['id'],b['id']]})['conflicts'])

    def test_baseline_supersession_marks_lines_and_combinations_stale(self):
        a=make(self.s,self.b);c=self.s.confirm({'line_ids':[a['id']]})
        self.request['expected_version']=1;self.s.baselines.establish(self.request)
        view=self.s.view(self.s.lines[a['id']]);self.assertEqual(view['status'],'stale');self.assertIsNone(view['effects'])
        self.assertIn('baseline_superseded',view['stale_reasons'])
        self.assertEqual(self.s.combination_view(self.s.combinations[c['id']])['status'],'stale')
        with self.assertRaises(IfError):make(self.s,self.b)

    def test_different_decision_and_baseline_not_combinable(self):
        a=make(self.s,self.b)
        other=next(r for r in self.store.snapshot.world.members if r.record_kind=='world_decision' and r.ref.id!=self.request['decision_ref']['ref']['id'])
        request=deepcopy(self.request);request['decision_ref']['ref']={'id':other.ref.id,'version':1}
        b=self.s.baselines.establish(request);line=make(self.s,b)
        self.assertIn('different_baselines',self.s.preview({'line_ids':[a['id'],line['id']]})['conflicts'])

    def test_unreadable_source_fails_closed(self):
        a=make(self.s,self.b)
        with patch.object(self.s.baselines.authority,'read',side_effect=RuntimeError('private raw failure')):
            v=self.s.view(self.s.lines[a['id']])
        self.assertEqual(v['status'],'stale');self.assertIsNone(v['effects'])
        self.assertNotIn('private raw',str(v))

    def test_added_dependency_correction_invalidates(self):
        self.mutates_fixture=True
        t=next(t for t in self.s.targets(self.s.baseline(self.b['ref'])) if t['kind']=='expected_inflow')
        a=make(self.s,self.b,'If I remove expected income?',target=t['key'])
        ref=next(r for r in self.store.snapshot.world.members if r.ref.id==t['ref']['ref']['id'])
        old=self.store.snapshot.get(ref)
        new=replace(old,ref=Ref(old.ref.id,2),purpose='Same money, corrected context',observation=replace(old.observation,corrects_ref=old.ref,correction_reason='Clarify income'))
        self.store.execute(prepare(self.store,changes(self.store,(PutRecord('correct',new,ref),),'correct'),'correct'))
        self.assertEqual(self.s.view(self.s.lines[a['id']])['status'],'stale')
        self.assertEqual(self.s.baselines.latest(self.request['decision_ref']['ref']['id'])['status'],'estimate_available')

    def test_unknown_and_range_not_sharpened(self):
        for a in (amount(precision='unknown'),amount('20','range',upper='40'),amount('25','approximate')):
            line=make(self.s,self.b,amount=a)
            delta=line['effects']['cost_delta'][0]
            self.assertEqual(delta['precision'],a['precision'])
            if a['precision']=='approximate':self.assertEqual(line['effects']['scenario_cost'][0]['precision'],'unknown')

    def test_creation_retry_and_mutable_output_isolation(self):
        p=self.s.propose({'baseline_ref':self.b['ref'],'text':'If I buy a bag for SGD 47?'})
        req={'proposal_id':p['id'],'clarification':p['draft']};a=self.s.create(req)
        a['clarification']['amount']['value']='999'
        self.assertEqual(self.s.create(req)['id'],a['id']);self.assertEqual(len(self.s.lines),1)
        self.assertEqual(self.s.create(req)['clarification']['amount']['value'],'47')
        req['clarification']['label']='Different'
        with self.assertRaisesRegex(IfError,'already_used'):self.s.create(req)

    def test_unsupported_meaning_never_coerced_to_add(self):
        p=self.s.propose({'baseline_ref':self.b['ref'],'text':'If things go differently?'})
        self.assertIsNone(p['draft']['kind']);self.assertIn('choose_kind',p['questions'])
        with self.assertRaises(IfError):self.s.create({'proposal_id':p['id'],'clarification':p['draft']})

    def test_target_ambiguity_no_automatic_match(self):
        targets=[{'key':'1','label':'Package'},{'key':'2','label':'Package'}]
        p=interpret('If I replace Package for SGD 30?',targets,None)
        self.assertIsNone(p['draft']['target']);self.assertIn('choose_target',p['questions'])

    def test_native_currency_en_zh_and_range_preparation(self):
        cases=[('If I buy a lens for about CNY 8000?','CNY','approximate','8000'),
               ('如果我买一个大约8000元的镜头？','CNY','approximate','8000'),
               ('If I add a move for SGD 45–70?','SGD','range',None),
               ('如果我去看这个演唱会呢？',None,None,None)]
        for source,currency,precision,value in cases:
            p=interpret(source,[],None)
            self.assertEqual(p['draft']['kind'],'add')
            if currency:self.assertEqual(p['draft']['amount'],dict(currency=currency,precision=precision,value=value,range_min='45' if precision=='range' else None,range_max='70' if precision=='range' else None))
            else:self.assertIsNone(p['draft']['amount'])

    def test_no_currency_inferred_and_no_cross_currency_replacement(self):
        self.assertIsNone(interpret('If I buy a package for 450?',[],None)['draft']['amount'])
        with self.assertRaisesRegex(IfError,'currency_mismatch'):
            make(self.s,self.b,'If I upgrade Course package for SGD 40?',target='component:course')

    def test_negation_is_not_addition(self):
        for source in ("If I don't buy Course package?",'If I do not attend Course package?','如果我不买Course package呢？'):
            self.assertEqual(interpret(source,[],None)['draft']['kind'],'remove')

    def test_remove_quoted_cost_uses_bound_amount_not_raw_quote(self):
        p=self.s.propose({'baseline_ref':self.b['ref'],'text':'If I remove Course package, quoted at CNY 999?'})
        self.assertIsNone(p['draft']['amount'])
        a=self.s.create({'proposal_id':p['id'],'clarification':p['draft']})
        self.assertEqual(a['effects']['cost_delta'][0]['lower_minor'],-16000)
        p=interpret('If I buy a supplement to Course package for CNY 22?', [{'label':'Course package','key':'component:course'}],None)
        self.assertIsNone(p['draft']['target'])

    def test_unproven_numeric_forms_and_unit_prices_need_clarification(self):
        for source in ('If I buy it for CNY 2.5k?', 'If I buy it for CNY 1e3?',
                       'If I buy it for CNY 10,00?', 'If I buy two items for CNY 50 each?'):
            self.assertIsNone(interpret(source,[],None)['draft']['amount'],source)
        p=interpret('如果把十五天改成十七天呢？',[],None)
        self.assertIsNone(p['draft']['parameter'])

    def test_parameter_conflict_and_stale_between_preview_confirm(self):
        a=make(self.s,self.b,'If I change 5 days to 7 days?',target='component:course',amount=amount('210'))
        b=make(self.s,self.b,'If I change 5 days to 8 days?',target='component:course',amount=amount('250'))
        self.assertIn('competing_parameters',self.s.preview({'line_ids':[a['id'],b['id']]})['conflicts'])
        self.assertEqual(self.s.preview({'line_ids':[a['id']]})['status'],'selection_only')
        self.request['expected_version']=1;self.s.baselines.establish(self.request)
        with self.assertRaisesRegex(IfError,'baseline_not_current'):self.s.confirm({'line_ids':[a['id']]})
        self.assertFalse(self.s.combinations)

    def test_session_only_lifecycle_reconstructed_empty(self):
        a=make(self.s,self.b);self.s.confirm({'line_ids':[a['id']]})
        new=IfLineService(BaselineService(authority(self.store)))
        self.assertEqual(new.listing(self.request['decision_ref']['ref']['id'])['lines'],[])
        with self.assertRaisesRegex(IfError,'baseline_missing'):new.propose({'baseline_ref':self.b['ref'],'text':'If I buy it?'})

    def test_no_defaults_and_no_financial_verdict(self):
        self.assertEqual(self.s.listing(self.request['decision_ref']['ref']['id'])['lines'],[])
        a=make(self.s,self.b)
        self.assertFalse(a['financial_authority']);self.assertEqual(a['effects']['metrics'],'unavailable_policy_required')
        self.assertFalse(any(k in a['effects'] for k in ('runway','solvency','affordable','balance')))

    def test_unresolved_baseline_refused(self):
        self.request.update(expected_version=1,components=[])
        b=self.s.baselines.establish(self.request)
        with self.assertRaisesRegex(IfError,'baseline_not_current'):make(self.s,b)

    def test_external_evidence_expiry_uses_d1_freshness(self):
        self.request.update(expected_version=1,components=[external(self.request)])
        b=self.s.baselines.establish(self.request);a=make(self.s,b)
        baseline=self.s.lines[a['id']].baseline
        at=datetime.now(timezone.utc)+timedelta(days=1)
        original=self.s.baselines.view
        with patch.object(self.s.baselines,'view',side_effect=lambda b:original(b,at)):
            v=self.s.view(self.s.lines[a['id']])
        self.assertEqual(v['status'],'stale');self.assertIn('evidence_stale',v['stale_reasons'])


if __name__=='__main__':unittest.main()

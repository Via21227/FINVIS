"""Compound compiler evidence: synthetic temporary stores, no provider calls."""
import unittest
from copy import deepcopy
import json

from finvis.compound_future import capabilities, plan, select
from finvis.future_experience import FutureExperience
from finvis.future_memory import FutureMemoryService
from finvis.future_space import FutureError, FutureNode, FutureOption, FutureVariable
from finvis.if_interaction import IfInteraction
from finvis.persistent_world import PersistentWorldStore
from finvis.persistence_schema import financial_signature
from tests import test_future_experience as base
from tests.test_persistent_world import seed


def claim(cid, label, value='200', *, currency='CNY', kind='future_outflow',
          precision='exact', lower=None, upper=None, source=None, target=None,
          timing=None):
    source = source or label + (' ' + str(value) + ' ' + str(currency) if value is not None else ' amount unknown')
    return dict(id=cid, label=label, kind=kind, source=source,
        money=dict(value=value, lower=lower, upper=upper, currency=currency, precision=precision),
        timing=timing or dict(text=None, iso=None, precision='unknown'), account=None, target=target)


def bundle(*claims, relationships=(), ambiguities=()):
    return dict(version=1, claims=list(claims), relationships=list(relationships),
                requests=[], ambiguities=list(ambiguities))


def source(b):
    return '; '.join(c['source'] for c in b['claims'])


def rel(kind, a, b, text='supported relationship'):
    return {'kind':kind, 'from':a, 'to':b, 'source':text}


class CompoundFutureTests(unittest.TestCase):
    keep = base.FutureExperienceTests.keep

    def setUp(self):
        base.FutureExperienceTests.setUp(self)
        self.i = IfInteraction(self.x)

    def draft(self, b, **kwargs):
        return plan(b, source(b), self.i, **kwargs)

    def total(self, p, currency='CNY'):
        row = next(r for r in p['result']['buckets'] if r['currency'] == currency)
        return row['known_selection_total'][0]['value_minor']

    def test_capability_inspection_is_pure(self):
        b = bundle(claim('a','Course'), claim('b','Already paid',kind='actual_expense'))
        before = financial_signature(self.store._db)
        self.assertEqual(capabilities(b)['supported_claim_ids'], ['a'])
        self.assertEqual(self.x.pending, {})
        self.assertEqual(financial_signature(self.store._db), before)

    def test_independent_future_units_are_one_transient_additive_operation(self):
        b = bundle(claim('a','Course'), claim('b','Materials','75'))
        before = financial_signature(self.store._db)
        p = self.draft(b)
        self.assertEqual(p['stage'], 'trial')
        self.assertEqual(self.total(p), 27500)
        self.assertEqual(self.m.read()[1], 0)
        self.assertEqual(financial_signature(self.store._db), before)
        self.i.keep({'proposal_id':p['proposal_id']})
        self.assertEqual(self.m.read()[1], 1)
        self.assertEqual(self.store.snapshot.revision, 0)

    def test_alternatives_have_no_inferred_choice_or_reference(self):
        b = bundle(claim('a','Train','80'),claim('b','Bus','30'),
                   relationships=[rel('alternative','a','b')])
        p = self.draft(b)
        self.assertEqual(p['selection']['options'], [])
        self.assertEqual(p['result']['status'], 'needs_information')
        variables = [r for r in self.x.pending[p['proposal_id']].records if type(r) is FutureVariable]
        self.assertEqual(len(variables), 1)
        self.assertIsNone(variables[0].reference_option)
        q = select(self.i,p['proposal_id'],p['claim_options'],['a'],automatic_options=p['automatic_options'])
        self.assertEqual(self.total(q), 8000)
        with self.assertRaisesRegex(FutureError,'exclusive_options'):
            select(self.i,p['proposal_id'],p['claim_options'],['a','b'])

    def test_conditional_effect_is_not_counted_without_antecedent_choice(self):
        b = bundle(claim('a','Train','80'),claim('b','Bus','30'),claim('c','Seat fee','10'),
                   relationships=[rel('alternative','a','b'),rel('conditional_on','c','a','if train')])
        p = self.draft(b)
        self.assertEqual(p['selection']['options'], [])
        q = select(self.i,p['proposal_id'],p['claim_options'],['a'],automatic_options=p['automatic_options'])
        self.assertEqual(self.total(q), 9000)
        q = select(self.i,p['proposal_id'],p['claim_options'],['b'],automatic_options=p['automatic_options'])
        self.assertEqual(self.total(q), 3000)
        self.assertNotIn(p['claim_options']['c'],q['selection']['options'])

    def test_production_preview_completes_newly_active_conditional_fee(self):
        b = bundle(claim('a','Train','80'),claim('b','Bus','30'),claim('c','Seat fee','10'),
                   relationships=[rel('alternative','a','b'),rel('conditional_on','c','a','if train')])
        p = self.draft(b)
        q = self.i.preview(dict(proposal_id=p['proposal_id'],roots=p['selection']['roots'],options=[p['claim_options']['a']]))
        self.assertEqual(self.total(q),9000)
        self.assertIn(p['claim_options']['c'],q['selection']['options'])
        self.i.keep({'proposal_id':p['proposal_id']})
        result = self.x.preview(dict(roots=p['selection']['roots'],options=[p['claim_options']['a']]))
        self.assertIn(p['claim_options']['c'],result['selection']['options'])

    def test_production_ab_cost_difference_includes_conditional_fee(self):
        from finvis.simple_surface import SimpleSurface
        b = bundle(claim('a','Train','80'),claim('b','Bus','30'),claim('c','Seat fee','10'),
                   relationships=[rel('alternative','a','b'),rel('conditional_on','c','a','if train')])
        p = self.draft(b)
        result = SimpleSurface(self.i).simulate(dict(proposal_id=p['proposal_id'],roots=p['selection']['roots'],options=[p['claim_options']['b']]))
        alternative = next(a for a in result['alternatives'] if a['alternative']==p['claim_options']['a'])
        self.assertEqual(alternative['cost_difference'],dict(lower_minor=6000,upper_minor=6000))
        self.assertEqual(alternative['result']['buckets'][0]['known_cost'],dict(lower_minor=9000,upper_minor=9000))

    def test_conditional_completion_does_not_reselect_unchecked_independent_event(self):
        from finvis.simple_surface import SimpleSurface
        b = bundle(claim('a','Train','80'),claim('b','Bus','30'),claim('c','Seat fee','10'),claim('d','Course','500'),
                   relationships=[rel('alternative','a','b'),rel('conditional_on','c','a','if train')])
        p = self.draft(b)
        independent = p['claim_options']['d']
        result = SimpleSurface(self.i).simulate(dict(proposal_id=p['proposal_id'],roots=p['selection']['roots'],options=[p['claim_options']['a']]))
        self.assertNotIn(independent,result['selection']['options'])
        self.assertEqual(result['buckets'][0]['known_cost'],dict(lower_minor=9000,upper_minor=9000))
        self.assertTrue(result['buckets'][0]['partial'])

    def test_negative_conditional_blocks_only_affected_unit(self):
        b = bundle(claim('a','Train','80'),claim('b','Fee','30'),claim('c','Course','25'),
                   relationships=[rel('conditional_on','b','a','if not train')])
        p = self.draft(b)
        self.assertEqual(self.total(p), 10500)
        self.assertEqual(p['blocked'],[dict(claim_id='b',reason='condition_polarity_requires_review')])

    def test_local_ambiguity_blocks_dependency_not_independent_unit(self):
        b = bundle(claim('a','New equipment','80'),claim('b','Fee','30'),claim('c','Course','25'),
            relationships=[rel('conditional_on','b','a')],
            ambiguities=[dict(id='q',claim_ids=['a'],field='price_basis',question='Which price?',choices=[],source='New equipment')])
        p = self.draft(b)
        self.assertEqual(p['supported_claim_ids'], ['c'])
        self.assertEqual(self.total(p), 2500)

    def test_unknown_currency_preserves_other_native_units(self):
        b = bundle(claim('a','Course','80',currency=None),claim('b','Materials','30'))
        p = self.draft(b)
        self.assertEqual(p['supported_claim_ids'], ['b'])
        self.assertEqual(self.total(p), 3000)

    def test_unknown_money_is_not_zero(self):
        p = self.draft(bundle(claim('a','Course',None,precision='unknown')))
        self.assertEqual(p['result']['status'],'needs_information')
        o = next(r for r in self.x.pending[p['proposal_id']].records if type(r) is FutureOption)
        self.assertIsNone(o.effects[0].observed.amount)

    def test_native_money_range_approximation_and_timing_are_preserved(self):
        b = bundle(claim('a','Course','80',currency='SGD',precision='approximate'),
                   claim('b','Materials',None,currency='EUR',precision='range',lower='20',upper='40'),
                   claim('c','Travel','200',source='Travel CNY 200 pay 2027-03-12',
                         timing=dict(text='2027-03-12',iso='2027-03-12',precision='day')))
        p = self.draft(b)
        self.assertEqual({r['currency'] for r in p['result']['buckets']},{'CNY','SGD','EUR'})
        rows = [r for r in self.x.pending[p['proposal_id']].records if type(r) is FutureOption]
        timed = next(r for r in rows if r.label=='Travel')
        self.assertEqual(timed.effects[0].money_timing.value,'2027-03-12')
        self.assertEqual(timed.effects[0].planning.precision.value,'approximate')

    def test_relative_timing_keeps_literal_without_inventing_date(self):
        p = self.draft(bundle(claim('a','Course',source='Course CNY 200 next month',
            timing=dict(text='next month',iso=None,precision='literal'))))
        o = next(r for r in self.x.pending[p['proposal_id']].records if type(r) is FutureOption)
        self.assertEqual(o.effects[0].money_timing.source_text,'next month')
        self.assertIsNone(o.effects[0].money_timing.value)

    def test_structural_parent_and_parts_do_not_double_count(self):
        b = bundle(claim('a','Workshop',None,currency=None,precision='unknown'),claim('b','Room','80'),claim('c','Materials','30'),
                   relationships=[rel('part_of','b','a'),rel('part_of','c','a')])
        p = self.draft(b)
        self.assertEqual(self.total(p),11000)
        self.assertEqual(len(p['selection']['roots']),1)
        self.assertEqual(len(p['selection']['options']),2)

    def test_parent_total_and_parts_are_locally_blocked(self):
        b = bundle(claim('a','Workshop','500'),claim('b','Room','80'),claim('c','Course','30'),
                   relationships=[rel('part_of','b','a')])
        p = self.draft(b)
        self.assertEqual(p['supported_claim_ids'],['c'])
        self.assertEqual(self.total(p),3000)

    def test_dependency_cycle_preserves_independent_supported_unit(self):
        b = bundle(claim('a','Fee A'),claim('b','Fee B'),claim('c','Course','30'),
                   relationships=[rel('conditional_on','a','b'),rel('conditional_on','b','a')])
        p = self.draft(b)
        self.assertEqual(p['supported_claim_ids'],['c'])
        self.assertEqual(self.total(p),3000)

    def test_mixed_direction_alternative_is_local_blocker(self):
        b = bundle(claim('a','Purchase'),claim('b','Expected gift',kind='expected_inflow'),claim('c','Course','30'),
                   relationships=[rel('alternative','a','b')])
        p = self.draft(b)
        self.assertEqual(p['supported_claim_ids'],['c'])
        self.assertEqual(self.total(p),3000)

    def test_future_replacement_is_exclusive_not_additive(self):
        b = bundle(claim('a','New course','80'),claim('b','Old course','30'),
                   relationships=[rel('replacement','a','b')])
        p = self.draft(b)
        self.assertEqual(p['selection']['options'],[])
        q = select(self.i,p['proposal_id'],p['claim_options'],['a'])
        self.assertEqual(self.total(q),8000)

    def test_chinese_explicit_money_date_uses_existing_timing_parser(self):
        p = self.draft(bundle(claim('a','课程',source='课程200元，2027年3月12日付款',
            timing=dict(text='2027年3月12日',iso='2027-03-12',precision='day'))))
        o = next(r for r in self.x.pending[p['proposal_id']].records if type(r) is FutureOption)
        self.assertEqual(o.effects[0].money_timing.value,'2027-03-12')

    def test_ab_shared_range_cancels_before_existing_interval_arithmetic(self):
        from finvis.simple_surface import SimpleSurface
        b = bundle(claim('a','Train','80'),claim('b','Bus','30'),
                   claim('c','Lodging',None,precision='range',lower='100',upper='200'),
                   relationships=[rel('alternative','a','b')])
        p = self.draft(b)
        result = SimpleSurface(self.i).simulate(dict(proposal_id=p['proposal_id'],roots=p['selection']['roots'],options=[p['claim_options']['b'],p['claim_options']['c']]))
        self.assertEqual(result['alternatives'][0]['cost_difference'],dict(lower_minor=5000,upper_minor=5000))

    def test_original_bundle_source_persists_in_existing_evidence(self):
        b = bundle(claim('a','Course'))
        p = self.draft(b);self.i.keep({'proposal_id':p['proposal_id']})
        payloads = [json.loads(e.payload) for r in self.m.read()[0].records for e in r.meta.evidence if e.payload]
        full = next(p['source'] for p in payloads if 'source' in p)
        self.assertEqual(full['original_text'], source(b))
        self.assertEqual(full['bundle'], b)

    def test_unrelated_retained_future_survives(self):
        self.keep('Unrelated',430)
        before = self.m.read()[0].records
        p = self.draft(bundle(claim('a','Course')))
        self.i.keep({'proposal_id':p['proposal_id']})
        self.assertEqual(self.m.read()[0].records[:len(before)],before)

    def test_same_action_retry_recovers_pending_and_committed_identity(self):
        b = bundle(claim('a','Course'))
        p = self.draft(b,operation_id='compound/test/one')
        self.assertEqual(self.draft(b,operation_id='compound/test/one')['proposal_id'],p['proposal_id'])
        self.assertEqual(len(self.x.pending),1)
        first = self.i.keep({'proposal_id':p['proposal_id']})
        self.assertEqual(self.draft(b,operation_id='compound/test/one')['acknowledgement'],first['acknowledgement'])
        self.assertEqual(self.m.read()[1],1)
        with self.assertRaisesRegex(FutureError,'idempotency_conflict'):
            self.draft(bundle(claim('a','Course','300')),operation_id='compound/test/one')

    def test_identical_words_with_new_action_are_not_suppressed(self):
        b = bundle(claim('a','Course'))
        a = self.draft(b);b = self.draft(b)
        self.assertNotEqual(a['proposal_id'],b['proposal_id'])

    def test_restart_recovers_only_committed_acknowledgement(self):
        b = bundle(claim('a','Course'))
        p = self.draft(b,operation_id='compound/test/restart')
        self.i.keep({'proposal_id':p['proposal_id']})
        self.store.close();self.store=PersistentWorldStore.open(self.loc);self.addCleanup(self.store.close)
        self.m=FutureMemoryService(self.store);self.x=FutureExperience(self.m);self.i=IfInteraction(self.x)
        self.assertEqual(self.draft(b,operation_id='compound/test/restart')['stage'],'retained')
        self.assertEqual(self.m.read()[1],1)

    def test_version_change_invalidates_pending(self):
        b = bundle(claim('a','Course'))
        p = self.draft(b,operation_id='compound/test/stale')
        self.keep('Other',300)
        with self.assertRaisesRegex(FutureError,'head_changed'):
            self.draft(b,operation_id='compound/test/stale')
        with self.assertRaisesRegex(FutureError,'head_changed'):
            self.i.keep({'proposal_id':p['proposal_id']})

    def test_financial_change_invalidates_pending(self):
        p = self.draft(bundle(claim('a','Course')))
        self.store.execute(seed(self.store))
        with self.assertRaisesRegex(FutureError,'financial_basis_changed'):
            self.i.keep({'proposal_id':p['proposal_id']})

    def test_exact_target_correction_keeps_old_and_unrelated_records(self):
        self.keep('Course',400)
        old = next(r for r in self.x.listing()['records'] if r['ref']['kind']=='option')
        b = bundle(claim('a','Course','350',kind='correction',target='current-option'))
        p = self.draft(b,context_refs={'current-option':old['ref']})
        self.assertEqual(self.total(p),35000)
        self.i.keep({'proposal_id':p['proposal_id']})
        self.assertEqual(len([r for r in self.m.read()[0].records if type(r) is FutureOption]),2)

    def test_bare_target_id_is_not_authority(self):
        p = self.draft(bundle(claim('a','Course','350',kind='correction',target='invented')))
        self.assertEqual(p['stage'],'blocked');self.assertEqual(self.x.pending,{})

    def test_actual_and_transfer_do_not_become_future_effects(self):
        b = bundle(claim('a','Paid','200',kind='actual_expense'),claim('b','Move money','200',kind='transfer'),claim('c','Course'))
        p = self.draft(b)
        self.assertEqual(p['supported_claim_ids'],['c'])
        self.assertEqual({r['claim_id'] for r in p['unsupported']},{'a','b'})
        self.assertEqual(self.total(p),20000)

    def test_inflow_uses_external_outcome_not_cash_or_spending(self):
        p = self.draft(bundle(claim('a','Expected grant','900',kind='expected_inflow')))
        records=self.x.pending[p['proposal_id']].records
        self.assertEqual(next(r.control for r in records if type(r) is FutureVariable),'external_outcome')
        self.assertEqual(next(r.effects[0].effect_type for r in records if type(r) is FutureOption),'expected_inflow')
        self.assertEqual(self.store.snapshot.revision,0)


    def test_requested_subset_never_flattens_either_exclusive_endpoint(self):
        for kind in ('alternative', 'replacement'):
            for wanted in ('a', 'b'):
                with self.subTest(kind=kind, wanted=wanted):
                    b = bundle(claim('a','Train','80'),claim('b','Coach','30'),
                        claim('c','Materials','10'),relationships=[rel(kind,'a','b')])
                    p = self.draft(b,claim_ids=[wanted,'c'])
                    self.assertEqual(p['supported_claim_ids'],['c'])
                    self.assertIn(wanted,{r['claim_id'] for r in p['blocked']})
                    self.assertEqual(self.total(p),1000)

    def test_subset_keeps_structural_parent_blocked_when_component_is_omitted(self):
        b = bundle(claim('parent','Kit',None,precision='unknown'),
            claim('child','Materials','80'),claim('other','Course','40'),
            relationships=[rel('part_of','child','parent')])
        p = self.draft(b,claim_ids=['parent','other'])
        self.assertEqual(p['supported_claim_ids'],['other'])
        self.assertIn('parent',{r['claim_id'] for r in p['blocked']})
        self.assertEqual(self.total(p),4000)
        self.assertEqual([r.title for r in self.x.pending[p['proposal_id']].records if type(r) is FutureNode],['Course'])


if __name__ == '__main__':
    unittest.main()

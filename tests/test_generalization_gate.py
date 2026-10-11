"""Post A-G Generalization Gate v0.1 deterministic evaluation.

These cases extend gaps in A-G coverage. Canonical regressions remain in their
original test modules. No live provider is called by this suite.
"""

from dataclasses import replace
from datetime import date, timedelta
import ast
from pathlib import Path
import subprocess
import sys
import unittest

from finvis.agent_reconciliation import (AccountReview, FactReview, OccurrenceReview,
    PositionReview, ReviewRequest, SourceSelection, review_json)
from finvis.agent_schema import SCHEMA_VERSION
from finvis.domain import Currency, Ref
from finvis.store import Rejected
from finvis.world_commands import PutRecord, record_ref
from finvis.world_domain import EarmarkRole, amount_for_currency
from finvis.world_planning import (FutureInflowStatus, OccurrenceKind,
    PlannedExpenseStatus)
from finvis.world_settlement import BalanceBasis
from finvis.world_store import current_assets
from finvis.world_values import (AmountEstimate, FreshnessAssessment,
    FreshnessStatus, ObservationRole, TemporalRelation, TemporalSpec, TypedRef)
from tests.test_agent_reconciliation import (AT, finish, records, setup, source,
    synthetic)
from tests.test_agent_understanding import realistic_output
from tests.test_world_assets import OWNER, exact
from tests.test_world_mutation import prepared, proposal, store


ROOT = Path(__file__).resolve().parents[1]
TEMPORAL = {
    'current_asset': 'current', 'earmarked_current_asset': 'current',
    'expected_future_inflow': 'future', 'planned_expense': 'planned',
    'actual_expense': 'actual', 'actual_inflow': 'actual',
    'contemplated_decision': 'contemplated', 'context': 'context',
}


def amount(value, currency='SGD', precision='exact', lower=None, upper=None):
    return {'currency': currency, 'value': None if value is None else str(value),
            'range_min': None if lower is None else str(lower),
            'range_max': None if upper is None else str(upper), 'precision': precision}


def fact(identity, kind, label, value=None, currency='SGD', precision='exact',
         account=None, earmark=None, purpose=None, timing=None, lower=None, upper=None):
    return {'id': identity, 'type': kind, 'label': label, 'account': account,
            'amount': None if value is None and precision != 'unknown' and lower is None else
                      amount(value, currency, precision, lower, upper),
            'earmark': earmark, 'purpose': purpose,
            'temporal_status': TEMPORAL[kind], 'timing_note': timing,
            'source': 'user_report'}


def candidate(facts, currency='SGD', conflicts=(), uncertainties=(), question=None):
    return {'schema_version': SCHEMA_VERSION, 'language': 'en',
            'user_intents': ['update_financial_state'], 'default_currency': currency,
            'candidate_facts': list(facts), 'destructive_actions': [], 'assumptions': [],
            'uncertainties': list(uncertainties), 'conflicts': list(conflicts),
            'clarification_questions': [], 'unsupported_requests': [],
            'user_question': question, 'interpretation_notes': []}


def text_and_reviews(facts, **reviews):
    lines = ['{}: {}'.format(item['id'], item['label']) for item in facts]
    text = '\n'.join(lines)
    return text, tuple(FactReview(item['id'], source(text, line), **reviews.get(item['id'], {}))
                       for item, line in zip(facts, lines))


class StaticGeneralizationAuditTests(unittest.TestCase):
    def test_live_runner_is_invocable_from_repository_root(self):
        result = subprocess.run([sys.executable, '-B', 'tools/evaluate_generalization_gate_agent.py', '--help'],
                                cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('--output', result.stdout)

    def test_financial_world_and_bridge_have_no_canonical_label_branches(self):
        paths = [ROOT / 'finvis/agent_reconciliation.py',
                 *sorted((ROOT / 'finvis').glob('world_*.py'))]
        forbidden = ('youming', 'icbc', 'alipay', 'winning', 'singapore', 'phone')
        for path in paths:
            lowered = path.read_text().lower()
            for token in forbidden:
                self.assertNotIn(token, lowered, '{} leaked into {}'.format(token, path.name))

    def test_financial_world_and_bridge_have_no_canonical_amount_constants(self):
        paths = [ROOT / 'finvis/agent_reconciliation.py',
                 *sorted((ROOT / 'finvis').glob('world_*.py'))]
        canonical = {9000, 25000, 5000, 17600, 13000, 10000, 6000}
        found = set()
        for path in paths:
            tree = ast.parse(path.read_text())
            found |= {node.value for node in ast.walk(tree)
                      if isinstance(node, ast.Constant) and type(node.value) is int
                      and node.value in canonical}
        self.assertEqual(found, set())


class DiverseSyntheticWorldTests(unittest.TestCase):
    def test_01_salaried_worker_total_and_savings_allocation(self):
        raw, text, request = synthetic(bank='North payroll', category='Work laptop')
        _, bridge, target = setup(raw, text)
        review = bridge.review(request, AT)
        finish(bridge, review)
        assets = current_assets(target.snapshot)
        self.assertEqual(amount_for_currency(assets.current_assets_by_currency(), Currency.SGD), exact(4200, Currency.SGD))
        self.assertEqual(amount_for_currency(assets.earmarked_assets_by_currency(), Currency.SGD), exact(800, Currency.SGD))
        self.assertEqual(len(records(review, 'planned_expense')), 1)

    def test_02_freelancer_keeps_two_irregular_inflows_separate(self):
        facts = (fact('mural', 'expected_future_inflow', 'Mural milestone', 1850, precision='approximate', timing='after delivery'),
                 fact('editing', 'expected_future_inflow', 'Editing invoice', None, precision='range', lower=700, upper=1100, timing='within six weeks'),
                 fact('camera', 'contemplated_decision', 'Camera body', 2400, precision='approximate'))
        text, reviews = text_and_reviews(facts,
            mural={'timing': TemporalSpec.relative('delivery', TemporalRelation.AFTER, 'after delivery')})
        _, bridge, target = setup(candidate(facts), text)
        review = bridge.review(ReviewRequest(reviews, independent_merge=True), AT)
        finish(bridge, review)
        inflows = records(review, 'future_inflow')
        self.assertEqual(len(inflows), 2)
        self.assertEqual({item.expected_amount.precision.value for item in inflows}, {'approximate', 'range'})
        self.assertEqual(current_assets(target.snapshot).positions[0].balance, exact(700))

    def test_03_young_household_has_two_coexisting_decisions_and_contexts(self):
        facts = (fact('childcare', 'contemplated_decision', 'Childcare program', 3200),
                 fact('appliance', 'contemplated_decision', 'Appliance replacement', 1450),
                 fact('family', 'context', 'Two adults share household costs'),
                 fact('warranty', 'context', 'Appliance warranty expires soon'))
        text, reviews = text_and_reviews(facts,
            family={'decision_ids': ('childcare',)}, warranty={'decision_ids': ('appliance',)})
        _, bridge, target = setup(candidate(facts), text)
        review = bridge.review(ReviewRequest(reviews, independent_merge=True), AT)
        finish(bridge, review)
        decisions = records(review, 'world_decision')
        contexts = records(review, 'decision_context')
        self.assertEqual(len(decisions), 2)
        self.assertEqual(len(contexts), 2)
        self.assertEqual({item.source_state_ref for item in decisions},
                         {TypedRef('world_state', target.snapshot.world.state_ref)})
        self.assertEqual(len({item.decision_refs[0] for item in contexts}), 2)

    def test_04_international_world_separates_cny_and_sgd_without_fx(self):
        raw, text, request = synthetic(currency='SGD', bank='Harbor account')
        _, bridge, target = setup(raw, text)
        finish(bridge, bridge.review(request, AT))
        assets = current_assets(target.snapshot).current_assets_by_currency()
        self.assertEqual(tuple(item.currency for item in assets), (Currency.CNY, Currency.SGD))
        self.assertEqual(amount_for_currency(assets, Currency.CNY), exact(700))
        self.assertEqual(amount_for_currency(assets, Currency.SGD), exact(4200, Currency.SGD))

    def test_05_low_liquidity_stores_facts_without_affordability(self):
        facts = (fact('cash', 'current_asset', 'Daily account', 260, account='Daily account'),
                 fact('refund', 'expected_future_inflow', 'Deposit refund', None, precision='range', lower=500, upper=900),
                 fact('repair', 'planned_expense', 'Bicycle repair', 680))
        text, reviews = text_and_reviews(facts)
        text += '\nI own Daily account.'
        request = ReviewRequest(reviews,
            (AccountReview('daily', 'Daily account', OWNER, source(text, 'I own Daily account.', 'account')),),
            (PositionReview('daily-cash', 'daily', ('cash',)),), coverage_note='Daily account report')
        raw = candidate(facts, question='Can I afford the repair?')
        _, bridge, target = setup(raw, text)
        review = bridge.review(request, AT)
        finish(bridge, review)
        encoded = review_json(review).lower()
        self.assertNotIn('affordable', encoded)
        self.assertNotIn('runway', encoded)
        self.assertEqual(len(records(review, 'planned_expense')), 1)

    def test_06_high_liquidity_multiple_allocations_do_not_double_count(self):
        facts = (fact('total', 'current_asset', 'Reserve account', 84000, account='Reserve account'),
                 fact('medical', 'earmarked_current_asset', 'Medical reserve', 12000, account='Reserve account', earmark='Medical'),
                 fact('education', 'earmarked_current_asset', 'Education reserve', 18000, account='Reserve account', earmark='Education'),
                 fact('tuition', 'planned_expense', 'Tuition', 7500),
                 fact('roof', 'planned_expense', 'Roof repair', 2400))
        text, base_reviews = text_and_reviews(facts)
        text += '\nI own Reserve account.'
        reviews = tuple(replace(item,
            earmark_role=EarmarkRole.EMERGENCY if item.fact_id == 'medical' else EarmarkRole.GOAL if item.fact_id == 'education' else item.earmark_role,
            emergency_use_designated=item.fact_id == 'medical',
            funding_fact_id='education' if item.fact_id == 'tuition' else 'medical' if item.fact_id == 'roof' else None)
            for item in base_reviews)
        request = ReviewRequest(reviews,
            (AccountReview('reserve', 'Reserve account', OWNER, source(text, 'I own Reserve account.', 'account')),),
            (PositionReview('reserve-cash', 'reserve', ('total',), ('medical', 'education')),),
            coverage_note='Reserve account total and allocations')
        _, bridge, target = setup(candidate(facts), text)
        review = bridge.review(request, AT)
        finish(bridge, review)
        assets = current_assets(target.snapshot)
        self.assertEqual(amount_for_currency(assets.current_assets_by_currency(), Currency.SGD), exact(84000, Currency.SGD))
        self.assertEqual(amount_for_currency(assets.earmarked_assets_by_currency(), Currency.SGD), exact(30000, Currency.SGD))
        self.assertEqual(len(records(review, 'planned_expense')), 2)

    def test_07_incomplete_data_blocks_bundle_then_allows_explicit_independent_future(self):
        facts = (fact('cash', 'current_asset', 'Unidentified cash', 900, account=None),
                 fact('refund', 'expected_future_inflow', 'Relocation refund', None, precision='range', lower=2000, upper=3000))
        text, reviews = text_and_reviews(facts)
        _, bridge, target = setup(candidate(facts), text)
        blocked = bridge.review(ReviewRequest(reviews, independent_merge=True), AT)
        self.assertIsNone(blocked.change_set)
        self.assertEqual(target.snapshot.revision, 0)
        future_only = bridge.review(ReviewRequest((reviews[1],), independent_merge=True), AT)
        finish(bridge, future_only)
        self.assertEqual(records(future_only, 'future_inflow')[0].expected_amount.precision.value, 'range')

    def test_08_conflicting_candidate_fails_closed(self):
        facts = (fact('first', 'current_asset', 'Harbor account', 1100, account='Harbor account'),
                 fact('second', 'current_asset', 'Harbor account', 1450, account='Harbor account'))
        text, reviews = text_and_reviews(facts)
        raw = candidate(facts, conflicts=('Same account and as-of has two exact balances',))
        _, bridge, target = setup(raw, text)
        review = bridge.review(ReviewRequest(reviews, independent_merge=True), AT)
        self.assertIsNone(review.change_set)
        self.assertIn('candidate_conflict', {item.code for item in review.issues})
        self.assertEqual(target.snapshot.revision, 0)

    def _actual(self, kind, amount_value):
        event = 'Reviewed standalone occurrence'
        coverage = 'The current balance is a prior baseline'
        text = event + '. ' + coverage + '.'
        raw = candidate(())
        _, bridge, target = setup(raw, text)
        position = current_assets(target.snapshot).positions[0]
        request = OccurrenceReview(kind, exact(amount_value), TemporalSpec.known_date(date(2026, 9, 22)),
            source(text, event), record_ref(position), BalanceBasis.PRIOR,
            source(text, coverage, 'coverage'), coverage,
            freshness=FreshnessAssessment(FreshnessStatus.CURRENT, 'Reviewed for this occurrence', AT))
        finish(bridge, bridge.review_occurrence(request, AT))
        return target

    def test_09_actual_expense_without_plan_uses_slice_e(self):
        target = self._actual(OccurrenceKind.EXPENSE, 175)
        self.assertEqual(current_assets(target.snapshot).positions[0].balance, exact(525))
        self.assertFalse(any(ref.record_kind == 'planned_expense' for ref in target.snapshot.world.members))

    def test_10_actual_inflow_without_expectation_uses_slice_e(self):
        target = self._actual(OccurrenceKind.INFLOW, 860)
        self.assertEqual(current_assets(target.snapshot).positions[0].balance, exact(1560))
        self.assertFalse(any(ref.record_kind == 'future_inflow' for ref in target.snapshot.world.members))

    def test_11_cancelled_future_event_does_not_change_current_assets(self):
        facts = (fact('grant', 'expected_future_inflow', 'Community grant', 1250, precision='approximate'),)
        text, reviews = text_and_reviews(facts)
        _, bridge, target = setup(candidate(facts), text)
        finish(bridge, bridge.review(ReviewRequest(reviews, independent_merge=True), AT))
        before = current_assets(target.snapshot).current_assets_by_currency()
        old_ref = next(ref for ref in target.snapshot.world.members if ref.record_kind == 'future_inflow')
        old = target.snapshot.get(old_ref)
        cancelled = replace(old, ref=Ref(old.ref.id, 2), status=FutureInflowStatus.CANCELLED,
            observation=replace(old.observation, corrects_ref=old.ref,
                                correction_reason='Reviewed cancellation', recorded_at=AT + timedelta(minutes=1)))
        change = proposal(target, (PutRecord('cancel-grant', cancelled, old_ref),),
                          name='cancel-grant', at=AT + timedelta(minutes=1))
        _, _, command = prepared(target, change, 'cancel-grant')
        target.execute(command)
        self.assertEqual(current_assets(target.snapshot).current_assets_by_currency(), before)
        self.assertIs(target.snapshot.get(record_ref(cancelled)).status, FutureInflowStatus.CANCELLED)

    def test_12_multiple_decisions_share_one_current_state(self):
        facts = (fact('move', 'contemplated_decision', 'Move closer to work', 5200),
                 fact('study', 'contemplated_decision', 'Evening certification', 1800))
        text, reviews = text_and_reviews(facts)
        _, bridge, target = setup(candidate(facts), text)
        review = bridge.review(ReviewRequest(reviews, independent_merge=True), AT)
        finish(bridge, review)
        decisions = records(review, 'world_decision')
        self.assertEqual(len(decisions), 2)
        self.assertEqual({item.source_state_ref for item in decisions},
                         {TypedRef('world_state', target.snapshot.world.state_ref)})


class MetamorphicAndAdversarialGateTests(unittest.TestCase):
    def test_label_permutation_and_equivalent_relationships(self):
        outcomes = []
        for bank, category in (('North vault', 'Studio repair'), ('River jar', 'Kitchen repair')):
            raw, text, request = synthetic(bank=bank, category=category)
            _, bridge, target = setup(raw, text)
            review = bridge.review(request, AT)
            finish(bridge, review)
            outcomes.append((tuple(record_ref(op.record).record_kind for op in review.change_set.operations if type(op) is PutRecord),
                             current_assets(target.snapshot).current_assets_by_currency()))
        self.assertEqual(outcomes[0], outcomes[1])

    def test_amount_scaling_preserves_settlement_structure(self):
        snapshots = [self._settled_expense(value) for value in (75, 150)]
        balances = [current_assets(item).positions[0].balance.amount.minor_units for item in snapshots]
        self.assertEqual(balances, [62500, 55000])
        self.assertEqual([sum(ref.record_kind == 'balance_effect' for ref in item.world.members) for item in snapshots], [1, 1])

    def _settled_expense(self, value):
        event, coverage = 'Standalone repair expense', 'Balance predates expense'
        text = event + '. ' + coverage + '.'
        _, bridge, target = setup(candidate(()), text)
        pos = current_assets(target.snapshot).positions[0]
        request = OccurrenceReview(OccurrenceKind.EXPENSE, exact(value), TemporalSpec.known_date(date(2026, 9, 22)),
            source(text, event), record_ref(pos), BalanceBasis.PRIOR, source(text, coverage, 'coverage'), coverage,
            freshness=FreshnessAssessment(FreshnessStatus.CURRENT, 'Reviewed actual', AT))
        finish(bridge, bridge.review_occurrence(request, AT))
        return target.snapshot

    def test_irrelevant_unlinked_context_creates_no_financial_record(self):
        facts = (fact('cash', 'current_asset', 'North cash', 900, account='North cash'),
                 fact('hobby', 'context', 'Enjoys landscape photography'))
        text, reviews = text_and_reviews(facts)
        text += '\nI own North cash.'
        request = ReviewRequest(reviews,
            (AccountReview('north', 'North cash', OWNER, source(text, 'I own North cash.', 'account')),),
            (PositionReview('north-pool', 'north', ('cash',)),), coverage_note='North cash report')
        _, bridge, target = setup(candidate(facts), text)
        blocked = bridge.review(request, AT)
        outcome = next(item for item in blocked.outcomes if item.fact_id == 'hobby')
        self.assertEqual(outcome.disposition, 'context_only')
        self.assertIsNone(blocked.change_set)
        self.assertEqual(target.snapshot.revision, 0)

    def test_cross_currency_funding_relation_fails_at_world_validation(self):
        facts = (fact('cash', 'current_asset', 'SGD wallet', 1500, account='SGD wallet'),
                 fact('course', 'planned_expense', 'CNY course', 800, currency='CNY'))
        text, base = text_and_reviews(facts)
        text += '\nI own SGD wallet.'
        reviews = (base[0], replace(base[1], funding_fact_id='cash'))
        request = ReviewRequest(reviews,
            (AccountReview('wallet', 'SGD wallet', OWNER, source(text, 'I own SGD wallet.', 'account')),),
            (PositionReview('sgd', 'wallet', ('cash',)),), coverage_note='SGD wallet report')
        _, bridge, target = setup(candidate(facts, currency=None), text)
        review = bridge.review(request, AT)
        with self.assertRaises(Rejected):
            bridge.preview(OWNER, review)
        self.assertEqual(target.snapshot.revision, 0)

    def test_duplicate_obligation_conflict_and_missing_material_field_fail_closed(self):
        duplicate = (fact('rent-a', 'planned_expense', 'September rent', 1300),
                     fact('rent-b', 'planned_expense', 'September rent duplicate', 1300))
        text, reviews = text_and_reviews(duplicate)
        _, bridge, target = setup(candidate(duplicate, conflicts=('Same September rent appears twice',)), text)
        review = bridge.review(ReviewRequest(reviews, independent_merge=True), AT)
        self.assertIsNone(review.change_set)
        self.assertEqual(target.snapshot.revision, 0)

        missing = (fact('cash', 'current_asset', 'Unknown owner cash', 500, account='Unknown owner cash'),)
        text, reviews = text_and_reviews(missing)
        _, bridge, target = setup(candidate(missing), text)
        review = bridge.review(ReviewRequest(reviews, independent_merge=True), AT)
        self.assertIsNone(review.change_set)
        self.assertIn('fact/cash', {item.code for item in review.issues})

    def test_planned_expense_never_becomes_actual_occurrence(self):
        facts = (fact('repair', 'planned_expense', 'Future boiler service', 420),)
        text, reviews = text_and_reviews(facts)
        _, bridge, target = setup(candidate(facts), text)
        review = bridge.review(ReviewRequest(reviews, independent_merge=True), AT)
        finish(bridge, review)
        expense = records(review, 'planned_expense')[0]
        self.assertIs(expense.status, PlannedExpenseStatus.PLANNED)
        self.assertFalse(any(ref.record_kind in ('actual_occurrence', 'balance_effect')
                             for ref in target.snapshot.world.members))


if __name__ == '__main__':
    unittest.main()

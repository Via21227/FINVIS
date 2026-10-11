"""S2 verified User History, independent of current authority and Demo."""
from dataclasses import replace
from datetime import timedelta
import unittest
from unittest.mock import patch

from finvis.application import DemoApplication
from finvis.domain import Currency, Ref
from finvis.store import Rejected
from finvis.world_application import UserWorldAuthority
from finvis.world_commands import CandidateChangeSet, ChangeMode, FinancialWorldCommand, PutRecord, record_ref
from finvis.world_history import history_view
from finvis.world_projection import ProjectionError
from finvis.world_values import ObservationRole
from finvis.world_serialization import dumps_world_mutation
from tests.test_agent_understanding import SequenceProvider
from tests.test_world_application import authority, synthetic
from tests.test_world_assets import NOW, money
from tests.test_world_mutation import store, replacement, future_add, prepared, proposal
from tests.test_world_planning import canonical_bundle, observation
from tests.test_world_settlement import operation, settle, correction
from tests.test_world_replay import generic_proposal, cutover, add_post_migration_future
from tests.test_i4_commit import authorized
from tests.test_i4_http import single_asset, review as single_asset_review, TEXT as SINGLE_TEXT
from finvis.i4_commit import I4Workflow


def commit(target, record, name):
    """A distinct workshop fixture with the existing D grant/execution gates."""
    before = target.snapshot
    old = next((ref for ref in before.world.members if ref.record_kind == record_ref(record).record_kind
                and ref.ref.id == record.ref.id), None)
    retained = tuple(ref for ref in before.world.members if ref != old)
    at = NOW + timedelta(minutes=before.revision + 2)
    asset = record_ref(record).record_kind in ('account', 'asset_position', 'earmark')
    change = CandidateChangeSet(Ref('s2/change/' + name, 1), before.world.owner_id,
        before.world.world_id, target.session_generation, before.world.ref,
        before.world.state_ref, before.world.plan_ref, before.revision,
        ChangeMode.MERGE, (PutRecord('s2/put/' + name, record, old),), None, retained,
        (Ref('s2/source/' + name, 1),),
        replace(observation('s2/' + name, ObservationRole.CURRENT), recorded_at=at),
        'Reviewed workshop subset' if asset else None, (), at)
    preview = target.preview(before.world.owner_id, change, Ref('s2/preview/' + name, 1))
    grant = target.authorize(before.world.owner_id, preview, Ref('s2/grant/' + name, 1), at)
    target.execute(FinancialWorldCommand('s2/command/' + name, before.world.owner_id,
        change, preview.ref, preview.digest, grant.ref, at))


def revise(target, kind, field, value, name):
    old = next(target.snapshot.get(ref) for ref in target.snapshot.world.members
               if ref.record_kind == kind)
    updated = replace(old, ref=Ref(old.ref.id, old.ref.version + 1),
        observation=replace(old.observation, corrects_ref=old.ref,
                            correction_reason='Reviewed revised report'), **{field: value})
    commit(target, updated, name)


class S2HistoryTests(unittest.TestCase):
    def test_initial_user_creation_history_does_not_advance_candidate_or_grant(self):
        workflow = I4Workflow.bootstrap(SequenceProvider([single_asset()]))
        initial = workflow.history()
        self.assertEqual(initial['entries'], [])
        args = authorized(workflow, workflow.store, SINGLE_TEXT, single_asset_review())
        stage = workflow.view()['stage']
        authorization_count = len(workflow.store.snapshot.authorizations)
        self.assertEqual(workflow.history()['entries'], [])
        self.assertEqual(workflow.view()['stage'], stage)
        self.assertEqual(len(workflow.store.snapshot.authorizations), authorization_count)
        workflow.apply(*args)
        snapshot = workflow.store.snapshot
        history = workflow.history(1)
        self.assertEqual([entry['revision'] for entry in history['entries']], [1, 0])
        self.assertEqual(history['detail']['projection'], workflow.home()['projection'])
        self.assertTrue(any(row['kind'] == 'asset_position' and row['before'] is None
                            and row['after']['amount']['minor_units'] == 72000
                            for row in history['detail']['rows']))
        self.assertIs(workflow.store.snapshot, snapshot)

    def test_empty_and_mixed_currency_baseline_have_no_fabricated_receipt(self):
        target = synthetic((Currency.CNY, Currency.SGD))
        before = target.snapshot
        history = history_view(authority(target).read())
        self.assertEqual(history['entries'], [])
        self.assertEqual(history['current_revision'], 0)
        self.assertIs(target.snapshot, before)
        self.assertEqual({row['currency'] for row in history_view(authority(target).read(), 0)
                          ['detail']['projection']['currencies']}, {'CNY', 'SGD'})

    def test_generic_sequential_corrections_and_prefix_excludes_future(self):
        target = synthetic((Currency.CNY, Currency.SGD))
        demo = DemoApplication(agent_provider=SequenceProvider([]))
        demo_before = demo.view()
        original = target.snapshot
        old_mark = next(target.snapshot.get(ref) for ref in target.snapshot.world.members
                        if ref.record_kind == 'earmark')
        revise(target, 'earmark', 'allocation', replace(old_mark.allocation,
               amount=money(80, Currency.CNY)), 'reserve')
        first = target.snapshot
        old_future = next(target.snapshot.get(ref) for ref in target.snapshot.world.members
                          if ref.record_kind == 'future_inflow')
        revise(target, 'future_inflow', 'expected_amount', replace(old_future.expected_amount,
               amount=money(150, Currency.CNY)), 'future')
        old_expense = next(target.snapshot.get(ref) for ref in target.snapshot.world.members
                           if ref.record_kind == 'planned_expense')
        revise(target, 'planned_expense', 'expected_amount', replace(old_expense.expected_amount,
               amount=money(40, Currency.CNY)), 'expense')
        committed = target.snapshot
        history = history_view(authority(target).read())
        self.assertEqual([e['revision'] for e in history['entries']], [3, 2, 1, 0])
        self.assertEqual([e['recorded_at'] for e in history['entries'][-1:]], [None])
        first_view = history_view(authority(target).read(), 1)['detail']
        self.assertFalse(first_view['is_current'])
        self.assertEqual(first_view['projection']['revision'], 1)
        self.assertEqual(first_view['rows'][0]['kind'], 'earmark')
        self.assertEqual(first_view['rows'][0]['before']['amount']['minor_units'], 10000)
        self.assertEqual(first_view['rows'][0]['after']['amount']['minor_units'], 8000)
        self.assertNotIn(record_ref(next(row for row in committed.records if row.ref.id == old_future.ref.id
                                         and row.ref.version == 2)),
                         first.world.members)
        self.assertEqual(history_view(authority(target).read(), 2)['detail']['rows'][0]['kind'], 'future_inflow')
        self.assertEqual(history_view(authority(target).read(), 3)['detail']['rows'][0]['kind'], 'planned_expense')
        self.assertIs(target.snapshot, committed)
        self.assertEqual(original.revision, 0)
        self.assertEqual(demo.view(), demo_before)

    def test_replacement_and_actual_settlement_lineage(self):
        replacement_target = store()
        replacement_target.execute(prepared(replacement_target, replacement(replacement_target, name='s2-replace'), 's2-replace')[2])
        replaced = history_view(authority(replacement_target).read(), 1)['detail']
        self.assertTrue(replaced['destructive'])
        self.assertTrue(any(row['retired'] for row in replaced['rows']))
        self.assertTrue(any(not row['retired'] and row['before_ref'] is None for row in replaced['rows']))
        target = store(canonical_bundle())
        target.execute(settle(target, operation(target), 's2-actual')[2])
        target.execute(settle(target, correction(target), 's2-correct')[2])
        detail = history_view(authority(target).read(), 2)['detail']
        self.assertTrue(any(row['kind'] == 'actual_occurrence' and row['corrected']
                            for row in detail['rows']))
        self.assertTrue(any(row['kind'] == 'balance_effect' and row['effect_direction'] != 0
                            for row in detail['rows']))
        self.assertEqual([entry['revision'] for entry in history_view(authority(target).read())['entries']],
                         [2, 1, 0])

    def test_tampered_history_fails_closed_without_mutation(self):
        target = store()
        target.execute(prepared(target, future_add(target, 's2-gift'), 's2-gift')[2])
        before = target.snapshot
        with patch('finvis.world_history.replay_world', side_effect=Rejected('tampered')):
            with self.assertRaises(ProjectionError):
                history_view(authority(target).read(), 0)
        self.assertIs(target.snapshot, before)
        self.assertEqual(dumps_world_mutation(target.snapshot), dumps_world_mutation(before))
        with self.assertRaises(ValueError):
            history_view(authority(target).read(), 9)

    def test_migrated_legacy_anchor_is_baseline_not_a_fabricated_receipt(self):
        proposal = generic_proposal()
        _, target, _ = cutover(proposal)
        self.assertEqual(history_view(authority(target).read())['entries'], [])
        add_post_migration_future(target, proposal)
        history = history_view(authority(target).read(), 0)
        self.assertEqual([entry['revision'] for entry in history['entries']], [1, 0])
        self.assertEqual(history['detail']['projection']['revision'], 0)
        self.assertEqual(history['detail']['root_ref']['version'], 1)
        self.assertTrue(history_view(authority(target).read(), 1)['detail']['is_current'])


if __name__ == '__main__':
    unittest.main()

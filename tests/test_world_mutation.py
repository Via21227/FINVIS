from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError, replace
from datetime import timedelta, timezone
import json
from pathlib import Path
import unittest

from finvis.domain import Currency, DecisionStatus, PlanStatus, Ref
from finvis.store import Rejected, StaleVersion
from finvis.world_commands import (
    CandidateChangeSet, ChangeMode, FinancialWorldCommand, IssueDisposition,
    PutRecord, ReconciliationIssue, ReplacementScope, RetireRecord, record_ref,
)
from finvis.world_domain import WorldAssetBundle, WorldState, amount_for_currency
from finvis.world_planning import (
    PlanEvaluationStatus, PlannedExpenseStatus, FutureInflowStatus,
    SettlementStatus, WorldPlanningBundle,
)
from finvis.world_values import AmountEstimate, ObservationRole, TypedRef
from finvis.world_serialization import (
    dumps_world_mutation, loads_world_mutation, serialize_world_mutation,
    deserialize_world_mutation, payload_digest,
)
from finvis.world_store import WorldStore, initial_world, current_assets
from tests.test_world_assets import account, position, exact, money, bundle_with_allocations, NOW, OWNER, WORLD
from tests.test_world_planning import canonical_bundle, completed_phone_bundle, observation


def baseline():
    location = account('old-wallet', 'Old wallet')
    cash = position('old-cny', location, exact(700))
    state = WorldState(Ref('world-state', 1), OWNER, WORLD,
        (record_ref(location),), (record_ref(cash),), (),
        observation('old-state', ObservationRole.CURRENT), 'Old reported subset')
    plan = replace(canonical_bundle().plan, supporting_decision_refs=(), planned_expense_refs=(), assumption_refs=())
    return WorldPlanningBundle(WorldAssetBundle(state, (location,), (cash,), ()), plan, (), (), (), (), ())


def store(bundle=None, hook=None, session='session-one'):
    return WorldStore(initial_world(bundle or baseline(), Ref('financial-world', 1)), session, hook)


def proposal(target, ops, name='change', scope=None, issues=(), at=None):
    snapshot = target.snapshot
    touched = {(record_ref(op.record).record_kind, op.record.ref.id) if type(op) is PutRecord
               else (op.expected_ref.record_kind, op.expected_ref.ref.id) for op in ops}
    retained = tuple(ref for ref in snapshot.world.members if (ref.record_kind, ref.ref.id) not in touched)
    assets_changed = any((record_ref(op.record) if type(op) is PutRecord else op.expected_ref).record_kind
                         in ('account', 'asset_position', 'earmark') for op in ops)
    at = at or NOW + timedelta(minutes=snapshot.revision + 1)
    return CandidateChangeSet(Ref(name, 1), OWNER, WORLD, target.session_generation,
        snapshot.world.ref, snapshot.world.state_ref, snapshot.world.plan_ref, snapshot.revision,
        ChangeMode.REPLACE_STATE if scope else ChangeMode.MERGE, tuple(ops), scope, retained,
        (Ref('candidate/' + name, 1),), replace(observation(name, ObservationRole.CURRENT), recorded_at=at),
        'Reviewed current asset subset' if assets_changed else None, issues, at)


def replacement(target, emergency=5000, phones=20000, name='replace'):
    old = current_assets(target.snapshot)
    scope = ReplacementScope(old.state.account_refs, (Currency.CNY,), old.state.position_refs, old.state.earmark_refs)
    # Invalid conservation must reach the staged boundary, not a prevalidated bundle.
    canonical = bundle_with_allocations()
    marks = tuple(replace(mark, allocation=exact(emergency if 'emergency' in mark.ref.id else phones)) for mark in canonical.earmarks)
    ops = [RetireRecord('retire/' + ref.ref.id, ref) for ref in (*scope.earmark_refs, *scope.position_refs, *scope.account_refs)]
    ops += [PutRecord('create/' + item.ref.id, item) for item in (*canonical.accounts, *canonical.positions, *marks)]
    return proposal(target, ops, name, scope)


def future_add(target, name='gift'):
    source = canonical_bundle().future_inflows[0]
    value = replace(source, ref=Ref('inflow/' + name, 1), observation=observation(name, ObservationRole.FUTURE))
    return proposal(target, (PutRecord('add', value),), name)


def prepared(target, changes, name='one'):
    preview = target.preview(OWNER, changes, Ref('preview/' + name, 1))
    grant = target.authorize(OWNER, preview, Ref('authorization/' + name, 1), changes.created_at)
    command = FinancialWorldCommand('command/' + name, OWNER, changes, preview.ref,
                                    preview.digest, grant.ref, changes.created_at)
    return preview, grant, command


class WorldMutationTests(unittest.TestCase):
    def reject_unchanged(self, target, action, error=Rejected):
        before = target.snapshot
        encoded = dumps_world_mutation(before)
        with self.assertRaises(error):
            action()
        self.assertIs(target.snapshot, before)
        self.assertEqual(dumps_world_mutation(target.snapshot), encoded)

    def test_proposal_and_preview_do_not_change_authoritative_snapshot(self):
        target = store()
        before = target.snapshot
        changes = replacement(target)
        preview = target.preview(OWNER, changes, Ref('preview', 1))
        self.assertIs(target.snapshot, before)
        self.assertTrue(preview.destructive)
        self.assertEqual(preview.before_world.ref.version, 1)
        self.assertEqual(preview.after_world.ref.version, 2)
        self.assertEqual(len([item for item in preview.changes if item.leaves_current_scope]), 2)
        self.assertIn('audit_history', preview.preserved_categories)

    def test_canonical_authorized_replacement_is_one_publication(self):
        target = store()
        before = target.snapshot
        preview, grant, command = prepared(target, replacement(target))
        self.assertIs(target.snapshot, before)
        receipt = target.execute(command)
        snapshot = target.snapshot
        self.assertEqual(snapshot.world.ref, Ref('financial-world', 2))
        self.assertEqual(snapshot.revision, 1)
        assets = current_assets(snapshot)
        for method, expected in ((assets.current_assets_by_currency, 34000),
                                 (assets.earmarked_assets_by_currency, 25000),
                                 (assets.unallocated_assets_by_currency, 9000)):
            self.assertEqual(amount_for_currency(method(), Currency.CNY).amount, money(expected))
        self.assertEqual(snapshot.records[:len(before.records)], before.records)
        self.assertEqual(receipt.authorization_ref, grant.ref)
        self.assertEqual(receipt.preview_digest, preview.digest)
        self.assertEqual(receipt.command_digest, payload_digest(command))
        self.assertEqual(snapshot.authorizations, (grant,))
        self.assertEqual(snapshot.receipts, (receipt,))

    def test_missing_authorization_rejected(self):
        target = store()
        changes = replacement(target)
        preview = target.preview(OWNER, changes, Ref('preview', 1))
        command = FinancialWorldCommand('command', OWNER, changes, preview.ref, preview.digest, Ref('missing', 1), changes.created_at)
        self.reject_unchanged(target, lambda: target.execute(command))

    def test_forged_deserialized_authorization_is_not_registered(self):
        first, second = store(), store()
        preview, grant, command = prepared(first, replacement(first))
        grant = loads_world_mutation(dumps_world_mutation(grant))
        second.preview(OWNER, command.change_set, preview.ref)
        self.reject_unchanged(second, lambda: second.execute(command))
        self.assertEqual(grant.preview_digest, preview.digest)

    def test_wrong_authorization_for_other_change_rejected(self):
        target = store()
        _, _, first = prepared(target, replacement(target), 'replace')
        _, _, second = prepared(target, future_add(target), 'future')
        self.reject_unchanged(target, lambda: target.execute(replace(first, authorization_ref=second.authorization_ref)))

    def test_non_destructive_grant_cannot_authorize_replacement(self):
        target = store()
        light, _, first = prepared(target, future_add(target), 'future')
        destructive, _, second = prepared(target, replacement(target), 'replace')
        self.assertFalse(light.destructive)
        self.assertTrue(destructive.destructive)
        self.reject_unchanged(target, lambda: target.execute(replace(second, authorization_ref=first.authorization_ref)))

    def test_tampered_amount_25000_to_30000_rejected(self):
        target = store()
        _, _, command = prepared(target, replacement(target))
        ops = tuple(replace(op, record=replace(op.record, balance=exact(30000)))
                    if type(op) is PutRecord and op.record.ref.id == 'position/alipay-cny' else op
                    for op in command.change_set.operations)
        tampered = replace(command, change_set=replace(command.change_set, operations=ops))
        self.reject_unchanged(target, lambda: target.execute(tampered))

    def test_tampered_preview_content_rejected_at_authorization(self):
        target = store()
        preview = target.preview(OWNER, replacement(target), Ref('preview', 1))
        self.reject_unchanged(target, lambda: target.authorize(OWNER, replace(preview, destructive=False), Ref('auth', 1), NOW))

    def test_stale_preview_after_legitimate_mutation(self):
        target = store()
        _, _, first = prepared(target, replacement(target), 'old')
        _, _, second = prepared(target, future_add(target), 'new')
        target.execute(second)
        self.reject_unchanged(target, lambda: target.execute(first), StaleVersion)

    def test_cancel_before_grant_preserves_world_and_prevents_authorization(self):
        target = store()
        preview = target.preview(OWNER, replacement(target), Ref('preview', 1))
        before = target.snapshot
        target.cancel(OWNER, preview.ref)
        self.assertIs(target.snapshot, before)
        self.reject_unchanged(target, lambda: target.authorize(OWNER, preview, Ref('auth', 1), NOW))

    def test_cancel_after_grant_prevents_execution(self):
        target = store()
        preview, _, command = prepared(target, replacement(target))
        target.cancel(OWNER, preview.ref)
        self.reject_unchanged(target, lambda: target.execute(command))

    def test_cancel_after_success_cannot_undo(self):
        target = store()
        preview, _, command = prepared(target, replacement(target))
        target.execute(command)
        self.reject_unchanged(target, lambda: target.cancel(OWNER, preview.ref))

    def test_duplicate_execution_returns_same_receipt_without_revision(self):
        target = store()
        _, _, command = prepared(target, replacement(target))
        receipt = target.execute(command)
        before = target.snapshot
        self.assertEqual(target.execute(command), receipt)
        self.assertIs(target.snapshot, before)

    def test_retry_after_later_world_version_returns_original_receipt(self):
        target = store()
        _, _, first = prepared(target, replacement(target), 'first')
        receipt = target.execute(first)
        _, _, second = prepared(target, future_add(target), 'second')
        target.execute(second)
        before = target.snapshot
        self.assertEqual(target.execute(first), receipt)
        self.assertIs(target.snapshot, before)

    def test_reused_command_id_with_changed_payload_rejected(self):
        target = store()
        _, _, command = prepared(target, replacement(target))
        target.execute(command)
        self.reject_unchanged(target, lambda: target.execute(replace(command, recorded_at=command.recorded_at + timedelta(seconds=1))))

    def test_consumed_authorization_cannot_execute_new_command_id(self):
        target = store()
        _, _, command = prepared(target, replacement(target))
        target.execute(command)
        self.reject_unchanged(target, lambda: target.execute(replace(command, command_id='second-use')))

    def test_invalid_6000_20000_replacement_rejects_whole_preview(self):
        target = store()
        change = replacement(target, emergency=6000)
        self.reject_unchanged(target, lambda: target.preview(OWNER, change, Ref('bad', 1)))
        self.assertEqual(len(target.snapshot.receipts), 0)
        self.assertEqual(amount_for_currency(current_assets(target.snapshot).current_assets_by_currency(), Currency.CNY).amount, money(700))

    def test_late_invalid_reference_aborts_valid_early_operations(self):
        target = store()
        change = replacement(target)
        ops = list(change.operations)
        last = ops[-1]
        ops[-1] = replace(last, record=replace(last.record, position_ref=TypedRef('asset_position', Ref('missing', 1))))
        self.reject_unchanged(target, lambda: target.preview(OWNER, replace(change, operations=tuple(ops)), Ref('bad', 1)))

    def test_injected_failure_preserves_history_receipt_and_unconsumed_grant(self):
        armed = [True]
        observed = []
        def hook(candidate):
            observed.append(candidate)
            if armed[0]:
                raise RuntimeError('Injected before publication')
        target = store(hook=hook)
        _, grant, command = prepared(target, replacement(target))
        self.reject_unchanged(target, lambda: target.execute(command), RuntimeError)
        self.assertEqual(target.snapshot.authorizations, ())
        self.assertEqual(len(observed[0].receipts), 1)
        armed[0] = False
        target.execute(command)
        self.assertEqual(target.snapshot.authorizations, (grant,))

    def test_two_competing_commands_have_one_commit(self):
        target = store()
        _, _, first = prepared(target, replacement(target), 'first')
        _, _, second = prepared(target, future_add(target), 'second')
        def execute(command):
            try:
                target.execute(command)
                return 'committed'
            except StaleVersion:
                return 'stale'
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(execute, (first, second)))
        self.assertCountEqual(results, ('committed', 'stale'))
        self.assertEqual(len(target.snapshot.receipts), 1)

    def test_concurrent_identical_retry_publishes_once(self):
        target = store()
        _, _, command = prepared(target, replacement(target))
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(target.execute, (command, command)))
        self.assertEqual(results[0], results[1])
        self.assertEqual(target.snapshot.revision, 1)

    def test_wrong_actor_owner_world_and_session_fail_closed(self):
        target = store()
        _, _, command = prepared(target, replacement(target))
        for field, value in (('owner_id', 'other'), ('world_id', 'other'), ('session_generation', 'other')):
            self.reject_unchanged(target, lambda field=field, value=value: target.execute(
                replace(command, change_set=replace(command.change_set, **{field: value}))))
        self.reject_unchanged(target, lambda: target.execute(replace(command, actor='other')))

    def test_old_session_command_cannot_enter_new_store(self):
        first = store()
        _, _, command = prepared(first, replacement(first))
        second = store(session='session-two')
        self.reject_unchanged(second, lambda: second.execute(command), StaleVersion)

    def test_incorrect_expected_world_state_plan_revision_fail(self):
        target = store()
        original = future_add(target)
        for field in ('expected_world_ref', 'expected_state_ref', 'expected_plan_ref'):
            bad = replace(original, **{field: Ref(getattr(original, field).id, 2)})
            self.reject_unchanged(target, lambda bad=bad: target.preview(OWNER, bad, Ref('bad', 1)), StaleVersion)
        self.reject_unchanged(target, lambda: target.preview(OWNER, replace(original, expected_revision=1), Ref('bad', 1)), StaleVersion)

    def test_replacement_requires_scope_and_exact_enumeration(self):
        target = store()
        change = replacement(target)
        with self.assertRaises(ValueError):
            replace(change, replacement_scope=None)
        bad = replace(change, replacement_scope=replace(change.replacement_scope, position_refs=()))
        self.reject_unchanged(target, lambda: target.preview(OWNER, bad, Ref('bad', 1)))

    def test_scope_cannot_silently_omit_old_position_operation(self):
        target = store()
        change = replacement(target)
        ops = tuple(op for op in change.operations if op.operation_id != 'retire/position/old-cny')
        bad = proposal(target, ops, 'bad', change.replacement_scope)
        self.reject_unchanged(target, lambda: target.preview(OWNER, bad, Ref('bad', 1)))

    def test_non_destructive_future_add_preserves_assets_and_entity_versions(self):
        target = store()
        before = target.snapshot
        preview, _, command = prepared(target, future_add(target))
        target.execute(command)
        self.assertFalse(preview.destructive)
        self.assertEqual(current_assets(target.snapshot), current_assets(before))
        self.assertEqual(target.snapshot.world.state_ref, before.world.state_ref)
        self.assertEqual(target.snapshot.world.plan_ref.version, 2)

    def test_update_bumps_only_changed_entity_and_preserves_lineage(self):
        target = store()
        original = current_assets(target.snapshot).positions[0]
        new = replace(original, ref=Ref(original.ref.id, 2), balance=exact(800),
                      observation=observation('corrected-balance', ObservationRole.CURRENT, original.ref))
        change = proposal(target, (PutRecord('balance', new, record_ref(original)),))
        _, _, command = prepared(target, change)
        target.execute(command)
        assets = current_assets(target.snapshot)
        self.assertEqual(assets.accounts[0].ref.version, 1)
        self.assertEqual(assets.positions[0].ref.version, 2)
        self.assertEqual(assets.positions[0].observation.corrects_ref, original.ref)
        self.assertEqual(target.snapshot.get(record_ref(original)), original)

    def test_wrong_entity_version_and_missing_correction_rejected(self):
        target = store()
        original = current_assets(target.snapshot).positions[0]
        for updated in (replace(original, balance=exact(900)), replace(original, ref=Ref(original.ref.id, 3))):
            change = proposal(target, (PutRecord('balance', updated, record_ref(original)),))
            self.reject_unchanged(target, lambda change=change: target.preview(OWNER, change, Ref('bad', 1)))

    def test_create_cannot_reuse_retired_identity(self):
        target = store()
        retired = current_assets(target.snapshot).accounts[0]
        _, _, command = prepared(target, replacement(target))
        target.execute(command)
        change = proposal(target, (PutRecord('reuse', retired),), 'reuse')
        self.reject_unchanged(target, lambda: target.preview(OWNER, change, Ref('bad', 1)))

    def test_retained_scope_is_exact(self):
        target = store()
        change = replace(future_add(target), retained_refs=())
        self.reject_unchanged(target, lambda: target.preview(OWNER, change, Ref('bad', 1)))

    def test_blocking_issue_prevents_ready_subset_commit(self):
        target = store()
        issue = ReconciliationIssue('ambiguous', IssueDisposition.NEEDS_USER_INPUT, 'Which account?')
        change = replace(future_add(target), issues=(issue,))
        self.reject_unchanged(target, lambda: target.preview(OWNER, change, Ref('bad', 1)))

    def test_dependency_order_and_duplicate_operations_fail(self):
        target = store()
        change = future_add(target)
        with self.assertRaises(ValueError):
            replace(change, operations=(replace(change.operations[0], depends_on=('missing',)),))
        with self.assertRaises(ValueError):
            replace(change, operations=(*change.operations, *change.operations))

    def test_preview_and_change_identity_cannot_be_overwritten(self):
        target = store()
        change = future_add(target)
        preview = target.preview(OWNER, change, Ref('preview', 1))
        bad = replace(change, candidate_refs=(Ref('different-candidate', 1),))
        self.reject_unchanged(target, lambda: target.preview(OWNER, bad, preview.ref))
        self.reject_unchanged(target, lambda: target.preview(OWNER, bad, Ref('new-preview', 1)))

    def test_temporal_authorization_and_execution_checks(self):
        target = store()
        change = future_add(target)
        preview = target.preview(OWNER, change, Ref('preview', 1))
        self.reject_unchanged(target, lambda: target.authorize(OWNER, preview, Ref('auth', 1), NOW))
        _, _, command = prepared(target, change, 'valid')
        self.reject_unchanged(target, lambda: target.execute(replace(command, recorded_at=NOW)))

    def test_immutable_protocol_and_tuple_collections(self):
        target = store()
        changes = future_add(target)
        with self.assertRaises(FrozenInstanceError):
            changes.mode = ChangeMode.CORRECT
        with self.assertRaises(ValueError):
            replace(changes, operations=list(changes.operations))
        with self.assertRaises(ValueError):
            replace(changes, expected_revision=True)

    def test_round_trip_all_mutation_records_and_snapshot(self):
        target = store()
        preview, grant, command = prepared(target, replacement(target))
        receipt = target.execute(command)
        values = (preview, grant, command, receipt, target.snapshot, command.change_set,
                  command.change_set.replacement_scope, *command.change_set.operations,
                  *preview.changes, *preview.pending, preview.before_world, preview.after_world)
        for value in values:
            with self.subTest(kind=type(value).__name__):
                encoded = dumps_world_mutation(value)
                self.assertEqual(loads_world_mutation(encoded), value)
                self.assertEqual(dumps_world_mutation(loads_world_mutation(encoded)), encoded)

    def test_serialization_rejects_unknown_fields_versions_and_wrong_nested_types(self):
        data = serialize_world_mutation(future_add(store()))
        for bad in ({**data, 'schema_version': 'v99'}, {**data, 'record_type': 'ExecuteCode'},
                    {**data, 'value': {**data['value'], 'script': 'anything'}},
                    {**data, 'value': {**data['value'], 'expected_revision': True}}):
            with self.assertRaises(ValueError):
                deserialize_world_mutation(bad)

    def test_json_duplicate_keys_floats_nonfinite_rejected(self):
        for payload in ('{"a":1,"a":2}', '{"a":1.1}', '{"a":NaN}', '[]', 'null'):
            with self.assertRaises(ValueError):
                loads_world_mutation(payload)

    def test_phase2_artifacts_are_byte_exact(self):
        from finvis.story import run_story
        from finvis.replay import markdown, structured
        root = Path(__file__).resolve().parents[1]
        snapshot = run_story().snapshot
        self.assertEqual((markdown(snapshot) + '\n').encode(), (root / 'artifacts/phase2-replay.md').read_bytes())
        self.assertEqual((json.dumps(structured(snapshot), ensure_ascii=False, indent=2, sort_keys=True) + '\n').encode(),
                         (root / 'artifacts/phase2-replay.json').read_bytes())

    def test_slice_a_b_c_serialization_is_unchanged(self):
        from finvis.world_values import dumps_world_value, loads_world_value
        from finvis.world_domain import dumps_world_asset, loads_world_asset
        from finvis.world_planning import dumps_world_planning, loads_world_planning
        bundle = canonical_bundle()
        for value, encode, decode in ((bundle.future_inflows[0].expected_amount, dumps_world_value, loads_world_value),
                                     (bundle.assets, dumps_world_asset, loads_world_asset),
                                     (bundle, dumps_world_planning, loads_world_planning)):
            self.assertEqual(decode(encode(value)), value)

    def test_provider_candidate_has_no_command_authority(self):
        target = store()
        self.reject_unchanged(target, lambda: target.execute({'approved': True, 'amount': 34000}))
        self.reject_unchanged(target, lambda: target.preview(OWNER, {'facts': []}, Ref('candidate', 1)))

    def test_settled_occurrence_is_rejected(self):
        target = store(canonical_bundle())
        occurrence = completed_phone_bundle().actual_occurrences[0]
        occurrence = replace(occurrence, related_entity_refs=(), settlement_status=SettlementStatus.APPLIED_TO_BALANCE,
            source_position_ref=record_ref(target.snapshot.get(TypedRef('asset_position', Ref('position/alipay-cny', 1)))),
            balance_effect_ref=TypedRef('balance_effect', Ref('effect', 1)))
        change = proposal(target, (PutRecord('settlement', occurrence),))
        self.reject_unchanged(target, lambda: target.preview(OWNER, change, Ref('bad', 1)))

    def test_future_plan_decision_and_occurrence_commands_do_not_change_balances(self):
        target = store()
        base = canonical_bundle()
        expense = replace(base.planned_expenses[0], funding_links=())
        decision = base.decisions[0]
        occurrence = replace(completed_phone_bundle().actual_occurrences[0], related_entity_refs=())
        ops = tuple(PutRecord(item.ref.id, item) for item in (*base.future_inflows, expense, decision, *base.contexts, occurrence))
        before = current_assets(target.snapshot)
        preview, _, command = prepared(target, proposal(target, ops))
        target.execute(command)
        self.assertEqual(current_assets(target.snapshot), before)
        self.assertIn('unsettled_occurrence', {issue.code for issue in preview.pending})
        self.assertEqual(target.snapshot.world.state_ref, before.state.ref)

    def test_state_change_preserves_historical_decision_baseline_and_marks_pending(self):
        target = store(canonical_bundle())
        original = next(item for item in current_assets(target.snapshot).positions if item.ref.id == 'position/icbc-cny')
        decision = target.snapshot.get(TypedRef('world_decision', Ref('decision/gloves', 1)))
        updated = replace(original, ref=Ref(original.ref.id, 2), balance=exact(26000),
                          observation=observation('updated', ObservationRole.CURRENT, original.ref))
        preview, _, command = prepared(target, proposal(target, (PutRecord('balance', updated, record_ref(original)),)))
        target.execute(command)
        self.assertEqual(target.snapshot.get(record_ref(decision)), decision)
        self.assertIn(record_ref(decision), target.snapshot.world.members)
        self.assertIn('historical_dependency', {issue.code for issue in preview.pending})
        plan = target.snapshot.get(TypedRef('world_plan', target.snapshot.world.plan_ref))
        self.assertIs(plan.evaluation_status, PlanEvaluationStatus.PENDING)

    def test_multicurrency_replace_cny_preserves_sgd(self):
        base = baseline()
        old_account = base.assets.accounts[0]
        sgd = position('old-sgd', old_account, exact(200, Currency.SGD), Currency.SGD, 'sgd')
        state = replace(base.assets.state, position_refs=(*base.assets.state.position_refs, record_ref(sgd)))
        bundle = replace(base, assets=WorldAssetBundle(state, base.assets.accounts, (*base.assets.positions, sgd), ()))
        target = store(bundle)
        original = current_assets(target.snapshot).positions[0]
        updated = replace(original, ref=Ref(original.ref.id, 2), balance=exact(9000),
                          observation=observation('cny', ObservationRole.CURRENT, original.ref))
        scope = ReplacementScope(state.account_refs, (Currency.CNY,), (record_ref(original),), ())
        _, _, command = prepared(target, proposal(target, (PutRecord('cny', updated, record_ref(original)),), scope=scope))
        target.execute(command)
        assets = current_assets(target.snapshot)
        self.assertEqual(amount_for_currency(assets.current_assets_by_currency(), Currency.SGD).amount, money(200, Currency.SGD))
        self.assertEqual(amount_for_currency(assets.current_assets_by_currency(), Currency.CNY).amount, money(9000))
        self.assertEqual(target.snapshot.get(record_ref(sgd)), sgd)

    def test_partial_earmark_rebind_fails_but_complete_observation_update_succeeds(self):
        target = store(canonical_bundle())
        assets = current_assets(target.snapshot)
        original = next(item for item in assets.positions if item.ref.id == 'position/alipay-cny')
        updated = replace(original, ref=Ref(original.ref.id, 2), balance=exact(26000),
                          observation=observation('updated', ObservationRole.CURRENT, original.ref))
        ops = (PutRecord('balance', updated, record_ref(original)),)
        self.reject_unchanged(target, lambda: target.preview(OWNER, proposal(target, ops), Ref('bad', 1)))
        for mark in assets.earmarks:
            new_mark = replace(mark, ref=Ref(mark.ref.id, 2), position_ref=record_ref(updated),
                               observation=observation(mark.ref.id, ObservationRole.CURRENT, mark.ref))
            ops += (PutRecord(mark.ref.id, new_mark, record_ref(mark), ('balance',)),)
        preview, _, command = prepared(target, proposal(target, ops))
        target.execute(command)
        self.assertEqual(amount_for_currency(current_assets(target.snapshot).current_assets_by_currency(), Currency.CNY).amount, money(35000))
        self.assertIn('historical_dependency', {item.code for item in preview.pending})

    def test_replacement_retains_future_decision_plan_and_historical_funding(self):
        target = store(canonical_bundle())
        before = target.snapshot
        assets = current_assets(before)
        scope = ReplacementScope(assets.state.account_refs, (Currency.CNY,), assets.state.position_refs, assets.state.earmark_refs)
        fresh_account = account('new-wallet', 'New wallet')
        fresh_position = position('new-cny', fresh_account, exact(34000))
        ops = tuple(RetireRecord('retire/' + ref.ref.id, ref) for ref in (*scope.earmark_refs, *scope.position_refs, *scope.account_refs))
        ops += (PutRecord('new-account', fresh_account), PutRecord('new-position', fresh_position))
        preview, _, command = prepared(target, proposal(target, ops, scope=scope))
        target.execute(command)
        for ref in before.world.members:
            self.assertEqual(target.snapshot.get(ref), before.get(ref))
            if ref.record_kind not in ('account', 'asset_position', 'earmark'):
                self.assertIn(ref, target.snapshot.world.members)
        before_plan = before.get(TypedRef('world_plan', before.world.plan_ref))
        after_plan = target.snapshot.get(TypedRef('world_plan', target.snapshot.world.plan_ref))
        self.assertEqual(before_plan.ref.id, after_plan.ref.id)
        self.assertEqual(after_plan.previous_plan_ref, record_ref(before_plan))
        self.assertEqual(after_plan.planned_expense_refs, before_plan.planned_expense_refs)
        self.assertEqual(after_plan.goal_allocations, before_plan.goal_allocations)
        self.assertIn('historical_dependency', {issue.code for issue in preview.pending})

    def test_new_context_cannot_reference_old_decision_version(self):
        target = store(canonical_bundle())
        decision = canonical_bundle().decisions[0]
        updated = replace(decision, ref=Ref(decision.ref.id, 2), status=DecisionStatus.DEFERRED,
                          observation=observation('deferred', ObservationRole.CONTEMPLATED, decision.ref))
        _, _, command = prepared(target, proposal(target, (PutRecord('decision', updated, record_ref(decision)),)))
        target.execute(command)
        context = replace(canonical_bundle().contexts[0], ref=Ref('context/new', 1))
        change = proposal(target, (PutRecord('context', context),), 'new-context')
        self.reject_unchanged(target, lambda: target.preview(OWNER, change, Ref('bad', 1)))

    def test_gift_cancellation_preserves_expectation_history_and_assets(self):
        target = store(canonical_bundle())
        gift = canonical_bundle().future_inflows[0]
        updated = replace(gift, ref=Ref(gift.ref.id, 2), status=FutureInflowStatus.CANCELLED,
                          observation=observation('cancelled', ObservationRole.FUTURE, gift.ref))
        before = current_assets(target.snapshot)
        _, _, command = prepared(target, proposal(target, (PutRecord('gift', updated, record_ref(gift)),)))
        target.execute(command)
        self.assertEqual(current_assets(target.snapshot), before)
        self.assertEqual(target.snapshot.get(record_ref(gift)), gift)
        self.assertIn(record_ref(updated), target.snapshot.world.members)

    def test_actual_phone_completion_is_atomic_without_settlement(self):
        target = store(canonical_bundle())
        completed = completed_phone_bundle()
        old_phone = canonical_bundle().planned_expenses[0]
        new_phone = completed.planned_expenses[0]
        occurrence = completed.actual_occurrences[0]
        change = proposal(target, (PutRecord('phone', new_phone, record_ref(old_phone)), PutRecord('actual', occurrence, depends_on=('phone',))))
        preview, _, command = prepared(target, change)
        before = current_assets(target.snapshot)
        target.execute(command)
        self.assertEqual(current_assets(target.snapshot), before)
        plan = target.snapshot.get(TypedRef('world_plan', target.snapshot.world.plan_ref))
        self.assertEqual(plan.planned_expense_refs, ())
        self.assertIn('unsettled_occurrence', {item.code for item in preview.pending})

    def test_one_sided_occurrence_link_cannot_erase_planned_semantics(self):
        target = store(canonical_bundle())
        occurrence = replace(completed_phone_bundle().actual_occurrences[0],
            related_entity_refs=(record_ref(canonical_bundle().planned_expenses[0]),))
        change = proposal(target, (PutRecord('actual', occurrence),))
        self.reject_unchanged(target, lambda: target.preview(OWNER, change, Ref('bad', 1)))

    def test_new_funding_outside_replacement_scope_fails(self):
        target = store(canonical_bundle())
        assets = current_assets(target.snapshot)
        icbc = next(item for item in assets.positions if item.ref.id == 'position/icbc-cny')
        icbc_account = target.snapshot.get(icbc.account_ref)
        scope = ReplacementScope((record_ref(icbc_account),), (Currency.CNY,), (record_ref(icbc),), ())
        updated = replace(icbc, ref=Ref(icbc.ref.id, 2), observation=observation('icbc', ObservationRole.CURRENT, icbc.ref))
        extra = replace(assets.earmarks[0], ref=Ref('earmark/outside', 1), allocation=exact(0))
        change = proposal(target, (PutRecord('icbc', updated, record_ref(icbc)), PutRecord('extra', extra)), scope=scope)
        self.reject_unchanged(target, lambda: target.preview(OWNER, change, Ref('bad', 1)))

    def test_duplicate_entity_target_with_different_operation_ids_fails(self):
        target = store()
        change = future_add(target)
        operation = change.operations[0]
        change = replace(change, operations=(operation, replace(operation, operation_id='duplicate')))
        self.reject_unchanged(target, lambda: target.preview(OWNER, change, Ref('bad', 1)))

    def test_failed_execution_is_retryable_and_no_success_receipt_leaks(self):
        target = store()
        _, _, command = prepared(target, replacement(target))
        self.reject_unchanged(target, lambda: target.execute(replace(command, preview_digest='tampered')))
        self.assertEqual(target.snapshot.receipts, ())
        self.assertEqual(target.snapshot.authorizations, ())
        target.execute(command)
        self.assertEqual(len(target.snapshot.receipts), 1)

    def test_scope_change_and_candidate_revision_invalidate_authorization(self):
        target = store()
        _, _, command = prepared(target, replacement(target))
        changed_scope = replace(command.change_set.replacement_scope, currencies=(Currency.CNY, Currency.SGD))
        for altered in (replace(command.change_set, replacement_scope=changed_scope),
                        replace(command.change_set, candidate_refs=(Ref('candidate/replace', 2),))):
            self.reject_unchanged(target, lambda altered=altered: target.execute(replace(command, change_set=altered)))

    def test_retry_requires_identical_canonical_timestamp_representation(self):
        target = store()
        _, _, command = prepared(target, replacement(target))
        target.execute(command)
        same_instant = command.recorded_at.astimezone(timezone(timedelta(hours=8)))
        self.assertEqual(same_instant, command.recorded_at)
        self.reject_unchanged(target, lambda: target.execute(replace(command, recorded_at=same_instant)))

    def test_native_sgd_future_range_preserves_precision_and_time(self):
        target = store()
        change = future_add(target)
        item = change.operations[0].record
        item = replace(item, expected_amount=AmountEstimate.range(money(100, Currency.SGD), money(200, Currency.SGD)))
        change = replace(change, operations=(replace(change.operations[0], record=item),))
        _, _, command = prepared(target, change)
        before = current_assets(target.snapshot)
        target.execute(command)
        result = target.snapshot.get(record_ref(item))
        self.assertEqual(result.expected_amount, item.expected_amount)
        self.assertEqual(result.timing, item.timing)
        self.assertEqual(current_assets(target.snapshot), before)

    def test_earmark_currency_failure_is_atomic(self):
        target = store()
        change = replacement(target)
        ops = list(change.operations)
        ops[-1] = replace(ops[-1], record=replace(ops[-1].record, allocation=exact(20000, Currency.SGD)))
        self.reject_unchanged(target, lambda: target.preview(OWNER, replace(change, operations=tuple(ops)), Ref('bad', 1)))


if __name__ == '__main__':
    unittest.main()

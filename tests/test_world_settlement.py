from dataclasses import replace, FrozenInstanceError
from datetime import timedelta
import json
import unittest

from finvis.domain import Currency, Ref, Money
from finvis.store import Rejected, StaleVersion
from finvis.world_values import AmountEstimate, AmountPrecision, ObservationRole, TypedRef, FreshnessStatus
from finvis.world_domain import amount_for_currency, WorldAssetBundle
from finvis.world_planning import ActualOccurrence, OccurrenceKind, SettlementStatus, PlannedExpenseStatus, FutureInflowStatus
from finvis.world_commands import PutRecord, RetireRecord, ReplacementScope, record_ref
from finvis.world_store import current_assets, validate_world
from finvis.world_serialization import dumps_world_mutation, loads_world_mutation, serialize_world_mutation, deserialize_world_mutation, payload_digest
from finvis.world_settlement import BalanceBasis, BalanceCoverage, BalanceEffect, SettleOccurrence, reconcile_settlement, propose_settlement
from finvis.world_liquidity import pending_liquidity
from tests.test_world_assets import NOW, OWNER, WORLD, exact, money, position
from tests.test_world_planning import canonical_bundle, completed_phone_bundle, observation
from tests.test_world_mutation import store, proposal, prepared, future_add, replacement


def current(target, kind, suffix):
    if kind == 'earmark' and suffix == 'phone':
        suffix = 'phones'
    return next(target.snapshot.get(ref) for ref in target.snapshot.world.members
                if ref.record_kind == kind and ref.ref.id.endswith(suffix))


def operation(target, name='phones', amount=17600, kind=OccurrenceKind.EXPENSE,
              source='alipay-cny', related='phones', earmark='phone', basis=BalanceBasis.PRIOR):
    pos = current(target, 'asset_position', source) if source else None
    linked = current(target, 'planned_expense' if kind is OccurrenceKind.EXPENSE else 'future_inflow', related) if related else None
    mark = current(target, 'earmark', earmark) if earmark else None
    at = NOW + timedelta(minutes=target.snapshot.revision + 1)
    meta = replace(observation(name, ObservationRole.ACTUAL), recorded_at=at)
    report = ActualOccurrence(Ref('occurrence/' + name, 1), OWNER, WORLD, kind, exact(amount), meta.as_of,
        (record_ref(linked),) if linked else (), SettlementStatus.UNALLOCATED, meta,
        record_ref(pos) if pos and kind is OccurrenceKind.EXPENSE else None,
        record_ref(pos) if pos and kind is OccurrenceKind.INFLOW else None)
    coverage = BalanceCoverage(record_ref(report), record_ref(pos), basis, meta,
        'Reviewed report explicitly states whether this exact balance includes the occurrence') if pos and basis else None
    return SettleOccurrence(name, report, None, coverage, record_ref(linked) if linked else None, record_ref(mark) if mark else None)


def settle(target, op, name='settle'):
    change = propose_settlement(target.snapshot, target.session_generation, op, Ref('change/' + name, 1))
    preview, grant, command = prepared(target, change, name)
    return preview, grant, command


def correction(target, amount=17300, source='alipay-cny'):
    old = current(target, 'actual_occurrence', 'phones')
    op = operation(target, amount=amount, source=source)
    report = replace(op.report, ref=Ref(old.ref.id, old.ref.version + 1),
        observation=replace(op.report.observation, corrects_ref=old.ref, correction_reason='Reported phone price correction'))
    return replace(op, report=report, expected_ref=record_ref(old), corrects_effect_ref=old.balance_effect_ref,
                   coverage=replace(op.coverage, occurrence_ref=record_ref(report)))


def totals(target):
    assets = current_assets(target.snapshot)
    return tuple(amount_for_currency(method(), Currency.CNY).amount.minor_units // 100
                 for method in (assets.current_assets_by_currency, assets.earmarked_assets_by_currency, assets.unallocated_assets_by_currency))


class SettlementTests(unittest.TestCase):
    def setUp(self):
        self.target = store(canonical_bundle())

    def unchanged(self, action, error=ValueError):
        before = self.target.snapshot
        serialized = dumps_world_mutation(before)
        with self.assertRaises(error):
            action()
        self.assertIs(self.target.snapshot, before)
        self.assertEqual(dumps_world_mutation(before), serialized)

    def phone(self):
        preview, grant, command = settle(self.target, operation(self.target))
        receipt = self.target.execute(command)
        return preview, grant, command, receipt

    def test_phone_canonical_values_and_residual(self):
        self.phone()
        self.assertEqual(totals(self.target), (16400, 7400, 9000))
        self.assertEqual(current(self.target, 'asset_position', 'alipay-cny').balance, exact(7400))
        self.assertEqual(current(self.target, 'asset_position', 'icbc-cny').balance, exact(9000))
        self.assertEqual(current(self.target, 'earmark', 'phone').allocation, exact(2400))

    def test_unrelated_emergency_value_and_designation_preserved(self):
        old = current(self.target, 'earmark', 'emergency')
        self.phone()
        new = current(self.target, 'earmark', 'emergency')
        self.assertEqual((new.allocation, new.role, new.purpose, new.emergency_use_designated),
                         (old.allocation, old.role, old.purpose, old.emergency_use_designated))
        self.assertEqual(new.position_ref, record_ref(current(self.target, 'asset_position', 'alipay-cny')))

    def test_planned_and_actual_amounts_auditable(self):
        old = current(self.target, 'planned_expense', 'phones')
        self.phone()
        new = current(self.target, 'planned_expense', 'phones')
        actual = current(self.target, 'actual_occurrence', 'phones')
        self.assertEqual(new.expected_amount, old.expected_amount)
        self.assertEqual(actual.actual_amount, exact(17600))
        self.assertEqual(self.target.snapshot.get(record_ref(old)), old)
        self.assertIs(new.status, PlannedExpenseStatus.COMPLETED)
        self.assertEqual(new.occurrence_ref, record_ref(actual))

    def test_preview_and_reconciliation_have_no_side_effects(self):
        before = self.target.snapshot
        op = operation(self.target)
        self.assertTrue(reconcile_settlement(before, op).ready)
        preview, grant, _ = settle(self.target, op)
        self.assertIs(self.target.snapshot, before)
        kinds = {record_ref(c.after).record_kind for c in preview.changes}
        self.assertTrue({'asset_position', 'earmark', 'actual_occurrence', 'planned_expense', 'balance_effect', 'world_state', 'world_plan'} <= kinds)
        self.assertEqual(grant.preview_digest, preview.digest)
        self.assertTrue(preview.destructive)
        self.assertIn('residual_earmark_release_pending', {i.code for i in preview.pending})

    def test_phone_source_unknown_is_pending(self):
        op = operation(self.target, source=None)
        result = reconcile_settlement(self.target.snapshot, op)
        self.assertFalse(result.ready)
        self.assertIn('source_unknown', {i.code for i in result.issues})
        self.unchanged(lambda: settle(self.target, op))
        self.assertEqual(totals(self.target), (34000, 25000, 9000))

    def test_unknown_source_occurrence_can_be_recorded_without_posting(self):
        old = current(self.target, 'planned_expense', 'phones')
        bundle = completed_phone_bundle()
        change = proposal(self.target, (PutRecord('complete', bundle.planned_expenses[0], record_ref(old)),
                                       PutRecord('report', bundle.actual_occurrences[0])))
        _, _, command = prepared(self.target, change)
        self.target.execute(command)
        self.assertEqual(totals(self.target), (34000, 25000, 9000))
        self.assertIn('unsettled_occurrence', {i.code for i in pending_liquidity(self.target.snapshot, Currency.CNY).reasons})

    def test_known_source_unknown_coverage_is_pending(self):
        op = operation(self.target, basis=None)
        self.assertIn('balance_coverage_unknown', {i.code for i in reconcile_settlement(self.target.snapshot, op).issues})
        self.unchanged(lambda: settle(self.target, op))

    def test_rent_actual_differs_from_expected(self):
        old = current(self.target, 'future_inflow', 'rent-refund')
        op = operation(self.target, 'rent', 12800, OccurrenceKind.INFLOW, 'icbc-cny', 'rent-refund', None)
        _, _, command = settle(self.target, op)
        self.target.execute(command)
        self.assertEqual(totals(self.target), (46800, 25000, 21800))
        self.assertEqual(current(self.target, 'asset_position', 'icbc-cny').balance, exact(21800))
        new = current(self.target, 'future_inflow', 'rent-refund')
        self.assertIs(new.status, FutureInflowStatus.REALIZED)
        self.assertEqual(new.expected_amount, old.expected_amount)
        self.assertEqual(self.target.snapshot.get(record_ref(old)), old)

    def test_rent_unknown_destination_pending(self):
        op = operation(self.target, 'rent', 12800, OccurrenceKind.INFLOW, None, 'rent-refund', None)
        self.assertIn('destination_unknown', {i.code for i in reconcile_settlement(self.target.snapshot, op).issues})
        self.unchanged(lambda: settle(self.target, op))

    def test_unplanned_expense(self):
        _, _, command = settle(self.target, operation(self.target, 'repair', 800, source='icbc-cny', related=None, earmark=None))
        self.target.execute(command)
        self.assertEqual(current(self.target, 'asset_position', 'icbc-cny').balance, exact(8200))
        self.assertEqual(totals(self.target)[0], 33200)

    def test_unexpected_inflow(self):
        _, _, command = settle(self.target, operation(self.target, 'reimbursement', 1000, OccurrenceKind.INFLOW, related=None, earmark=None))
        self.target.execute(command)
        self.assertEqual(current(self.target, 'asset_position', 'alipay-cny').balance, exact(26000))
        self.assertEqual(len([r for r in self.target.snapshot.world.members if r.record_kind == 'future_inflow']), 2)

    def test_future_cancellation_has_no_balance_effect(self):
        gift = current(self.target, 'future_inflow', 'family-gift')
        new = replace(gift, ref=Ref(gift.ref.id, 2), status=FutureInflowStatus.CANCELLED,
                      observation=observation('cancel-gift', ObservationRole.FUTURE, gift.ref))
        _, _, cmd = prepared(self.target, proposal(self.target, (PutRecord('cancel', new, record_ref(gift)),)))
        self.target.execute(cmd)
        self.assertEqual(totals(self.target), (34000, 25000, 9000))
        op = operation(self.target, 'gift', 10000, OccurrenceKind.INFLOW, 'icbc-cny', 'family-gift', None)
        self.unchanged(lambda: settle(self.target, op))

    def test_planned_cancellation_does_not_release_reserve(self):
        phone = current(self.target, 'planned_expense', 'phones')
        new = replace(phone, ref=Ref(phone.ref.id, 2), status=PlannedExpenseStatus.CANCELLED,
                      observation=observation('cancel-phone', ObservationRole.PLANNED, phone.ref))
        _, _, cmd = prepared(self.target, proposal(self.target, (PutRecord('cancel', new, record_ref(phone)),)))
        self.target.execute(cmd)
        self.assertEqual(totals(self.target), (34000, 25000, 9000))
        self.unchanged(lambda: settle(self.target, operation(self.target)))

    def test_unauthorized_settlement_rejected(self):
        _, _, cmd = settle(self.target, operation(self.target))
        self.unchanged(lambda: self.target.execute(replace(cmd, authorization_ref=Ref('missing', 1))))

    def test_stale_settlement_rejected(self):
        _, _, old = settle(self.target, operation(self.target))
        _, _, other = prepared(self.target, future_add(self.target), 'other')
        self.target.execute(other)
        self.unchanged(lambda: self.target.execute(old), StaleVersion)

    def test_amount_tamper_rejected(self):
        _, _, cmd = settle(self.target, operation(self.target))
        op = cmd.change_set.operations[0]
        op = replace(op, report=replace(op.report, actual_amount=exact(18600)))
        self.unchanged(lambda: self.target.execute(replace(cmd, change_set=replace(cmd.change_set, operations=(op,)))))

    def test_source_tamper_rejected(self):
        _, _, cmd = settle(self.target, operation(self.target))
        op = cmd.change_set.operations[0]
        op = replace(op, report=replace(op.report, source_position_ref=record_ref(current(self.target, 'asset_position', 'icbc-cny'))))
        self.unchanged(lambda: self.target.execute(replace(cmd, change_set=replace(cmd.change_set, operations=(op,)))))

    def test_earmark_tamper_rejected(self):
        _, _, cmd = settle(self.target, operation(self.target))
        op = replace(cmd.change_set.operations[0], earmark_ref=None)
        self.unchanged(lambda: self.target.execute(replace(cmd, change_set=replace(cmd.change_set, operations=(op,)))))

    def test_wrong_authorization_rejected(self):
        _, _, cmd = settle(self.target, operation(self.target))
        _, _, other = prepared(self.target, future_add(self.target), 'other')
        self.unchanged(lambda: self.target.execute(replace(cmd, authorization_ref=other.authorization_ref)))

    def test_duplicate_settlement_is_exact_retry(self):
        _, _, cmd, receipt = self.phone()
        before = self.target.snapshot
        self.assertEqual(self.target.execute(cmd), receipt)
        self.assertIs(self.target.snapshot, before)
        self.assertEqual(len(before.receipts), 1)

    def test_authorization_cannot_be_reused(self):
        _, _, cmd, _ = self.phone()
        self.unchanged(lambda: self.target.execute(replace(cmd, command_id='different')))

    def test_same_occurrence_new_command_cannot_repost(self):
        self.phone()
        op = correction(self.target)
        op = replace(op, corrects_effect_ref=None)
        self.unchanged(lambda: settle(self.target, op, 'repost'))

    def test_atomic_failure_at_publish_and_retry(self):
        armed = [True]
        def fail(candidate):
            if armed[0]:
                self.assertEqual(len(candidate.receipts), 1)
                raise RuntimeError('Injected before publish')
        self.target = store(canonical_bundle(), hook=fail)
        _, _, cmd = settle(self.target, operation(self.target))
        self.unchanged(lambda: self.target.execute(cmd), RuntimeError)
        self.assertEqual(self.target.snapshot.authorizations, ())
        armed[0] = False
        self.target.execute(cmd)
        self.assertEqual(totals(self.target)[0], 16400)

    def test_late_conservation_failure_preserves_whole_snapshot(self):
        op = operation(self.target, earmark=None)  # Position debit works, allocations then fail.
        self.unchanged(lambda: settle(self.target, op))
        self.assertEqual(self.target.snapshot.receipts, ())

    def test_correction_only_net_300_and_history(self):
        _, _, _, receipt = self.phone()
        old = self.target.snapshot
        _, _, cmd = settle(self.target, correction(self.target), 'correct')
        self.target.execute(cmd)
        self.assertEqual(totals(self.target), (16700, 7700, 9000))
        self.assertEqual(current(self.target, 'asset_position', 'alipay-cny').balance, exact(7700))
        self.assertEqual(current(self.target, 'earmark', 'phone').allocation, exact(2700))
        effect = current(self.target, 'balance_effect', 'phones')
        self.assertEqual((effect.direction, effect.amount), (1, exact(300)))
        self.assertEqual(effect.previous_effect_ref.ref.version, 1)
        self.assertEqual(self.target.snapshot.records[:len(old.records)], old.records)
        self.assertEqual(self.target.snapshot.receipts[0], receipt)

    def test_duplicate_correction_no_extra_300(self):
        self.phone()
        _, _, cmd = settle(self.target, correction(self.target), 'correct')
        receipt = self.target.execute(cmd)
        before = self.target.snapshot
        self.assertEqual(self.target.execute(cmd), receipt)
        self.assertIs(before, self.target.snapshot)

    def test_correction_larger_amount(self):
        self.phone()
        _, _, cmd = settle(self.target, correction(self.target, 17900), 'correct')
        self.target.execute(cmd)
        self.assertEqual(current(self.target, 'asset_position', 'alipay-cny').balance, exact(7100))
        self.assertEqual(current(self.target, 'earmark', 'phone').allocation, exact(2100))

    def test_wrong_source_correction_deferred(self):
        self.phone()
        self.unchanged(lambda: settle(self.target, correction(self.target, source='icbc-cny'), 'wrong-source'))

    def test_correction_rejects_later_reported_balance(self):
        self.phone()
        pos = current(self.target, 'asset_position', 'alipay-cny')
        ops = [PutRecord('balance', replace(pos, ref=Ref(pos.ref.id, 3), balance=exact(8000),
            observation=replace(observation('later-balance', ObservationRole.CURRENT, pos.ref), recorded_at=NOW + timedelta(minutes=2))), record_ref(pos))]
        for ref in self.target.snapshot.world.members:
            mark = self.target.snapshot.get(ref)
            if ref.record_kind == 'earmark':
                ops.append(PutRecord(mark.ref.id, replace(mark, ref=Ref(mark.ref.id, 3), position_ref=record_ref(ops[0].record),
                    observation=replace(observation('rebind', ObservationRole.CURRENT, mark.ref), recorded_at=NOW + timedelta(minutes=2))), ref))
        _, _, cmd = prepared(self.target, proposal(self.target, tuple(ops), 'balance'), 'balance')
        self.target.execute(cmd)
        self.unchanged(lambda: settle(self.target, correction(self.target), 'correct'))

    def test_correction_allows_known_later_effect(self):
        self.phone()
        _, _, cmd = settle(self.target, operation(self.target, 'bonus', 1000, OccurrenceKind.INFLOW, related=None, earmark=None), 'bonus')
        self.target.execute(cmd)
        _, _, cmd = settle(self.target, correction(self.target), 'correct')
        self.target.execute(cmd)
        self.assertEqual(current(self.target, 'asset_position', 'alipay-cny').balance, exact(8700))

    def test_inclusive_balance_has_zero_posting(self):
        op = operation(self.target, 'known-repair', 800, source='icbc-cny', related=None, earmark=None, basis=BalanceBasis.INCLUDED)
        _, _, cmd = settle(self.target, op)
        before = current_assets(self.target.snapshot)
        self.target.execute(cmd)
        self.assertEqual(current_assets(self.target.snapshot), before)
        effect = current(self.target, 'balance_effect', 'known-repair')
        self.assertEqual((effect.direction, effect.amount), (0, exact(0)))
        self.assertEqual(effect.position_before_ref, effect.position_after_ref)

    def test_inclusive_balance_cannot_consume_allocation_twice(self):
        self.unchanged(lambda: settle(self.target, operation(self.target, basis=BalanceBasis.INCLUDED)))

    def test_cross_currency_fails(self):
        op = operation(self.target)
        op = replace(op, report=replace(op.report, actual_amount=exact(17600, Currency.SGD)))
        self.unchanged(lambda: settle(self.target, op))

    def test_approximate_credit_preserves_precision(self):
        op = operation(self.target, 'refund', 12800, OccurrenceKind.INFLOW, 'icbc-cny', 'rent-refund', None)
        op = replace(op, report=replace(op.report, actual_amount=AmountEstimate.approximate(money(12800))))
        _, _, cmd = settle(self.target, op)
        self.target.execute(cmd)
        self.assertEqual(current(self.target, 'asset_position', 'icbc-cny').balance, AmountEstimate.approximate(money(21800)))

    def test_unknown_actual_amount_is_pending(self):
        op = operation(self.target)
        op = replace(op, report=replace(op.report, actual_amount=AmountEstimate.unknown('Missing price', Currency.CNY)))
        self.assertFalse(reconcile_settlement(self.target.snapshot, op).ready)
        self.unchanged(lambda: settle(self.target, op))

    def test_stale_balance_fails(self):
        bundle = canonical_bundle()
        positions = tuple(replace(p, observation=replace(p.observation, freshness=replace(p.observation.freshness, status=FreshnessStatus.STALE))) for p in bundle.assets.positions)
        self.target = store(replace(bundle, assets=replace(bundle.assets, positions=positions)))
        self.unchanged(lambda: settle(self.target, operation(self.target)))

    def test_no_assumption_only_settlement(self):
        op = operation(self.target)
        op = replace(op, report=replace(op.report, observation=replace(op.report.observation, evidence_refs=(), assumption_refs=(Ref('assumption', 1),))))
        self.unchanged(lambda: settle(self.target, op))

    def test_effect_records_and_snapshot_roundtrip(self):
        op = operation(self.target)
        reconciliation = reconcile_settlement(self.target.snapshot, op)
        preview, grant, cmd, receipt = self.phone()
        for value in (op, op.coverage, reconciliation, preview, grant, cmd, receipt, self.target.snapshot):
            with self.subTest(kind=type(value).__name__):
                encoded = dumps_world_mutation(value)
                self.assertEqual(loads_world_mutation(encoded), value)
                self.assertEqual(dumps_world_mutation(loads_world_mutation(encoded)), encoded)

    def test_correction_roundtrip(self):
        self.phone()
        _, _, cmd = settle(self.target, correction(self.target), 'correct')
        self.target.execute(cmd)
        self.assertEqual(loads_world_mutation(dumps_world_mutation(self.target.snapshot)), self.target.snapshot)

    def test_fail_closed_serialization(self):
        data = serialize_world_mutation(operation(self.target))
        for bad in ({**data, 'schema_version': 'finvis.world-settlement.v99'},
                    {**data, 'value': {**data['value'], 'hidden_fx': 1}},
                    {**data, 'record_type': 'AutoPay'}):
            with self.assertRaises(ValueError):
                deserialize_world_mutation(bad)

    def test_effect_tamper_audit_rejected(self):
        self.phone()
        effect = current(self.target, 'balance_effect', 'phones')
        bad = replace(effect, amount=exact(100))
        snapshot = replace(self.target.snapshot, records=tuple(bad if r == effect else r for r in self.target.snapshot.records))
        with self.assertRaises(Rejected):
            validate_world(snapshot)

    def test_direct_put_cannot_modify_settled_occurrence(self):
        self.phone()
        old = current(self.target, 'actual_occurrence', 'phones')
        new = correction(self.target).report
        change = proposal(self.target, (PutRecord('bypass', new, record_ref(old)),), 'bypass')
        self.unchanged(lambda: self.target.preview(OWNER, change, Ref('bypass', 1)))

    def test_second_occurrence_cannot_realize_completed_plan(self):
        self.phone()
        self.unchanged(lambda: settle(self.target, operation(self.target, name='another'), 'duplicate'))

    def test_multiple_positions_or_partial_settlement_not_in_protocol(self):
        op = operation(self.target)
        with self.assertRaises(ValueError):
            replace(op, coverage=(op.coverage, op.coverage))
        with self.assertRaises(TypeError):
            replace(op, partial_amount=exact(800))

    def test_refunds_reversals_and_negative_balances_not_supported(self):
        with self.assertRaises(ValueError):
            exact(-800)
        self.unchanged(lambda: settle(self.target, operation(self.target, 'overdraft', 10000, source='icbc-cny', related=None, earmark=None)))
        self.assertEqual({k.value for k in OccurrenceKind}, {'expense', 'inflow'})

    def test_time_passing_does_not_settle_future(self):
        before = self.target.snapshot
        for _ in range(2):
            result = pending_liquidity(before, Currency.CNY)
            self.assertIs(result.value.precision, AmountPrecision.UNKNOWN)
        self.assertIs(before, self.target.snapshot)
        self.assertEqual(totals(self.target)[0], 34000)

    def test_receipt_binds_effect_and_authorization(self):
        preview, grant, cmd, receipt = self.phone()
        effect = current(self.target, 'balance_effect', 'phones')
        self.assertIn(record_ref(effect), receipt.inserted_refs)
        self.assertEqual(receipt.authorization_ref, grant.ref)
        self.assertEqual(receipt.preview_digest, preview.digest)
        self.assertEqual(self.target.snapshot.commands, (cmd,))
        self.assertEqual((self.target.snapshot.world.ref.version, self.target.snapshot.world.state_ref.version, self.target.snapshot.world.plan_ref.version), (2, 2, 2))

    def test_multicurrency_isolation(self):
        bundle = canonical_bundle()
        sgd = position('icbc-sgd', bundle.assets.accounts[0], exact(500, Currency.SGD), Currency.SGD, 'sgd')
        state = replace(bundle.assets.state, position_refs=(*bundle.assets.state.position_refs, record_ref(sgd)))
        self.target = store(replace(bundle, assets=WorldAssetBundle(state, bundle.assets.accounts, (*bundle.assets.positions, sgd), bundle.assets.earmarks)))
        self.phone()
        self.assertEqual(current(self.target, 'asset_position', 'icbc-sgd'), sgd)
        self.assertEqual(amount_for_currency(current_assets(self.target.snapshot).current_assets_by_currency(), Currency.SGD), exact(500, Currency.SGD))

    def record_unallocated_phone(self):
        old = current(self.target, 'planned_expense', 'phones')
        bundle = completed_phone_bundle()
        _, _, command = prepared(self.target, proposal(self.target, (
            PutRecord('complete', bundle.planned_expenses[0], record_ref(old)),
            PutRecord('report', bundle.actual_occurrences[0]),)), 'record')
        self.target.execute(command)

    def test_existing_unallocated_occurrence_can_later_settle(self):
        self.record_unallocated_phone()
        old = current(self.target, 'actual_occurrence', 'phones')
        op = operation(self.target)
        report = replace(op.report, ref=Ref(old.ref.id, 2),
            observation=replace(op.report.observation, corrects_ref=old.ref, correction_reason='Source and coverage now reviewed'))
        op = replace(op, report=report, expected_ref=record_ref(old), coverage=replace(op.coverage, occurrence_ref=record_ref(report)))
        _, _, cmd = settle(self.target, op)
        self.target.execute(cmd)
        self.assertEqual(totals(self.target), (16400, 7400, 9000))
        self.assertEqual(current(self.target, 'actual_occurrence', 'phones').ref.version, 2)
        self.assertEqual(self.target.snapshot.get(record_ref(old)).settlement_status, SettlementStatus.UNALLOCATED)

    def test_unknown_destination_report_can_realize_without_credit(self):
        old = current(self.target, 'future_inflow', 'rent-refund')
        op = operation(self.target, 'rent', 12800, OccurrenceKind.INFLOW, None, 'rent-refund', None)
        realized = replace(old, ref=Ref(old.ref.id, 2), status=FutureInflowStatus.REALIZED,
            realization_ref=record_ref(op.report), observation=replace(op.report.observation, role=ObservationRole.FUTURE,
                corrects_ref=old.ref, correction_reason='Observed arrival; destination unresolved'))
        report = replace(op.report, related_entity_refs=(record_ref(realized),))
        _, _, cmd = prepared(self.target, proposal(self.target, (PutRecord('realized', realized, record_ref(old)), PutRecord('report', report))), 'record')
        self.target.execute(cmd)
        self.assertEqual(totals(self.target), (34000, 25000, 9000))
        self.assertIs(current(self.target, 'actual_occurrence', 'rent').settlement_status, SettlementStatus.UNALLOCATED)

    def test_canonical_inclusive_phone_balance_not_debited_twice(self):
        # Separately reviewed current observations already include purchase and reserve use.
        assets = current_assets(self.target.snapshot)
        pos = current(self.target, 'asset_position', 'alipay-cny')
        meta = observation('inclusive-balance', ObservationRole.CURRENT, pos.ref)
        new_pos = replace(pos, ref=Ref(pos.ref.id, 2), balance=exact(7400), observation=meta)
        ops = [PutRecord('balance', new_pos, record_ref(pos))]
        for mark in assets.earmarks:
            new_mark = replace(mark, ref=Ref(mark.ref.id, 2), position_ref=record_ref(new_pos),
                allocation=exact(2400) if mark.purpose == 'Phones' else mark.allocation,
                observation=observation('inclusive-allocation', ObservationRole.CURRENT, mark.ref))
            ops.append(PutRecord(mark.ref.id, new_mark, record_ref(mark)))
        _, _, cmd = prepared(self.target, proposal(self.target, tuple(ops)), 'balance')
        self.target.execute(cmd)
        op = operation(self.target, earmark=None, basis=BalanceBasis.INCLUDED)
        _, _, cmd = settle(self.target, op)
        self.target.execute(cmd)
        self.assertEqual(totals(self.target), (16400, 7400, 9000))
        self.assertIs(current(self.target, 'actual_occurrence', 'phones').settlement_status, SettlementStatus.ALREADY_IN_BALANCE)

    def test_explicit_residual_release_uses_existing_d_authority(self):
        self.phone()
        assets = current_assets(self.target.snapshot)
        pos = current(self.target, 'asset_position', 'alipay-cny')
        phone = current(self.target, 'earmark', 'phone')
        reserve = current(self.target, 'earmark', 'emergency')
        at = NOW + timedelta(minutes=2)
        next_pos = replace(pos, ref=Ref(pos.ref.id, 3), observation=replace(observation('review', ObservationRole.CURRENT, pos.ref), recorded_at=at))
        next_reserve = replace(reserve, ref=Ref(reserve.ref.id, 3), position_ref=record_ref(next_pos),
            observation=replace(observation('rebind', ObservationRole.CURRENT, reserve.ref), recorded_at=at))
        scope = ReplacementScope((pos.account_ref,), (Currency.CNY,), (record_ref(pos),), (record_ref(phone), record_ref(reserve)))
        ops = (PutRecord('position', next_pos, record_ref(pos)), PutRecord('reserve', next_reserve, record_ref(reserve)), RetireRecord('release', record_ref(phone)))
        _, _, cmd = prepared(self.target, proposal(self.target, ops, 'release', scope), 'release')
        self.target.execute(cmd)
        self.assertEqual(totals(self.target), (16400, 5000, 11400))
        self.assertEqual(self.target.snapshot.get(record_ref(phone)), phone)

    def test_correction_after_explicit_reserve_release_is_pending(self):
        for allocation, purpose in ((exact(1000), 'Phones'), (exact(2400), 'Repurposed reserve')):
            with self.subTest(purpose=purpose):
                self.target = store(canonical_bundle())
                self.phone()
                mark = current(self.target, 'earmark', 'phone')
                at = NOW + timedelta(minutes=2)
                resized = replace(mark, ref=Ref(mark.ref.id, 3), allocation=allocation, purpose=purpose,
                    observation=replace(observation('resize', ObservationRole.CURRENT, mark.ref), recorded_at=at))
                _, _, cmd = prepared(self.target, proposal(self.target, (PutRecord('resize', resized, record_ref(mark)),), 'resize'), 'resize')
                self.target.execute(cmd)
                self.unchanged(lambda: settle(self.target, correction(self.target), 'correct'))

    def test_reinstatement_requires_new_evidence_version(self):
        gift = current(self.target, 'future_inflow', 'family-gift')
        cancelled = replace(gift, ref=Ref(gift.ref.id, 2), status=FutureInflowStatus.CANCELLED,
            observation=observation('cancelled', ObservationRole.FUTURE, gift.ref))
        _, _, cmd = prepared(self.target, proposal(self.target, (PutRecord('cancel', cancelled, record_ref(gift)),)), 'cancel')
        self.target.execute(cmd)
        revived = replace(cancelled, ref=Ref(gift.ref.id, 3), status=FutureInflowStatus.EXPECTED,
            observation=replace(observation('new-report', ObservationRole.FUTURE, cancelled.ref), recorded_at=NOW + timedelta(minutes=2)))
        _, _, cmd = prepared(self.target, proposal(self.target, (PutRecord('reinstate', revived, record_ref(cancelled)),), 'reinstate'), 'reinstate')
        self.target.execute(cmd)
        op = operation(self.target, 'gift', 10000, OccurrenceKind.INFLOW, 'icbc-cny', 'family-gift', None)
        _, _, cmd = settle(self.target, op)
        self.target.execute(cmd)
        self.assertEqual(totals(self.target)[0], 44000)

    def test_coverage_must_bind_exact_occurrence(self):
        op = operation(self.target)
        op = replace(op, coverage=replace(op.coverage, occurrence_ref=TypedRef('actual_occurrence', Ref('other', 1))))
        self.unchanged(lambda: settle(self.target, op))

    def test_coverage_must_bind_exact_position(self):
        op = operation(self.target)
        op = replace(op, coverage=replace(op.coverage, position_ref=record_ref(current(self.target, 'asset_position', 'icbc-cny'))))
        self.unchanged(lambda: settle(self.target, op))

    def test_sgd_native_settlement_preserves_currency_isolation(self):
        bundle = canonical_bundle()
        sgd = position('icbc-sgd', bundle.assets.accounts[0], exact(500, Currency.SGD), Currency.SGD, 'sgd')
        state = replace(bundle.assets.state, position_refs=(*bundle.assets.state.position_refs, record_ref(sgd)))
        self.target = store(replace(bundle, assets=replace(bundle.assets, state=state, positions=(*bundle.assets.positions, sgd))))
        op = operation(self.target, 'sgd-expense', 80, source='icbc-sgd', related=None, earmark=None)
        op = replace(op, report=replace(op.report, actual_amount=exact(80, Currency.SGD)))
        _, _, cmd = settle(self.target, op)
        self.target.execute(cmd)
        self.assertEqual(current(self.target, 'asset_position', 'icbc-sgd').balance, exact(420, Currency.SGD))
        self.assertEqual(totals(self.target)[0], 34000)
        self.assertEqual({c.value for c in Currency}, {'CNY', 'SGD', 'EUR'})

    def test_bounded_approximate_expense_preserves_precision(self):
        op = operation(self.target, 'repair', 800, source='icbc-cny', related=None, earmark=None)
        op = replace(op, report=replace(op.report, actual_amount=AmountEstimate.approximate(money(800), money(790), money(810))))
        _, _, cmd = settle(self.target, op)
        self.target.execute(cmd)
        self.assertEqual(current(self.target, 'asset_position', 'icbc-cny').balance,
                         AmountEstimate.approximate(money(8200), money(8190), money(8210)))

    def test_range_inflow_preserves_bounds(self):
        op = operation(self.target, 'refund', 12800, OccurrenceKind.INFLOW, 'icbc-cny', 'rent-refund', None)
        op = replace(op, report=replace(op.report, actual_amount=AmountEstimate.range(money(12700), money(12900))))
        _, _, cmd = settle(self.target, op)
        self.target.execute(cmd)
        self.assertEqual(current(self.target, 'asset_position', 'icbc-cny').balance, AmountEstimate.range(money(21700), money(21900)))

    def test_unbounded_expense_remains_pending_without_negative_proof(self):
        op = operation(self.target, 'repair', 800, source='icbc-cny', related=None, earmark=None)
        op = replace(op, report=replace(op.report, actual_amount=AmountEstimate.approximate(money(800))))
        self.assertFalse(reconcile_settlement(self.target.snapshot, op).ready)
        self.unchanged(lambda: settle(self.target, op))

    def test_old_d_wire_bytes_unchanged(self):
        target = store()
        preview, grant, cmd = prepared(target, replacement(target))
        receipt = target.execute(cmd)
        expected = ('d13ce81e6b4af17ff7166493ea5b5054351c385e7be7180b7e949d29806ee214',
                    '1ff97c72c122e29f959ccb2400b621237748cd2fc81481767254b3bcbce593f0',
                    '57f6e7159b89688eb0e1a7536aaa03104419a0adeb83aba8066d5bd9d1d41ba3',
                    '1a7cf719e416c5d855e00478eca3c18325851ef90e8c583d5f83e3f3e9ede2b7',
                    '1cabeb842df9a7b0115b3649bed9ba7c1ac0efb992fcdb520eba33e33531d5ab')
        self.assertEqual(tuple(payload_digest(v) for v in (preview, grant, cmd, receipt, target.snapshot)), expected)

    def test_cancel_settlement_preview_preserves_world(self):
        preview, _, cmd = settle(self.target, operation(self.target))
        before = self.target.snapshot
        self.target.cancel(OWNER, preview.ref)
        self.assertIs(before, self.target.snapshot)
        self.unchanged(lambda: self.target.execute(cmd))

    def test_immutable_settlement_protocol(self):
        op = operation(self.target)
        with self.assertRaises(FrozenInstanceError):
            op.report = None
        with self.assertRaises(ValueError):
            replace(op, depends_on=[])

    def test_repeated_corrections_preserve_chain(self):
        self.phone()
        for amount in (17300, 17200):
            _, _, cmd = settle(self.target, correction(self.target, amount), 'correct-' + str(amount))
            self.target.execute(cmd)
        self.assertEqual(current(self.target, 'asset_position', 'alipay-cny').balance, exact(7800))
        self.assertEqual(current(self.target, 'balance_effect', 'phones').ref.version, 3)

    def test_realized_inflow_preserves_historical_expected_destination(self):
        bundle = canonical_bundle()
        destination = next(p for p in bundle.assets.positions if p.ref.id.endswith('icbc-cny'))
        inflows = tuple(replace(i, destination_ref=record_ref(destination)) if i.ref.id.endswith('rent-refund') else i for i in bundle.future_inflows)
        self.target = store(replace(bundle, future_inflows=inflows))
        op = operation(self.target, 'rent', 12800, OccurrenceKind.INFLOW, 'icbc-cny', 'rent-refund', None)
        _, _, cmd = settle(self.target, op)
        self.target.execute(cmd)
        self.assertEqual(current(self.target, 'future_inflow', 'rent-refund').destination_ref, record_ref(destination))
        self.assertEqual(current(self.target, 'asset_position', 'icbc-cny').balance, exact(21800))

    def test_rent_correction_and_retry(self):
        op = operation(self.target, 'rent', 12800, OccurrenceKind.INFLOW, 'icbc-cny', 'rent-refund', None)
        _, _, cmd = settle(self.target, op)
        self.target.execute(cmd)
        old = current(self.target, 'actual_occurrence', 'rent')
        op = operation(self.target, 'rent', 12700, OccurrenceKind.INFLOW, 'icbc-cny', 'rent-refund', None)
        report = replace(op.report, ref=Ref(old.ref.id, 2), observation=replace(op.report.observation,
            corrects_ref=old.ref, correction_reason='Correct rent receipt'))
        op = replace(op, report=report, expected_ref=record_ref(old), corrects_effect_ref=old.balance_effect_ref,
                     coverage=replace(op.coverage, occurrence_ref=record_ref(report)))
        _, _, cmd = settle(self.target, op, 'correct')
        receipt = self.target.execute(cmd)
        self.assertEqual(current(self.target, 'asset_position', 'icbc-cny').balance, exact(21700))
        self.assertEqual(self.target.execute(cmd), receipt)

    def test_inclusive_basis_correction_requires_reconciliation(self):
        op = operation(self.target, 'phones', 17600, source='alipay-cny', related=None, earmark=None, basis=BalanceBasis.INCLUDED)
        _, _, cmd = settle(self.target, op)
        self.target.execute(cmd)
        old = current(self.target, 'actual_occurrence', 'phones')
        op = operation(self.target, 'phones', 17300, related=None, earmark=None)
        report = replace(op.report, ref=Ref(old.ref.id, 2), observation=replace(op.report.observation,
            corrects_ref=old.ref, correction_reason='Correct inclusive report'))
        op = replace(op, report=report, expected_ref=record_ref(old), corrects_effect_ref=old.balance_effect_ref,
                     coverage=replace(op.coverage, occurrence_ref=record_ref(report)))
        self.unchanged(lambda: settle(self.target, op, 'correct'))

    def test_late_reference_failure_preserves_whole_world(self):
        op = operation(self.target)
        op = replace(op, report=replace(op.report, related_entity_refs=(*op.report.related_entity_refs,
            TypedRef('world_decision', Ref('missing-decision', 1)))))
        self.unchanged(lambda: settle(self.target, op))

    def test_forged_effect_is_not_a_generic_editable_record(self):
        self.phone()
        effect = current(self.target, 'balance_effect', 'phones')
        with self.assertRaises(ValueError):
            PutRecord('forge', effect)

    def test_correction_does_not_detach_prior_plan(self):
        self.phone()
        op = correction(self.target)
        op = replace(op, related_ref=None, report=replace(op.report, related_entity_refs=()))
        self.unchanged(lambda: settle(self.target, op, 'detach'))


if __name__ == '__main__':
    unittest.main()

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import unittest

from finvis.domain import (
    Assumption, AssumptionStatus, Confidence, ConfidenceAssessment, Currency,
    Decision, DecisionOption, DecisionStatus, EmploymentStatus, FinancialAmount, FinancialState,
    Goal, GoalAllocation, GoalProtection, LivingPlan, Money,
    PlannedOutflow, PlanStatus, Precision, RealitySource, Ref,
)
from finvis.store import Rejected, Snapshot
from finvis.story import run_story
from finvis.world_commands import (
    CandidateChangeSet, ChangeMode, FinancialWorldCommand, PutRecord, record_ref,
)
from finvis.world_planning import (
    FutureInflow, FutureInflowStatus, PlanEvaluationStatus, PlannedExpense,
    WorldPlan,
)
from finvis.world_replay import (
    LEGACY_SCHEMA, MigrationAuthorization, MigrationDisposition, MigrationGate,
    dumps_world_replay, export_world_replay, loads_world_replay,
    migrate_legacy_snapshot, replay_world,
)
from finvis.world_values import (
    AmountEstimate, FreshnessAssessment, FreshnessStatus, ObservationMetadata,
    ObservationRole, TemporalSpec,
)
from finvis.world_settlement import BalanceEffect
from tests.test_world_mutation import future_add, prepared, store
from tests.test_world_planning import canonical_bundle
from tests.test_world_settlement import correction, operation, settle


UTC = timezone.utc
MIGRATION_TIME = datetime(2026, 10, 2, 9, 30, tzinfo=UTC)


def migration_observation(assumption_ref=Ref('synthetic-balances', 1),
                          at=MIGRATION_TIME):
    confidence = Confidence(ConfidenceAssessment.UNASSESSED,
        'Human-reviewed migration boundary; no new financial fact inferred')
    return ObservationMetadata(
        RealitySource.USER_REPORT, (), (assumption_ref,), at,
        TemporalSpec.known_date(at.date()), at, ObservationRole.CURRENT,
        confidence, FreshnessAssessment(FreshnessStatus.CURRENT,
            'Reviewed at migration cutover', at))


def canonical_proposal():
    return migrate_legacy_snapshot(
        run_story().snapshot, 'migrated-synthetic-world', Ref('migration-manifest', 1),
        Ref('migrated-world', 1), migration_observation())


def generic_snapshot():
    owner = 'sample-household'
    assumption = Assumption(
        Ref('sample-basis', 1), 'Synthetic aggregate only', 'Test migration limits',
        'Non-user migration fixture', AssumptionStatus.PROVISIONAL,
        'Replace with reviewed account evidence')
    confidence = Confidence(ConfidenceAssessment.TENTATIVE,
                            'Synthetic non-user-specific fixture')
    liquidity = FinancialAmount(Money(876_500, Currency.SGD), Precision.APPROXIMATE,
                                confidence, (), (assumption.ref,))
    essentials = FinancialAmount(Money(234_500, Currency.SGD), Precision.EXACT_REPORTED,
                                 confidence, (), (assumption.ref,))
    goal = Goal(Ref('studio-goal', 1), owner, 'Creative studio',
                Money(5_000_000, Currency.SGD), date(2030, 6, 1),
                GoalProtection.FLEXIBLE)
    state = FinancialState(Ref('sample-state', 1), owner, date(2026, 10, 1),
                           liquidity, essentials, (goal.ref,))
    decision = Decision(Ref('course-decision', 1), owner,
        'Should I take a six-week course?', state.ref, DecisionStatus.SELECTED,
        Ref('course-now', 1))
    option = DecisionOption(Ref('course-now', 1), decision.ref,
        'Take the course', 'Reserve tuition in the plan only',
        planned_outflow=Money(125_000, Currency.SGD))
    first = LivingPlan(Ref('sample-plan', 1), owner, state.ref, decision.ref,
        (assumption.ref,), (GoalAllocation(goal.ref, Money(45_000, Currency.SGD)),),
        PlanStatus.ADOPTED)
    second = replace(first, ref=Ref('sample-plan', 2), previous_plan_ref=first.ref,
        planned_outflows=(PlannedOutflow(decision.ref, Money(125_000, Currency.SGD)),))
    return Snapshot((assumption, state, goal, decision, option, first, second),
                    state.ref, second.ref)


def generic_proposal():
    return migrate_legacy_snapshot(
        generic_snapshot(), 'sample-world', Ref('sample-manifest', 1),
        Ref('sample-world-root', 1), migration_observation(Ref('sample-basis', 1)))


def cutover(proposal=None):
    proposal = proposal or canonical_proposal()
    gate = MigrationGate(proposal, 'migration-session')
    preview = gate.preview(proposal.manifest.owner_id, Ref('migration-preview', 1))
    authorization = gate.authorize(proposal.manifest.owner_id, preview,
        Ref('migration-authorization', 1), MIGRATION_TIME + timedelta(minutes=1))
    return gate, gate.cutover(authorization), authorization


def add_post_migration_future(target, proposal):
    snapshot = target.snapshot
    observation = replace(proposal.manifest.observation,
        role=ObservationRole.FUTURE, recorded_at=MIGRATION_TIME + timedelta(minutes=2))
    future = FutureInflow(Ref('training-rebate', 1), snapshot.world.owner_id,
        snapshot.world.world_id, AmountEstimate.approximate(Money(80_000, Currency.SGD)),
        'Possible training rebate', TemporalSpec.unknown('Payment date not confirmed'),
        None, FutureInflowStatus.EXPECTED, observation)
    operation = PutRecord('add-training-rebate', future)
    change = CandidateChangeSet(Ref('post-migration-change', 1), snapshot.world.owner_id,
        snapshot.world.world_id, target.session_generation, snapshot.world.ref,
        snapshot.world.state_ref, snapshot.world.plan_ref, snapshot.revision,
        ChangeMode.MERGE, (operation,), None, snapshot.world.members,
        (Ref('reviewed-rebate-candidate', 1),), observation, None, (),
        observation.recorded_at)
    preview = target.preview(snapshot.world.owner_id, change, Ref('post-migration-preview', 1))
    authorization = target.authorize(snapshot.world.owner_id, preview,
        Ref('post-migration-authorization', 1), observation.recorded_at)
    command = FinancialWorldCommand('post-migration-rebate', snapshot.world.owner_id,
        change, preview.ref, preview.digest, authorization.ref, observation.recorded_at)
    target.execute(command)


class MigrationTests(unittest.TestCase):
    def test_migration_is_deterministic(self):
        self.assertEqual(canonical_proposal(), canonical_proposal())
        self.assertEqual(canonical_proposal().digest, canonical_proposal().digest)

    def test_source_export_hash_is_bound(self):
        proposal = generic_proposal()
        self.assertEqual(hashlib.sha256(proposal.source_export.encode()).hexdigest(),
                         proposal.manifest.source_hash)

    def test_aggregate_liquidity_creates_no_account_or_position(self):
        proposal = generic_proposal()
        kinds = {record_ref(item).record_kind for item in proposal.initial_snapshot.records}
        self.assertNotIn('account', kinds)
        self.assertNotIn('asset_position', kinds)
        self.assertEqual(proposal.initial_snapshot.world.members,
            tuple(ref for ref in proposal.initial_snapshot.world.members
                  if ref.record_kind in ('world_decision', 'planned_expense')))

    def test_legacy_liquidity_and_essentials_are_exactly_preserved(self):
        legacy = generic_snapshot().get(FinancialState, Ref('sample-state', 1))
        assessment = generic_proposal().legacy_assessment
        self.assertEqual(assessment.available_emergency_liquidity.value,
                         legacy.available_emergency_liquidity.value)
        self.assertEqual(assessment.available_emergency_liquidity.precision,
                         Precision.APPROXIMATE)
        self.assertEqual(assessment.monthly_essential_outflow.value,
                         legacy.monthly_essential_outflow.value)

    def test_employment_and_goals_are_preserved_in_assessment(self):
        legacy = run_story().snapshot
        state = legacy.get(FinancialState, legacy.current_state_ref)
        assessment = canonical_proposal().legacy_assessment
        self.assertEqual(assessment.goal_refs, state.goal_refs)
        self.assertEqual(assessment.employment.status, EmploymentStatus.ACCEPTED_OFFER.value)
        self.assertEqual(assessment.employment.monthly_income.value,
                         state.employment.offer.monthly_income.value)

    def test_plan_identity_continues_v3_to_v4_through_legacy_anchor(self):
        proposal = canonical_proposal()
        plan = proposal.initial_snapshot.get(record_ref(next(item for item in
            proposal.initial_snapshot.records if type(item) is WorldPlan)))
        self.assertEqual(plan.ref, Ref('living-plan', 4))
        self.assertEqual(plan.previous_plan_ref.record_kind, 'legacy_world_plan')
        self.assertEqual(plan.previous_plan_ref.ref, Ref('living-plan', 3))
        anchor = proposal.initial_snapshot.get(plan.previous_plan_ref)
        self.assertEqual(anchor.source_schema, LEGACY_SCHEMA)

    def test_state_and_decision_versions_continue_above_legacy(self):
        proposal = canonical_proposal()
        self.assertEqual(proposal.initial_snapshot.world.state_ref, Ref('synthetic-state', 4))
        decision = next(proposal.initial_snapshot.get(ref) for ref in
                        proposal.initial_snapshot.world.members
                        if ref.record_kind == 'world_decision')
        self.assertEqual(decision.ref, Ref('travel-decision', 3))

    def test_carried_planned_outflow_maps_once(self):
        proposal = canonical_proposal()
        expenses = [item for item in proposal.initial_snapshot.records
                    if type(item) is PlannedExpense]
        self.assertEqual(len(expenses), 1)
        self.assertIn('living-plan@2/0', expenses[0].ref.id)
        self.assertEqual(expenses[0].expected_amount.amount,
                         Money(300_000, Currency.SGD))
        mappings = [item for item in proposal.manifest.mappings
                    if item.source_kind == 'PlannedOutflow']
        self.assertEqual(len(mappings), 2)
        self.assertEqual({item.target_ref for item in mappings},
                         {record_ref(expenses[0])})

    def test_migrated_outflow_has_no_posting_or_invented_funding(self):
        proposal = generic_proposal()
        expense = next(item for item in proposal.initial_snapshot.records
                       if type(item) is PlannedExpense)
        self.assertEqual(expense.funding_links, ())
        self.assertFalse(any(type(item) is BalanceEffect
                             for item in proposal.initial_snapshot.records))
        self.assertEqual(proposal.initial_snapshot.get(
            record_ref(next(item for item in proposal.initial_snapshot.records
                            if type(item) is WorldPlan))).evaluation_status,
            PlanEvaluationStatus.PENDING)

    def test_generic_fixture_preserves_native_currency_and_different_labels(self):
        proposal = generic_proposal()
        expense = next(item for item in proposal.initial_snapshot.records
                       if type(item) is PlannedExpense)
        decision = next(item for item in proposal.initial_snapshot.records
                        if item.__class__.__name__ == 'WorldDecision')
        self.assertEqual(expense.expected_amount.currency, Currency.SGD)
        self.assertIn('six-week course', decision.question)
        self.assertNotIn('phone', expense.purpose.lower())

    def test_goal_contribution_is_plan_commitment_not_earmark(self):
        proposal = generic_proposal()
        plan = next(item for item in proposal.initial_snapshot.records
                    if type(item) is WorldPlan)
        self.assertEqual(plan.goal_allocations[0].monthly_contribution,
                         Money(45_000, Currency.SGD))
        self.assertFalse(any(record_ref(item).record_kind == 'earmark'
                             for item in proposal.initial_snapshot.records))

    def test_if_lines_and_legacy_authorizations_are_non_authoritative(self):
        mappings = canonical_proposal().manifest.mappings
        self.assertTrue(any(item.source_kind == 'IfLine' and
                            item.disposition is MigrationDisposition.UNREPRESENTED
                            for item in mappings))
        self.assertTrue(any(item.source_kind == 'PlanAuthorization' and
                            item.disposition is MigrationDisposition.NON_EXECUTABLE
                            for item in mappings))

    def test_incomplete_legacy_snapshot_fails_closed(self):
        snapshot = generic_snapshot()
        bad = replace(snapshot, current_plan_ref=Ref('missing-plan', 1))
        with self.assertRaises(Rejected):
            migrate_legacy_snapshot(bad, 'sample-world', Ref('manifest', 1),
                                    Ref('world', 1),
                                    migration_observation(Ref('sample-basis', 1)))

    def test_mixed_owner_snapshot_fails_closed(self):
        snapshot = generic_snapshot()
        decision = snapshot.get(Decision, Ref('course-decision', 1))
        records = tuple(replace(item, owner_id='other-owner') if item == decision else item
                        for item in snapshot.records)
        with self.assertRaises(Rejected):
            migrate_legacy_snapshot(replace(snapshot, records=records), 'sample-world',
                Ref('manifest', 1), Ref('world', 1),
                migration_observation(Ref('sample-basis', 1)))

    def test_unresolved_migration_provenance_fails_closed(self):
        with self.assertRaises(Rejected):
            migrate_legacy_snapshot(generic_snapshot(), 'sample-world', Ref('manifest', 1),
                Ref('world', 1), migration_observation(Ref('absent', 1)))

    def test_non_current_migration_observation_fails_closed(self):
        observation = replace(migration_observation(Ref('sample-basis', 1)),
                              role=ObservationRole.PLANNED)
        with self.assertRaises(Rejected):
            migrate_legacy_snapshot(generic_snapshot(), 'sample-world', Ref('manifest', 1),
                                    Ref('world', 1), observation)

    def test_gate_requires_registered_preview_and_new_authorization(self):
        proposal = generic_proposal()
        gate = MigrationGate(proposal, 'generation')
        forged = MigrationAuthorization(Ref('auth', 1), proposal.manifest.owner_id,
            proposal.manifest.owner_id, proposal.manifest.world_id, 'generation',
            Ref('preview', 1), proposal.digest, proposal.manifest.source_hash,
            proposal.manifest.target_world_ref, MIGRATION_TIME + timedelta(minutes=1))
        with self.assertRaises(Rejected):
            gate.cutover(forged)

    def test_deserialized_authorization_does_not_register_consent(self):
        proposal = generic_proposal()
        first = MigrationGate(proposal, 'generation')
        preview = first.preview(proposal.manifest.owner_id, Ref('preview', 1))
        auth = first.authorize(proposal.manifest.owner_id, preview, Ref('auth', 1),
                               MIGRATION_TIME + timedelta(minutes=1))
        decoded = loads_world_replay(dumps_world_replay(auth))
        second = MigrationGate(proposal, 'generation')
        second.preview(proposal.manifest.owner_id, preview.ref)
        with self.assertRaises(Rejected):
            second.cutover(decoded)

    def test_exact_cutover_is_idempotent_and_starts_with_no_d_authority(self):
        gate, target, authorization = cutover(generic_proposal())
        self.assertIs(gate.cutover(authorization), target)
        self.assertEqual(target.snapshot.authorizations, ())
        self.assertEqual(target.snapshot.commands, ())
        self.assertEqual(target.snapshot.receipts, ())

    def test_cutover_cannot_switch_authorization(self):
        gate, _, authorization = cutover(generic_proposal())
        with self.assertRaises(Rejected):
            gate.cutover(replace(authorization, ref=Ref('other-auth', 1)))

    def test_migration_round_trip_is_strict_and_equal(self):
        proposal = generic_proposal()
        self.assertEqual(loads_world_replay(dumps_world_replay(proposal)), proposal)
        data = json.loads(dumps_world_replay(proposal))
        data['extra'] = True
        with self.assertRaises(ValueError):
            loads_world_replay(json.dumps(data))

    def test_duplicate_json_key_fails_closed(self):
        with self.assertRaises(ValueError):
            loads_world_replay('{"schema_version":"finvis.world-replay.v0.1","schema_version":"bad"}')


class ReplayTests(unittest.TestCase):
    def test_zero_step_migrated_world_replays_exactly(self):
        gate, target, _ = cutover(generic_proposal())
        package = export_world_replay(target.snapshot,
            migration_evidence=gate.evidence)
        self.assertEqual(replay_world(package), target.snapshot)
        self.assertEqual(hashlib.sha256(
            package.migration_evidence.source_export.encode()).hexdigest(),
            package.migration_evidence.manifest.source_hash)

    def test_replay_initial_rejects_unreferenced_hidden_history(self):
        proposal = generic_proposal()
        hidden = next(item for item in canonical_bundle().future_inflows)
        hidden = replace(hidden, owner_id=proposal.manifest.owner_id,
                         world_id=proposal.manifest.world_id)
        bad = replace(proposal.initial_snapshot,
                      records=proposal.initial_snapshot.records + (hidden,))
        with self.assertRaises(Rejected):
            export_world_replay(bad, session_generation='migration-session')

    def test_slice_d_mutation_replays_exactly(self):
        target = store()
        _, _, command = prepared(target, future_add(target))
        target.execute(command)
        package = export_world_replay(target.snapshot)
        self.assertEqual(replay_world(package), target.snapshot)

    def test_slice_e_settlement_replays_one_effect(self):
        target = store(canonical_bundle())
        _, _, command = settle(target, operation(target))
        target.execute(command)
        replayed = replay_world(export_world_replay(target.snapshot))
        effects = [item for item in replayed.records if type(item) is BalanceEffect]
        self.assertEqual(len(effects), 1)
        self.assertEqual(len(replayed.receipts), 1)

    def test_settlement_correction_lineage_survives_replay(self):
        target = store(canonical_bundle())
        _, _, first = settle(target, operation(target))
        target.execute(first)
        _, _, second = settle(target, correction(target), 'correct')
        target.execute(second)
        replayed = replay_world(export_world_replay(target.snapshot))
        effects = sorted((item for item in replayed.records if type(item) is BalanceEffect),
                         key=lambda item: item.ref.version)
        self.assertEqual(len(effects), 2)
        self.assertEqual(effects[1].observation.corrects_ref, effects[0].ref)
        self.assertEqual(len(replayed.receipts), 2)

    def test_export_and_replay_are_deterministic(self):
        target = store(canonical_bundle())
        _, _, command = settle(target, operation(target))
        target.execute(command)
        first = export_world_replay(target.snapshot)
        second = export_world_replay(target.snapshot)
        self.assertEqual(dumps_world_replay(first), dumps_world_replay(second))
        self.assertEqual(replay_world(first), replay_world(second))

    def test_repeated_replay_does_not_accumulate_records_or_money(self):
        target = store(canonical_bundle())
        _, _, command = settle(target, operation(target))
        target.execute(command)
        package = export_world_replay(target.snapshot)
        first, second = replay_world(package), replay_world(package)
        self.assertEqual(first, second)
        self.assertEqual(len(first.records), len(target.snapshot.records))
        self.assertEqual(len(first.receipts), 1)

    def test_tampered_final_snapshot_digest_fails_closed(self):
        target = store()
        _, _, command = prepared(target, future_add(target))
        target.execute(command)
        package = export_world_replay(target.snapshot)
        with self.assertRaises(Rejected):
            replay_world(replace(package, final_snapshot_digest='0' * 64))

    def test_tampered_introduced_record_fails_closed(self):
        target = store()
        _, _, command = prepared(target, future_add(target))
        target.execute(command)
        package = export_world_replay(target.snapshot)
        step = package.steps[0]
        future = next(item for item in step.introduced_records if type(item) is FutureInflow)
        changed = replace(future, purpose='Tampered purpose')
        records = tuple(changed if item == future else item for item in step.introduced_records)
        with self.assertRaises(Rejected):
            replay_world(replace(package, steps=(replace(step, introduced_records=records),)))

    def test_unknown_command_schema_fails_closed(self):
        target = store()
        _, _, command = prepared(target, future_add(target))
        target.execute(command)
        step = export_world_replay(target.snapshot).steps[0]
        with self.assertRaises(ValueError):
            replace(step, command_schema='finvis.unknown.v9')

    def test_replay_package_round_trip(self):
        target = store(canonical_bundle())
        _, _, command = settle(target, operation(target))
        target.execute(command)
        package = export_world_replay(target.snapshot)
        decoded = loads_world_replay(dumps_world_replay(package))
        self.assertEqual(decoded, package)
        self.assertEqual(replay_world(decoded), target.snapshot)

    def test_migrated_world_accepts_only_fresh_d_authorization_and_replays(self):
        proposal = generic_proposal()
        gate, target, _ = cutover(proposal)
        add_post_migration_future(target, proposal)
        package = export_world_replay(target.snapshot,
            migration_evidence=gate.evidence)
        replayed = replay_world(loads_world_replay(dumps_world_replay(package)))
        self.assertEqual(replayed, target.snapshot)
        self.assertEqual(len(replayed.authorizations), 1)
        self.assertNotEqual(replayed.authorizations[0].ref,
                            gate.evidence.authorization.ref)

    def test_phase2_replay_remains_byte_exact(self):
        from finvis.replay import markdown, structured
        snapshot = run_story().snapshot
        root = Path(__file__).resolve().parents[1]
        md = (markdown(snapshot) + '\n').encode()
        js = (json.dumps(structured(snapshot), ensure_ascii=False, indent=2,
                         sort_keys=True) + '\n').encode()
        self.assertEqual(md, (root / 'artifacts/phase2-replay.md').read_bytes())
        self.assertEqual(js, (root / 'artifacts/phase2-replay.json').read_bytes())
        self.assertEqual(hashlib.sha256(md).hexdigest(),
            '7142f96757c63d24ac49bfd62abd4fb5ec328abbca97e8b448f667996eec81cd')
        self.assertEqual(hashlib.sha256(js).hexdigest(),
            '2b871fdd6c440424cd23c88b53e0ae20d9a8fe29d2cea4da94ea656cf98b0869')


if __name__ == '__main__':
    unittest.main()

from dataclasses import FrozenInstanceError, fields, replace
from datetime import date
import json
from pathlib import Path
import unittest

from finvis.domain import (
    Confidence,
    ConfidenceAssessment,
    Currency,
    DecisionStatus,
    GoalAllocation,
    Money,
    PlanStatus,
    RealitySource,
    Ref,
)
from finvis.replay import markdown, structured
from finvis.story import run_story
from finvis.world_domain import WorldRecordKind, amount_for_currency, typed_ref
from finvis.world_planning import (
    ActualOccurrence,
    ContextLifecycle,
    DecisionContext,
    FundingLink,
    FutureInflow,
    FutureInflowStatus,
    OccurrenceKind,
    PlanEvaluationStatus,
    PlannedExpense,
    PlannedExpenseStatus,
    PlanningRecordKind,
    SettlementStatus,
    WorldDecision,
    WorldPlan,
    WorldPlanningBundle,
    deserialize_world_planning,
    dumps_world_planning,
    loads_world_planning,
    planning_ref,
    serialize_world_planning,
)
from finvis.world_values import (
    AmountEstimate,
    AmountPrecision,
    FreshnessAssessment,
    FreshnessStatus,
    ObservationMetadata,
    ObservationRole,
    TemporalAnchorKind,
    TemporalKind,
    TemporalRelation,
    TemporalSpec,
    dumps_world_value,
    loads_world_value,
)
from tests.test_world_assets import NOW, OWNER, WORLD, bundle_with_allocations, exact, money


ROOT = Path(__file__).resolve().parents[1]


def observation(identity: str, role: ObservationRole, corrects: Ref = None) -> ObservationMetadata:
    return ObservationMetadata(
        RealitySource.USER_REPORT,
        (Ref(f"evidence/{identity}", 1),),
        (),
        NOW,
        TemporalSpec.known_date(date(2026, 9, 20)),
        NOW,
        role,
        Confidence(ConfidenceAssessment.SUPPORTED, "Reviewed synthetic report"),
        FreshnessAssessment(FreshnessStatus.CURRENT, "Reviewed for this snapshot", NOW),
        corrects,
        None if corrects is None else "Reviewed lifecycle update",
    )


def approximate(major: int, currency: Currency = Currency.CNY) -> AmountEstimate:
    return AmountEstimate.approximate(money(major, currency))


def canonical_parts():
    assets = bundle_with_allocations()
    state_ref = typed_ref(WorldRecordKind.WORLD_STATE, assets.state.ref)
    phone_earmark = next(item for item in assets.earmarks if item.purpose == "Phones")

    rent = FutureInflow(
        Ref("inflow/rent-refund", 1), OWNER, WORLD, approximate(13_000), "Rent refund",
        TemporalSpec.relative("graduation", TemporalRelation.AFTER, "毕业以后", TemporalAnchorKind.LITERAL),
        None, FutureInflowStatus.EXPECTED, observation("rent", ObservationRole.FUTURE),
    )
    gift = FutureInflow(
        Ref("inflow/family-gift", 1), OWNER, WORLD, approximate(10_000), "Family gift",
        TemporalSpec.unknown("Timing not reported", "我奶奶还会给我1万"),
        None, FutureInflowStatus.EXPECTED, observation("gift", ObservationRole.FUTURE),
    )
    phone = PlannedExpense(
        Ref("expense/phones", 1), OWNER, WORLD, approximate(18_000), "Phones",
        TemporalSpec.unknown("Purchase date not reported", "大概要花1.8万左右"),
        (FundingLink(typed_ref(WorldRecordKind.EARMARK, phone_earmark.ref), approximate(18_000)),),
        None, PlannedExpenseStatus.PLANNED, observation("phones", ObservationRole.PLANNED),
    )
    gloves = WorldDecision(
        Ref("decision/gloves", 1), OWNER, WORLD, "Should I buy Winning 16oz gloves?",
        "Winning 16oz gloves for sparring after returning home", state_ref,
        DecisionStatus.EXPLORING, None, approximate(6_000),
        TemporalSpec.unknown("Purchase timing not decided", "正在考虑买"), (),
        observation("gloves", ObservationRole.CONTEMPLATED),
    )
    context = DecisionContext(
        Ref("context/boxing", 1), OWNER, WORLD,
        (planning_ref(PlanningRecordKind.WORLD_DECISION, gloves.ref),),
        "About one year of boxing training in Singapore; intends to spar after returning home.",
        TemporalSpec.unknown("Context is relevant to the contemplated purchase"),
        observation("boxing", ObservationRole.CONTEMPLATED),
    )
    plan = WorldPlan(
        Ref("world-plan", 1), OWNER, WORLD, state_ref, None,
        (planning_ref(PlanningRecordKind.WORLD_DECISION, gloves.ref),),
        (planning_ref(PlanningRecordKind.PLANNED_EXPENSE, phone.ref),),
        (), (Ref("assumption/phone-timing", 1),), PlanStatus.DRAFT,
        PlanEvaluationStatus.PENDING, observation("plan", ObservationRole.PLANNED),
    )
    return assets, plan, rent, gift, phone, gloves, context


def canonical_bundle() -> WorldPlanningBundle:
    assets, plan, rent, gift, phone, gloves, context = canonical_parts()
    return WorldPlanningBundle(assets, plan, (gift, rent), (phone,), (), (gloves,), (context,))


def completed_phone_bundle() -> WorldPlanningBundle:
    assets, old_plan, rent, gift, phone, gloves, context = canonical_parts()
    occurrence = ActualOccurrence(
        Ref("occurrence/phones", 1), OWNER, WORLD, OccurrenceKind.EXPENSE, exact(17_600),
        TemporalSpec.known_date(date(2026, 9, 20), "手机已经买了"),
        (planning_ref(PlanningRecordKind.PLANNED_EXPENSE, Ref(phone.ref.id, 2)),),
        SettlementStatus.UNALLOCATED, observation("phones-actual", ObservationRole.ACTUAL),
    )
    completed = replace(
        phone,
        ref=Ref(phone.ref.id, 2),
        status=PlannedExpenseStatus.COMPLETED,
        occurrence_ref=planning_ref(PlanningRecordKind.ACTUAL_OCCURRENCE, occurrence.ref),
        observation=observation("phones-completed", ObservationRole.PLANNED, phone.ref),
    )
    plan = replace(
        old_plan,
        ref=Ref(old_plan.ref.id, 2),
        previous_plan_ref=planning_ref(PlanningRecordKind.WORLD_PLAN, old_plan.ref),
        planned_expense_refs=(),
        observation=observation("plan-2", ObservationRole.PLANNED, old_plan.ref),
    )
    return WorldPlanningBundle(assets, plan, (rent, gift), (completed,), (occurrence,), (gloves,), (context,))


class EntityContractTests(unittest.TestCase):
    def test_future_inflow_is_future_and_not_an_asset(self):
        value = canonical_bundle().future_inflows[0]
        self.assertIs(value.observation.role, ObservationRole.FUTURE)
        self.assertNotIn("balance", {item.name for item in fields(FutureInflow)})

    def test_future_lifecycle_requires_exactly_one_realization_for_realized(self):
        value = canonical_bundle().future_inflows[0]
        with self.assertRaisesRegex(ValueError, "realized"):
            replace(value, status=FutureInflowStatus.REALIZED)
        with self.assertRaisesRegex(ValueError, "realized"):
            replace(value, realization_ref=planning_ref(PlanningRecordKind.ACTUAL_OCCURRENCE, Ref("occurrence/x", 1)))

    def test_planned_expense_is_planned_and_not_a_posting(self):
        value = canonical_bundle().planned_expenses[0]
        self.assertIs(value.observation.role, ObservationRole.PLANNED)
        self.assertNotIn("balance_effect", {item.name for item in fields(PlannedExpense)})

    def test_completed_expense_requires_occurrence_and_closed_states_forbid_it(self):
        value = canonical_bundle().planned_expenses[0]
        with self.assertRaisesRegex(ValueError, "completed"):
            replace(value, status=PlannedExpenseStatus.COMPLETED)
        with self.assertRaisesRegex(ValueError, "completed"):
            replace(value, status=PlannedExpenseStatus.CANCELLED, occurrence_ref=planning_ref(PlanningRecordKind.ACTUAL_OCCURRENCE, Ref("occurrence/x", 1)))

    def test_actual_occurrence_records_reality_without_required_position(self):
        value = completed_phone_bundle().actual_occurrences[0]
        self.assertEqual(value.actual_amount.amount, money(17_600))
        self.assertIsNone(value.source_position_ref)
        self.assertIs(value.settlement_status, SettlementStatus.UNALLOCATED)

    def test_settlement_shapes_are_fail_closed(self):
        value = completed_phone_bundle().actual_occurrences[0]
        source = typed_ref(WorldRecordKind.ASSET_POSITION, canonical_bundle().assets.positions[0].ref)
        with self.assertRaisesRegex(ValueError, "effect"):
            replace(value, settlement_status=SettlementStatus.APPLIED_TO_BALANCE, source_position_ref=source)
        settled = replace(
            value, settlement_status=SettlementStatus.APPLIED_TO_BALANCE,
            source_position_ref=source,
            balance_effect_ref=planning_ref(PlanningRecordKind.BALANCE_EFFECT, Ref("effect/phones", 1)),
        )
        self.assertIs(settled.settlement_status, SettlementStatus.APPLIED_TO_BALANCE)

    def test_decision_preserves_existing_selected_option_semantics(self):
        value = canonical_bundle().decisions[0]
        with self.assertRaisesRegex(ValueError, "selected"):
            replace(value, status=DecisionStatus.SELECTED)
        selected = replace(value, status=DecisionStatus.SELECTED, selected_option_ref=Ref("option/buy", 1))
        self.assertEqual(selected.selected_option_ref.id, "option/buy")

    def test_context_has_no_money_field_and_requires_decision(self):
        value = canonical_bundle().contexts[0]
        self.assertFalse({"amount", "money", "balance"} & {item.name for item in fields(DecisionContext)})
        with self.assertRaisesRegex(ValueError, "at least one"):
            replace(value, decision_refs=())

    def test_plan_preserves_chain_and_rejects_wrong_predecessor(self):
        value = canonical_bundle().plan
        next_plan = replace(
            value, ref=Ref(value.ref.id, 2),
            previous_plan_ref=planning_ref(PlanningRecordKind.WORLD_PLAN, value.ref),
            observation=observation("plan-next", ObservationRole.PLANNED, value.ref),
        )
        self.assertEqual(next_plan.previous_plan_ref.ref, value.ref)
        with self.assertRaisesRegex(ValueError, "earlier version"):
            replace(next_plan, previous_plan_ref=planning_ref(PlanningRecordKind.WORLD_PLAN, Ref("other-plan", 1)))

    def test_records_and_bundle_are_immutable(self):
        value = canonical_bundle()
        with self.assertRaises(FrozenInstanceError):
            value.plan.status = PlanStatus.ADOPTED
        with self.assertRaises(FrozenInstanceError):
            value.future_inflows[0].purpose = "Changed"


class CanonicalBehaviorTests(unittest.TestCase):
    def test_current_totals_remain_34000_25000_9000(self):
        bundle = canonical_bundle()
        self.assertEqual(amount_for_currency(bundle.current_assets_by_currency(), Currency.CNY).amount, money(34_000))
        self.assertEqual(amount_for_currency(bundle.earmarked_assets_by_currency(), Currency.CNY).amount, money(25_000))
        self.assertEqual(amount_for_currency(bundle.unallocated_assets_by_currency(), Currency.CNY).amount, money(9_000))

    def test_expected_future_total_is_separate_approximate_23000(self):
        total = amount_for_currency(canonical_bundle().expected_inflows_by_currency(), Currency.CNY)
        self.assertIs(total.precision, AmountPrecision.APPROXIMATE)
        self.assertEqual(total.amount, money(23_000))

    def test_phone_plan_is_approximate_18000_with_zero_current_side_effect(self):
        bundle = canonical_bundle()
        total = amount_for_currency(bundle.planned_expenses_by_currency(), Currency.CNY)
        self.assertEqual(total.amount, money(18_000))
        self.assertIs(total.precision, AmountPrecision.APPROXIMATE)
        self.assertEqual(amount_for_currency(bundle.current_assets_by_currency(), Currency.CNY).amount, money(34_000))

    def test_gloves_decision_does_not_create_expense_or_change_money(self):
        bundle = canonical_bundle()
        self.assertEqual(bundle.decisions[0].amount.amount, money(6_000))
        self.assertEqual(len(bundle.planned_expenses), 1)
        self.assertEqual(amount_for_currency(bundle.current_assets_by_currency(), Currency.CNY).amount, money(34_000))

    def test_relative_rent_and_unknown_gift_time_remain_unresolved(self):
        bundle = canonical_bundle()
        rent = next(item for item in bundle.future_inflows if item.ref.id.endswith("rent-refund"))
        gift = next(item for item in bundle.future_inflows if item.ref.id.endswith("family-gift"))
        self.assertIs(rent.timing.kind, TemporalKind.RELATIVE)
        self.assertEqual(rent.timing.source_text, "毕业以后")
        self.assertIs(gift.timing.kind, TemporalKind.UNKNOWN)

    def test_unknown_source_purchase_is_completed_but_unsettled_and_no_debit(self):
        bundle = completed_phone_bundle()
        self.assertEqual(bundle.planned_expenses_by_currency(), ())
        self.assertEqual(len(bundle.unsettled_occurrences()), 1)
        self.assertEqual(amount_for_currency(bundle.current_assets_by_currency(), Currency.CNY).amount, money(34_000))

    def test_cancelled_gift_v2_is_excluded_and_retains_lineage(self):
        bundle = canonical_bundle()
        gift = next(item for item in bundle.future_inflows if item.ref.id.endswith("family-gift"))
        cancelled = replace(
            gift, ref=Ref(gift.ref.id, 2), status=FutureInflowStatus.CANCELLED,
            observation=observation("gift-cancelled", ObservationRole.FUTURE, gift.ref),
        )
        updated = replace(bundle, future_inflows=tuple(item if item.ref != gift.ref else cancelled for item in bundle.future_inflows))
        total = amount_for_currency(updated.expected_inflows_by_currency(), Currency.CNY)
        self.assertEqual(total.amount, money(13_000))
        self.assertEqual(cancelled.observation.corrects_ref, gift.ref)
        self.assertEqual(amount_for_currency(updated.current_assets_by_currency(), Currency.CNY).amount, money(34_000))

    def test_multi_currency_future_and_planned_totals_stay_separate(self):
        bundle = canonical_bundle()
        sgd_future = replace(
            bundle.future_inflows[0], ref=Ref("inflow/sgd", 1), expected_amount=approximate(100, Currency.SGD),
            observation=observation("sgd-future", ObservationRole.FUTURE),
        )
        values = replace(bundle, future_inflows=(*bundle.future_inflows, sgd_future)).expected_inflows_by_currency()
        self.assertEqual(tuple(item.currency for item in values), (Currency.CNY, Currency.SGD))
        self.assertEqual(amount_for_currency(values, Currency.SGD).amount, money(100, Currency.SGD))

    def test_decision_amount_is_not_in_any_financial_total(self):
        bundle = canonical_bundle()
        current = amount_for_currency(bundle.current_assets_by_currency(), Currency.CNY).amount
        future = amount_for_currency(bundle.expected_inflows_by_currency(), Currency.CNY).amount
        planned = amount_for_currency(bundle.planned_expenses_by_currency(), Currency.CNY).amount
        self.assertEqual((current, future, planned), (money(34_000), money(23_000), money(18_000)))


class RelationshipInvariantTests(unittest.TestCase):
    def test_bundle_rejects_cross_owner_or_world_records(self):
        bundle = canonical_bundle()
        for bad in (replace(bundle.future_inflows[0], owner_id="other"), replace(bundle.plan, world_id="other")):
            with self.subTest(bad=bad), self.assertRaisesRegex(ValueError, "share"):
                replace(bundle, future_inflows=(bad, *bundle.future_inflows[1:])) if isinstance(bad, FutureInflow) else replace(bundle, plan=bad)

    def test_bundle_rejects_wrong_state_version(self):
        bundle = canonical_bundle()
        bad = replace(bundle.plan, source_state_ref=typed_ref(WorldRecordKind.WORLD_STATE, Ref(bundle.assets.state.ref.id, 99)))
        with self.assertRaisesRegex(ValueError, "source state"):
            replace(bundle, plan=bad)

    def test_bundle_rejects_dangling_and_wrong_type_plan_refs(self):
        bundle = canonical_bundle()
        missing = planning_ref(PlanningRecordKind.PLANNED_EXPENSE, Ref("expense/missing", 1))
        bad = replace(bundle.plan, planned_expense_refs=(missing,))
        with self.assertRaisesRegex(ValueError, "missing or wrong-version"):
            replace(bundle, plan=bad)
        wrong_kind = planning_ref(PlanningRecordKind.WORLD_DECISION, bundle.decisions[0].ref)
        with self.assertRaisesRegex(ValueError, "typed planned_expense"):
            replace(bundle.plan, planned_expense_refs=(wrong_kind,))

    def test_bundle_rejects_dangling_context_decision(self):
        bundle = canonical_bundle()
        bad = replace(bundle.contexts[0], decision_refs=(planning_ref(PlanningRecordKind.WORLD_DECISION, Ref("decision/missing", 1)),))
        with self.assertRaisesRegex(ValueError, "missing or wrong-version"):
            replace(bundle, contexts=(bad,))

    def test_bundle_rejects_duplicate_stable_ids(self):
        bundle = canonical_bundle()
        duplicate = replace(bundle.future_inflows[0], ref=Ref(bundle.future_inflows[1].ref.id, 2))
        with self.assertRaisesRegex(ValueError, "duplicate stable ID"):
            replace(bundle, future_inflows=(duplicate, bundle.future_inflows[1]))

    def test_funding_link_requires_existing_same_currency_source(self):
        bundle = canonical_bundle()
        missing = FundingLink(typed_ref(WorldRecordKind.EARMARK, Ref("earmark/missing", 1)), approximate(18_000))
        with self.assertRaisesRegex(ValueError, "missing or wrong-version"):
            replace(bundle, planned_expenses=(replace(bundle.planned_expenses[0], funding_links=(missing,)),))
        mismatch = FundingLink(bundle.planned_expenses[0].funding_links[0].source_ref, approximate(18_000, Currency.SGD))
        with self.assertRaisesRegex(ValueError, "match"):
            replace(bundle.planned_expenses[0], funding_links=(mismatch,))

    def test_completed_expense_requires_matching_expense_occurrence(self):
        bundle = completed_phone_bundle()
        occurrence = replace(bundle.actual_occurrences[0], kind=OccurrenceKind.INFLOW)
        with self.assertRaisesRegex(ValueError, "matching expense"):
            replace(bundle, actual_occurrences=(occurrence,))

    def test_realized_future_requires_matching_inflow_occurrence(self):
        bundle = canonical_bundle()
        rent = next(item for item in bundle.future_inflows if item.ref.id.endswith("rent-refund"))
        occurrence = ActualOccurrence(
            Ref("occurrence/rent", 1), OWNER, WORLD, OccurrenceKind.INFLOW, exact(12_800),
            TemporalSpec.known_date(date(2026, 10, 1)),
            (planning_ref(PlanningRecordKind.FUTURE_INFLOW, Ref(rent.ref.id, 2)),),
            SettlementStatus.UNALLOCATED, observation("rent-actual", ObservationRole.ACTUAL),
        )
        realized = replace(
            rent, ref=Ref(rent.ref.id, 2), status=FutureInflowStatus.REALIZED,
            realization_ref=planning_ref(PlanningRecordKind.ACTUAL_OCCURRENCE, occurrence.ref),
            observation=observation("rent-realized", ObservationRole.FUTURE, rent.ref),
        )
        updated = replace(
            bundle,
            future_inflows=tuple(realized if item.ref == rent.ref else item for item in bundle.future_inflows),
            actual_occurrences=(occurrence,),
        )
        self.assertIs(next(item for item in updated.future_inflows if item.ref.id.endswith("rent-refund")).status, FutureInflowStatus.REALIZED)
        self.assertEqual(amount_for_currency(updated.expected_inflows_by_currency(), Currency.CNY).amount, money(10_000))

    def test_slice_c_bundle_rejects_settlement_behavior(self):
        bundle = completed_phone_bundle()
        occurrence = bundle.actual_occurrences[0]
        source = typed_ref(WorldRecordKind.ASSET_POSITION, bundle.assets.positions[0].ref)
        settled = replace(
            occurrence, settlement_status=SettlementStatus.ALREADY_IN_BALANCE,
            source_position_ref=source,
            balance_effect_ref=planning_ref(PlanningRecordKind.BALANCE_EFFECT, Ref("effect/phones", 1)),
        )
        with self.assertRaisesRegex(ValueError, "settlement is deferred"):
            replace(bundle, actual_occurrences=(settled,))

    def test_plan_membership_is_exactly_still_planned_expenses(self):
        bundle = canonical_bundle()
        with self.assertRaisesRegex(ValueError, "exactly"):
            replace(bundle, plan=replace(bundle.plan, planned_expense_refs=()))


class SerializationAndRegressionTests(unittest.TestCase):
    def test_every_slice_c_type_and_bundle_round_trip(self):
        bundle = canonical_bundle()
        plan_with_goal = replace(
            bundle.plan,
            goal_allocations=(GoalAllocation(Ref("goal/car", 1), money(1_000, Currency.SGD)),),
        )
        values = (
            *bundle.future_inflows, *bundle.planned_expenses, *bundle.decisions, *bundle.contexts,
            bundle.planned_expenses[0].funding_links[0], bundle.plan, plan_with_goal, bundle,
            completed_phone_bundle().actual_occurrences[0],
        )
        for value in values:
            with self.subTest(value=value):
                self.assertEqual(deserialize_world_planning(serialize_world_planning(value)), value)
                self.assertEqual(loads_world_planning(dumps_world_planning(value)), value)

    def test_canonical_serialization_is_deterministic_compact_and_versioned(self):
        first = dumps_world_planning(canonical_bundle())
        second = dumps_world_planning(loads_world_planning(first))
        self.assertEqual(first, second)
        self.assertIn('"schema_version":"finvis.world-planning.v0.1"', first)
        self.assertNotIn(": ", first)
        self.assertNotIn(", ", first)

    def test_deserialization_rejects_unknown_schema_type_fields_and_nested_asset_type(self):
        data = serialize_world_planning(canonical_bundle())
        cases = (
            {**data, "schema_version": "finvis.world-planning.v9"},
            {**data, "value_type": "financial_world"},
            {**data, "unexpected": True},
            {**data, "value": {**data["value"], "assets": serialize_world_planning(canonical_bundle().plan)}},
        )
        for case in cases:
            with self.subTest(case=case), self.assertRaises((ValueError, TypeError)):
                deserialize_world_planning(case)

    def test_nonfinite_or_nontext_json_fails_closed(self):
        for value in ('{"schema_version":NaN}', 3):
            with self.subTest(value=value), self.assertRaises(ValueError):
                loads_world_planning(value)

    def test_slice_a_and_slice_b_canonical_regressions_remain_intact(self):
        amount = AmountEstimate.range(money(17_000), money(19_000))
        self.assertEqual(loads_world_value(dumps_world_value(amount)), amount)
        assets = canonical_bundle().assets
        self.assertEqual(amount_for_currency(assets.current_assets_by_currency(), Currency.CNY).amount, money(34_000))
        with self.assertRaisesRegex(ValueError, "exceed"):
            bundle_with_allocations(6_000, 20_000, 2)

    def test_phase2_replay_artifacts_remain_exact(self):
        snapshot = run_story().snapshot
        actual_markdown = (markdown(snapshot) + "\n").encode()
        actual_json = (json.dumps(structured(snapshot), ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
        self.assertEqual(actual_markdown, (ROOT / "artifacts/phase2-replay.md").read_bytes())
        self.assertEqual(actual_json, (ROOT / "artifacts/phase2-replay.json").read_bytes())


if __name__ == "__main__":
    unittest.main()

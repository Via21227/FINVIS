"""Future, planned, contemplated, and actual records for World Core Slice C.

The module is additive.  It represents relationships and validates one coherent
view over a Slice B asset bundle, but it does not settle occurrences, mutate
balances, execute commands, or define a FinancialWorld store.
"""

from dataclasses import dataclass
from enum import Enum
import json
from typing import Any, Dict, Iterable, Mapping, Optional, Tuple

from .domain import (
    Currency,
    DecisionStatus,
    GoalAllocation,
    Money,
    PlanStatus,
    Ref,
    enum_value,
    nonempty,
    typed_tuple,
)
from .world_domain import (
    CurrencyAmount,
    WorldAssetBundle,
    WorldRecordKind,
    deserialize_world_asset,
    serialize_world_asset,
    sum_amount_estimates,
)
from .world_values import (
    AmountEstimate,
    ObservationMetadata,
    ObservationRole,
    TemporalSpec,
    TypedRef,
    deserialize_world_value,
    serialize_world_value,
)


from .world_fulfillment import ExpenseFulfillment, OccurrenceLineage, encode as encode_lifecycle, decode as decode_lifecycle, is_open

WORLD_PLANNING_SCHEMA_VERSION = "finvis.world-planning.v0.1"
EXPANDED_PLANNING_SCHEMA = "finvis.world-planning.v0.2"


class PlanningRecordKind(Enum):
    FUTURE_INFLOW = "future_inflow"
    PLANNED_EXPENSE = "planned_expense"
    ACTUAL_OCCURRENCE = "actual_occurrence"
    WORLD_DECISION = "world_decision"
    DECISION_CONTEXT = "decision_context"
    WORLD_PLAN = "world_plan"
    BALANCE_EFFECT = "balance_effect"


class FutureInflowStatus(Enum):
    EXPECTED = "expected"
    REALIZED = "realized"
    CANCELLED = "cancelled"


class PlannedExpenseStatus(Enum):
    PLANNED = "planned"
    PARTIALLY_REALIZED = "partially_realized"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class OccurrenceKind(Enum):
    EXPENSE = "expense"
    INFLOW = "inflow"


class SettlementStatus(Enum):
    UNALLOCATED = "unallocated"
    APPLIED_TO_BALANCE = "applied_to_balance"
    ALREADY_IN_BALANCE = "already_in_balance"
    SUPERSEDED = "superseded"


class ContextLifecycle(Enum):
    ACTIVE = "active"
    SUPERSEDED = "superseded"


class PlanEvaluationStatus(Enum):
    CURRENT = "current"
    PENDING = "pending"
    STALE = "stale"


@dataclass(frozen=True)
class FundingLink:
    """A planned same-currency use of a position or earmark, never a posting."""

    source_ref: TypedRef
    amount: AmountEstimate

    def __post_init__(self) -> None:
        _typed_one_of(
            self.source_ref,
            (WorldRecordKind.ASSET_POSITION.value, WorldRecordKind.EARMARK.value),
            "funding source",
        )
        _amount_with_currency(self.amount, "funding amount")


@dataclass(frozen=True)
class FutureInflow:
    ref: Ref
    owner_id: str
    world_id: str
    expected_amount: AmountEstimate
    purpose: str
    timing: TemporalSpec
    destination_ref: Optional[TypedRef]
    status: FutureInflowStatus
    observation: ObservationMetadata
    realization_ref: Optional[TypedRef] = None

    def __post_init__(self) -> None:
        _entity(self.ref, self.owner_id, self.world_id, self.observation, ObservationRole.FUTURE)
        _amount_with_currency(self.expected_amount, "future inflow amount")
        nonempty(self.purpose, "future inflow purpose")
        _temporal(self.timing, "future inflow timing")
        _optional_typed_kind(self.destination_ref, WorldRecordKind.ASSET_POSITION.value, "future destination")
        enum_value(self.status, FutureInflowStatus)
        _optional_typed_kind(self.realization_ref, PlanningRecordKind.ACTUAL_OCCURRENCE.value, "realization")
        if (self.status is FutureInflowStatus.REALIZED) != (self.realization_ref is not None):
            raise ValueError("exactly a realized future inflow must name one actual occurrence")
        _correction_lineage(self.ref, self.observation)


@dataclass(frozen=True)
class PlannedExpense:
    ref: Ref
    owner_id: str
    world_id: str
    expected_amount: AmountEstimate
    purpose: str
    timing: TemporalSpec
    funding_links: Tuple[FundingLink, ...]
    decision_ref: Optional[TypedRef]
    status: PlannedExpenseStatus
    observation: ObservationMetadata
    occurrence_ref: Optional[TypedRef] = None
    fulfillment: Optional[ExpenseFulfillment] = None

    def __post_init__(self) -> None:
        _entity(self.ref, self.owner_id, self.world_id, self.observation, ObservationRole.PLANNED)
        currency = _amount_with_currency(self.expected_amount, "planned expense amount")
        nonempty(self.purpose, "planned expense purpose")
        _temporal(self.timing, "planned expense timing")
        typed_tuple(self.funding_links, FundingLink, "funding_links")
        for link in self.funding_links:
            if link.amount.currency is not currency:
                raise ValueError("funding amount currency must match planned expense")
        _unique_typed_ids((link.source_ref for link in self.funding_links), "funding_links")
        object.__setattr__(self, "funding_links", tuple(sorted(self.funding_links, key=lambda item: _typed_key(item.source_ref))))
        _optional_typed_kind(self.decision_ref, PlanningRecordKind.WORLD_DECISION.value, "expense decision")
        enum_value(self.status, PlannedExpenseStatus)
        _optional_typed_kind(self.occurrence_ref, PlanningRecordKind.ACTUAL_OCCURRENCE.value, "expense occurrence")
        if self.fulfillment is None:
            if self.status is PlannedExpenseStatus.PARTIALLY_REALIZED or (self.status is PlannedExpenseStatus.COMPLETED) != (self.occurrence_ref is not None):
                raise ValueError("exactly a completed planned expense must name one actual occurrence")
        else:
            if type(self.fulfillment) is not ExpenseFulfillment or self.occurrence_ref is not None:
                raise ValueError("Gradual expense uses explicit fulfillment instead of legacy completion")
            has_events = bool(self.fulfillment.occurrence_refs)
            if (self.status is PlannedExpenseStatus.PLANNED and has_events or
                    self.status in (PlannedExpenseStatus.PARTIALLY_REALIZED, PlannedExpenseStatus.COMPLETED) and not has_events):
                raise ValueError("Gradual lifecycle status must agree with retained events")
            if self.fulfillment.committed_amount is not None and self.fulfillment.committed_amount.currency != currency:
                raise ValueError("Commitment currency must match plan")
        _correction_lineage(self.ref, self.observation)


@dataclass(frozen=True)
class ActualOccurrence:
    ref: Ref
    owner_id: str
    world_id: str
    kind: OccurrenceKind
    actual_amount: AmountEstimate
    timing: TemporalSpec
    related_entity_refs: Tuple[TypedRef, ...]
    settlement_status: SettlementStatus
    observation: ObservationMetadata
    source_position_ref: Optional[TypedRef] = None
    destination_position_ref: Optional[TypedRef] = None
    balance_effect_ref: Optional[TypedRef] = None
    superseded_by_ref: Optional[TypedRef] = None
    lineage: Optional[OccurrenceLineage] = None

    def __post_init__(self) -> None:
        _entity(self.ref, self.owner_id, self.world_id, self.observation, ObservationRole.ACTUAL)
        enum_value(self.kind, OccurrenceKind)
        if self.lineage is not None and type(self.lineage) is not OccurrenceLineage:
            raise ValueError("Typed occurrence lineage required")
        _amount_with_currency(self.actual_amount, "actual occurrence amount")
        _temporal(self.timing, "actual occurrence timing")
        typed_tuple(self.related_entity_refs, TypedRef, "related_entity_refs")
        allowed = (
            PlanningRecordKind.FUTURE_INFLOW.value,
            PlanningRecordKind.PLANNED_EXPENSE.value,
            PlanningRecordKind.WORLD_DECISION.value,
        )
        for value in self.related_entity_refs:
            _typed_one_of(value, allowed, "occurrence related entity")
        _unique_typed_ids(self.related_entity_refs, "related_entity_refs")
        object.__setattr__(self, "related_entity_refs", tuple(sorted(self.related_entity_refs, key=_typed_key)))
        enum_value(self.settlement_status, SettlementStatus)
        _optional_typed_kind(self.source_position_ref, WorldRecordKind.ASSET_POSITION.value, "occurrence source")
        _optional_typed_kind(self.destination_position_ref, WorldRecordKind.ASSET_POSITION.value, "occurrence destination")
        _optional_typed_kind(self.balance_effect_ref, PlanningRecordKind.BALANCE_EFFECT.value, "balance effect")
        _optional_typed_kind(self.superseded_by_ref, PlanningRecordKind.ACTUAL_OCCURRENCE.value, "superseding occurrence")
        if self.kind is OccurrenceKind.EXPENSE and self.destination_position_ref is not None:
            raise ValueError("expense occurrence cannot name a destination position")
        if self.kind is OccurrenceKind.INFLOW and self.source_position_ref is not None:
            raise ValueError("inflow occurrence cannot name a source position")
        if self.settlement_status is SettlementStatus.UNALLOCATED:
            if self.balance_effect_ref is not None or self.superseded_by_ref is not None:
                raise ValueError("unallocated occurrence cannot name an effect or successor")
        elif self.settlement_status in (SettlementStatus.APPLIED_TO_BALANCE, SettlementStatus.ALREADY_IN_BALANCE):
            required_position = self.source_position_ref if self.kind is OccurrenceKind.EXPENSE else self.destination_position_ref
            if required_position is None or self.balance_effect_ref is None or self.superseded_by_ref is not None:
                raise ValueError("settled occurrence requires its position and one balance effect")
        elif self.superseded_by_ref is None:
            raise ValueError("superseded occurrence must name its successor")
        _correction_lineage(self.ref, self.observation)


@dataclass(frozen=True)
class WorldDecision:
    ref: Ref
    owner_id: str
    world_id: str
    question: str
    subject: str
    source_state_ref: TypedRef
    status: DecisionStatus
    selected_option_ref: Optional[Ref]
    amount: Optional[AmountEstimate]
    timing: TemporalSpec
    related_refs: Tuple[TypedRef, ...]
    observation: ObservationMetadata

    def __post_init__(self) -> None:
        _entity(self.ref, self.owner_id, self.world_id, self.observation, ObservationRole.CONTEMPLATED)
        nonempty(self.question, "decision question")
        nonempty(self.subject, "decision subject")
        _typed_kind(self.source_state_ref, WorldRecordKind.WORLD_STATE.value, "decision source state")
        enum_value(self.status, DecisionStatus)
        if (self.status is DecisionStatus.SELECTED) != (self.selected_option_ref is not None):
            raise ValueError("exactly a selected decision must name the selected option")
        if self.selected_option_ref is not None and not isinstance(self.selected_option_ref, Ref):
            raise ValueError("selected_option_ref must be Ref")
        if self.amount is not None:
            _amount_with_currency(self.amount, "decision amount")
        _temporal(self.timing, "decision timing")
        typed_tuple(self.related_refs, TypedRef, "decision related_refs")
        allowed = (
            WorldRecordKind.ASSET_POSITION.value,
            WorldRecordKind.EARMARK.value,
            PlanningRecordKind.PLANNED_EXPENSE.value,
        )
        for value in self.related_refs:
            _typed_one_of(value, allowed, "decision related record")
        _unique_typed_ids(self.related_refs, "decision related_refs")
        object.__setattr__(self, "related_refs", tuple(sorted(self.related_refs, key=_typed_key)))
        _correction_lineage(self.ref, self.observation)


@dataclass(frozen=True)
class DecisionContext:
    ref: Ref
    owner_id: str
    world_id: str
    decision_refs: Tuple[TypedRef, ...]
    statement: str
    timing: TemporalSpec
    observation: ObservationMetadata
    lifecycle: ContextLifecycle = ContextLifecycle.ACTIVE

    def __post_init__(self) -> None:
        _entity(self.ref, self.owner_id, self.world_id, self.observation, ObservationRole.CONTEMPLATED)
        typed_tuple(self.decision_refs, TypedRef, "context decision_refs")
        if not self.decision_refs:
            raise ValueError("decision context must link at least one decision")
        for value in self.decision_refs:
            _typed_kind(value, PlanningRecordKind.WORLD_DECISION.value, "context decision")
        _unique_typed_ids(self.decision_refs, "context decision_refs")
        object.__setattr__(self, "decision_refs", tuple(sorted(self.decision_refs, key=_typed_key)))
        nonempty(self.statement, "decision context statement")
        _temporal(self.timing, "decision context timing")
        enum_value(self.lifecycle, ContextLifecycle)
        _correction_lineage(self.ref, self.observation)


@dataclass(frozen=True)
class WorldPlan:
    ref: Ref
    owner_id: str
    world_id: str
    source_state_ref: TypedRef
    previous_plan_ref: Optional[TypedRef]
    supporting_decision_refs: Tuple[TypedRef, ...]
    planned_expense_refs: Tuple[TypedRef, ...]
    goal_allocations: Tuple[GoalAllocation, ...]
    assumption_refs: Tuple[Ref, ...]
    status: PlanStatus
    evaluation_status: PlanEvaluationStatus
    observation: ObservationMetadata

    def __post_init__(self) -> None:
        _entity(self.ref, self.owner_id, self.world_id, self.observation, ObservationRole.PLANNED)
        _typed_kind(self.source_state_ref, WorldRecordKind.WORLD_STATE.value, "plan source state")
        if self.previous_plan_ref is not None and self.previous_plan_ref.record_kind not in (
            PlanningRecordKind.WORLD_PLAN.value,
            "legacy_world_plan",
        ):
            raise ValueError("previous plan must be a world plan or explicit legacy plan anchor")
        if self.previous_plan_ref is not None:
            previous = self.previous_plan_ref.ref
            if previous.id != self.ref.id or previous.version >= self.ref.version:
                raise ValueError("previous plan must be an earlier version of the same plan")
        for name, values, kind in (
            ("supporting_decision_refs", self.supporting_decision_refs, PlanningRecordKind.WORLD_DECISION.value),
            ("planned_expense_refs", self.planned_expense_refs, PlanningRecordKind.PLANNED_EXPENSE.value),
        ):
            typed_tuple(values, TypedRef, name)
            for value in values:
                _typed_kind(value, kind, name)
            _unique_typed_ids(values, name)
            object.__setattr__(self, name, tuple(sorted(values, key=_typed_key)))
        typed_tuple(self.goal_allocations, GoalAllocation, "goal_allocations")
        if len({item.goal_ref.id for item in self.goal_allocations}) != len(self.goal_allocations):
            raise ValueError("a plan cannot allocate to duplicate versions of one goal")
        object.__setattr__(self, "goal_allocations", tuple(sorted(self.goal_allocations, key=lambda item: _ref_key(item.goal_ref))))
        typed_tuple(self.assumption_refs, Ref, "assumption_refs")
        if len(set(self.assumption_refs)) != len(self.assumption_refs):
            raise ValueError("assumption_refs contains duplicates")
        object.__setattr__(self, "assumption_refs", tuple(sorted(self.assumption_refs, key=_ref_key)))
        enum_value(self.status, PlanStatus)
        enum_value(self.evaluation_status, PlanEvaluationStatus)
        _correction_lineage(self.ref, self.observation)


@dataclass(frozen=True)
class WorldPlanningBundle:
    """Validated Slice C relationships over one unchanged Slice B state."""

    assets: WorldAssetBundle
    plan: WorldPlan
    future_inflows: Tuple[FutureInflow, ...]
    planned_expenses: Tuple[PlannedExpense, ...]
    actual_occurrences: Tuple[ActualOccurrence, ...]
    decisions: Tuple[WorldDecision, ...]
    contexts: Tuple[DecisionContext, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.assets, WorldAssetBundle):
            raise ValueError("planning bundle assets must be WorldAssetBundle")
        if not isinstance(self.plan, WorldPlan):
            raise ValueError("planning bundle plan must be WorldPlan")
        for name, values, item_type in (
            ("future_inflows", self.future_inflows, FutureInflow),
            ("planned_expenses", self.planned_expenses, PlannedExpense),
            ("actual_occurrences", self.actual_occurrences, ActualOccurrence),
            ("decisions", self.decisions, WorldDecision),
            ("contexts", self.contexts, DecisionContext),
        ):
            typed_tuple(values, item_type, name)
            _unique_stable_ids(values, name)
            object.__setattr__(self, name, tuple(sorted(values, key=lambda item: _ref_key(item.ref))))
        self._validate_relationships()

    def _validate_relationships(self) -> None:
        owner_id = self.assets.state.owner_id
        world_id = self.assets.state.world_id
        for record in (
            self.plan,
            *self.future_inflows,
            *self.planned_expenses,
            *self.actual_occurrences,
            *self.decisions,
            *self.contexts,
        ):
            if record.owner_id != owner_id or record.world_id != world_id:
                raise ValueError("all planning records must share the asset state owner and world")

        expected_state = TypedRef(WorldRecordKind.WORLD_STATE.value, self.assets.state.ref)
        if self.plan.source_state_ref != expected_state:
            raise ValueError("plan source state must be the supplied current WorldState")
        for decision in self.decisions:
            if decision.source_state_ref != expected_state:
                raise ValueError("decision source state must be the supplied current WorldState")

        positions = {item.ref: item for item in self.assets.positions}
        earmarks = {item.ref: item for item in self.assets.earmarks}
        futures = {item.ref: item for item in self.future_inflows}
        expenses = {item.ref: item for item in self.planned_expenses}
        occurrences = {item.ref: item for item in self.actual_occurrences}
        decisions = {item.ref: item for item in self.decisions}

        _resolve_refs(self.plan.supporting_decision_refs, PlanningRecordKind.WORLD_DECISION.value, decisions, "plan decision")
        _resolve_refs(self.plan.planned_expense_refs, PlanningRecordKind.PLANNED_EXPENSE.value, expenses, "plan expense")
        expected_open_expenses = tuple(
            planning_ref(PlanningRecordKind.PLANNED_EXPENSE, item.ref)
            for item in self.planned_expenses
            if is_open(item)
        )
        if self.plan.planned_expense_refs != expected_open_expenses:
            raise ValueError("plan expense membership must contain exactly the still-planned expenses")

        for future in self.future_inflows:
            if future.destination_ref is not None:
                _resolve_ref(future.destination_ref, WorldRecordKind.ASSET_POSITION.value, positions, "future destination")
            if future.realization_ref is not None:
                occurrence = _resolve_ref(future.realization_ref, PlanningRecordKind.ACTUAL_OCCURRENCE.value, occurrences, "future realization")
                if occurrence.kind is not OccurrenceKind.INFLOW or planning_ref(PlanningRecordKind.FUTURE_INFLOW, future.ref) not in occurrence.related_entity_refs:
                    raise ValueError("realized future inflow must link a matching inflow occurrence")

        for expense in self.planned_expenses:
            if expense.decision_ref is not None:
                _resolve_ref(expense.decision_ref, PlanningRecordKind.WORLD_DECISION.value, decisions, "expense decision")
            for funding in expense.funding_links:
                if funding.source_ref.record_kind == WorldRecordKind.ASSET_POSITION.value:
                    source = _resolve_ref(funding.source_ref, WorldRecordKind.ASSET_POSITION.value, positions, "funding position")
                    currency = source.currency
                else:
                    earmark = _resolve_ref(funding.source_ref, WorldRecordKind.EARMARK.value, earmarks, "funding earmark")
                    source = positions[earmark.position_ref.ref]
                    currency = source.currency
                if funding.amount.currency is not currency or expense.expected_amount.currency is not currency:
                    raise ValueError("funding, expense, and source currencies must match")
            if expense.occurrence_ref is not None:
                occurrence = _resolve_ref(expense.occurrence_ref, PlanningRecordKind.ACTUAL_OCCURRENCE.value, occurrences, "expense occurrence")
                if occurrence.kind is not OccurrenceKind.EXPENSE or planning_ref(PlanningRecordKind.PLANNED_EXPENSE, expense.ref) not in occurrence.related_entity_refs:
                    raise ValueError("completed expense must link a matching expense occurrence")

        for decision in self.decisions:
            for related in decision.related_refs:
                if related.record_kind == WorldRecordKind.ASSET_POSITION.value:
                    _resolve_ref(related, WorldRecordKind.ASSET_POSITION.value, positions, "decision position")
                elif related.record_kind == WorldRecordKind.EARMARK.value:
                    _resolve_ref(related, WorldRecordKind.EARMARK.value, earmarks, "decision earmark")
                else:
                    _resolve_ref(related, PlanningRecordKind.PLANNED_EXPENSE.value, expenses, "decision expense")

        for context in self.contexts:
            _resolve_refs(context.decision_refs, PlanningRecordKind.WORLD_DECISION.value, decisions, "context decision")

        for occurrence in self.actual_occurrences:
            if occurrence.settlement_status is not SettlementStatus.UNALLOCATED:
                raise ValueError("Slice C bundle accepts only unallocated occurrences; settlement is deferred")
            if occurrence.source_position_ref is not None:
                source = _resolve_ref(occurrence.source_position_ref, WorldRecordKind.ASSET_POSITION.value, positions, "occurrence source")
                if source.currency is not occurrence.actual_amount.currency:
                    raise ValueError("occurrence source currency must match actual amount")
            if occurrence.destination_position_ref is not None:
                destination = _resolve_ref(occurrence.destination_position_ref, WorldRecordKind.ASSET_POSITION.value, positions, "occurrence destination")
                if destination.currency is not occurrence.actual_amount.currency:
                    raise ValueError("occurrence destination currency must match actual amount")
            for related in occurrence.related_entity_refs:
                if related.record_kind == PlanningRecordKind.FUTURE_INFLOW.value:
                    _resolve_ref(related, PlanningRecordKind.FUTURE_INFLOW.value, futures, "occurrence future")
                elif related.record_kind == PlanningRecordKind.PLANNED_EXPENSE.value:
                    _resolve_ref(related, PlanningRecordKind.PLANNED_EXPENSE.value, expenses, "occurrence expense")
                else:
                    _resolve_ref(related, PlanningRecordKind.WORLD_DECISION.value, decisions, "occurrence decision")

    def current_assets_by_currency(self) -> Tuple[CurrencyAmount, ...]:
        return self.assets.current_assets_by_currency()

    def earmarked_assets_by_currency(self) -> Tuple[CurrencyAmount, ...]:
        return self.assets.earmarked_assets_by_currency()

    def unallocated_assets_by_currency(self) -> Tuple[CurrencyAmount, ...]:
        return self.assets.unallocated_assets_by_currency()

    def expected_inflows_by_currency(self) -> Tuple[CurrencyAmount, ...]:
        return _group_amounts(
            item.expected_amount for item in self.future_inflows if item.status is FutureInflowStatus.EXPECTED
        )

    def planned_expenses_by_currency(self) -> Tuple[CurrencyAmount, ...]:
        return _group_amounts(
            item.expected_amount for item in self.planned_expenses if is_open(item)
        )

    def unsettled_occurrences(self) -> Tuple[ActualOccurrence, ...]:
        return tuple(item for item in self.actual_occurrences if item.settlement_status is SettlementStatus.UNALLOCATED)


def planning_ref(kind: PlanningRecordKind, ref: Ref) -> TypedRef:
    enum_value(kind, PlanningRecordKind)
    if not isinstance(ref, Ref):
        raise ValueError("planning_ref requires Ref")
    return TypedRef(kind.value, ref)


def _group_amounts(values: Iterable[AmountEstimate]) -> Tuple[CurrencyAmount, ...]:
    grouped: Dict[Currency, list] = {}
    for value in values:
        currency = _amount_with_currency(value, "derived planning amount")
        grouped.setdefault(currency, []).append(value)
    return tuple(
        CurrencyAmount(currency, sum_amount_estimates(amounts, currency))
        for currency, amounts in sorted(grouped.items(), key=lambda item: item[0].value)
    )


def _entity(ref: Ref, owner_id: str, world_id: str, observation: ObservationMetadata, role: ObservationRole) -> None:
    if not isinstance(ref, Ref):
        raise ValueError("entity ref must be Ref")
    nonempty(owner_id, "owner_id")
    nonempty(world_id, "world_id")
    if not isinstance(observation, ObservationMetadata) or observation.role is not role:
        raise ValueError(f"entity observation must have role {role.value}")


def _temporal(value: TemporalSpec, name: str) -> None:
    if not isinstance(value, TemporalSpec):
        raise ValueError(f"{name} must be TemporalSpec")


def _amount_with_currency(value: AmountEstimate, name: str) -> Currency:
    if not isinstance(value, AmountEstimate) or value.currency is None:
        raise ValueError(f"{name} must be AmountEstimate with explicit currency")
    return value.currency


def _typed_kind(value: TypedRef, kind: str, name: str) -> None:
    if not isinstance(value, TypedRef) or value.record_kind != kind:
        raise ValueError(f"{name} must be typed {kind} reference")


def _typed_one_of(value: TypedRef, kinds: Tuple[str, ...], name: str) -> None:
    if not isinstance(value, TypedRef) or value.record_kind not in kinds:
        raise ValueError(f"{name} has unsupported record kind")


def _optional_typed_kind(value: Optional[TypedRef], kind: str, name: str) -> None:
    if value is not None:
        _typed_kind(value, kind, name)


def _correction_lineage(ref: Ref, observation: ObservationMetadata) -> None:
    corrected = observation.corrects_ref
    if corrected is not None and (corrected.id != ref.id or corrected.version >= ref.version):
        raise ValueError("correction must reference an earlier version of the same stable record")


def _unique_stable_ids(values: Iterable[Any], name: str) -> None:
    seen = set()
    for value in values:
        if value.ref.id in seen:
            raise ValueError(f"{name} contains duplicate stable ID: {value.ref.id}")
        seen.add(value.ref.id)


def _unique_typed_ids(values: Iterable[TypedRef], name: str) -> None:
    seen = set()
    for value in values:
        key = value.record_kind, value.ref.id
        if key in seen:
            raise ValueError(f"{name} contains duplicate stable typed ID")
        seen.add(key)


def _ref_key(value: Ref) -> tuple:
    return value.id, value.version


def _typed_key(value: TypedRef) -> tuple:
    return value.record_kind, value.ref.id, value.ref.version


def _resolve_ref(value: TypedRef, kind: str, index: Mapping[Ref, Any], name: str) -> Any:
    _typed_kind(value, kind, name)
    result = index.get(value.ref)
    if result is None:
        raise ValueError(f"{name} references a missing or wrong-version record")
    return result


def _resolve_refs(values: Iterable[TypedRef], kind: str, index: Mapping[Ref, Any], name: str) -> None:
    for value in values:
        _resolve_ref(value, kind, index, name)


def _slice_a(value: Any) -> Dict[str, Any]:
    return serialize_world_value(value)


def _slice_a_value(data: Any, expected_type: type, name: str) -> Any:
    value = deserialize_world_value(data)
    if not isinstance(value, expected_type):
        raise ValueError(f"{name} has wrong Slice A value type")
    return value


def _future_data(value: FutureInflow) -> Dict[str, Any]:
    return {
        "ref": _slice_a(value.ref), "owner_id": value.owner_id, "world_id": value.world_id,
        "expected_amount": _slice_a(value.expected_amount), "purpose": value.purpose,
        "timing": _slice_a(value.timing), "destination_ref": _optional_slice_a(value.destination_ref),
        "status": value.status.value, "observation": _slice_a(value.observation),
        "realization_ref": _optional_slice_a(value.realization_ref),
    }


def _future_from_data(data: Any) -> FutureInflow:
    keys = {"ref", "owner_id", "world_id", "expected_amount", "purpose", "timing", "destination_ref", "status", "observation", "realization_ref"}
    data = _object(data, keys, "future inflow")
    return FutureInflow(
        _slice_a_value(data["ref"], Ref, "future ref"), data["owner_id"], data["world_id"],
        _slice_a_value(data["expected_amount"], AmountEstimate, "future amount"), data["purpose"],
        _slice_a_value(data["timing"], TemporalSpec, "future timing"),
        _optional_slice_a_value(data["destination_ref"], TypedRef, "future destination"),
        FutureInflowStatus(data["status"]),
        _slice_a_value(data["observation"], ObservationMetadata, "future observation"),
        _optional_slice_a_value(data["realization_ref"], TypedRef, "future realization"),
    )


def _funding_data(value: FundingLink) -> Dict[str, Any]:
    return {"source_ref": _slice_a(value.source_ref), "amount": _slice_a(value.amount)}


def _funding_from_data(data: Any) -> FundingLink:
    data = _object(data, {"source_ref", "amount"}, "funding link")
    return FundingLink(
        _slice_a_value(data["source_ref"], TypedRef, "funding source"),
        _slice_a_value(data["amount"], AmountEstimate, "funding amount"),
    )


def _expense_data(value: PlannedExpense) -> Dict[str, Any]:
    return {
        "ref": _slice_a(value.ref), "owner_id": value.owner_id, "world_id": value.world_id,
        "expected_amount": _slice_a(value.expected_amount), "purpose": value.purpose,
        "timing": _slice_a(value.timing), "funding_links": [_funding_data(item) for item in value.funding_links],
        "decision_ref": _optional_slice_a(value.decision_ref), "status": value.status.value,
        "observation": _slice_a(value.observation), "occurrence_ref": _optional_slice_a(value.occurrence_ref),
        **({"fulfillment": encode_lifecycle(value.fulfillment)} if value.fulfillment is not None else {}),
    }


def _expense_from_data(data: Any) -> PlannedExpense:
    keys = {"ref", "owner_id", "world_id", "expected_amount", "purpose", "timing", "funding_links", "decision_ref", "status", "observation", "occurrence_ref"}
    data = _object(data, keys | ({"fulfillment"} if "fulfillment" in data else set()), "planned expense")
    return PlannedExpense(
        _slice_a_value(data["ref"], Ref, "expense ref"), data["owner_id"], data["world_id"],
        _slice_a_value(data["expected_amount"], AmountEstimate, "expense amount"), data["purpose"],
        _slice_a_value(data["timing"], TemporalSpec, "expense timing"),
        tuple(_funding_from_data(item) for item in _list(data["funding_links"], "funding_links")),
        _optional_slice_a_value(data["decision_ref"], TypedRef, "expense decision"),
        PlannedExpenseStatus(data["status"]),
        _slice_a_value(data["observation"], ObservationMetadata, "expense observation"),
        _optional_slice_a_value(data["occurrence_ref"], TypedRef, "expense occurrence"),
        decode_lifecycle(data["fulfillment"]) if "fulfillment" in data else None,
    )


def _occurrence_data(value: ActualOccurrence) -> Dict[str, Any]:
    return {
        "ref": _slice_a(value.ref), "owner_id": value.owner_id, "world_id": value.world_id,
        "kind": value.kind.value, "actual_amount": _slice_a(value.actual_amount), "timing": _slice_a(value.timing),
        "related_entity_refs": [_slice_a(item) for item in value.related_entity_refs],
        "settlement_status": value.settlement_status.value, "observation": _slice_a(value.observation),
        "source_position_ref": _optional_slice_a(value.source_position_ref),
        "destination_position_ref": _optional_slice_a(value.destination_position_ref),
        "balance_effect_ref": _optional_slice_a(value.balance_effect_ref),
        "superseded_by_ref": _optional_slice_a(value.superseded_by_ref),
        **({"lineage": encode_lifecycle(value.lineage)} if value.lineage is not None else {}),
    }


def _occurrence_from_data(data: Any) -> ActualOccurrence:
    keys = {"ref", "owner_id", "world_id", "kind", "actual_amount", "timing", "related_entity_refs", "settlement_status", "observation", "source_position_ref", "destination_position_ref", "balance_effect_ref", "superseded_by_ref"}
    data = _object(data, keys | ({"lineage"} if "lineage" in data else set()), "actual occurrence")
    return ActualOccurrence(
        _slice_a_value(data["ref"], Ref, "occurrence ref"), data["owner_id"], data["world_id"],
        OccurrenceKind(data["kind"]), _slice_a_value(data["actual_amount"], AmountEstimate, "actual amount"),
        _slice_a_value(data["timing"], TemporalSpec, "occurrence timing"),
        tuple(_slice_a_value(item, TypedRef, "occurrence related ref") for item in _list(data["related_entity_refs"], "related_entity_refs")),
        SettlementStatus(data["settlement_status"]),
        _slice_a_value(data["observation"], ObservationMetadata, "occurrence observation"),
        _optional_slice_a_value(data["source_position_ref"], TypedRef, "occurrence source"),
        _optional_slice_a_value(data["destination_position_ref"], TypedRef, "occurrence destination"),
        _optional_slice_a_value(data["balance_effect_ref"], TypedRef, "balance effect"),
        _optional_slice_a_value(data["superseded_by_ref"], TypedRef, "occurrence successor"),
        decode_lifecycle(data["lineage"]) if "lineage" in data else None,
    )


def _decision_data(value: WorldDecision) -> Dict[str, Any]:
    return {
        "ref": _slice_a(value.ref), "owner_id": value.owner_id, "world_id": value.world_id,
        "question": value.question, "subject": value.subject, "source_state_ref": _slice_a(value.source_state_ref),
        "status": value.status.value, "selected_option_ref": _optional_slice_a(value.selected_option_ref),
        "amount": _optional_slice_a(value.amount), "timing": _slice_a(value.timing),
        "related_refs": [_slice_a(item) for item in value.related_refs], "observation": _slice_a(value.observation),
    }


def _decision_from_data(data: Any) -> WorldDecision:
    keys = {"ref", "owner_id", "world_id", "question", "subject", "source_state_ref", "status", "selected_option_ref", "amount", "timing", "related_refs", "observation"}
    data = _object(data, keys, "world decision")
    return WorldDecision(
        _slice_a_value(data["ref"], Ref, "decision ref"), data["owner_id"], data["world_id"],
        data["question"], data["subject"], _slice_a_value(data["source_state_ref"], TypedRef, "decision state"),
        DecisionStatus(data["status"]), _optional_slice_a_value(data["selected_option_ref"], Ref, "selected option"),
        _optional_slice_a_value(data["amount"], AmountEstimate, "decision amount"),
        _slice_a_value(data["timing"], TemporalSpec, "decision timing"),
        tuple(_slice_a_value(item, TypedRef, "decision related ref") for item in _list(data["related_refs"], "related_refs")),
        _slice_a_value(data["observation"], ObservationMetadata, "decision observation"),
    )


def _context_data(value: DecisionContext) -> Dict[str, Any]:
    return {
        "ref": _slice_a(value.ref), "owner_id": value.owner_id, "world_id": value.world_id,
        "decision_refs": [_slice_a(item) for item in value.decision_refs], "statement": value.statement,
        "timing": _slice_a(value.timing), "observation": _slice_a(value.observation), "lifecycle": value.lifecycle.value,
    }


def _context_from_data(data: Any) -> DecisionContext:
    keys = {"ref", "owner_id", "world_id", "decision_refs", "statement", "timing", "observation", "lifecycle"}
    data = _object(data, keys, "decision context")
    return DecisionContext(
        _slice_a_value(data["ref"], Ref, "context ref"), data["owner_id"], data["world_id"],
        tuple(_slice_a_value(item, TypedRef, "context decision") for item in _list(data["decision_refs"], "decision_refs")),
        data["statement"], _slice_a_value(data["timing"], TemporalSpec, "context timing"),
        _slice_a_value(data["observation"], ObservationMetadata, "context observation"),
        ContextLifecycle(data["lifecycle"]),
    )


def _allocation_data(value: GoalAllocation) -> Dict[str, Any]:
    return {"goal_ref": _slice_a(value.goal_ref), "monthly_contribution": _slice_a(value.monthly_contribution)}


def _allocation_from_data(data: Any) -> GoalAllocation:
    data = _object(data, {"goal_ref", "monthly_contribution"}, "goal allocation")
    return GoalAllocation(
        _slice_a_value(data["goal_ref"], Ref, "allocation goal"),
        _slice_a_value(data["monthly_contribution"], Money, "monthly contribution"),
    )


def _plan_data(value: WorldPlan) -> Dict[str, Any]:
    return {
        "ref": _slice_a(value.ref), "owner_id": value.owner_id, "world_id": value.world_id,
        "source_state_ref": _slice_a(value.source_state_ref), "previous_plan_ref": _optional_slice_a(value.previous_plan_ref),
        "supporting_decision_refs": [_slice_a(item) for item in value.supporting_decision_refs],
        "planned_expense_refs": [_slice_a(item) for item in value.planned_expense_refs],
        "goal_allocations": [_allocation_data(item) for item in value.goal_allocations],
        "assumption_refs": [_slice_a(item) for item in value.assumption_refs], "status": value.status.value,
        "evaluation_status": value.evaluation_status.value, "observation": _slice_a(value.observation),
    }


def _plan_from_data(data: Any) -> WorldPlan:
    keys = {"ref", "owner_id", "world_id", "source_state_ref", "previous_plan_ref", "supporting_decision_refs", "planned_expense_refs", "goal_allocations", "assumption_refs", "status", "evaluation_status", "observation"}
    data = _object(data, keys, "world plan")
    return WorldPlan(
        _slice_a_value(data["ref"], Ref, "plan ref"), data["owner_id"], data["world_id"],
        _slice_a_value(data["source_state_ref"], TypedRef, "plan source state"),
        _optional_slice_a_value(data["previous_plan_ref"], TypedRef, "previous plan"),
        tuple(_slice_a_value(item, TypedRef, "plan decision") for item in _list(data["supporting_decision_refs"], "supporting_decision_refs")),
        tuple(_slice_a_value(item, TypedRef, "plan expense") for item in _list(data["planned_expense_refs"], "planned_expense_refs")),
        tuple(_allocation_from_data(item) for item in _list(data["goal_allocations"], "goal_allocations")),
        tuple(_slice_a_value(item, Ref, "plan assumption") for item in _list(data["assumption_refs"], "assumption_refs")),
        PlanStatus(data["status"]), PlanEvaluationStatus(data["evaluation_status"]),
        _slice_a_value(data["observation"], ObservationMetadata, "plan observation"),
    )


def _bundle_data(value: WorldPlanningBundle) -> Dict[str, Any]:
    return {
        "assets": serialize_world_asset(value.assets), "plan": _plan_data(value.plan),
        "future_inflows": [_future_data(item) for item in value.future_inflows],
        "planned_expenses": [_expense_data(item) for item in value.planned_expenses],
        "actual_occurrences": [_occurrence_data(item) for item in value.actual_occurrences],
        "decisions": [_decision_data(item) for item in value.decisions],
        "contexts": [_context_data(item) for item in value.contexts],
    }


def _bundle_from_data(data: Any) -> WorldPlanningBundle:
    data = _object(data, {"assets", "plan", "future_inflows", "planned_expenses", "actual_occurrences", "decisions", "contexts"}, "world planning bundle")
    assets = deserialize_world_asset(data["assets"])
    if not isinstance(assets, WorldAssetBundle):
        raise ValueError("planning bundle assets have wrong Slice B type")
    return WorldPlanningBundle(
        assets, _plan_from_data(data["plan"]),
        tuple(_future_from_data(item) for item in _list(data["future_inflows"], "future_inflows")),
        tuple(_expense_from_data(item) for item in _list(data["planned_expenses"], "planned_expenses")),
        tuple(_occurrence_from_data(item) for item in _list(data["actual_occurrences"], "actual_occurrences")),
        tuple(_decision_from_data(item) for item in _list(data["decisions"], "decisions")),
        tuple(_context_from_data(item) for item in _list(data["contexts"], "contexts")),
    )


def serialize_world_planning(value: Any) -> Dict[str, Any]:
    encoders = (
        (FutureInflow, "future_inflow", _future_data),
        (PlannedExpense, "planned_expense", _expense_data),
        (ActualOccurrence, "actual_occurrence", _occurrence_data),
        (WorldDecision, "world_decision", _decision_data),
        (DecisionContext, "decision_context", _context_data),
        (WorldPlan, "world_plan", _plan_data),
        (WorldPlanningBundle, "world_planning_bundle", _bundle_data),
        (FundingLink, "funding_link", _funding_data),
    )
    for value_type, type_name, encoder in encoders:
        if isinstance(value, value_type):
            expanded = ((type(value) is PlannedExpense and value.fulfillment is not None) or
                        (type(value) is ActualOccurrence and value.lineage is not None) or
                        (type(value) is WorldPlanningBundle and any(
                            e.fulfillment is not None for e in value.planned_expenses)) or
                        (type(value) is WorldPlanningBundle and any(o.lineage is not None for o in value.actual_occurrences)))
            return {"schema_version": EXPANDED_PLANNING_SCHEMA if expanded else WORLD_PLANNING_SCHEMA_VERSION,
                    "value_type": type_name, "value": encoder(value)}
    raise TypeError(f"Unsupported Slice C value: {type(value)}")


def deserialize_world_planning(data: Mapping[str, Any]) -> Any:
    envelope = _object(data, {"schema_version", "value_type", "value"}, "world planning envelope")
    if envelope["schema_version"] not in (WORLD_PLANNING_SCHEMA_VERSION, EXPANDED_PLANNING_SCHEMA):
        raise ValueError("unsupported world planning schema version")
    decoders = {
        "future_inflow": _future_from_data, "planned_expense": _expense_from_data,
        "actual_occurrence": _occurrence_from_data, "world_decision": _decision_from_data,
        "decision_context": _context_from_data, "world_plan": _plan_from_data,
        "world_planning_bundle": _bundle_from_data, "funding_link": _funding_from_data,
    }
    decoder = decoders.get(envelope["value_type"])
    if decoder is None:
        raise ValueError("unsupported world planning type")
    result = decoder(envelope["value"])
    if serialize_world_planning(result)["schema_version"] != envelope["schema_version"]:
        raise ValueError("Planning extension must use its exact schema version")
    return result


def dumps_world_planning(value: Any) -> str:
    return json.dumps(
        serialize_world_planning(value), ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False,
    )


def loads_world_planning(payload: str) -> Any:
    if not isinstance(payload, str):
        raise ValueError("world planning JSON must be text")
    try:
        data = json.loads(payload, parse_constant=lambda value: _reject_constant(value))
    except (json.JSONDecodeError, TypeError) as error:
        raise ValueError("invalid world planning JSON") from error
    return deserialize_world_planning(data)


def _optional_slice_a(value: Any) -> Optional[Dict[str, Any]]:
    return None if value is None else _slice_a(value)


def _optional_slice_a_value(data: Any, expected_type: type, name: str) -> Any:
    return None if data is None else _slice_a_value(data, expected_type, name)


def _object(data: Any, keys: set, name: str) -> Dict[str, Any]:
    if not isinstance(data, dict) or set(data) != keys:
        raise ValueError(f"{name} must contain exactly: {', '.join(sorted(keys))}")
    return data


def _list(value: Any, name: str) -> list:
    if not isinstance(value, list):
        raise ValueError(f"{name} must be a list")
    return value


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant is not supported: {value}")

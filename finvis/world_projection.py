"""I1 immutable read projections. No writer, provider or financial policy."""

from dataclasses import dataclass, fields, is_dataclass
from datetime import date, datetime
from enum import Enum
import json
from typing import Optional, Tuple

from .domain import Currency, GoalAllocation, Ref
from .store import Rejected
from .world_commands import WorldSnapshot, MonthlyEssentialOutflow, record_ref, ref_key
from .world_domain import sum_amount_estimates, amount_for_currency
from .world_planning import (FutureInflow, PlannedExpense, WorldDecision,
    DecisionContext, ActualOccurrence, FundingLink, FutureInflowStatus,
    PlannedExpenseStatus)
from .world_values import AmountEstimate, AmountPrecision, ObservationMetadata, TemporalSpec, TypedRef
from .world_serialization import serialize_world_mutation, deserialize_world_mutation
from .world_store import current_assets, validate_world
from .world_liquidity import pending_liquidity


SCHEMA = 'finvis.world-projection.v0.1'


class ProjectionError(Rejected):
    """No projection was produced; callers must not substitute demo values."""


class CoverageStatus(Enum):
    AVAILABLE = 'available'
    UNKNOWN = 'unknown'
    INSUFFICIENT_INPUT = 'insufficient_input'
    ENGINE_REQUIRED = 'engine_required'
    NOT_SUPPORTED = 'not_supported'
    DEMO_ONLY = 'demo_only'


@dataclass(frozen=True)
class Coverage:
    status: CoverageStatus
    reasons: Tuple[str, ...]
    input_refs: Tuple[TypedRef, ...] = ()


@dataclass(frozen=True)
class AmountView:
    value: AmountEstimate
    coverage: Coverage


@dataclass(frozen=True)
class CurrencySummary:
    currency: Currency
    current_assets: AmountView
    earmarked: AmountView
    unallocated: AmountView


@dataclass(frozen=True)
class AccountView:
    ref: TypedRef
    label: str
    institution_label: Optional[str]
    observation: ObservationMetadata


@dataclass(frozen=True)
class PositionView:
    ref: TypedRef
    account_ref: TypedRef
    partition_key: str
    balance: AmountEstimate
    access_status: str
    access_reason: str
    access_observation: ObservationMetadata
    observation: ObservationMetadata


@dataclass(frozen=True)
class EarmarkView:
    ref: TypedRef
    position_ref: TypedRef
    amount: AmountEstimate
    purpose: str
    role: str
    emergency_use_designated: bool
    observation: ObservationMetadata


@dataclass(frozen=True)
class TimedAmountView:
    ref: TypedRef
    label: str
    amount: AmountEstimate
    timing: TemporalSpec
    status: str
    observation: ObservationMetadata
    related_refs: Tuple[TypedRef, ...]
    funding_links: Tuple[FundingLink, ...] = ()


@dataclass(frozen=True)
class CurrencyTotal:
    currency: Currency
    amount: AmountView


@dataclass(frozen=True)
class DecisionView:
    ref: TypedRef
    subject: str
    question: str
    status: str
    amount: Optional[AmountEstimate]
    timing: TemporalSpec
    source_state_ref: TypedRef
    selected_option_ref: Optional[Ref]
    related_refs: Tuple[TypedRef, ...]
    observation: ObservationMetadata


@dataclass(frozen=True)
class ContextView:
    ref: TypedRef
    statement: str
    decision_refs: Tuple[TypedRef, ...]
    timing: TemporalSpec
    lifecycle: str
    observation: ObservationMetadata


@dataclass(frozen=True)
class OccurrenceView:
    ref: TypedRef
    kind: str
    amount: AmountEstimate
    timing: TemporalSpec
    settlement_status: str
    source_position_ref: Optional[TypedRef]
    destination_position_ref: Optional[TypedRef]
    balance_effect_ref: Optional[TypedRef]
    related_refs: Tuple[TypedRef, ...]
    observation: ObservationMetadata


@dataclass(frozen=True)
class PlanView:
    ref: TypedRef
    source_state_ref: TypedRef
    previous_plan_ref: Optional[TypedRef]
    status: str
    evaluation_status: str
    decision_refs: Tuple[TypedRef, ...]
    expense_refs: Tuple[TypedRef, ...]
    goal_allocations: Tuple[GoalAllocation, ...]
    assumption_refs: Tuple[Ref, ...]
    observation: ObservationMetadata


@dataclass(frozen=True)
class PendingItem:
    code: str
    reason: str
    related_refs: Tuple[TypedRef, ...]


@dataclass(frozen=True)
class WorldProjection:
    schema_version: str
    owner_id: str
    world_id: str
    world_ref: Ref
    state_ref: Ref
    revision: int
    coverage_note: str
    state_observation: ObservationMetadata
    assets_coverage: Coverage
    currencies: Tuple[CurrencySummary, ...]
    accounts: Tuple[AccountView, ...]
    positions: Tuple[PositionView, ...]
    earmarks: Tuple[EarmarkView, ...]
    future_inflows: Tuple[TimedAmountView, ...]
    expected_inflows_by_currency: Tuple[CurrencyTotal, ...]
    planned_expenses: Tuple[TimedAmountView, ...]
    open_expenses_by_currency: Tuple[CurrencyTotal, ...]
    decisions: Tuple[DecisionView, ...]
    contexts: Tuple[ContextView, ...]
    occurrences: Tuple[OccurrenceView, ...]
    plan: PlanView
    receipt_refs: Tuple[Ref, ...]
    pending: Tuple[PendingItem, ...]
    essential_monthly_outflow: Coverage
    emergency_liquidity: Coverage
    runway: Coverage
    goal_details: Coverage
    employment: Coverage
    if_lines: Coverage
    monthly_essential_observations: Tuple[TimedAmountView, ...] = ()


def _refs(values):
    return tuple(sorted(set(values), key=ref_key))


def _amount(value, refs):
    unknown = value.precision is AmountPrecision.UNKNOWN
    return AmountView(value, Coverage(
        CoverageStatus.UNKNOWN if unknown else CoverageStatus.AVAILABLE,
        ('reported_scope_only',) + ((value.missing_reason,) if unknown else ()), _refs(refs)))


from .world_fulfillment import is_open


def _totals(records):
    currencies = sorted({r.expected_amount.currency for r in records}, key=lambda c: c.value)
    return tuple(CurrencyTotal(c, _amount(
        sum_amount_estimates((r.expected_amount for r in records if r.expected_amount.currency is c), c),
        (record_ref(r) for r in records if r.expected_amount.currency is c))) for c in currencies)


def checked_snapshot(snapshot):
    """Validate closed values and the current graph, including D/E history.

    This is a read guard, not a replay verifier or authorization/import path.
    """
    if type(snapshot) is not WorldSnapshot:
        raise ProjectionError('invalid_snapshot')
    try:
        # Reuse the closed constructors for nested type/currency/value checks.
        deserialize_world_mutation(serialize_world_mutation(snapshot))
        pending = validate_world(snapshot)
        if not (snapshot.revision == len(snapshot.receipts) == len(snapshot.commands)
                == len(snapshot.authorizations) == len(snapshot.worlds) - 1):
            raise ValueError('Inconsistent publication history')
        for kind, ref in (('world_state', snapshot.world.state_ref), ('world_plan', snapshot.world.plan_ref)):
            if ref.version != max(r.ref.version for r in snapshot.records
                                  if record_ref(r).record_kind == kind and r.ref.id == ref.id):
                raise ValueError('Historical current pointer')
        return pending
    except (ValueError, TypeError, AttributeError, KeyError, StopIteration) as error:
        raise ProjectionError('invalid_snapshot') from error


def project_world(snapshot: WorldSnapshot) -> WorldProjection:
    """Derive one immutable projection; never read another snapshot or mutate."""
    try:
        issues = checked_snapshot(snapshot)
        root = snapshot.world
        assets = current_assets(snapshot)
        members = tuple(sorted((snapshot.get(ref) for ref in root.members), key=lambda r: ref_key(record_ref(r))))
        select = lambda kind: tuple(r for r in members if type(r) is kind)
        futures, expenses = select(FutureInflow), select(PlannedExpense)
        monthly = select(MonthlyEssentialOutflow)
        essentials_reason = 'essentials_coverage_unverified' if monthly else 'native_essentials_not_recorded'
        currencies = sorted({p.currency for p in assets.positions}, key=lambda c: c.value)
        totals, reserved, free = (assets.current_assets_by_currency(),
                                  assets.earmarked_assets_by_currency(), assets.unallocated_assets_by_currency())
        summaries = []
        for currency in currencies:
            positions = tuple(p for p in assets.positions if p.currency is currency)
            marks = tuple(e for e in assets.earmarks if e.allocation.currency is currency)
            refs = tuple(record_ref(p) for p in positions)
            all_refs = (*refs, *(record_ref(e) for e in marks))
            summaries.append(CurrencySummary(currency,
                _amount(amount_for_currency(totals, currency), refs),
                _amount(amount_for_currency(reserved, currency), all_refs),
                _amount(amount_for_currency(free, currency), all_refs)))
        pending = list(issues)
        for currency in sorted({p.currency for p in assets.positions} |
                               {o.actual_amount.currency for o in select(ActualOccurrence)}, key=lambda c: c.value):
            pending.extend(pending_liquidity(snapshot, currency).reasons)
        items = { (p.code, p.rationale, _refs(p.related_refs)) for p in pending }
        pending_views = tuple(PendingItem(code, reason, refs) for code, reason, refs in
                              sorted(items, key=lambda p: (p[0], p[1], tuple(ref_key(r) for r in p[2]))))
        plan = snapshot.get(TypedRef('world_plan', root.plan_ref))
        unknown = any(p.balance.precision is AmountPrecision.UNKNOWN for p in assets.positions)
        asset_status = CoverageStatus.UNKNOWN if unknown or not assets.positions else CoverageStatus.AVAILABLE
        asset_reasons = ('reported_scope_only',) + (('no_positions_reported',) if not assets.positions else ())
        if unknown:
            asset_reasons += ('unknown_position_balance',)
        return WorldProjection(SCHEMA, root.owner_id, root.world_id, root.ref, root.state_ref,
            snapshot.revision, assets.state.coverage_note, assets.state.snapshot_observation,
            Coverage(asset_status, asset_reasons, assets.state.position_refs), tuple(summaries),
            tuple(AccountView(record_ref(a), a.label, a.institution_label, a.observation) for a in assets.accounts),
            tuple(PositionView(record_ref(p), p.account_ref, p.partition_key, p.balance,
                p.access.status.value, p.access.rationale, p.access.observation, p.observation) for p in assets.positions),
            tuple(EarmarkView(record_ref(e), e.position_ref, e.allocation, e.purpose, e.role.value,
                e.emergency_use_designated, e.observation) for e in assets.earmarks),
            tuple(TimedAmountView(record_ref(f), f.purpose, f.expected_amount, f.timing, f.status.value,
                f.observation, _refs(r for r in (f.destination_ref, f.realization_ref) if r)) for f in futures),
            _totals(tuple(f for f in futures if f.status is FutureInflowStatus.EXPECTED)),
            tuple(TimedAmountView(record_ref(e), e.purpose, e.expected_amount, e.timing, e.status.value,
                e.observation, _refs(r for r in (e.decision_ref, e.occurrence_ref, *(e.fulfillment.occurrence_refs if e.fulfillment else ())) if r), e.funding_links) for e in expenses),
            _totals(tuple(e for e in expenses if is_open(e))),
            tuple(DecisionView(record_ref(d), d.subject, d.question, d.status.value, d.amount, d.timing,
                d.source_state_ref, d.selected_option_ref, d.related_refs, d.observation) for d in select(WorldDecision)),
            tuple(ContextView(record_ref(c), c.statement, c.decision_refs, c.timing, c.lifecycle.value,
                c.observation) for c in select(DecisionContext)),
            tuple(OccurrenceView(record_ref(o), o.kind.value, o.actual_amount, o.timing,
                o.settlement_status.value, o.source_position_ref, o.destination_position_ref,
                o.balance_effect_ref, o.related_entity_refs, o.observation) for o in select(ActualOccurrence)),
            PlanView(record_ref(plan), plan.source_state_ref, plan.previous_plan_ref, plan.status.value,
                plan.evaluation_status.value, plan.supporting_decision_refs, plan.planned_expense_refs,
                plan.goal_allocations, plan.assumption_refs, plan.observation),
            tuple(r.ref for r in snapshot.receipts), pending_views,
            Coverage(CoverageStatus.INSUFFICIENT_INPUT if monthly else CoverageStatus.UNKNOWN,
                     (essentials_reason,), tuple(record_ref(m) for m in monthly)),
            Coverage(CoverageStatus.ENGINE_REQUIRED, ('eligibility_policy_pending',)),
            Coverage(CoverageStatus.INSUFFICIENT_INPUT, (essentials_reason, 'eligibility_policy_pending')),
            Coverage(CoverageStatus.NOT_SUPPORTED, ('native_goal_registry_not_implemented',)),
            Coverage(CoverageStatus.UNKNOWN, ('native_employment_not_recorded',)),
            Coverage(CoverageStatus.DEMO_ONLY, ('native_if_lines_not_implemented',)),
            tuple(TimedAmountView(record_ref(m), 'Monthly essential outflow', m.amount,
                m.observation.as_of, m.coverage, m.observation, ()) for m in monthly))
    except ProjectionError:
        raise
    except (ValueError, TypeError, AttributeError, KeyError, StopIteration) as error:
        raise ProjectionError('projection_failed') from error


def projection_data(value: WorldProjection):
    """Detached JSON-compatible output; no decoder, importer or authority."""
    if type(value) is not WorldProjection or value.schema_version != SCHEMA:
        raise ProjectionError('unsupported_projection')

    def encode(item):
        if isinstance(item, Enum):
            return item.value
        if item is None or type(item) in (str, int, bool):
            return item
        if isinstance(item, (date, datetime)):
            return item.isoformat()
        if type(item) is tuple:
            return [encode(part) for part in item]
        if is_dataclass(item):
            return {field.name: encode(getattr(item, field.name)) for field in fields(item)}
        raise ProjectionError('unsupported_projection_value')
    return encode(value)


def projection_json(value: WorldProjection):
    return json.dumps(projection_data(value), ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False)

"""I6.1 read-only User World input manifest and capability gates.

No financial calculation, policy, writer, Demo fallback, or result cache lives here.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Optional, Tuple

from .domain import Currency, Ref
from .world_application import ContextKind, ReadVersion, UserWorldAuthority, UserWorldRead, WorldContext
from .world_projection import Coverage, CoverageStatus, WorldProjection
from .world_values import AmountEstimate, AmountPrecision, ObservationMetadata, TemporalKind, TemporalSpec, TypedRef


class InputStatus(Enum):
    AVAILABLE = 'available'
    UNKNOWN = 'unknown'
    INSUFFICIENT_INPUT = 'insufficient_input'
    POLICY_REQUIRED = 'policy_required'
    UNSUPPORTED = 'unsupported'


class InputKind(Enum):
    CURRENT_ASSETS = 'current_assets'
    ASSET_POSITION = 'asset_position'
    EARMARKED = 'earmarked'
    EARMARK = 'earmark'
    UNALLOCATED = 'unallocated'
    FUTURE_INFLOW = 'future_inflow'
    PLANNED_EXPENSE = 'planned_expense'
    DECISION = 'decision'
    ACTUAL_OCCURRENCE = 'actual_occurrence'
    PLAN = 'plan'
    GOAL_DETAILS = 'goal_details'
    MONTHLY_ESSENTIALS = 'monthly_essentials'
    EMERGENCY_LIQUIDITY = 'emergency_liquidity'
    EMPLOYMENT = 'employment'
    IF_LINES = 'if_lines'


class Capability(Enum):
    REPORTED_ASSETS = 'reported_assets'
    RUNWAY = 'runway'
    SOLVENCY = 'solvency'
    RESILIENCE = 'resilience'
    GOAL_COVERAGE = 'goal_coverage'
    DECISION_ANALYSIS = 'decision_analysis'
    DECISION_DELTA = 'decision_delta'
    DECISION_BOUNDARY = 'decision_boundary'
    TRADE_OFFS = 'trade_offs'
    IF_LINES = 'if_lines'
    FUTURE_SPACE = 'future_space'
    MATERIALITY = 'materiality'
    REPLANNING = 'replanning'


class GateStatus(Enum):
    READY = 'ready'
    BLOCKED = 'blocked'


@dataclass(frozen=True)
class ManifestInput:
    kind: InputKind
    currency: Optional[Currency]
    status: InputStatus
    value: Optional[AmountEstimate] = None
    timing: Optional[TemporalSpec] = None
    observation: Optional[ObservationMetadata] = None
    refs: Tuple[TypedRef, ...] = ()
    reasons: Tuple[str, ...] = ()


@dataclass(frozen=True)
class GateReason:
    category: InputStatus
    code: str
    refs: Tuple[TypedRef, ...] = ()


@dataclass(frozen=True)
class CapabilityGate:
    capability: Capability
    currency: Optional[Currency]
    status: GateStatus
    reasons: Tuple[GateReason, ...]
    dependencies: Tuple[TypedRef, ...]


@dataclass(frozen=True)
class EngineInputManifest:
    context: WorldContext
    version: Optional[ReadVersion]
    inputs: Tuple[ManifestInput, ...]
    gates: Tuple[CapabilityGate, ...]


def _coverage_status(coverage: Coverage) -> InputStatus:
    return {
        CoverageStatus.AVAILABLE: InputStatus.AVAILABLE,
        CoverageStatus.UNKNOWN: InputStatus.UNKNOWN,
        CoverageStatus.INSUFFICIENT_INPUT: InputStatus.INSUFFICIENT_INPUT,
        CoverageStatus.ENGINE_REQUIRED: InputStatus.POLICY_REQUIRED,
        CoverageStatus.NOT_SUPPORTED: InputStatus.UNSUPPORTED,
        CoverageStatus.DEMO_ONLY: InputStatus.UNSUPPORTED,
    }[coverage.status]


def _amount_input(kind, currency, view):
    return ManifestInput(kind, currency, _coverage_status(view.coverage), view.value,
                         refs=view.coverage.input_refs, reasons=view.coverage.reasons)


def _record_input(kind, currency, value, timing, observation, refs, reasons=(), incomplete=False):
    status = (InputStatus.UNKNOWN if value is None or value.precision is AmountPrecision.UNKNOWN
              else InputStatus.INSUFFICIENT_INPUT if incomplete else InputStatus.AVAILABLE)
    if value is None:
        reasons = (*reasons, 'amount_not_recorded')
    elif value.precision is AmountPrecision.UNKNOWN:
        reasons = (*reasons, value.missing_reason)
    return ManifestInput(kind, currency, status, value, timing, observation, refs, tuple(reasons))


def _refs(*groups):
    return tuple(sorted(set(ref for group in groups for ref in group),
                        key=lambda ref: (ref.record_kind, ref.ref.id, ref.ref.version)))


def _reason(category, code, refs=()):
    return GateReason(category, code, refs)


def _gate(capability, currency, reasons, refs=()):
    unique = tuple(sorted(set(reasons), key=lambda r: (r.category.value, r.code,
                    tuple((ref.record_kind, ref.ref.id, ref.ref.version) for ref in r.refs))))
    return CapabilityGate(capability, currency,
                          GateStatus.BLOCKED if unique else GateStatus.READY, unique, _refs(refs, *(r.refs for r in unique)))


def _missing_gate(capability, currency, code='user_world_not_initialized'):
    return _gate(capability, currency, (_reason(InputStatus.UNKNOWN, code),))


def _world_gates(projection, inputs):
    by_kind = lambda kind: tuple(item for item in inputs if item.kind is kind)
    currencies = sorted({item.currency for item in inputs if item.currency is not None}, key=lambda c: c.value)
    gates = []
    for currency in currencies:
        assets = next((item for item in by_kind(InputKind.CURRENT_ASSETS) if item.currency is currency), None)
        asset_refs = assets.refs if assets else ()
        eligibility_refs = _refs(*(item.refs for item in inputs if item.currency is currency
                                   and item.kind in (InputKind.ASSET_POSITION, InputKind.EARMARK,
                                                     InputKind.ACTUAL_OCCURRENCE)))
        asset_reasons = []
        if assets is None:
            asset_reasons.append(_reason(InputStatus.UNKNOWN, 'current_assets_not_reported'))
        elif assets.status is not InputStatus.AVAILABLE:
            asset_reasons.append(_reason(assets.status, 'current_assets_unknown', asset_refs))
        gates.append(_gate(Capability.REPORTED_ASSETS, currency, asset_reasons, asset_refs))

        reasons = list(asset_reasons)
        if assets is not None and assets.value.precision is not AmountPrecision.EXACT_REPORTED:
            reasons.append(_reason(InputStatus.INSUFFICIENT_INPUT, 'exact_assets_not_available', asset_refs))
        reasons.extend((
            _reason(InputStatus.INSUFFICIENT_INPUT, 'essentials_coverage_unverified')
            if any(m.amount.currency is currency for m in projection.monthly_essential_observations)
            else _reason(InputStatus.UNKNOWN, 'native_essentials_not_recorded'),
            _reason(InputStatus.POLICY_REQUIRED, 'eligibility_policy_pending', eligibility_refs),
        ))
        for position in (item for item in by_kind(InputKind.ASSET_POSITION) if item.currency is currency):
            if 'current' not in position.reasons or position.value.precision is AmountPrecision.UNKNOWN:
                reasons.append(_reason(InputStatus.INSUFFICIENT_INPUT, 'balance_unknown_or_stale', position.refs))
            if 'accessible' not in position.reasons:
                reasons.append(_reason(InputStatus.INSUFFICIENT_INPUT, 'access_unresolved_or_restricted', position.refs))
        affected = _refs(*(item.refs for item in by_kind(InputKind.ACTUAL_OCCURRENCE) if item.currency is currency
                           and 'unallocated' in item.reasons))
        if affected:
            reasons.append(_reason(InputStatus.INSUFFICIENT_INPUT, 'unsettled_occurrence', affected))
        gates.append(_gate(Capability.RUNWAY, currency, reasons, eligibility_refs))

    if not currencies:
        gates.append(_missing_gate(Capability.REPORTED_ASSETS, None, 'current_assets_not_reported'))
        gates.append(_gate(Capability.RUNWAY, None, (
            _reason(InputStatus.UNKNOWN, 'current_assets_not_reported'),
            _reason(InputStatus.UNKNOWN, 'native_essentials_not_recorded'),
            _reason(InputStatus.POLICY_REQUIRED, 'eligibility_policy_pending'))))

    relevant = {
        Capability.SOLVENCY: (InputKind.CURRENT_ASSETS, InputKind.ASSET_POSITION, InputKind.FUTURE_INFLOW, InputKind.PLANNED_EXPENSE, InputKind.ACTUAL_OCCURRENCE),
        Capability.RESILIENCE: (InputKind.CURRENT_ASSETS, InputKind.ASSET_POSITION, InputKind.EARMARK, InputKind.ACTUAL_OCCURRENCE),
        Capability.GOAL_COVERAGE: (InputKind.PLAN, InputKind.GOAL_DETAILS, InputKind.EARMARK),
        Capability.DECISION_ANALYSIS: (InputKind.DECISION, InputKind.PLAN, InputKind.PLANNED_EXPENSE, InputKind.CURRENT_ASSETS),
        Capability.DECISION_DELTA: (InputKind.DECISION, InputKind.PLAN, InputKind.PLANNED_EXPENSE, InputKind.CURRENT_ASSETS),
        Capability.DECISION_BOUNDARY: (InputKind.DECISION, InputKind.PLAN, InputKind.CURRENT_ASSETS),
        Capability.TRADE_OFFS: (InputKind.DECISION, InputKind.PLAN, InputKind.GOAL_DETAILS, InputKind.CURRENT_ASSETS),
        Capability.IF_LINES: (InputKind.DECISION, InputKind.PLAN, InputKind.FUTURE_INFLOW, InputKind.PLANNED_EXPENSE),
        Capability.FUTURE_SPACE: (InputKind.DECISION, InputKind.PLAN, InputKind.FUTURE_INFLOW, InputKind.PLANNED_EXPENSE),
        Capability.MATERIALITY: (InputKind.PLAN, InputKind.ACTUAL_OCCURRENCE, InputKind.CURRENT_ASSETS),
        Capability.REPLANNING: (InputKind.PLAN, InputKind.DECISION, InputKind.ACTUAL_OCCURRENCE),
    }
    policy = lambda code: _reason(InputStatus.POLICY_REQUIRED, code)
    missing = lambda code: _reason(InputStatus.UNKNOWN, code)
    unsupported = lambda code: _reason(InputStatus.UNSUPPORTED, code)
    specs = (
        (Capability.SOLVENCY, (missing('mandatory_obligation_coverage_missing'), policy('solvency_rule_pending'))),
        (Capability.RESILIENCE, (missing('essentials_coverage_unverified' if projection.monthly_essential_observations else 'native_essentials_not_recorded'), policy('eligibility_policy_pending'), policy('resilience_calibration_pending'))),
        (Capability.GOAL_COVERAGE, (unsupported('native_goal_registry_not_implemented'), policy('goal_coverage_semantics_pending'))),
        (Capability.DECISION_ANALYSIS, (unsupported('native_decision_alternatives_not_implemented'), policy('decision_scenario_policy_pending'))),
        (Capability.DECISION_DELTA, (unsupported('native_decision_alternatives_not_implemented'), policy('decision_comparison_policy_pending'))),
        (Capability.DECISION_BOUNDARY, (unsupported('native_decision_alternatives_not_implemented'), policy('decision_boundary_calibration_pending'))),
        (Capability.TRADE_OFFS, (unsupported('native_decision_alternatives_not_implemented'), policy('trade_off_rule_pending'))),
        (Capability.IF_LINES, (unsupported('native_if_lines_not_implemented'), policy('future_scenario_policy_pending'))),
        (Capability.FUTURE_SPACE, (unsupported('native_if_lines_not_implemented'), policy('future_scenario_policy_pending'))),
        (Capability.MATERIALITY, (unsupported('generic_materiality_not_implemented'), policy('materiality_policy_pending'))),
        (Capability.REPLANNING, (unsupported('generic_replanning_not_implemented'), policy('replanning_policy_pending'))),
    )
    for capability, base in specs:
        reasons = list(base)
        if not by_kind(InputKind.CURRENT_ASSETS) and capability in (
                Capability.SOLVENCY, Capability.RESILIENCE, Capability.DECISION_ANALYSIS,
                Capability.DECISION_DELTA, Capability.DECISION_BOUNDARY, Capability.TRADE_OFFS):
            reasons.append(missing('current_assets_not_reported'))
        if capability in (Capability.DECISION_ANALYSIS, Capability.DECISION_DELTA, Capability.DECISION_BOUNDARY,
                          Capability.TRADE_OFFS, Capability.IF_LINES, Capability.FUTURE_SPACE) and not projection.decisions:
            reasons.append(missing('no_native_decision_recorded'))
        if len(currencies) > 1 and capability in (Capability.SOLVENCY, Capability.RESILIENCE,
                                                  Capability.GOAL_COVERAGE, Capability.DECISION_BOUNDARY,
                                                  Capability.TRADE_OFFS, Capability.FUTURE_SPACE):
            reasons.append(unsupported('unified_currency_analysis_unavailable'))
        gates.append(_gate(capability, None, reasons,
                           _refs(*(item.refs for item in inputs if item.kind in relevant[capability]))))
    return tuple(gates)


def manifest_from_read(read: UserWorldRead) -> EngineInputManifest:
    """Pure mapping from one validated I1 publication; no second source read."""
    if type(read) is not UserWorldRead or read.context.kind is not ContextKind.USER:
        raise ValueError('I6.1 requires an explicit User World read')
    if not read.initialized:
        if read.version is not None or read.snapshot is not None or read.projection is not None:
            raise ValueError('Inconsistent uninitialized User read')
        kinds = (InputKind.CURRENT_ASSETS, InputKind.MONTHLY_ESSENTIALS, InputKind.EMERGENCY_LIQUIDITY,
                 InputKind.GOAL_DETAILS, InputKind.PLAN)
        inputs = tuple(ManifestInput(kind, None, InputStatus.UNKNOWN, reasons=('user_world_not_initialized',)) for kind in kinds)
        gates = tuple(_missing_gate(capability, None) for capability in Capability)
        return EngineInputManifest(read.context, None, inputs, gates)
    p = read.projection
    if (type(read.version) is not ReadVersion or type(p) is not WorldProjection or read.snapshot is None
            or read.version.context != read.context or read.version.world_ref != p.world_ref
            or read.version.state_ref != p.state_ref or read.version.revision != p.revision
            or read.version.plan_ref != p.plan.ref.ref or read.snapshot.world.ref != p.world_ref
            or read.context.owner_id != p.owner_id or read.context.world_id != p.world_id):
        raise ValueError('Manifest requires one coherent I1 User publication')
    inputs = []
    for row in p.currencies:
        inputs.extend((_amount_input(InputKind.CURRENT_ASSETS, row.currency, row.current_assets),
                       _amount_input(InputKind.EARMARKED, row.currency, row.earmarked),
                       _amount_input(InputKind.UNALLOCATED, row.currency, row.unallocated)))
    for item in p.positions:
        incomplete = item.access_status != 'accessible' or item.observation.freshness.status.value != 'current'
        inputs.append(_record_input(InputKind.ASSET_POSITION, item.balance.currency, item.balance, None,
                                    item.observation, (item.ref, item.account_ref),
                                    (item.access_status, item.observation.freshness.status.value), incomplete))
    for item in p.earmarks:
        inputs.append(_record_input(InputKind.EARMARK, item.amount.currency, item.amount, None,
                                    item.observation, (item.ref, item.position_ref),
                                    (item.role, 'emergency_designated' if item.emergency_use_designated else 'not_emergency_designated')))
    for kind, collection in ((InputKind.FUTURE_INFLOW, p.future_inflows),
                             (InputKind.PLANNED_EXPENSE, p.planned_expenses)):
        for item in collection:
            unresolved_time = item.timing.kind in (TemporalKind.RELATIVE, TemporalKind.UNKNOWN)
            inputs.append(_record_input(kind, item.amount.currency, item.amount, item.timing,
                                        item.observation, (item.ref, *item.related_refs),
                                        (item.status, 'timing_unresolved') if unresolved_time else (item.status,),
                                        unresolved_time))
    for item in p.decisions:
        inputs.append(_record_input(InputKind.DECISION, item.amount.currency if item.amount else None,
                                    item.amount, item.timing, item.observation,
                                    (item.ref, item.source_state_ref, *item.related_refs),
                                    (item.status, 'timing_unresolved') if item.timing.kind in (TemporalKind.RELATIVE, TemporalKind.UNKNOWN)
                                    else (item.status,),
                                    item.timing.kind in (TemporalKind.RELATIVE, TemporalKind.UNKNOWN)))
    for item in p.occurrences:
        refs = (item.ref, *item.related_refs,
                *(ref for ref in (item.source_position_ref, item.destination_position_ref, item.balance_effect_ref) if ref))
        inputs.append(_record_input(InputKind.ACTUAL_OCCURRENCE, item.amount.currency,
                                    item.amount, item.timing, item.observation, refs,
                                    (item.settlement_status, item.kind), item.settlement_status == 'unallocated'))
    inputs.append(ManifestInput(InputKind.PLAN, None, InputStatus.AVAILABLE,
                                observation=p.plan.observation,
                                refs=_refs((p.plan.ref, p.plan.source_state_ref), p.plan.decision_refs,
                                           p.plan.expense_refs,
                                           tuple(TypedRef('legacy_goal', allocation.goal_ref)
                                                 for allocation in p.plan.goal_allocations),
                                           tuple(TypedRef('assumption', ref) for ref in p.plan.assumption_refs)),
                                reasons=(p.plan.evaluation_status,)))
    inputs.append(ManifestInput(InputKind.GOAL_DETAILS, None, _coverage_status(p.goal_details),
                                refs=_refs((p.plan.ref,),
                                           tuple(TypedRef('legacy_goal', allocation.goal_ref)
                                                 for allocation in p.plan.goal_allocations)),
                                reasons=p.goal_details.reasons))
    native_currencies = tuple(sorted({item.currency for item in inputs if item.currency is not None} |
        {m.amount.currency for m in p.monthly_essential_observations}, key=lambda c: c.value))
    for currency in native_currencies or (None,):
        for kind, coverage in ((InputKind.MONTHLY_ESSENTIALS, p.essential_monthly_outflow),
                               (InputKind.EMERGENCY_LIQUIDITY, p.emergency_liquidity)):
            if kind is InputKind.MONTHLY_ESSENTIALS:
                monthly = next((m for m in p.monthly_essential_observations if m.amount.currency is currency), None)
                inputs.append(ManifestInput(kind, currency,
                    InputStatus.INSUFFICIENT_INPUT if monthly else InputStatus.UNKNOWN,
                    value=monthly.amount if monthly else None, timing=monthly.timing if monthly else None,
                    observation=monthly.observation if monthly else None, refs=(monthly.ref,) if monthly else (),
                    reasons=('monthly', 'user_reported_unverified', 'essentials_coverage_unverified') if monthly
                        else ('native_essentials_not_recorded',)))
                continue
            refs = _refs(*(item.refs for item in inputs if item.currency is currency
                           and item.kind in (InputKind.ASSET_POSITION, InputKind.EARMARK,
                                             InputKind.ACTUAL_OCCURRENCE))) if kind is InputKind.EMERGENCY_LIQUIDITY else ()
            inputs.append(ManifestInput(kind, currency, _coverage_status(coverage),
                                        refs=refs, reasons=coverage.reasons))
    for kind, coverage in ((InputKind.EMPLOYMENT, p.employment), (InputKind.IF_LINES, p.if_lines)):
        inputs.append(ManifestInput(kind, None, _coverage_status(coverage),
                                    refs=coverage.input_refs, reasons=coverage.reasons))
    result = tuple(inputs)
    return EngineInputManifest(read.context, read.version, result, _world_gates(p, result))


def read_engine_inputs(authority: UserWorldAuthority, expected: Optional[ReadVersion] = None) -> EngineInputManifest:
    """Small application read API; I1 owns the only authoritative read."""
    if not isinstance(authority, UserWorldAuthority):
        raise ValueError('UserWorldAuthority required; Demo cannot supply User inputs')
    return manifest_from_read(authority.read(expected))

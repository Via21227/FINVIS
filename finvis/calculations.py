"""Threshold-free arithmetic under explicit synthetic projection assumptions.

Returns metrics and provenance, never affordability/safety recommendations.
Exact rational arithmetic is independent of decimal context and display rounding.
"""

from dataclasses import dataclass
from enum import Enum
from fractions import Fraction
from typing import Optional, Tuple

from .domain import FinancialState, Money, Ref, nonempty, typed_tuple, whole


CALCULATION_VERSION = "synthetic-runway/v0.1"


class RunwayStatus(Enum):
    CALCULATED = "calculated"
    ZERO_OUTFLOW_UNDEFINED = "zero_outflow_undefined"


@dataclass(frozen=True)
class Runway:
    status: RunwayStatus
    months: Optional[Fraction]


def emergency_runway(liquidity: Money, monthly_essential_outflow: Money) -> Runway:
    """Available emergency liquidity / monthly essential outflow.

    Zero outflow is undefined, not infinity, safe, or an assumed expense.
    Negative liquidity is outside this metric; deficits are separate outputs.
    """
    liquidity._check(monthly_essential_outflow)
    whole(liquidity.minor_units, "liquidity")
    whole(monthly_essential_outflow.minor_units, "monthly essential outflow")
    if monthly_essential_outflow.minor_units == 0:
        return Runway(RunwayStatus.ZERO_OUTFLOW_UNDEFINED, None)
    return Runway(
        RunwayStatus.CALCULATED,
        Fraction(liquidity.minor_units, monthly_essential_outflow.minor_units),
    )


@dataclass(frozen=True)
class ProjectionAssumptions:
    """Caller must opt in to each simplification. No production defaults."""

    ref: Ref
    rationale: str
    synthetic_only: bool
    trip_paid_upfront_from_available_liquidity: bool
    zero_income_during_unemployment: bool
    constant_monthly_essential_outflow: bool
    no_other_cashflows_interest_tax_or_fx: bool
    whole_months_from_decision_date: bool

    def __post_init__(self) -> None:
        nonempty(self.rationale, "rationale")
        for field in (
            "synthetic_only", "trip_paid_upfront_from_available_liquidity",
            "zero_income_during_unemployment", "constant_monthly_essential_outflow",
            "no_other_cashflows_interest_tax_or_fx", "whole_months_from_decision_date",
        ):
            if getattr(self, field) is not True:
                raise ValueError(f"This provisional model requires explicit acknowledgement: {field}")


@dataclass(frozen=True)
class RunwayDelta:
    before: Runway
    after: Runway
    change_months: Optional[Fraction]


@dataclass(frozen=True)
class ScenarioProjection:
    unemployment_months: int
    without_trip_balance: Money
    with_trip_balance: Money
    # Signed balances preserve deficits. They are not claims of executable debt.


@dataclass(frozen=True)
class CalculationTrace:
    model_version: str
    state_ref: Ref
    available_liquidity: Money
    monthly_essential_outflow: Money
    trip_cost: Money
    evidence_refs: Tuple[Ref, ...]
    assumption_refs: Tuple[Ref, ...]
    projection_assumptions: ProjectionAssumptions


@dataclass(frozen=True)
class TripEvaluation:
    trace: CalculationTrace
    liquidity_after_trip: Money
    runway_delta: RunwayDelta
    scenarios: Tuple[ScenarioProjection, ...]


def evaluate_trip(
    state: FinancialState,
    trip_cost: Money,
    unemployment_months: Tuple[int, ...],
    assumptions: ProjectionAssumptions,
) -> TripEvaluation:
    """Pure metric calculation. Never mutates state or creates a Living Plan.

    This excludes the three pre-graduation months and any prospective salary.
    Negative projected balances are visible, never clamped into apparent safety.
    A trip costing more than current available liquidity is outside this model.
    """
    if not isinstance(assumptions, ProjectionAssumptions):
        raise ValueError("Explicit projection assumptions are required")
    liquidity = state.available_emergency_liquidity.value
    essential = state.monthly_essential_outflow.value
    liquidity._check(trip_cost)
    whole(trip_cost.minor_units, "trip cost")
    if trip_cost.minor_units > liquidity.minor_units:
        raise ValueError("Trip exceeds supplied available liquidity; funding/borrowing is not modeled")
    typed_tuple(unemployment_months, int, "unemployment_months")
    if not unemployment_months or len(set(unemployment_months)) != len(unemployment_months):
        raise ValueError("Supply a nonempty, unique tuple of scenario durations")
    for months in unemployment_months:
        whole(months, "unemployment months", 1)
    after_liquidity = liquidity - trip_cost
    before = emergency_runway(liquidity, essential)
    after = emergency_runway(after_liquidity, essential)
    delta = None if before.months is None or after.months is None else after.months - before.months
    scenarios = tuple(
        ScenarioProjection(
            months,
            Money(liquidity.minor_units - essential.minor_units * months, liquidity.currency),
            Money(after_liquidity.minor_units - essential.minor_units * months, liquidity.currency),
        )
        for months in unemployment_months
    )
    # Sorting makes provenance reproducible even when caller supplied sources in
    # different order. The actual input snapshot remains in the trace by version.
    def unique_refs(refs: Tuple[Ref, ...]) -> Tuple[Ref, ...]:
        return tuple(sorted(set(refs), key=lambda ref: (ref.id, ref.version)))

    trace = CalculationTrace(
        CALCULATION_VERSION, state.ref, liquidity, essential, trip_cost,
        unique_refs(state.available_emergency_liquidity.evidence_refs + state.monthly_essential_outflow.evidence_refs),
        unique_refs(state.available_emergency_liquidity.assumption_refs + state.monthly_essential_outflow.assumption_refs + (assumptions.ref,)),
        assumptions,
    )
    return TripEvaluation(trace, after_liquidity, RunwayDelta(before, after, delta), scenarios)

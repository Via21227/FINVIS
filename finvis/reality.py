"""Scoped employment materiality and honest partial plan re-evaluation."""

from dataclasses import dataclass
from enum import Enum
from typing import Optional, Tuple

from .calculations import Runway, emergency_runway
from .domain import (
    EmploymentStatus, FinancialEvent, FinancialState, LivingPlan,
    Money, Ref, SalaryBasis,
)
from .ports import MaterialityAssessment, MaterialityDisposition


class EmploymentMateriality:
    """One reviewed case, no numerical materiality score or generic heuristic."""

    def assess(self, before: FinancialState, after: FinancialState,
               relevant_history: Tuple[FinancialEvent, ...], plan: LivingPlan) -> MaterialityAssessment:
        resolved = (
            before.employment is not None and after.employment is not None
            and before.employment.status is EmploymentStatus.NO_CONFIRMED_OFFER
            and after.employment.status is EmploymentStatus.ACCEPTED_OFFER
            and before.employment.uncertainty_ref in plan.assumption_refs
        )
        return MaterialityAssessment(
            MaterialityDisposition.REEVALUATE if resolved else MaterialityDisposition.RECORD_ONLY,
            "Accepted offer resolves a driving plan assumption" if resolved else "No driving employment assumption resolved",
            tuple(e.ref for e in relevant_history), Ref("employment-assumption-resolution-policy", 1),
        )


class PendingReason(Enum):
    FX_REQUIRED = "fx_required"
    SALARY_BASIS_UNRESOLVED = "salary_basis_unresolved"
    NET_INCOME_UNRESOLVED = "net_income_unresolved"
    EMPLOYMENT_START_UNRESOLVED = "employment_start_unresolved"
    INCOME_CASHFLOW_MODEL_NOT_IMPLEMENTED = "income_cashflow_model_not_implemented"


@dataclass(frozen=True)
class PlanReevaluation:
    ref: Ref
    event_ref: Ref
    before_state_ref: Ref
    after_state_ref: Ref
    before_plan_ref: Ref
    after_plan_ref: Ref
    resolved_if_line_refs: Tuple[Ref, ...]
    materiality: MaterialityAssessment
    liquidity_after_planned_outflows: Money
    observed_runway: Runway
    planned_runway: Runway
    pending_reasons: Tuple[PendingReason, ...]
    projected_monthly_net_cashflow: Optional[Money] = None
    calculation_model_ref: Ref = Ref("plan-metrics-without-income", 1)


def plan_metrics(state: FinancialState, plan: LivingPlan):
    """Recompute known same-currency quantities; do not incorporate salary."""
    liquidity = state.available_emergency_liquidity.value
    essential = state.monthly_essential_outflow.value
    after = liquidity
    for outflow in plan.planned_outflows:
        after = after - outflow.amount
    if after.minor_units < 0:
        raise ValueError("Planned spending exceeds supported available funding")
    pending = []
    if state.employment is not None and state.employment.offer is not None:
        offer = state.employment.offer
        if offer.monthly_income.value.currency is not essential.currency:
            pending.append(PendingReason.FX_REQUIRED)
        if offer.salary_basis is SalaryBasis.UNSPECIFIED:
            pending.append(PendingReason.SALARY_BASIS_UNRESOLVED)
        elif offer.salary_basis is SalaryBasis.GROSS:
            pending.append(PendingReason.NET_INCOME_UNRESOLVED)
        if offer.employment_start_on is None:
            pending.append(PendingReason.EMPLOYMENT_START_UNRESOLVED)
        pending.append(PendingReason.INCOME_CASHFLOW_MODEL_NOT_IMPLEMENTED)
    return after, emergency_runway(liquidity, essential), emergency_runway(after, essential), tuple(pending)

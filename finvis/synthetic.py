"""Entirely fabricated arithmetic fixtures, not financial recommendations."""

from datetime import date, datetime, timezone

from finvis.calculations import ProjectionAssumptions
from finvis.domain import (
    Assumption, AssumptionStatus, Confidence, ConfidenceAssessment, Currency,
    Evidence, FinancialAmount, FinancialState, Goal, GoalProtection, Money,
    Precision, RealitySource, Ref, EmploymentState, EmploymentStatus,
)


FIXTURE_ASSUMPTION = Assumption(
    Ref("synthetic-balances", 1),
    "SGD 12,000 available emergency liquidity; SGD 2,000 monthly essential outflow.",
    "Chosen for hand-checkable arithmetic, unrelated to the user's real finances.",
    "Phase 2 synthetic story; no graduation lead-in or elapsed spending modeled.",
    AssumptionStatus.PROVISIONAL,
    "Replace with reviewed story inputs before any competition demonstration.",
)

CONFIDENCE = Confidence(ConfidenceAssessment.TENTATIVE, "Fabricated fixture, not an observed fact")


def sgd(minor_units: int) -> Money:
    return Money(minor_units, Currency.SGD)


def amount(minor_units: int) -> FinancialAmount:
    return FinancialAmount(sgd(minor_units), Precision.EXACT_REPORTED, CONFIDENCE, (), (FIXTURE_ASSUMPTION.ref,))


def synthetic_state() -> FinancialState:
    return FinancialState(
        Ref("synthetic-state", 1), "synthetic-user", date(2026, 9, 18),
        amount(1_200_000), amount(200_000), (Ref("car-goal", 1),),
        EmploymentState(EmploymentStatus.NO_CONFIRMED_OFFER, Ref("no-confirmed-offer", 1)),
    )


def car_goal() -> Goal:
    return Goal(Ref("car-goal", 1), "synthetic-user", "Car", sgd(2_400_000), date(2028, 9, 18), GoalProtection.FLEXIBLE)


def projection_assumptions() -> ProjectionAssumptions:
    return ProjectionAssumptions(
        ref=Ref("synthetic-zero-income-projection", 1),
        rationale="Hand-checkable test: immediate trip and whole-month zero-income interval; excludes graduation lead-in, FX, tax, interest and other flows.",
        synthetic_only=True,
        trip_paid_upfront_from_available_liquidity=True,
        zero_income_during_unemployment=True,
        constant_monthly_essential_outflow=True,
        no_other_cashflows_interest_tax_or_fx=True,
        whole_months_from_decision_date=True,
    )


def evidence() -> Evidence:
    return Evidence(
        Ref("reported-inflow", 1), "synthetic-user", RealitySource.USER_REPORT,
        datetime(2026, 9, 18, 12, tzinfo=timezone.utc), "synthetic:manual-report",
        "RMB 12,000 came in; purpose not yet established.",
    )

"""One named synthetic story. All narrative-specific values live here/ synthetic.py."""

from dataclasses import dataclass
from datetime import date, datetime, timezone

from .domain import (
    Assumption, AssumptionStatus, Confidence, ConfidenceAssessment, Currency,
    Decision, DecisionOption, DecisionStatus, DelayGoalChange, EmploymentOffer,
    Evidence, EventInterpretation, FinancialAmount, FinancialEvent, GoalAllocation,
    IfLine, IfLineStatus, InterpretationOutcome, LivingPlan, Money, PlanStatus,
    Precision, RealitySource, Ref, SalaryBasis,
)
from .lifecycle import Lifecycle
from .store import MemoryStore, Snapshot
from .synthetic import FIXTURE_ASSUMPTION, car_goal, projection_assumptions, sgd, synthetic_state


@dataclass(frozen=True)
class StoryInputs:
    owner_id: str = "synthetic-user"
    decision_ref: Ref = Ref("travel-decision", 1)
    option_ref: Ref = Ref("travel-and-delay-car", 1)
    action_ref: Ref = Ref("delay-car", 1)
    authorization_ref: Ref = Ref("consent-delay-car", 1)
    evaluation_ref: Ref = Ref("travel-evaluation", 1)
    trip_cost: Money = Money(300_000, Currency.SGD)
    car_new_date: date = date(2029, 9, 18)
    initial_car_contribution: Money = Money(100_000, Currency.SGD)
    durations: tuple = (1, 3, 6)
    consent_time: datetime = datetime(2026, 9, 18, 12, 0, tzinfo=timezone.utc)
    execution_time: datetime = datetime(2026, 9, 18, 12, 1, tzinfo=timezone.utc)
    event_time: datetime = datetime(2026, 9, 19, 12, 0, tzinfo=timezone.utc)
    salary: Money = Money(1_200_000, Currency.CNY)


STORY = StoryInputs()


def initial_snapshot(inputs=STORY) -> Snapshot:
    state = synthetic_state()
    goal = car_goal()
    uncertainty = Assumption(state.employment.uncertainty_ref,
        "No accepted job offer; employment-delay futures remain open", "Synthetic starting reality",
        "This user's current no-confirmed-offer decision context", AssumptionStatus.ACCEPTED_FOR_SCENARIO,
        "An explicitly reviewed accepted offer resolves this premise")
    projection = projection_assumptions()
    premise = Assumption(projection.ref, projection.rationale, "Explicit arithmetic simplifications",
        "Synthetic single-currency projection", AssumptionStatus.PROVISIONAL, "New flows, timing or FX information")
    decision = Decision(inputs.decision_ref, inputs.owner_id, "Can I take the trip before returning home?", state.ref, DecisionStatus.EXPLORING)
    option = DecisionOption(inputs.option_ref, decision.ref, "Proceed with trip; Delay Car",
        "Plan travel and move the flexible car target; no transfer or observed spending is performed",
        DelayGoalChange(goal.ref, goal.target_date, inputs.car_new_date), inputs.trip_cost)
    lines = tuple(IfLine(Ref(f"unemployment-{n}-months", 1), decision.ref, state.ref,
        f"{n} months without employment income", (uncertainty.ref, premise.ref), IfLineStatus.ACTIVE,
        unemployment_months=n, employment_uncertainty_ref=uncertainty.ref) for n in inputs.durations)
    plan = LivingPlan(Ref("living-plan", 1), inputs.owner_id, state.ref, decision.ref,
        (uncertainty.ref, premise.ref), (GoalAllocation(goal.ref, inputs.initial_car_contribution),), PlanStatus.ADOPTED)
    return Snapshot((FIXTURE_ASSUMPTION, uncertainty, premise, state, goal, decision, option, *lines, plan), state.ref, plan.ref)


def offer_records(state_ref: Ref, inputs=STORY):
    evidence = Evidence(Ref("accepted-offer-evidence", 1), inputs.owner_id, RealitySource.USER_PROVIDED_EVIDENCE,
        inputs.event_time, "synthetic:accepted-offer",
        f"Offer received and accepted; {inputs.salary.currency.value} {inputs.salary.minor_units // 100:,}.{inputs.salary.minor_units % 100:02d}/month. Gross/net and start date unspecified.")
    confidence = Confidence(ConfidenceAssessment.SUPPORTED, "Explicit acceptance stipulated by the synthetic case; not inferred from source category")
    interpretation = EventInterpretation(Ref("accepted-offer-interpretation", 1), inputs.owner_id,
        (evidence.ref,), "An offer has been accepted; no claim that work or salary payments have begun",
        InterpretationOutcome.INFER, confidence, "Manually reviewed synthetic event; amount is not yet net spendable income")
    income = FinancialAmount(inputs.salary, Precision.EXACT_REPORTED, confidence, (evidence.ref,), ())
    event = FinancialEvent(Ref("accepted-employment-event", 1), inputs.owner_id, inputs.event_time.date(),
        inputs.event_time, "Accepted job offer", interpretation.ref, confidence, state_ref,
        EmploymentOffer(income, SalaryBasis.UNSPECIFIED, inputs.event_time.date()), Ref("no-confirmed-offer", 1))
    return evidence, interpretation, event


def run_story(inputs=STORY) -> MemoryStore:
    store = MemoryStore(initial_snapshot(inputs))
    service = Lifecycle(store)
    line_refs = tuple(r.ref for r in store.snapshot.records if type(r) is IfLine)
    service.evaluate(inputs.owner_id, inputs.decision_ref, inputs.option_ref, line_refs,
        projection_assumptions(), inputs.evaluation_ref)
    decision = service.select(inputs.owner_id, inputs.decision_ref, inputs.option_ref)
    action = service.propose(inputs.owner_id, decision.ref, inputs.action_ref)
    consent = service.authorize(inputs.owner_id, action.ref, inputs.authorization_ref, inputs.consent_time)
    service.execute(inputs.owner_id, action.ref, consent.ref, inputs.execution_time)
    before = store.snapshot
    evidence, interpretation, event = offer_records(before.current_state_ref, inputs)
    service.accept_employment(inputs.owner_id, evidence, interpretation, event, before.current_state_ref, before.current_plan_ref)
    return store

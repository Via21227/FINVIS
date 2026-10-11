"""Deterministic application commands, independent of UI, LLM and story values."""

from dataclasses import dataclass, replace
from datetime import datetime
from typing import Tuple

from .calculations import ProjectionAssumptions, ScenarioProjection, TripEvaluation, evaluate_trip
from .domain import (
    Assumption, Decision, DecisionOption, DecisionStatus, EmploymentState,
    EmploymentStatus, Evidence, EventInterpretation, ExecutionLog, ExecutionOutcome,
    FinancialEvent, FinancialState, Goal, GoalProtection, IfLine, IfLineStatus,
    InterpretationOutcome, LivingPlan, PlanAction, PlanAuthorization,
    PlannedOutflow, PlanStatus, Ref, aware,
)
from .ports import MaterialityDisposition, MaterialityPolicy
from .reality import EmploymentMateriality, PlanReevaluation, plan_metrics
from .store import MemoryStore, Rejected, Snapshot, StaleVersion


def next_ref(ref: Ref) -> Ref:
    return Ref(ref.id, ref.version + 1)


def owner(actual: str, expected: str) -> None:
    if actual != expected:
        raise Rejected("Actor and record ownership do not match")


def current(snapshot: Snapshot, state_ref: Ref, plan_ref: Ref) -> None:
    if snapshot.current_state_ref != state_ref or snapshot.current_plan_ref != plan_ref:
        raise StaleVersion("Expected state/plan version is no longer current")


@dataclass(frozen=True)
class LineCalculation:
    if_line_ref: Ref
    projection: ScenarioProjection


@dataclass(frozen=True)
class DecisionEvaluation:
    ref: Ref
    decision_ref: Ref
    option_ref: Ref
    lines: Tuple[LineCalculation, ...]
    calculation: TripEvaluation


class Lifecycle:
    def __init__(self, store: MemoryStore, materiality: MaterialityPolicy = None):
        self.store = store
        self.materiality = materiality if materiality is not None else EmploymentMateriality()

    def evaluate(self, actor: str, decision_ref: Ref, option_ref: Ref,
                 line_refs: Tuple[Ref, ...], assumptions: ProjectionAssumptions,
                 evaluation_ref: Ref) -> DecisionEvaluation:
        def command(s):
            decision = s.get(Decision, decision_ref)
            owner(actor, decision.owner_id)
            option = s.get(DecisionOption, option_ref)
            state = s.get(FinancialState, decision.state_ref)
            owner(actor, state.owner_id)
            if state.ref != s.current_state_ref or option.decision_ref != decision.ref:
                raise StaleVersion("Evaluation must use the current state and the option's originating decision")
            lines = tuple(s.get(IfLine, ref) for ref in line_refs)
            if not lines or option.planned_outflow is None:
                raise Rejected("A scalar planned outflow and scenario lines are required")
            for line in lines:
                if line.status is not IfLineStatus.ACTIVE or s.latest(IfLine, line.ref.id) != line:
                    raise Rejected("Historical or closed lines cannot be evaluated as current futures")
                if line.decision_ref != decision.ref or line.state_ref != state.ref or line.unemployment_months is None:
                    raise Rejected("If Line belongs to a different decision/state or unsupported scenario")
                if state.employment is None or state.employment.status is not EmploymentStatus.NO_CONFIRMED_OFFER or line.employment_uncertainty_ref != state.employment.uncertainty_ref:
                    raise Rejected("Unemployment scenario is not supported by current employment state")
                for ref in line.assumption_refs:
                    s.get(Assumption, ref)
            result = evaluate_trip(state, option.planned_outflow, tuple(l.unemployment_months for l in lines), assumptions)
            evaluation = DecisionEvaluation(evaluation_ref, decision.ref, option.ref,
                tuple(LineCalculation(l.ref, p) for l, p in zip(lines, result.scenarios)), result)
            return s.append(evaluation), evaluation
        return self.store.transact(command)

    def select(self, actor: str, decision_ref: Ref, option_ref: Ref) -> Decision:
        def command(s):
            decision = s.get(Decision, decision_ref)
            option = s.get(DecisionOption, option_ref)
            owner(actor, decision.owner_id)
            if s.latest(Decision, decision.ref.id) != decision or decision.state_ref != s.current_state_ref:
                raise StaleVersion("Decision or its state changed")
            if option.decision_ref != decision.ref or decision.status is DecisionStatus.SELECTED:
                raise Rejected("Option is not an available choice for this decision")
            selected = replace(decision, ref=next_ref(decision.ref), status=DecisionStatus.SELECTED, selected_option_ref=option.ref)
            return s.append(selected), selected
        return self.store.transact(command)

    def propose(self, actor: str, decision_ref: Ref, action_ref: Ref) -> PlanAction:
        def command(s):
            decision = s.get(Decision, decision_ref)
            owner(actor, decision.owner_id)
            plan = s.get(LivingPlan, s.current_plan_ref)
            owner(actor, plan.owner_id)
            if decision.status is not DecisionStatus.SELECTED or s.latest(Decision, decision.ref.id) != decision:
                raise Rejected("A current user-selected decision is required")
            if decision.state_ref != s.current_state_ref:
                raise StaleVersion("Decision state is stale")
            option = s.get(DecisionOption, decision.selected_option_ref)
            if option.goal_change is None:
                raise Rejected("Only explicit goal-delay options are supported")
            action = PlanAction(action_ref, actor, decision.ref, plan.ref,
                option.goal_change, "Apply the user-selected plan alternative", option.ref, option.planned_outflow)
            return s.append(action), action
        return self.store.transact(command)

    def authorize(self, actor: str, action_ref: Ref, authorization_ref: Ref,
                  granted_at: datetime) -> PlanAuthorization:
        def command(s):
            action = s.get(PlanAction, action_ref)
            owner(actor, action.owner_id)
            decision = s.get(Decision, action.decision_ref)
            current(s, decision.state_ref, action.expected_plan_ref)
            authorization = PlanAuthorization(authorization_ref, actor, action.ref, action.expected_plan_ref, granted_at)
            return s.append(authorization), authorization
        return self.store.transact(command)

    def execute(self, actor: str, action_ref: Ref, authorization_ref: Ref,
                executed_at: datetime) -> ExecutionLog:
        aware(executed_at, "executed_at")
        def command(s):
            action = s.get(PlanAction, action_ref)
            owner(actor, action.owner_id)
            consent = s.get(PlanAuthorization, authorization_ref)
            owner(actor, consent.owner_id)
            if consent.action_ref != action.ref or consent.expected_plan_ref != action.expected_plan_ref:
                raise Rejected("Consent is not for this exact immutable action and plan version")
            if consent.granted_at > executed_at:
                raise Rejected("Execution cannot precede consent")
            receipt_ref = Ref(f"execute/{action.ref.id}@{action.ref.version}", 1)
            existing = [r for r in s.records if type(r) is ExecutionLog and r.ref == receipt_ref]
            if existing:
                if existing[0].authorization_ref != consent.ref:
                    raise Rejected("Retry must reference the original consent")
                return s, existing[0]
            decision = s.get(Decision, action.decision_ref)
            owner(actor, decision.owner_id)
            current(s, decision.state_ref, action.expected_plan_ref)
            if s.latest(Decision, decision.ref.id) != decision or decision.status is not DecisionStatus.SELECTED or decision.selected_option_ref != action.option_ref:
                raise Rejected("Action is not bound to the current user selection")
            option = s.get(DecisionOption, action.option_ref)
            if option.decision_ref.id != decision.ref.id or option.goal_change != action.change or option.planned_outflow != action.planned_outflow:
                raise Rejected("Action payload differs from the selected option")
            state = s.get(FinancialState, s.current_state_ref)
            plan = s.get(LivingPlan, s.current_plan_ref)
            goal = s.get(Goal, action.change.goal_ref)
            owner(actor, goal.owner_id)
            owner(actor, plan.owner_id)
            owner(actor, state.owner_id)
            if goal.protection is not GoalProtection.FLEXIBLE:
                raise Rejected("This slice supports flexible goals only; protection exceptions are undefined")
            if s.latest(Goal, goal.ref.id) != goal or goal.target_date != action.change.previous_target_date:
                raise StaleVersion("Goal version/date changed")
            if goal.ref not in state.goal_refs or goal.ref not in tuple(a.goal_ref for a in plan.allocations):
                raise Rejected("Goal must be present in the current state and plan")
            changed_goal = replace(goal, ref=next_ref(goal.ref), target_date=action.change.proposed_target_date)
            changed_state = replace(state, ref=next_ref(state.ref), goal_refs=tuple(changed_goal.ref if ref == goal.ref else ref for ref in state.goal_refs))
            outflows = plan.planned_outflows
            if action.planned_outflow is not None:
                outflows += (PlannedOutflow(decision.ref, action.planned_outflow),)
            changed_plan = replace(plan, ref=next_ref(plan.ref), state_ref=changed_state.ref,
                previous_plan_ref=plan.ref, decision_ref=decision.ref, status=PlanStatus.ADOPTED,
                allocations=tuple(replace(a, goal_ref=changed_goal.ref) if a.goal_ref == goal.ref else a for a in plan.allocations),
                planned_outflows=outflows)
            plan_metrics(changed_state, changed_plan)  # Reject mixed currency/unsupported funding before commit.
            fields = ("goal.target_date", "state.goal_refs", "plan.allocations.goal_ref", "plan.state_ref", "plan.decision_ref")
            if action.planned_outflow is not None:
                fields += ("plan.planned_outflows",)
            receipt = ExecutionLog(receipt_ref, action.ref, consent.ref, plan.ref, changed_plan.ref,
                executed_at, ExecutionOutcome.APPLIED, "Authorized plan-level change committed; no money moved",
                decision.ref, option.ref, goal.ref, changed_goal.ref, state.ref, changed_state.ref, fields)
            candidate = s.append(changed_goal, changed_state, changed_plan, receipt)
            return replace(candidate, current_state_ref=changed_state.ref, current_plan_ref=changed_plan.ref), receipt
        return self.store.transact(command)

    def accept_employment(self, actor: str, evidence: Evidence,
                          interpretation: EventInterpretation, event: FinancialEvent,
                          expected_state_ref: Ref, expected_plan_ref: Ref) -> PlanReevaluation:
        """Explicit reviewed event application; no text classifier or source ranking."""
        def command(s):
            for record in (evidence, interpretation, event):
                owner(actor, record.owner_id)
            reevaluation_ref = Ref(f"reevaluate/{event.ref.id}@{event.ref.version}", 1)
            existing = [r for r in s.records if type(r) is PlanReevaluation and r.ref == reevaluation_ref]
            if existing:
                for record in (evidence, interpretation, event):
                    if s.get(type(record), record.ref) != record:
                        raise Rejected("Event retry changed original evidence or event content")
                result = existing[0]
                if (result.before_state_ref, result.before_plan_ref) != (expected_state_ref, expected_plan_ref):
                    raise Rejected("Event retry changed expected versions")
                return s, result
            current(s, expected_state_ref, expected_plan_ref)
            state = s.get(FinancialState, expected_state_ref)
            plan = s.get(LivingPlan, expected_plan_ref)
            owner(actor, state.owner_id)
            if state.employment is None or state.employment.status is not EmploymentStatus.NO_CONFIRMED_OFFER:
                raise Rejected("This command only resolves a previously unconfirmed offer")
            if event.employment_offer is None or event.resolves_assumption_ref != state.employment.uncertainty_ref:
                raise Rejected("Event must explicitly resolve this employment assumption")
            s.get(Assumption, event.resolves_assumption_ref)
            if event.affected_state_ref != state.ref or event.interpretation_ref != interpretation.ref:
                raise Rejected("Event interpretation/state provenance mismatch")
            if interpretation.outcome is not InterpretationOutcome.INFER or interpretation.evidence_refs != (evidence.ref,):
                raise Rejected("Explicit reviewed interpretation of the supplied evidence is required")
            if event.confidence != interpretation.confidence or event.employment_offer.monthly_income.evidence_refs != (evidence.ref,):
                raise Rejected("Confidence/income provenance must match the reviewed interpretation")
            if event.employment_offer.accepted_on != event.occurred_on or event.occurred_on < state.as_of or event.recorded_at < evidence.captured_at:
                raise Rejected("Event timing is inconsistent; retroactive changes need a correction workflow")
            employment = EmploymentState(EmploymentStatus.ACCEPTED_OFFER, state.employment.uncertainty_ref, event.employment_offer, event.ref)
            changed_state = replace(state, ref=next_ref(state.ref), as_of=event.occurred_on, employment=employment)
            materiality = self.materiality.assess(state, changed_state, (event,), plan)
            # The Phase 2 command is intentionally scoped to an assumption driving
            # the current plan; a generic record-only event path is not implemented.
            if materiality.disposition is not MaterialityDisposition.REEVALUATE:
                raise Rejected("Event is outside this material plan-assumption resolution path")
            archived = []
            identities = sorted({r.ref.id for r in s.records if type(r) is IfLine})
            for identity in identities:
                line = s.latest(IfLine, identity)
                linked_state = s.get(FinancialState, line.state_ref)
                if linked_state.owner_id == actor and line.status is IfLineStatus.ACTIVE and line.employment_uncertainty_ref == event.resolves_assumption_ref:
                    archived.append(replace(line, ref=next_ref(line.ref), status=IfLineStatus.ARCHIVED,
                        archive_reason="Accepted offer resolves the no-confirmed-offer premise; income cashflow remains pending",
                        resolution_event_ref=event.ref))
            changed_plan = replace(plan, ref=next_ref(plan.ref), state_ref=changed_state.ref, previous_plan_ref=plan.ref,
                assumption_refs=tuple(ref for ref in plan.assumption_refs if ref != event.resolves_assumption_ref))
            after, observed, planned, pending = plan_metrics(changed_state, changed_plan)
            reevaluation = PlanReevaluation(reevaluation_ref, event.ref, state.ref, changed_state.ref,
                plan.ref, changed_plan.ref, tuple(line.ref for line in archived), materiality,
                after, observed, planned, pending)
            candidate = s.append(evidence, interpretation, event, changed_state, *archived, changed_plan, reevaluation)
            return replace(candidate, current_state_ref=changed_state.ref, current_plan_ref=changed_plan.ref), reevaluation
        return self.store.transact(command)

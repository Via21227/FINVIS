"""Single synthetic demo session. Domain snapshots are the only product state.

Presentation strings are derived here; no financial rule is implemented here.
A generation + revision precondition protects multi-tab writes and reset retries.
"""
from threading import RLock
from uuid import uuid4

from .agent_providers import LocalAIConnection, ProviderError
from .provider_selection import production_connection
from .agent_understanding import AgentUnderstandingService
from .domain import (Assumption, Decision, DecisionStatus, ExecutionLog, FinancialState,
                     Goal, IfLine, LivingPlan, PlanAction, PlanAuthorization)
from .lifecycle import DecisionEvaluation, Lifecycle
from .reality import PlanReevaluation, plan_metrics
from .replay import money, structured
from .store import MemoryStore, Rejected, StaleVersion
from .story import STORY, initial_snapshot, offer_records
from .synthetic import projection_assumptions


class AppError(Exception):
    def __init__(self, code, message, status=409):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


def months(value):
    if value is None:
        return 'Not calculated'
    # Display only; authoritative rational remains in the exported calculation.
    return f'{float(value):.1f}'


class DemoApplication:
    def __init__(self, store_factory=MemoryStore, agent_provider=None, local_ai=None):
        self._lock = RLock()
        self._store_factory = store_factory
        # Production shares one provider between discovery/selection and inference.
        # Explicit test providers remain isolated from the LM Studio connection UI.
        self.local_ai = local_ai or (production_connection() if agent_provider is None else None)
        self.agent = AgentUnderstandingService(
            agent_provider or (self.local_ai.provider if self.local_ai else None))
        self._reset()

    def _reset(self):
        # Publish new session only after the canonical evaluation succeeds.
        store = self._store_factory(initial_snapshot())
        service = Lifecycle(store)
        service.evaluate(STORY.owner_id, STORY.decision_ref, STORY.option_ref,
                         tuple(r.ref for r in store.snapshot.records if type(r) is IfLine),
                         projection_assumptions(), STORY.evaluation_ref)
        self.store, self.service, self.generation = store, service, uuid4().hex
        self.agent.reset()

    def view(self):
        with self._lock:
            s = self.store.snapshot
            state = s.get(FinancialState, s.current_state_ref)
            plan = s.get(LivingPlan, s.current_plan_ref)
            evaluation = s.get(DecisionEvaluation, STORY.evaluation_ref)
            calc = evaluation.calculation
            records = lambda kind: [r for r in s.records if type(r) is kind]
            actions, consents, receipts = records(PlanAction), records(PlanAuthorization), records(ExecutionLog)
            reevaluations = records(PlanReevaluation)
            lines = []
            for result in evaluation.lines:
                line = s.latest(IfLine, result.if_line_ref.id)
                lines.append(dict(record=structured(line),
                                  projection=structured(result.projection),
                                  with_trip=money(result.projection.with_trip_balance),
                                  without_trip=money(result.projection.without_trip_balance),
                                  assumptions=[structured(s.get(Assumption, ref)) for ref in line.assumption_refs]))
            history = []
            for p in records(LivingPlan):
                receipt = next((r for r in receipts if r.after_plan_ref == p.ref), None)
                reeval = next((r for r in reevaluations if r.after_plan_ref == p.ref), None)
                reason = ('Employment update incorporated; plan re-evaluated' if reeval else
                          'Car goal delayed after your authorized travel decision' if receipt else 'Original plan')
                history.append(dict(record=structured(p), reason=reason,
                                    evidence_ref=structured(reeval.ref if reeval else receipt.ref if receipt else None)))
            after, observed, planned, pending = plan_metrics(state, plan)
            offer = state.employment.offer
            action = actions[-1] if actions else None
            preview_offer = offer_records(s.current_state_ref)[2].employment_offer
            return dict(
                generation=self.generation, revision=s.revision,
                state=structured(state), plan=structured(plan),
                goals=[structured(s.get(Goal, ref)) for ref in state.goal_refs],
                decision=structured(s.latest(Decision, STORY.decision_ref.id)),
                evaluation=structured(evaluation), lines=lines, history=history,
                action=structured(action), authorization=structured(consents[-1] if consents else None),
                receipts=[structured(r) for r in receipts],
                reevaluation=structured(reevaluations[-1] if reevaluations else None),
                display=dict(liquidity=money(state.available_emergency_liquidity.value),
                             essentials=money(state.monthly_essential_outflow.value),
                             trip=money(calc.trace.trip_cost), after_trip=money(calc.liquidity_after_trip),
                             liquidity_delta=money(calc.liquidity_after_trip - calc.trace.available_liquidity),
                             before_runway=months(calc.runway_delta.before.months),
                             after_runway=months(calc.runway_delta.after.months),
                             runway_delta=months(calc.runway_delta.change_months),
                             planned_liquidity=money(after), planned_runway=months(planned.months),
                             observed_runway=months(observed.months),
                             salary=money(offer.monthly_income.value) if offer else None,
                             preview_salary=money(preview_offer.monthly_income.value),
                             contribution=money(plan.allocations[0].monthly_contribution)),
                pending_reasons=[p.value for p in pending],
                agent_candidate=self.agent.candidate.as_dict() if self.agent.candidate else None,
                allowed=dict(prepare=not actions and not receipts and not reevaluations,
                             authorize=bool(action) and not receipts and not reevaluations,
                             employment=bool(receipts) and not reevaluations),
                audit=structured(s))

    def _check_expected(self, expected):
        snapshot = self.store.snapshot
        if not isinstance(expected, dict) or set(expected) != {'generation', 'revision'}:
            raise AppError('invalid_request', 'Refresh the page before trying this action.', 400)
        if type(expected['revision']) is not int or expected != dict(generation=self.generation, revision=snapshot.revision):
            raise AppError('stale_version', 'This demo changed in another tab or was reset. The latest state has been loaded; review it before trying again.')
        return snapshot

    def interpret_agent_input(self, text, expected, cloud_consent=None):
        with self._lock:
            snapshot = self._check_expected(expected)
            if self.local_ai:
                status = self.local_ai.refresh()
                if not status['requests_enabled']:
                    raise AppError(status['state'],
                                   'The selected local model is not ready.', 503)
                if hasattr(self.local_ai, "authorize_request"):
                    try:self.local_ai.authorize_request(cloud_consent)
                    except ProviderError as error:raise AppError(error.category,"Provider unavailable",503) from None
                else:self.local_ai.begin_request()
            try:
                return self.agent.interpret(text, self.generation, snapshot.revision).as_dict()
            except ProviderError as error:
                status = 400 if error.category == 'invalid_input' else 503
                raise AppError(error.category, 'Local Agent Understanding is unavailable. The deterministic demo remains available.', status) from None
            finally:
                if self.local_ai:
                    self.local_ai.end_request()

    def local_ai_status(self):
        if not self.local_ai:
            return {'provider': 'test-local', 'connection': 'unavailable',
                    'endpoint': None, 'selected_model': None, 'models': (),
                    'loaded_model_ids': (), 'state': 'provider_error',
                    'ready': False, 'busy': False, 'requests_enabled': False,
                    'last_successful_check': None,
                    'error_category': 'unsupported_provider'}
        return self.local_ai.refresh()

    def select_local_ai_model(self, model_id):
        if not self.local_ai:
            raise AppError('unsupported_provider', 'Local model selection is unavailable.', 409)
        try:
            return self.local_ai.select(model_id)
        except ProviderError as error:
            status = 400 if error.category == 'invalid_input' else 409
            raise AppError(error.category, 'The local model selection was not changed.', status) from None

    def resolve_agent_candidate(self, action, candidate_id, expected):
        with self._lock:
            snapshot = self._check_expected(expected)
            try:
                if action == 'confirm':
                    return self.agent.confirm(candidate_id, self.generation, snapshot.revision).as_dict()
                if action == 'cancel':
                    return self.agent.cancel(candidate_id, self.generation, snapshot.revision).as_dict()
                raise AppError('invalid_request', 'This candidate action is unavailable.', 400)
            except ProviderError as error:
                raise AppError(error.category, 'The candidate could not be resolved. Refresh and review it again.', 409) from None

    def command(self, name, expected):
        with self._lock:
            s = self._check_expected(expected)
            try:
                if name == 'reset':
                    self._reset()
                elif name == 'prepare':
                    if not self.view()['allowed']['prepare']:
                        raise AppError('invalid_transition', 'This option has already been reviewed. Continue from the current plan.')
                    decision = s.latest(Decision, STORY.decision_ref.id)
                    if decision.status is not DecisionStatus.SELECTED:
                        decision = self.service.select(STORY.owner_id, decision.ref, STORY.option_ref)
                    self.service.propose(STORY.owner_id, decision.ref, STORY.action_ref)
                elif name in ('authorize', 'execute'):
                    if not self.view()['allowed']['authorize']:
                        raise AppError('invalid_transition', 'Review the proposal first, or inspect the existing execution evidence.')
                    consent = next((r for r in s.records if type(r) is PlanAuthorization), None)
                    if name == 'authorize' and consent is None:
                        consent = self.service.authorize(STORY.owner_id, STORY.action_ref, STORY.authorization_ref, STORY.consent_time)
                    if consent is None:
                        raise AppError('not_authorized', 'The plan has not changed. Explicitly authorize the reviewed proposal first.')
                    self.service.execute(STORY.owner_id, STORY.action_ref, consent.ref, STORY.execution_time)
                elif name == 'employment':
                    if not self.view()['allowed']['employment']:
                        raise AppError('invalid_transition', 'Complete the authorized plan update before simulating this reality update.')
                    self.service.accept_employment(STORY.owner_id, *offer_records(s.current_state_ref), s.current_state_ref, s.current_plan_ref)
                else:
                    raise AppError('invalid_request', 'This action is not available in the demo.', 400)
            except AppError:
                raise
            except StaleVersion:
                raise AppError('stale_version', 'The plan changed. Refresh and review the current version before authorizing.') from None
            except Rejected:
                raise AppError('invalid_transition', 'This action is not available in the current state. Refresh and review the plan.') from None
            except Exception:
                # Consent may have been recorded before execution failed. Never
                # claim rollback of consent or success without a real receipt.
                raise AppError('execution_failed', 'The action could not be completed. Review the refreshed plan and audit trail before retrying. Authorization may already be recorded.', 500) from None
            return self.view()

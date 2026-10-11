"""I3 User World review, exact preview, and unconsumed authorization.

The visible workflow composes I2 and Slice D.  It deliberately has no execute
method or financial publication path.  The legacy Demo is not an input.
"""

from datetime import datetime, timezone
from copy import deepcopy
from threading import Lock, RLock
from typing import Optional
from uuid import uuid4

from .agent_reconciliation import (AccountReview, FactReview, OccurrenceReview,
    PositionReview, ReviewRequest, SourceSelection, _amount as candidate_amount)
from .domain import (Confidence, ConfidenceAssessment, Currency, RealitySource, Ref)
from .agent_understanding import AgentUnderstandingService
from .agent_providers import ProviderError
from .reviewed_proposals import (ProposalStatus, ReviewedProposalAPI,
    _wire as proposal_wire)
from .store import Rejected, StaleVersion
from .world_application import ContextKind, UserWorldAuthority, WorldContext
from .world_commands import ReplacementScope, record_ref
from .world_commands import PutRecord
from .world_domain import Account, AssetPosition, EarmarkRole
from .world_planning import OccurrenceKind
from .world_settlement import BalanceBasis
from .world_values import (FreshnessAssessment, FreshnessStatus,
    ObservationMetadata, ObservationRole, TemporalSpec, TypedRef)
from .interaction_review import prepare_interaction


class I3Error(Exception):
    def __init__(self, code, status=409):
        super().__init__(code)
        self.code, self.status = code, status


def _ref(value, snapshot, kind):
    if not isinstance(value, dict) or set(value) != {'kind', 'id', 'version'}:
        raise ValueError('Exact current reference required')
    if value['kind'] != kind or type(value['id']) is not str or type(value['version']) is not int:
        raise ValueError('Reference identity is invalid')
    ref = TypedRef(kind, Ref(value['id'], value['version']))
    if ref not in snapshot.world.members:
        raise ValueError('Reference is no longer current')
    return ref


def _source(value, text, field):
    if not isinstance(value, dict) or set(value) != {'start', 'end'}:
        raise ValueError('Select an excerpt from the submitted text')
    start, end = value['start'], value['end']
    if type(start) is not int or type(end) is not int or start < 0 or end > len(text) or end <= start:
        raise ValueError('Evidence selection is outside the submitted text')
    if not text[start:end].strip():
        raise ValueError('Evidence selection is empty')
    return (SourceSelection(field, start, end),)


def _excerpts(text):
    """Offer exact offsets; the reviewer still selects evidence per meaning."""
    boundaries = set('。！？；;\n')
    spans, start = [], 0
    for index, char in enumerate(text):
        if char in boundaries:
            if text[start:index + 1].strip():
                spans.append({'start': start, 'end': index + 1,
                              'text': text[start:index + 1].strip()})
            start = index + 1
    if text[start:].strip():
        spans.append({'start': start, 'end': len(text), 'text': text[start:].strip()})
    if len(spans) != 1:
        spans.append({'start': 0, 'end': len(text), 'text': text})
    return spans


def _review_from_payload(payload, candidate, text, snapshot):
    """Convert explicit UI selections into the existing G review contract."""
    if not isinstance(payload, dict) or set(payload) != {
            'facts', 'accounts', 'positions', 'coverage_note', 'uncertainties',
            'mode', 'replacement', 'occurrence'}:
        raise ValueError('Review fields are incomplete')
    facts = {fact.id: fact for fact in candidate.interpretation.candidate_facts}
    if payload['occurrence'] is not None:
        if payload['facts'] or payload['accounts'] or payload['positions'] or payload['replacement']:
            raise ValueError('Actual occurrence needs separate review')
        item = payload['occurrence']
        if not isinstance(item, dict) or set(item) != {
                'fact_id', 'source', 'account_source', 'position_ref', 'basis', 'coverage_source',
                'coverage_rationale', 'freshness_current'}:
            raise ValueError('Actual review fields are incomplete')
        fact = facts.get(item['fact_id'])
        actuals = [value for value in facts.values() if value.type in ('actual_expense', 'actual_inflow')]
        if fact not in actuals or len(facts) != 1:
            raise ValueError('Review one actual occurrence in its own Candidate')
        position = (_ref(item['position_ref'], snapshot, 'asset_position')
                    if item['position_ref'] is not None else None)
        basis = BalanceBasis(item['basis']) if item['basis'] is not None else None
        if type(item['freshness_current']) is not bool:
            raise ValueError('Explicit current-evidence review required')
        freshness = (FreshnessAssessment(FreshnessStatus.CURRENT,
            'Reviewer affirmed current occurrence and balance coverage evidence',
            datetime.now(timezone.utc)) if item['freshness_current'] else None)
        return OccurrenceReview(
            OccurrenceKind.EXPENSE if fact.type == 'actual_expense' else OccurrenceKind.INFLOW,
            candidate_amount(fact, candidate.interpretation.default_currency),
            TemporalSpec.unknown('Exact occurrence time not resolved', fact.timing_note),
            _source(item['source'], text, 'fact') +
            (_source(item['account_source'], text, 'account') if item['account_source'] else ()),
            position, basis,
            _source(item['coverage_source'], text, 'coverage') if item['coverage_source'] else (),
            item['coverage_rationale'] or None, freshness=freshness, fact_id=fact.id)

    if not isinstance(payload['facts'], list) or not isinstance(payload['accounts'], list) or not isinstance(payload['positions'], list):
        raise ValueError('Review collections must be lists')
    reviewed_facts = []
    for item in payload['facts']:
        if not isinstance(item, dict) or set(item) != {
                'id', 'source', 'earmark_role', 'emergency_use', 'decision_ids',
                'funding_fact_id', 'expected_ref', 'correction_reason'}:
            raise ValueError('Fact review is incomplete')
        fact = facts.get(item['id'])
        if fact is None or fact.type in ('actual_expense', 'actual_inflow'):
            raise ValueError('Unknown or separately reviewed fact')
        target = (_ref(item['expected_ref'], snapshot, {
            'monthly_essential_outflow': 'monthly_essential_outflow',
            'current_asset': 'asset_position', 'earmarked_current_asset': 'earmark',
            'expected_future_inflow': 'future_inflow', 'planned_expense': 'planned_expense',
            'contemplated_decision': 'world_decision', 'context': 'decision_context'}[fact.type])
            if item['expected_ref'] is not None else None)
        if not isinstance(item['decision_ids'], list) or any(type(v) is not str for v in item['decision_ids']):
            raise ValueError('Reviewed decision relationships are invalid')
        reviewed_facts.append(FactReview(
            fact.id, _source(item['source'], text, 'fact'),
            earmark_role=EarmarkRole(item['earmark_role']),
            emergency_use_designated=item['emergency_use'],
            decision_ids=tuple(item['decision_ids']),
            funding_fact_id=item['funding_fact_id'], expected_ref=target,
            correction_reason=item['correction_reason'] or None))
    accounts = []
    for item in payload['accounts']:
        if not isinstance(item, dict) or set(item) != {'key', 'label', 'source', 'existing_ref'}:
            raise ValueError('Account review is incomplete')
        if type(item['key']) is not str or type(item['label']) is not str:
            raise ValueError('Account identity is invalid')
        excerpt = _source(item['source'], text, 'account')
        if item['label'] not in text[excerpt[0].start:excerpt[0].end]:
            raise ValueError('Selected account evidence does not name the account')
        accounts.append(AccountReview(item['key'], item['label'], snapshot.world.owner_id,
            excerpt, _ref(item['existing_ref'], snapshot, 'account') if item['existing_ref'] else None))
    positions = []
    for item in payload['positions']:
        if not isinstance(item, dict) or set(item) != {
                'key', 'account_key', 'balance_ids', 'allocation_ids', 'composition',
                'expected_ref', 'correction_reason'}:
            raise ValueError('Position review is incomplete')
        if not isinstance(item['balance_ids'], list) or not isinstance(item['allocation_ids'], list):
            raise ValueError('Balance composition is invalid')
        positions.append(PositionReview(item['key'], item['account_key'],
            tuple(item['balance_ids']), tuple(item['allocation_ids']), item['composition'],
            _ref(item['expected_ref'], snapshot, 'asset_position') if item['expected_ref'] else None,
            item['correction_reason'] or None))
    replacement = None
    mode = payload['mode']
    if mode not in ('merge', 'independent_merge', 'replace'):
        raise ValueError('Choose a review scope')
    if mode == 'replace':
        scope = payload['replacement']
        if not isinstance(scope, dict) or set(scope) != {'account_refs', 'currencies'}:
            raise ValueError('Explicit replacement scope required')
        if not isinstance(scope['account_refs'], list) or not isinstance(scope['currencies'], list):
            raise ValueError('Replacement selection is invalid')
        selected_accounts = tuple(_ref(value, snapshot, 'account') for value in scope['account_refs'])
        currencies = tuple(Currency(value) for value in scope['currencies'])
        positions_in_scope = tuple(ref for ref in snapshot.world.members
            if ref.record_kind == 'asset_position' and
            (record := snapshot.get(ref)).account_ref in selected_accounts and record.currency in currencies)
        earmarks_in_scope = tuple(ref for ref in snapshot.world.members
            if ref.record_kind == 'earmark' and snapshot.get(ref).position_ref in positions_in_scope)
        replacement = ReplacementScope(selected_accounts, currencies,
            positions_in_scope, earmarks_in_scope)
    elif payload['replacement'] is not None:
        raise ValueError('Replacement selection requires scoped replacement mode')
    if type(payload['coverage_note']) is not str or not isinstance(payload['uncertainties'], list):
        raise ValueError('Coverage and uncertainty review are invalid')
    if any(type(value) is not str for value in payload['uncertainties']):
        raise ValueError('Uncertainty acknowledgement is invalid')
    return ReviewRequest(tuple(reviewed_facts), tuple(accounts), tuple(positions),
        replacement, mode == 'independent_merge', payload['coverage_note'] or None,
        tuple(payload['uncertainties']))


def _amount_view(record):
    if record is None:
        return None
    for field in ('balance', 'allocation', 'expected_amount', 'amount', 'actual_amount'):
        value = getattr(record, field, None)
        if value is not None and hasattr(value, 'precision'):
            return proposal_wire(value)
    return None


def _proposal_projection_view(proposal, snapshot):
    """Replace generated partition keys with reviewed account labels for UI only."""
    result = proposal_wire(proposal.projection)
    changes = proposal.reviewed.change_set
    if changes is None:
        return result
    labels = {ref: snapshot.get(ref).label for ref in snapshot.world.members
              if ref.record_kind == 'account'}
    for operation in changes.operations:
        if type(operation) is PutRecord and type(operation.record) is Account:
            labels[record_ref(operation.record)] = operation.record.label
    for item, operation in zip(result['items'], changes.operations):
        if type(operation) is PutRecord and type(operation.record) is AssetPosition:
            item['subject'] = (labels.get(operation.record.account_ref, 'Account')
                               + ' · ' + operation.record.currency.value)
    return result


def _preview_view(preview, snapshot):
    rows = []
    system_rows = []
    account_labels = {}
    positions = {}
    for ref in snapshot.world.members:
        if ref.record_kind == 'account':
            account_labels[ref] = snapshot.get(ref).label
        elif ref.record_kind == 'asset_position':
            positions[ref] = snapshot.get(ref)
    for change in preview.changes:
        if record_ref(change.after).record_kind == 'account':
            account_labels[record_ref(change.after)] = change.after.label
        elif record_ref(change.after).record_kind == 'asset_position':
            positions[record_ref(change.after)] = change.after
    for change in preview.changes:
        kind = record_ref(change.after).record_kind
        if kind not in ('account', 'asset_position', 'earmark', 'future_inflow',
                        'planned_expense', 'world_decision', 'decision_context',
                        'actual_occurrence', 'balance_effect', 'monthly_essential_outflow'):
            system_rows.append({'kind': kind,
                                'before_ref': proposal_wire(record_ref(change.before)) if change.before else None,
                                'after_ref': proposal_wire(record_ref(change.after))})
            continue
        effect_position = positions.get(change.after.position_after_ref) if kind == 'balance_effect' else None
        rows.append({'kind': kind, 'subject': getattr(change.after, 'label', None)
                     or getattr(change.after, 'purpose', None)
                     or getattr(change.after, 'subject', None)
                     or getattr(change.after, 'statement', None)
                     or (change.after.kind.value if kind == 'actual_occurrence' else None)
                     or (account_labels.get(change.after.account_ref, 'Account')
                         + ' · ' + change.after.currency.value
                         if kind == 'asset_position' else None)
                     or (account_labels.get(effect_position.account_ref, '')
                         if effect_position is not None else None)
                     or kind,
                     'before': _amount_view(change.before),
                     'after': _amount_view(change.after),
                     'retired': change.leaves_current_scope,
                     'cancelled': getattr(getattr(change.after, 'status', None), 'value', None) == 'cancelled',
                     'before_status': getattr(getattr(change.before, 'status', None), 'value', None),
                     'after_status': getattr(getattr(change.after, 'status', None), 'value', None),
                     'text_changes': [{'field': f, 'before': getattr(change.before, f), 'after': getattr(change.after, f)}
                         for f in ('label', 'purpose', 'subject', 'question', 'statement')
                         if change.before is not None and hasattr(change.after, f)
                         and getattr(change.before, f) != getattr(change.after, f)],
                     'before_text': next((getattr(change.before, f) for f in ('label', 'purpose', 'subject', 'statement')
                                          if getattr(change.before, f, None)), None),
                     'after_text': next((getattr(change.after, f) for f in ('label', 'purpose', 'subject', 'statement')
                                         if getattr(change.after, f, None)), None),
                     'effect_direction': change.after.direction if kind == 'balance_effect' else None,
                     'before_ref': proposal_wire(record_ref(change.before)) if change.before else None,
                     'after_ref': proposal_wire(record_ref(change.after))})
    return {'id': preview.ref.id, 'digest': preview.digest,
            'before_world': proposal_wire(preview.before_world.ref),
            'after_world': proposal_wire(preview.after_world.ref),
            'changes': rows, 'system_changes': system_rows,
            'exact_change_count': len(preview.changes),
            'retained_refs': proposal_wire(preview.retained_refs),
            'pending': proposal_wire(preview.pending),
            'preserved_categories': list(preview.preserved_categories),
            'destructive': preview.destructive}


class I3Workflow:
    """Session-local I3 coordinator.  There is intentionally no execute method."""

    def __init__(self, authority: UserWorldAuthority, understanding, world_store):
        if authority.context.kind is not ContextKind.USER:
            raise ValueError('I3 requires an explicit User World')
        self.authority = authority
        self.understanding = understanding
        self.store = world_store
        self.proposals = ReviewedProposalAPI(authority, understanding, world_store)
        self.context = authority.context
        self._lock = RLock()
        self._provider_gate = Lock()
        self._interpret_requests = {}
        self._active_interpret_request = None
        self._text: Optional[str] = None
        self._session = None
        self._proposal = None
        self._preview = None
        self._grant = None
        self._interaction = None
        self._interaction_answers = {}

    @classmethod
    def bootstrap(cls, provider):
        """Create an empty, distinct User World only for an explicit User flow."""
        identity = uuid4().hex
        now = datetime.now(timezone.utc)
        context = WorldContext(
            ContextKind.USER, 'user:' + identity, 'user/' + identity, 'user-world/' + identity)
        observation = ObservationMetadata(RealitySource.USER_REPORT, (),
            (Ref('empty-world-initialization/' + identity, 1),), None,
            TemporalSpec.unknown('No asset observation submitted'), now,
            ObservationRole.CURRENT,
            Confidence(ConfidenceAssessment.UNASSESSED, 'Empty User World initialization'),
            FreshnessAssessment(FreshnessStatus.UNKNOWN, 'No financial observations', now))
        authority = UserWorldAuthority.empty(context, identity, observation)
        return cls(authority, AgentUnderstandingService(provider), authority._source)

    def _cancel_preview(self):
        if self._preview is not None:
            try:
                self.store.cancel(self.context.owner_id, self._preview.ref)
            except Rejected:
                pass
        self._preview = self._grant = None

    def interpret(self, text, request_id=None, provider_call=None, route=None):
        if not isinstance(text, str) or not text.strip() or len(text) > 20_000:
            raise ProviderError('invalid_input', 'Input must contain 1 to 20,000 characters')
        request_id = request_id or uuid4().hex
        with self._lock:
            if (type(request_id) is not str or not request_id or len(request_id) > 128
                    or request_id in self._interpret_requests):
                raise I3Error('interpretation_request_invalid', 400)
            if len(self._interpret_requests) >= 4096:
                raise I3Error('interpretation_registry_full', 503)
            read = self.authority.read()
            if not read.initialized:
                raise I3Error('user_world_not_initialized')
            if (self._active_interpret_request is not None and
                    self._interpret_requests[self._active_interpret_request]['status'] == 'active'):
                raise I3Error('interpretation_busy')
            # A new intent cannot leave an older review or grant actionable.
            self._cancel_preview()
            if self._proposal is not None:
                self.proposals.cancel(self.context, self._proposal.proposal_id)
            previous = self.understanding.candidate
            if previous is not None and previous.status == 'awaiting_confirmation':
                self.understanding.cancel(previous.id, previous.generation, previous.revision)
            self._text = None
            self._session = self._proposal = None
            self._interaction = None
            self._interaction_answers = {}
            self._interpret_requests[request_id] = {'status': 'active', 'candidate_id': None}
            self._active_interpret_request = request_id
            captured = read.version

            if route is not None:
                try:
                    routing = route(text, read, request_id)
                except Exception:
                    self._interpret_requests[request_id]['status'] = 'failed'
                    self._active_interpret_request = None
                    raise
                if routing is not None:
                    self._interpret_requests[request_id]['status'] = 'ready'
                    self._active_interpret_request = None
                    return {'routing': routing}

        def current():
            with self._lock:
                return (self._active_interpret_request == request_id and
                        self._interpret_requests[request_id]['status'] == 'active')

        try:
            # The gate limits this User workflow to one provider call at a time.
            # Waiting and provider I/O hold neither workflow nor Candidate locks.
            with self._provider_gate:
                if not current():
                    raise ProviderError('interpretation_cancelled', 'Interpretation request was stopped')
                run = lambda: self.understanding.interpret(
                    text, captured.session_generation, captured.revision,
                    publish=False, should_continue=current)
                candidate = provider_call(run) if provider_call else run()
            with self._lock:
                if not current():
                    raise ProviderError('interpretation_cancelled', 'Interpretation request was stopped')
                if self.authority.read().version != captured:
                    raise I3Error('candidate_stale')
                with self.understanding.lock:
                    self.understanding.candidate = candidate
                self._cancel_preview()
                self._text = text
                self._session = self._proposal = None
                self._interpret_requests[request_id].update(status='ready', candidate_id=candidate.id)
                self._active_interpret_request = None
                return candidate.as_dict()
        except Exception:
            with self._lock:
                entry = self._interpret_requests[request_id]
                if entry['status'] == 'active':
                    entry['status'] = 'failed'
                if self._active_interpret_request == request_id:
                    self._active_interpret_request = None
            raise

    def stop_interpretation(self, request_id):
        if type(request_id) is not str or not request_id or len(request_id) > 128:
            raise I3Error('interpretation_request_invalid', 400)
        with self._lock:
            entry = self._interpret_requests.get(request_id)
            if entry is None:
                if len(self._interpret_requests) >= 4096:
                    raise I3Error('interpretation_registry_full', 503)
                self._interpret_requests[request_id] = {'status': 'cancelled', 'candidate_id': None}
                return 'cancelled'
            if entry['status'] == 'cancelled':
                return 'cancelled'
            if entry['status'] == 'active':
                entry['status'] = 'cancelled'
                if self._active_interpret_request == request_id:
                    self._active_interpret_request = None
                return 'cancelled'
            if entry['status'] == 'ready' and self._session is None:
                candidate = self.understanding.candidate
                if (candidate is not None and candidate.id == entry['candidate_id']
                        and candidate.status == 'awaiting_confirmation'):
                    self.understanding.cancel(candidate.id, candidate.generation, candidate.revision)
                    self._text = None
                    entry['status'] = 'cancelled'
                    return 'cancelled'
            return 'too_late'

    def start(self, candidate_id):
        with self._lock:
            candidate = self.understanding.candidate
            if candidate is None or candidate.id != candidate_id or self._text is None:
                raise I3Error('candidate_stale')
            response = self.proposals.start(self.context, self._text)
            if response.status is not ProposalStatus.REVIEW_REQUIRED:
                raise I3Error('candidate_stale' if response.status is ProposalStatus.STALE
                              else 'review_unavailable')
            self._session = response.session
            self._interaction = None
            self._interaction_answers = {}
            return self.view()

    def submit(self, session_id, request, *, prepared=False):
        with self._lock:
            if self._session is None or self._session.session_id != session_id:
                raise I3Error('review_stale')
            candidate = self.understanding.candidate
            if (candidate is None or candidate.id != self._session.candidate_id
                    or self.proposals.requirements(self.context, session_id).status
                    is ProposalStatus.STALE):
                raise I3Error('review_stale')
            try:
                typed = (_review_from_payload(request, candidate,
                                              self._text, self.store.snapshot)
                         if isinstance(request, dict) else request)
            except (ValueError, TypeError, KeyError):
                raise I3Error('review_incomplete', 400) from None
            self._cancel_preview()
            if not prepared:
                self._interaction = None
                self._interaction_answers = {}
            response = self.proposals.submit(self.context, session_id, typed,
                                             datetime.now(timezone.utc))
            if response.status is ProposalStatus.STALE:
                raise I3Error('review_stale')
            if response.status is ProposalStatus.INVALID_REVIEW:
                raise I3Error('review_incomplete', 400)
            self._proposal = response.proposal
            return self.view()

    def prepare(self, candidate_id):
        """Explicit application transition; never invoked by a status/read call."""
        with self._lock:
            candidate = self.understanding.candidate
            if candidate is None or candidate.id != candidate_id or self._text is None:
                raise I3Error('candidate_stale')
            if self._session is None:
                self.start(candidate_id)
            if self.proposals.requirements(self.context, self._session.session_id).status is ProposalStatus.STALE:
                raise I3Error('review_stale')
            if self._preview is not None:
                return self.view()
            return self._prepare_interaction()

    def clarify(self, session_id, answers):
        with self._lock:
            if (self._session is None or self._session.session_id != session_id or
                    self._interaction is None or self._preview is not None or
                    self.proposals.requirements(self.context, session_id).status is ProposalStatus.STALE):
                raise I3Error('review_stale')
            allowed = {row['id'] for row in self._interaction.questions}
            if type(answers) is not dict or not answers or not set(answers) <= allowed:
                raise I3Error('review_incomplete', 400)
            merged = deepcopy(dict(self._interaction_answers, **answers))
            # Validate completely before publishing even application-only answers.
            try:
                plan = prepare_interaction(self.understanding.candidate, self._text,
                                           self.authority.read(), merged)
                if not plan.questions and not plan.blockers:
                    _review_from_payload(plan.payload, self.understanding.candidate,
                                         self._text, self.store.snapshot)
            except (ValueError, TypeError, KeyError):
                raise I3Error('review_incomplete', 400) from None
            self._interaction_answers = merged
            return self._prepare_interaction()

    def _prepare_interaction(self):
        try:
            self._interaction = prepare_interaction(self.understanding.candidate,
                self._text, self.authority.read(), self._interaction_answers)
        except (ValueError, TypeError, KeyError):
            raise I3Error('review_incomplete', 400) from None
        if not self._interaction.questions and not self._interaction.blockers:
            result = self.submit(self._session.session_id, self._interaction.payload, prepared=True)
            if result['stage'] == 'proposal':
                return self.preview(self._proposal.proposal_id)
        return self.view()

    def preview(self, proposal_id):
        with self._lock:
            if self._proposal is None or self._proposal.proposal_id != proposal_id:
                raise I3Error('proposal_stale')
            if self.proposals.status(self.context, proposal_id).status is not ProposalStatus.READY_FOR_PROPOSAL:
                raise I3Error('proposal_stale')
            if self._proposal.reviewed.change_set is None:
                raise I3Error('proposal_blocked')
            try:
                self._cancel_preview()
                self._preview = self.store.preview(self.context.owner_id,
                    self._proposal.reviewed.change_set, Ref('i3-preview/' + uuid4().hex, 1))
            except (Rejected, StaleVersion, ValueError):
                raise I3Error('preview_stale') from None
            return self.view()

    def authorize(self, proposal_id, preview_id, digest):
        with self._lock:
            if (self._proposal is None or self._preview is None
                    or self._proposal.proposal_id != proposal_id
                    or self._preview.ref.id != preview_id or self._preview.digest != digest):
                raise I3Error('preview_stale')
            if self.proposals.status(self.context, proposal_id).status is not ProposalStatus.READY_FOR_PROPOSAL:
                self._cancel_preview()
                raise I3Error('proposal_stale')
            if self._grant is not None:
                return self.view()
            before = self.store.snapshot
            try:
                self._grant = self.store.authorize(self.context.owner_id, self._preview,
                    Ref('i3-authorization/' + uuid4().hex, 1), datetime.now(timezone.utc))
            except (Rejected, StaleVersion, ValueError):
                raise I3Error('preview_stale') from None
            if self.store.snapshot is not before:
                raise AssertionError('I3 authorization must never publish a World mutation')
            return self.view()

    def cancel(self):
        with self._lock:
            if self._active_interpret_request is not None:
                self._interpret_requests[self._active_interpret_request]['status'] = 'cancelled'
                self._active_interpret_request = None
            self._cancel_preview()
            if self._proposal is not None:
                self.proposals.cancel(self.context, self._proposal.proposal_id)
            candidate = self.understanding.candidate
            if candidate is not None and candidate.status == 'awaiting_confirmation':
                try:
                    self.understanding.cancel(candidate.id, candidate.generation,
                                              candidate.revision)
                except ProviderError:
                    pass
            self._session = self._proposal = None
            self._text = None
            self._interaction = None
            self._interaction_answers = {}
            return self.view()

    def view(self):
        with self._lock:
            read = self.authority.read()
            candidate = self.understanding.candidate
            stage = 'idle'
            reasons = []
            proposal = self._proposal
            if candidate is not None and candidate.status == 'awaiting_confirmation':
                stage = 'understanding'
            if self._session is not None:
                check = self.proposals.requirements(self.context, self._session.session_id)
                if check.status is ProposalStatus.STALE:
                    stage = 'stale'; reasons = ['candidate_or_world_stale']
                    self._cancel_preview()
                elif stage != 'stale':
                    stage = 'review'
            if proposal is not None and stage != 'stale':
                check = self.proposals.status(self.context, proposal.proposal_id)
                if check.status in (ProposalStatus.STALE, ProposalStatus.CANCELLED):
                    stage = 'stale'; reasons = list(check.reasons)
                    self._cancel_preview()
                elif check.status is ProposalStatus.READY_FOR_PROPOSAL:
                    stage = 'authorized' if self._grant is not None else 'preview' if self._preview else 'proposal'
                else:
                    stage = 'review'; reasons = list(check.reasons)
            return {'stage': stage, 'reasons': reasons,
                'context': proposal_wire(self.context),
                'world_version': proposal_wire(read.version),
                'candidate': candidate.as_dict() if candidate and candidate.status == 'awaiting_confirmation' else None,
                'submitted_text': self._text if candidate and candidate.status == 'awaiting_confirmation' else None,
                'excerpts': _excerpts(self._text) if self._text else [],
                'requirements': proposal_wire(self._session.requirements) if self._session else None,
                'session_id': self._session.session_id if self._session else None,
                'interaction': {'questions': deepcopy(list(self._interaction.questions)),
                    'blockers': list(self._interaction.blockers),
                    'evidence': deepcopy(self._interaction.evidence),
                    'binding': {'session_id': self._session.session_id,
                        'world_version': proposal_wire(self._session.world_version),
                        'review_digest': proposal.review_digest if proposal else None,
                        'change_set_digest': proposal.change_set_digest if proposal else None}}
                    if self._interaction and self._session else None,
                'proposal': {'id': proposal.proposal_id, 'status': proposal.status.value,
                    'projection': _proposal_projection_view(proposal, read.snapshot),
                    'review_mode': ('replace' if proposal.reviewed.request.replacement_scope else
                        'add_only' if proposal.reviewed.request.independent_merge else 'merge')
                        if isinstance(proposal.reviewed.request, ReviewRequest) else 'actual',
                    'review_digest': proposal.review_digest,
                    'change_set_digest': proposal.change_set_digest,
                    'issues': proposal_wire(proposal.reviewed.issues),
                    'outcomes': proposal_wire(proposal.reviewed.outcomes)} if proposal else None,
                'preview': _preview_view(self._preview, read.snapshot) if self._preview else None,
                'authorization': {'id': self._grant.ref.id,
                    'preview_digest': self._grant.preview_digest,
                    'change_set_digest': self._grant.change_set_digest,
                    'world_ref': proposal_wire(self._grant.expected_world_ref)} if self._grant else None,
                'current_accounts': [
                    {'ref': proposal_wire(item.ref), 'label': item.label,
                     'positions': [proposal_wire(position.ref) for position in read.projection.positions
                                   if position.account_ref == item.ref]}
                    for item in read.projection.accounts] if read.projection else [],
                'current_currencies': [item.currency.value for item in read.projection.currencies]
                    if read.projection else [],
                'current_records': [
                    {'ref': proposal_wire(ref), 'kind': ref.record_kind,
                     'currency': read.snapshot.get(ref).amount.currency.value if ref.record_kind == 'monthly_essential_outflow' else None,
                     'label': (next((item.label for item in read.projection.accounts
                        if item.ref == read.snapshot.get(ref).account_ref), 'Current account')
                        + ' · ' + read.snapshot.get(ref).currency.value
                        + ' · ' + read.snapshot.get(ref).partition_key
                        if ref.record_kind == 'asset_position' else None)
                        or getattr(read.snapshot.get(ref), 'label', None)
                        or getattr(read.snapshot.get(ref), 'purpose', None)
                        or getattr(read.snapshot.get(ref), 'subject', None)
                        or getattr(read.snapshot.get(ref), 'statement', None)
                        or ref.ref.id}
                    for ref in read.snapshot.world.members] if read.snapshot else [],
                'world_revision': read.version.revision if read.version else None,
                'unknowns': ([name for name in ('essential_monthly_outflow', 'runway')
                    if getattr(read.projection, name).status.value != 'available'
                    and not (name == 'essential_monthly_outflow' and read.projection.monthly_essential_observations)]
                    if read.projection else []),
                'receipts': len(read.snapshot.receipts) if read.snapshot else 0}

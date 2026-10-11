"""I2 reviewed-proposal application boundary.

This module binds one validated Candidate, one exact I1 User World read and
explicit Slice G review evidence.  It can only create immutable proposals.  It
does not expose preview, authorization, command execution or settlement.
"""

from dataclasses import dataclass, fields, is_dataclass
from datetime import date, datetime
from enum import Enum
from hashlib import sha256
from threading import RLock
from typing import Any, Dict, Optional, Tuple, Union
import json

from .agent_reconciliation import (
    AgentWorldBridge, OccurrenceReview, ReviewRequest, ReviewedChanges, review_json,
)
from .agent_schema import CandidateInterpretation
from .domain import Ref
from .store import Rejected, StaleVersion
from .world_application import ContextKind, ReadVersion, UserWorldAuthority, WorldContext
from .world_commands import IssueDisposition, PutRecord, RetireRecord, record_ref
from .world_domain import Account, AssetPosition, Earmark
from .world_planning import DecisionContext, FutureInflow, PlannedExpense, WorldDecision
from .world_projection import ProjectionError
from .world_settlement import SettleOccurrence
from .world_values import AmountEstimate, TypedRef


class ProposalStatus(Enum):
    REVIEW_REQUIRED = 'review_required'
    READY_FOR_PROPOSAL = 'ready_for_proposal'
    CLARIFICATION_REQUIRED = 'clarification_required'
    UNRESOLVED = 'unresolved'
    UNSUPPORTED = 'unsupported'
    STALE = 'stale'
    INVALID_REVIEW = 'invalid_review'
    CANCELLED = 'cancelled'


class ProposalAction(Enum):
    ADD = 'add'
    CHANGE_REPLACE = 'change_replace'
    PRESERVE = 'preserve'
    REMOVE_RETIRE = 'remove_retire'
    UNRESOLVED = 'unresolved'


class ProposalEffect(Enum):
    CURRENT_ASSET_OBSERVATION = 'current_asset_observation'
    ALLOCATION_ONLY = 'allocation_only'
    NO_FINANCIAL_EFFECT = 'no_financial_effect'
    SETTLEMENT_REVIEW_REQUIRED = 'settlement_review_required'
    HISTORY_PRESERVED = 'history_preserved'
    UNRESOLVED = 'unresolved'


@dataclass(frozen=True)
class ProposalAmount:
    currency: Optional[str]
    precision: str
    minor_units: Optional[int]
    lower_minor_units: Optional[int]
    upper_minor_units: Optional[int]
    missing_reason: Optional[str]


@dataclass(frozen=True)
class ReviewRequirement:
    fact_id: str
    fact_type: str
    review_contract: str
    account_identity_required: bool
    position_composition_required: bool
    relationship_review_supported: bool


@dataclass(frozen=True)
class ReviewRequirements:
    facts: Tuple[ReviewRequirement, ...]
    replacement_scope_required: bool
    unresolved_candidate_items: Tuple[str, ...]
    accepted_review_types: Tuple[str, ...] = ('ReviewRequest', 'OccurrenceReview')


@dataclass(frozen=True)
class ReviewSession:
    session_id: str
    context: WorldContext
    candidate_id: str
    candidate_generation: str
    candidate_revision: int
    candidate_digest: str
    world_version: ReadVersion
    submitted_text_digest: str
    requirements: ReviewRequirements


@dataclass(frozen=True)
class ProposalItem:
    action: ProposalAction
    record_kind: str
    subject: str
    effect: ProposalEffect
    amount: Optional[ProposalAmount] = None
    target_ref: Optional[TypedRef] = None
    reason: Optional[str] = None


@dataclass(frozen=True)
class ProposalProjection:
    heading: str
    items: Tuple[ProposalItem, ...]
    semantic_digest: str


@dataclass(frozen=True)
class ReviewedProposal:
    proposal_id: str
    status: ProposalStatus
    session: ReviewSession
    review_digest: str
    reviewed_ref: Ref
    reviewed_digest: str
    change_set_ref: Optional[Ref]
    change_set_digest: Optional[str]
    projection: ProposalProjection
    reviewed: ReviewedChanges


@dataclass(frozen=True)
class ProposalResponse:
    status: ProposalStatus
    reasons: Tuple[str, ...] = ()
    session: Optional[ReviewSession] = None
    proposal: Optional[ReviewedProposal] = None

    def as_dict(self) -> Dict[str, Any]:
        return _wire(self)


@dataclass
class _SessionState:
    public: ReviewSession
    bridge: AgentWorldBridge
    active_review_digest: Optional[str] = None
    active_proposal_id: Optional[str] = None


def _wire(value):
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return {field.name: _wire(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, tuple):
        return [_wire(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _wire(item) for key, item in value.items()}
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def _hash(value: Any) -> str:
    if isinstance(value, str):
        payload = value
    else:
        payload = json.dumps(_wire(value), ensure_ascii=False, sort_keys=True,
                             separators=(',', ':'))
    return sha256(payload.encode('utf-8')).hexdigest()


def _candidate_digest(interpretation: CandidateInterpretation) -> str:
    return _hash(interpretation.as_dict())


def _amount(value: Optional[AmountEstimate]) -> Optional[ProposalAmount]:
    if value is None:
        return None
    return ProposalAmount(
        value.currency.value if value.currency is not None else None,
        value.precision.value,
        value.amount.minor_units if value.amount is not None else None,
        value.lower_bound.minor_units if value.lower_bound is not None else None,
        value.upper_bound.minor_units if value.upper_bound is not None else None,
        value.missing_reason,
    )


def _record_item(operation: PutRecord) -> ProposalItem:
    from .world_commands import MonthlyEssentialOutflow
    record = operation.record
    kind = record_ref(record).record_kind
    action = ProposalAction.CHANGE_REPLACE if operation.expected_ref is not None else ProposalAction.ADD
    if type(record) is MonthlyEssentialOutflow:
        return ProposalItem(action, kind, 'Monthly essential outflow', ProposalEffect.NO_FINANCIAL_EFFECT,
                            _amount(record.amount), operation.expected_ref)
    if type(record) is Account:
        return ProposalItem(action, kind, record.label, ProposalEffect.NO_FINANCIAL_EFFECT,
                            target_ref=operation.expected_ref)
    if type(record) is AssetPosition:
        return ProposalItem(action, kind, record.partition_key,
                            ProposalEffect.CURRENT_ASSET_OBSERVATION, _amount(record.balance),
                            operation.expected_ref)
    if type(record) is Earmark:
        return ProposalItem(action, kind, record.purpose, ProposalEffect.ALLOCATION_ONLY,
                            _amount(record.allocation), operation.expected_ref)
    if type(record) is FutureInflow:
        return ProposalItem(action, kind, record.purpose, ProposalEffect.NO_FINANCIAL_EFFECT,
                            _amount(record.expected_amount), operation.expected_ref)
    if type(record) is PlannedExpense:
        return ProposalItem(action, kind, record.purpose, ProposalEffect.NO_FINANCIAL_EFFECT,
                            _amount(record.expected_amount), operation.expected_ref)
    if type(record) is WorldDecision:
        return ProposalItem(action, kind, record.subject, ProposalEffect.NO_FINANCIAL_EFFECT,
                            _amount(record.amount), operation.expected_ref)
    if type(record) is DecisionContext:
        return ProposalItem(action, kind, record.statement, ProposalEffect.NO_FINANCIAL_EFFECT,
                            target_ref=operation.expected_ref)
    raise ValueError('Unsupported proposed record')


def _projection(reviewed: ReviewedChanges) -> ProposalProjection:
    items = []
    if reviewed.change_set is not None:
        for operation in reviewed.change_set.operations:
            if type(operation) is PutRecord:
                items.append(_record_item(operation))
            elif type(operation) is RetireRecord:
                effect = (ProposalEffect.CURRENT_ASSET_OBSERVATION
                          if operation.expected_ref.record_kind in ('account', 'asset_position')
                          else ProposalEffect.ALLOCATION_ONLY
                          if operation.expected_ref.record_kind == 'earmark'
                          else ProposalEffect.NO_FINANCIAL_EFFECT)
                items.append(ProposalItem(ProposalAction.REMOVE_RETIRE,
                    operation.expected_ref.record_kind,
                    operation.expected_ref.ref.id, effect,
                    target_ref=operation.expected_ref))
            elif type(operation) is SettleOccurrence:
                report = operation.report
                items.append(ProposalItem(ProposalAction.ADD, 'actual_occurrence',
                    report.kind.value, ProposalEffect.SETTLEMENT_REVIEW_REQUIRED,
                    _amount(report.actual_amount),
                    operation.coverage.position_ref if operation.coverage else operation.expected_ref,
                    'Slice E settlement review is required; I2 does not execute it'))
            else:
                raise ValueError('Unsupported proposed operation')
        for ref in reviewed.change_set.retained_refs:
            items.append(ProposalItem(ProposalAction.PRESERVE, ref.record_kind, ref.ref.id,
                ProposalEffect.HISTORY_PRESERVED, target_ref=ref))
        if reviewed.change_set.replacement_scope is not None:
            items.append(ProposalItem(ProposalAction.PRESERVE, 'audit_history',
                'immutable prior records and command history',
                ProposalEffect.HISTORY_PRESERVED,
                reason='Scoped replacement retires active observations; it does not erase history'))
    for issue in reviewed.issues:
        items.append(ProposalItem(ProposalAction.UNRESOLVED, 'review_issue', issue.code,
            ProposalEffect.UNRESOLVED, reason=issue.rationale))
    for outcome in reviewed.outcomes:
        if outcome.disposition != 'ready' and not any(
            item.subject == outcome.fact_id and item.record_kind == 'candidate_fact' for item in items
        ):
            items.append(ProposalItem(ProposalAction.UNRESOLVED, 'candidate_fact', outcome.fact_id,
                ProposalEffect.UNRESOLVED, reason=outcome.reason))
    items = tuple(sorted(items, key=lambda item: (
        item.action.value, item.record_kind, item.subject,
        item.target_ref.ref.id if item.target_ref else '',
        item.target_ref.ref.version if item.target_ref else -1,
    )))
    # New Slice G record IDs are audit identities, not proposal meaning.  They
    # remain in the bound CandidateChangeSet but are deliberately absent here.
    semantic = _hash(items)
    return ProposalProjection('What FINVIS would change', items, semantic)


def _requirements(interpretation: CandidateInterpretation) -> ReviewRequirements:
    facts = []
    for fact in interpretation.candidate_facts:
        actual = fact.type in ('actual_expense', 'actual_inflow')
        current = fact.type in ('current_asset', 'earmarked_current_asset')
        facts.append(ReviewRequirement(
            fact.id, fact.type,
            'OccurrenceReview' if actual else 'FactReview',
            current or (actual and fact.account is not None),
            current or actual,
            fact.type in ('earmarked_current_asset', 'planned_expense',
                          'contemplated_decision', 'context', 'actual_expense', 'actual_inflow'),
        ))
    unresolved = tuple(interpretation.conflicts + interpretation.clarification_questions
                       + interpretation.unsupported_requests)
    return ReviewRequirements(tuple(facts), bool(interpretation.destructive_actions), unresolved)


def _review_status(reviewed: ReviewedChanges) -> ProposalStatus:
    dispositions = {issue.disposition for issue in reviewed.issues}
    outcomes = {outcome.disposition for outcome in reviewed.outcomes}
    if IssueDisposition.UNSUPPORTED in dispositions or 'unsupported' in outcomes:
        return ProposalStatus.UNSUPPORTED
    if IssueDisposition.CONFLICT in dispositions:
        return ProposalStatus.UNRESOLVED
    if IssueDisposition.NEEDS_USER_INPUT in dispositions or 'needs_user_input' in outcomes:
        return ProposalStatus.CLARIFICATION_REQUIRED
    if reviewed.change_set is None:
        return ProposalStatus.UNRESOLVED
    return ProposalStatus.READY_FOR_PROPOSAL


class ReviewedProposalAPI:
    """Small typed application API for User World proposal lifecycle only."""

    def __init__(self, authority: UserWorldAuthority, understanding, world_store):
        if not isinstance(authority, UserWorldAuthority):
            raise ValueError('I1 UserWorldAuthority required')
        if authority.context.kind is not ContextKind.USER:
            raise ValueError('Reviewed proposals require User World context')
        self._authority = authority
        self._understanding = understanding
        self._world_store = world_store
        self._sessions: Dict[str, _SessionState] = {}
        self._proposals: Dict[str, ReviewedProposal] = {}
        self._cancelled = set()
        self._superseded = set()
        self._lock = RLock()

    @property
    def context(self):
        return self._authority.context

    def _context_ok(self, context):
        return type(context) is WorldContext and context == self.context

    def _current(self, session: ReviewSession):
        read = self._authority.read(session.world_version)
        if (read.snapshot is not self._world_store.snapshot
                or session.world_version.session_generation != self._world_store.session_generation):
            raise StaleVersion('Authority source changed')
        candidate = self._understanding.candidate
        if (candidate is None or candidate.status != 'awaiting_confirmation'
                or candidate.id != session.candidate_id
                or candidate.generation != session.candidate_generation
                or candidate.revision != session.candidate_revision
                or _candidate_digest(candidate.interpretation) != session.candidate_digest):
            raise StaleVersion('Candidate changed or closed')
        return read

    def start(self, context: WorldContext, submitted_text: str) -> ProposalResponse:
        with self._lock:
            if not self._context_ok(context):
                return ProposalResponse(ProposalStatus.INVALID_REVIEW, ('user_context_required',))
            try:
                read = self._authority.read()
                if not read.initialized or read.version is None:
                    return ProposalResponse(ProposalStatus.UNRESOLVED, ('user_world_not_initialized',))
                if (read.snapshot is not self._world_store.snapshot
                        or read.version.session_generation != self._world_store.session_generation):
                    return ProposalResponse(ProposalStatus.INVALID_REVIEW,
                                            ('authority_store_mismatch',))
                candidate = self._understanding.candidate
                if candidate is None or candidate.status != 'awaiting_confirmation':
                    return ProposalResponse(ProposalStatus.STALE, ('candidate_missing_or_closed',))
                if (candidate.generation != read.version.session_generation
                        or candidate.revision != read.version.revision):
                    return ProposalResponse(ProposalStatus.STALE, ('candidate_world_version_mismatch',))
                bridge = AgentWorldBridge(self._understanding, self._world_store)
                bridge.attach(submitted_text)
                digest = _candidate_digest(candidate.interpretation)
                text_digest = _hash(submitted_text)
                identity = _hash((context, candidate.id, candidate.generation,
                                  candidate.revision, digest, read.version, text_digest))
                session = ReviewSession('review-session/' + identity, context, candidate.id,
                    candidate.generation, candidate.revision, digest, read.version,
                    text_digest, _requirements(candidate.interpretation))
                existing = self._sessions.get(session.session_id)
                if existing is None:
                    self._sessions[session.session_id] = _SessionState(session, bridge)
                return ProposalResponse(ProposalStatus.REVIEW_REQUIRED,
                    session.requirements.unresolved_candidate_items, session)
            except (ValueError, Rejected, ProjectionError):
                return ProposalResponse(ProposalStatus.INVALID_REVIEW, ('invalid_session_request',))
            except StaleVersion:
                return ProposalResponse(ProposalStatus.STALE, ('candidate_or_world_stale',))

    def requirements(self, context: WorldContext, session_id: str) -> ProposalResponse:
        with self._lock:
            state = self._sessions.get(session_id)
            if not self._context_ok(context) or state is None:
                return ProposalResponse(ProposalStatus.INVALID_REVIEW, ('unknown_session_or_context',))
            try:
                self._current(state.public)
                return ProposalResponse(ProposalStatus.REVIEW_REQUIRED,
                    state.public.requirements.unresolved_candidate_items, state.public)
            except (StaleVersion, ProjectionError):
                return ProposalResponse(ProposalStatus.STALE, ('candidate_or_world_stale',), state.public)

    def submit(self, context: WorldContext, session_id: str,
               request: Union[ReviewRequest, OccurrenceReview], at: datetime) -> ProposalResponse:
        with self._lock:
            state = self._sessions.get(session_id)
            if not self._context_ok(context) or state is None:
                return ProposalResponse(ProposalStatus.INVALID_REVIEW, ('unknown_session_or_context',))
            if type(request) not in (ReviewRequest, OccurrenceReview):
                return ProposalResponse(ProposalStatus.INVALID_REVIEW, ('typed_review_required',), state.public)
            try:
                self._current(state.public)
                request_digest = _hash(json.loads(review_json(request)))
                if (state.active_review_digest == request_digest
                        and state.active_proposal_id in self._proposals
                        and state.active_proposal_id not in self._cancelled):
                    proposal = self._proposals[state.active_proposal_id]
                    return ProposalResponse(proposal.status, (), state.public, proposal)
                if state.active_proposal_id is not None:
                    self._superseded.add(state.active_proposal_id)
                reviewed = (state.bridge.review_occurrence(request, at)
                            if type(request) is OccurrenceReview
                            else state.bridge.review(request, at))
                status = _review_status(reviewed)
                projection = _projection(reviewed)
                change_digest = (_hash(json.loads(review_json(reviewed.change_set)))
                                 if reviewed.change_set is not None else None)
                proposal_id = 'reviewed-proposal/' + _hash((
                    state.public.session_id, request_digest, reviewed.digest,
                    reviewed.change_set.ref if reviewed.change_set else None, change_digest,
                ))
                proposal = ReviewedProposal(proposal_id, status, state.public,
                    request_digest, reviewed.ref, reviewed.digest,
                    reviewed.change_set.ref if reviewed.change_set else None,
                    change_digest, projection, reviewed)
                self._proposals[proposal_id] = proposal
                state.active_review_digest = request_digest
                state.active_proposal_id = proposal_id
                reasons = tuple(item.subject for item in projection.items
                                if item.action is ProposalAction.UNRESOLVED)
                return ProposalResponse(status, reasons, state.public, proposal)
            except (StaleVersion, ProjectionError):
                return ProposalResponse(ProposalStatus.STALE, ('candidate_or_world_stale',), state.public)
            except (ValueError, Rejected, TypeError):
                return ProposalResponse(ProposalStatus.INVALID_REVIEW, ('review_rejected',), state.public)

    def status(self, context: WorldContext, proposal_id: str) -> ProposalResponse:
        with self._lock:
            proposal = self._proposals.get(proposal_id)
            if not self._context_ok(context) or proposal is None:
                return ProposalResponse(ProposalStatus.INVALID_REVIEW, ('unknown_proposal_or_context',))
            if proposal_id in self._cancelled:
                return ProposalResponse(ProposalStatus.CANCELLED, ('proposal_discarded',),
                                        proposal.session, proposal)
            if proposal_id in self._superseded:
                return ProposalResponse(ProposalStatus.STALE, ('review_evidence_changed',),
                                        proposal.session, proposal)
            try:
                self._current(proposal.session)
                return ProposalResponse(proposal.status, (), proposal.session, proposal)
            except (StaleVersion, ProjectionError):
                return ProposalResponse(ProposalStatus.STALE, ('candidate_or_world_stale',),
                                        proposal.session, proposal)

    def cancel(self, context: WorldContext, proposal_id: str) -> ProposalResponse:
        with self._lock:
            proposal = self._proposals.get(proposal_id)
            if not self._context_ok(context) or proposal is None:
                return ProposalResponse(ProposalStatus.INVALID_REVIEW, ('unknown_proposal_or_context',))
            self._cancelled.add(proposal_id)
            for state in self._sessions.values():
                if state.active_proposal_id == proposal_id:
                    state.active_proposal_id = None
                    state.active_review_digest = None
            return ProposalResponse(ProposalStatus.CANCELLED, ('proposal_discarded',),
                                    proposal.session, proposal)

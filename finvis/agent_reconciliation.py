"""Slice G: deterministic, explicitly reviewed proposals, never model authority.

This module has no provider calls or application/UI routes. Review inputs come
from the trusted local user boundary. They are not part of the model schema.
"""

from dataclasses import dataclass, fields, is_dataclass, replace
from datetime import datetime
from enum import Enum
import hashlib
import json
from typing import Optional, Tuple, Union
from uuid import uuid4

from .agent_schema import ACTUAL_FACT_TYPES, CandidateFact, CandidateInterpretation
from .domain import (Confidence, ConfidenceAssessment, Currency, DecisionStatus,
                     Evidence, RealitySource, Ref, aware)
from .store import Rejected, StaleVersion
from .world_commands import (CandidateChangeSet, ChangeMode, FinancialWorldCommand,
    IssueDisposition, PutRecord, ReconciliationIssue, ReplacementScope,
    RetireRecord, MonthlyEssentialOutflow, record_ref, validate_fields)
from .world_domain import (Account, AccountKind, AccessAssessment, AccessStatus,
    AssetPosition, AssetPositionKind, Earmark, EarmarkRole, sum_amount_estimates)
from .world_planning import (DecisionContext, FutureInflow, FutureInflowStatus,
    PlannedExpense, PlannedExpenseStatus, WorldDecision, FundingLink,
    ActualOccurrence, OccurrenceKind, SettlementStatus)
from .world_values import (AmountEstimate, FreshnessAssessment, FreshnessStatus,
    ObservationMetadata, ObservationRole, TemporalSpec, TypedRef, money_from_major_units)
from .world_settlement import (BalanceBasis, BalanceCoverage, SettleOccurrence,
    propose_settlement, reconcile_settlement)


def _data(value):
    if is_dataclass(value):
        from .world_fulfillment import compatible_fields
        return {f.name: _data(getattr(value, f.name)) for f in compatible_fields(value)}
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, 'isoformat'):
        return value.isoformat()
    if type(value) is tuple:
        return [_data(item) for item in value]
    if value is None or type(value) in (str, int, bool):
        return value
    raise ValueError('Unsupported review value')


def review_json(value):
    """Portable minimal review evidence; loading JSON confers no authority."""
    return json.dumps({'schema_version': 'finvis.agent-review.v0.1', 'value': _data(value)},
                      ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _digest(value):
    return hashlib.sha256(review_json(value).encode()).hexdigest()


@dataclass(frozen=True)
class SourceSelection:
    """Explicit field-to-excerpt binding into the transient submitted text."""
    field: str
    start: int
    end: int

    def __post_init__(self):
        validate_fields(self)
        if self.field not in ('fact', 'amount', 'timing', 'account', 'relationship', 'coverage'):
            raise ValueError('Unsupported evidence field')
        if self.end <= self.start:
            raise ValueError('Evidence excerpt must be nonempty')


@dataclass(frozen=True)
class FactReview:
    fact_id: str
    sources: Tuple[SourceSelection, ...]
    timing: Optional[TemporalSpec] = None
    earmark_role: EarmarkRole = EarmarkRole.OTHER
    emergency_use_designated: bool = False
    decision_ids: Tuple[str, ...] = ()
    funding_fact_id: Optional[str] = None
    expected_ref: Optional[TypedRef] = None
    correction_reason: Optional[str] = None
    freshness: Optional[FreshnessAssessment] = None

    def __post_init__(self):
        validate_fields(self)
        if (self.expected_ref is None) != (self.correction_reason is None):
            raise ValueError('Correction target and reason required together')


@dataclass(frozen=True)
class AccountReview:
    key: str
    label: str
    owner_id: str
    sources: Tuple[SourceSelection, ...]
    existing_ref: Optional[TypedRef] = None

    def __post_init__(self):
        validate_fields(self)


@dataclass(frozen=True)
class PositionReview:
    """Explicit balance composition; allocations are never added to a total.

    total: exactly one balance fact. disjoint: explicitly reviewed, distinct
    partitions summed once. allocation_ids name non-additive purpose claims.
    """
    key: str
    account_key: str
    balance_ids: Tuple[str, ...]
    allocation_ids: Tuple[str, ...] = ()
    composition: str = 'total'
    expected_ref: Optional[TypedRef] = None
    correction_reason: Optional[str] = None

    def __post_init__(self):
        validate_fields(self)
        if self.composition not in ('total', 'disjoint') or not self.balance_ids:
            raise ValueError('Explicit total or disjoint composition required')
        if self.composition == 'total' and len(self.balance_ids) != 1:
            raise ValueError('A total has exactly one balance fact')
        if len(set(self.balance_ids)) != len(self.balance_ids) or len(set(self.allocation_ids)) != len(self.allocation_ids):
            raise ValueError('Duplicate balance/allocation fact')
        if (self.expected_ref is None) != (self.correction_reason is None):
            raise ValueError('Position correction needs target and reason')


@dataclass(frozen=True)
class ReviewRequest:
    facts: Tuple[FactReview, ...]
    accounts: Tuple[AccountReview, ...] = ()
    positions: Tuple[PositionReview, ...] = ()
    replacement_scope: Optional[ReplacementScope] = None
    independent_merge: bool = False
    coverage_note: Optional[str] = None
    acknowledged_uncertainties: Tuple[str, ...] = ()

    def __post_init__(self):
        validate_fields(self)
        for values, key in ((self.facts, 'fact_id'), (self.accounts, 'key'), (self.positions, 'key')):
            if len({getattr(item, key) for item in values}) != len(values):
                raise ValueError('Duplicate review identity')
        if self.independent_merge and self.replacement_scope is not None:
            raise ValueError('Independent merge cannot carry replacement scope')


@dataclass(frozen=True)
class OccurrenceReview:
    """Explicit review of one actual Candidate fact or a legacy user addendum.

    It is never a reinterpretation of an expected/planned amount. Unknown
    source/destination or coverage cannot settle.
    """
    kind: OccurrenceKind
    amount: AmountEstimate
    timing: TemporalSpec
    sources: Tuple[SourceSelection, ...]
    position_ref: Optional[TypedRef] = None
    basis: Optional[BalanceBasis] = None
    coverage_sources: Tuple[SourceSelection, ...] = ()
    coverage_rationale: Optional[str] = None
    related_ref: Optional[TypedRef] = None
    earmark_ref: Optional[TypedRef] = None
    expected_ref: Optional[TypedRef] = None
    correction_reason: Optional[str] = None
    corrects_effect_ref: Optional[TypedRef] = None
    freshness: Optional[FreshnessAssessment] = None
    fact_id: Optional[str] = None

    def __post_init__(self):
        validate_fields(self)
        if (self.expected_ref is None) != (self.correction_reason is None):
            raise ValueError('Occurrence correction needs target and reason')


@dataclass(frozen=True)
class FactOutcome:
    fact_id: str
    disposition: str
    reason: str
    record_refs: Tuple[TypedRef, ...] = ()


@dataclass(frozen=True)
class EvidenceBinding:
    fact_id: str
    field: str
    evidence: Evidence


@dataclass(frozen=True)
class ReviewedChanges:
    ref: Ref
    candidate_id: str
    candidate_digest: str
    generation: str
    revision: int
    facts: Tuple[CandidateFact, ...]
    request: Union[ReviewRequest, OccurrenceReview]
    bindings: Tuple[EvidenceBinding, ...]
    outcomes: Tuple[FactOutcome, ...]
    issues: Tuple[ReconciliationIssue, ...]
    change_set: Optional[CandidateChangeSet]

    @property
    def committable_count(self):
        if self.change_set is None:
            return 0
        return sum(item.disposition == 'ready' for item in self.outcomes)

    @property
    def digest(self):
        return _digest(self)

    @property
    def binding_digest(self):
        """Bind evidence/meaning/relations into D consent without a hash cycle."""
        return _digest((self.ref, self.candidate_id, self.candidate_digest, self.generation,
            self.revision, self.facts, self.request, self.bindings, self.outcomes, self.issues))


def _bind_review(value):
    if value.change_set is None:
        return value
    return replace(value, change_set=replace(value.change_set,
        candidate_refs=(Ref('reviewed-interpretation/' + value.binding_digest, 1),)))


def _amount(fact, default_currency):
    raw = fact.amount
    currency = Currency((raw.currency if raw else None) or default_currency)
    if raw is None or raw.precision == 'unknown':
        return AmountEstimate.unknown('Amount not reported', currency)
    money = lambda text: money_from_major_units(text, currency)
    if raw.precision == 'exact':
        return AmountEstimate.exact_reported(money(raw.value))
    if raw.precision == 'approximate':
        return AmountEstimate.approximate(money(raw.value))
    return AmountEstimate.range(money(raw.range_min), money(raw.range_max))


def _metadata(refs, timing, role, at, expected=None, reason=None):
    return ObservationMetadata(RealitySource.USER_REPORT, refs, (), None, timing, at, role,
        Confidence(ConfidenceAssessment.UNASSESSED, 'Reviewed user report; not independently verified'),
        FreshnessAssessment(FreshnessStatus.UNKNOWN, 'No freshness assessment supplied', at),
        expected.ref if expected else None, reason)


def _bindings(text, selections, subject, prefix, owner_id, at):
    result = []
    for index, selection in enumerate(selections):
        if selection.end > len(text) or not text[selection.start:selection.end].strip():
            raise ValueError('Evidence offsets must select supplied source text')
        evidence = Evidence(Ref(prefix + '/evidence/' + subject + '/' + str(index), 1),
            owner_id, RealitySource.USER_REPORT, at,
            'submitted-text:{}:{}'.format(selection.start, selection.end),
            text[selection.start:selection.end])
        result.append(EvidenceBinding(subject, selection.field, evidence))
    if not result:
        raise ValueError('Explicit reviewed source excerpt required')
    return tuple(result)


def reconcile(candidate, text, request, snapshot, generation, review_ref, at):
    """Pure mapping of validated meaning plus explicit review, with no writes.

    IDs, time, snapshot and reviewed relations are inputs, so repeated mapping
    is identical. Schema validation does not certify the extracted meaning.
    """
    if type(request) is not ReviewRequest:
        raise ValueError('Typed review required')
    aware(at, 'review time')
    interpretation = CandidateInterpretation.parse(candidate.interpretation.as_dict())
    if candidate.status != 'awaiting_confirmation' or candidate.generation != generation or candidate.revision != snapshot.revision:
        raise StaleVersion('Candidate session/revision is stale or closed')
    root = snapshot.world
    prefix = 'agent-review/' + review_ref.id
    facts = {f.id: f for f in interpretation.candidate_facts}
    reviews = {r.fact_id: r for r in request.facts}
    if not set(reviews) <= set(facts):
        raise ValueError('Review names an unknown candidate fact')
    # Candidate fact IDs never become authoritative identities.
    ids = {fid: prefix + '/fact/' + str(i) for i, fid in enumerate(sorted(facts))}
    outcomes, bindings, issues, records = {}, [], [], []
    mapped = {}

    def issue(code, disposition, message):
        issues.append(ReconciliationIssue(code, disposition, message))

    def block(fid, reason, disposition='needs_user_input'):
        outcomes[fid] = FactOutcome(fid, disposition, reason)

    for fid in sorted(facts):
        if fid not in reviews:
            block(fid, 'Not selected for this explicitly reviewed bundle', 'candidate_only')
    destructive = bool(interpretation.destructive_actions) or 'replace_financial_state' in interpretation.user_intents
    if destructive and request.replacement_scope is None and not request.independent_merge:
        issue('replacement_scope_required', IssueDisposition.NEEDS_USER_INPUT, 'Enumerate current account/currency/position/earmark scope')
    if request.replacement_scope and set(reviews) != set(facts):
        issue('replacement_subset', IssueDisposition.NEEDS_USER_INPUT, 'Replacement cannot silently omit candidate facts; use a separately reviewed independent merge')
    if set(reviews) != set(facts) and not request.independent_merge:
        issue('independent_merge_required', IssueDisposition.NEEDS_USER_INPUT, 'Select an explicit independent merge for a subset')
    if interpretation.conflicts or interpretation.assumptions:
        issue('candidate_conflict', IssueDisposition.CONFLICT, 'Conflicts/assumptions require an explicitly revised interpretation; no silent repair')
    if set(interpretation.uncertainties) != set(request.acknowledged_uncertainties):
        issue('uncertainty_review_required', IssueDisposition.NEEDS_USER_INPUT, 'Explicitly review all candidate uncertainties; retained uncertainty does not prove a balance or relationship')
    if interpretation.unsupported_requests and not request.independent_merge:
        issue('unsupported_request', IssueDisposition.UNSUPPORTED, 'Unsupported requests require revision or an explicit independent supported merge')
    if 'correct_information' in interpretation.user_intents and not any(r.expected_ref for r in request.facts) and not any(p.expected_ref for p in request.positions):
        issue('correction_target_required', IssueDisposition.NEEDS_USER_INPUT, 'Identify exact current correction targets')

    metas = {}
    for fid, review in sorted(reviews.items()):
        fact = facts[fid]
        try:
            if fact.type in ACTUAL_FACT_TYPES:
                raise ValueError('Actual candidate facts require OccurrenceReview and Slice E')
            if review.decision_ids and fact.type != 'context':
                raise ValueError('Decision relationships here apply only to context')
            if review.funding_fact_id and fact.type != 'planned_expense':
                raise ValueError('Funding relationships here apply only to planned expenses')
            if (review.earmark_role is not EarmarkRole.OTHER or review.emergency_use_designated) and fact.type != 'earmarked_current_asset':
                raise ValueError('Allocation designation requires an earmarked fact')
            links = _bindings(text, review.sources, ids[fid].split('/')[-1], prefix, root.owner_id, at)
            links = tuple(replace(link, fact_id=fid) for link in links)
            bindings.extend(links)
            timing = review.timing or TemporalSpec.unknown('Timing not structurally resolved', fact.timing_note)
            if review.timing and fact.timing_note and review.timing.source_text != fact.timing_note:
                raise ValueError('Resolved timing must retain candidate timing source text')
            role = {'current_asset': ObservationRole.CURRENT, 'earmarked_current_asset': ObservationRole.CURRENT,
                    'monthly_essential_outflow': ObservationRole.CURRENT,
                    'expected_future_inflow': ObservationRole.FUTURE, 'planned_expense': ObservationRole.PLANNED,
                    'contemplated_decision': ObservationRole.CONTEMPLATED, 'context': ObservationRole.CONTEMPLATED}[fact.type]
            metas[fid] = _metadata(tuple(link.evidence.ref for link in links), timing, role, at,
                                   review.expected_ref, review.correction_reason)
            if review.freshness:
                if review.freshness.assessed_at > at:
                    raise ValueError('Freshness assessment cannot be in the future')
                metas[fid] = replace(metas[fid], freshness=review.freshness)
            if review.expected_ref and review.expected_ref not in root.members:
                raise ValueError('Correction target is not a current world member')
            kinds = {'current_asset': 'asset_position', 'earmarked_current_asset': 'earmark',
                     'monthly_essential_outflow': 'monthly_essential_outflow',
                     'expected_future_inflow': 'future_inflow', 'planned_expense': 'planned_expense',
                     'contemplated_decision': 'world_decision', 'context': 'decision_context'}
            if review.expected_ref and review.expected_ref.record_kind != kinds[fact.type]:
                raise ValueError('Correction target does not match the candidate fact type')
        except ValueError as error:
            block(fid, str(error))

    accounts = {}
    if {a.key for a in request.accounts} != {p.account_key for p in request.positions}:
        issue('account_composition_required', IssueDisposition.NEEDS_USER_INPUT, 'Account review must bind exactly the proposed position groups')
    for index, selection in enumerate(sorted(request.accounts, key=lambda item: item.key)):
        try:
            if selection.owner_id != root.owner_id:
                raise ValueError('Account ownership must be explicitly the world owner')
            links = _bindings(text, selection.sources, 'account-' + str(index), prefix, root.owner_id, at)
            bindings.extend(links)
            if selection.existing_ref:
                if selection.existing_ref not in root.members or selection.existing_ref.record_kind != 'account':
                    raise ValueError('Exact current account identity required')
                account = snapshot.get(selection.existing_ref)
                if account.label != selection.label:
                    raise ValueError('Existing account label mismatch; resolve alias explicitly')
            else:
                if any(ref.record_kind == 'account' and snapshot.get(ref).label == selection.label for ref in root.members):
                    raise ValueError('Existing same-label account requires explicit identity resolution')
                meta = _metadata(tuple(b.evidence.ref for b in links), TemporalSpec.unknown('Account observation as-of unknown'), ObservationRole.CURRENT, at)
                account = Account(Ref(prefix + '/account/' + str(index), 1), root.owner_id,
                    root.world_id, selection.label, None, AccountKind.CASH_CONTAINER, meta)
                records.append((account, None))
            accounts[selection.key] = account
        except ValueError as error:
            issue('account_review', IssueDisposition.NEEDS_USER_INPUT, str(error))

    claimed = set()
    for index, group in enumerate(sorted(request.positions, key=lambda item: item.key)):
        involved = set(group.balance_ids) | set(group.allocation_ids)
        try:
            if not involved <= set(reviews) or not involved <= set(metas) or involved & claimed:
                raise ValueError('Position facts must be reviewed, evidenced and used in only one pool')
            claimed.update(involved)
            account = accounts.get(group.account_key)
            if account is None:
                raise ValueError('Explicit reviewed account required')
            for fid in sorted(involved):
                if facts[fid].type not in ('current_asset', 'earmarked_current_asset'):
                    raise ValueError('Only current assets can establish current money')
                if facts[fid].account is None or facts[fid].account != account.label:
                    raise ValueError('Account association is missing/mismatched; revise meaning explicitly')
                if reviews[fid].expected_ref and fid in group.balance_ids and facts[fid].type == 'current_asset':
                    raise ValueError('Position correction target belongs on PositionReview')
            if any(facts[fid].type != 'earmarked_current_asset' for fid in group.allocation_ids):
                raise ValueError('Allocation requires an earmarked candidate fact')
            if any(facts[fid].type == 'earmarked_current_asset' and fid not in group.allocation_ids for fid in group.balance_ids):
                raise ValueError('Cannot discard a reported purpose allocation')
            amounts = [_amount(facts[fid], interpretation.default_currency) for fid in sorted(group.balance_ids)]
            currency = amounts[0].currency
            balance = sum_amount_estimates(amounts, currency)
            evidence = tuple(dict.fromkeys(ref for fid in sorted(involved) for ref in metas[fid].evidence_refs))
            times = {metas[fid].as_of for fid in group.balance_ids}
            if len(times) != 1:
                raise ValueError('Disjoint balance observations need one explicitly resolved as-of')
            meta = _metadata(evidence, next(iter(times)), ObservationRole.CURRENT, at, group.expected_ref, group.correction_reason)
            assessments = {metas[fid].freshness for fid in group.balance_ids}
            if len(assessments) == 1:
                meta = replace(meta, freshness=next(iter(assessments)))
            ref = Ref(group.expected_ref.ref.id, group.expected_ref.ref.version + 1) if group.expected_ref else Ref(prefix + '/position/' + str(index), 1)
            if group.expected_ref:
                if group.expected_ref not in root.members or group.expected_ref.record_kind != 'asset_position':
                    raise ValueError('Exact current position correction target required')
                old = snapshot.get(group.expected_ref)
                if old.account_ref != record_ref(account) or old.currency != currency:
                    raise ValueError('Cannot silently change corrected position identity/currency')
                partition = old.partition_key
            else:
                partition = group.key
                if any(r.record_kind == 'asset_position' and snapshot.get(r).account_ref == record_ref(account)
                       and snapshot.get(r).currency == currency for r in root.members) and not request.replacement_scope:
                    raise ValueError('Existing account balance requires explicit correction or scoped replacement')
            pos = AssetPosition(ref, root.owner_id, root.world_id, record_ref(account), currency,
                AssetPositionKind.CASH, partition, balance, meta,
                AccessAssessment(AccessStatus.UNKNOWN, 'Access was not assessed', replace(meta, corrects_ref=None, correction_reason=None)))
            records.append((pos, group.expected_ref))
            for fid in group.balance_ids:
                mapped[fid] = pos
            for fid in sorted(group.allocation_ids):
                fact, review = facts[fid], reviews[fid]
                if not fact.earmark and not fact.purpose:
                    raise ValueError('Allocation purpose is missing')
                mark_ref = Ref(review.expected_ref.ref.id, review.expected_ref.ref.version + 1) if review.expected_ref else Ref(ids[fid], 1)
                mark = Earmark(mark_ref, root.owner_id, root.world_id, record_ref(pos),
                    _amount(fact, interpretation.default_currency), fact.earmark or fact.purpose,
                    review.earmark_role, review.emergency_use_designated, metas[fid])
                records.append((mark, review.expected_ref)); mapped[fid] = mark
        except (ValueError, TypeError) as error:
            for fid in sorted(involved):
                if fid in facts:
                    block(fid, str(error))
            issue('position_review', IssueDisposition.NEEDS_USER_INPUT, str(error))

    asset_changed = bool(request.positions or request.replacement_scope or records)
    state_ref = Ref(root.state_ref.id, root.state_ref.version + int(asset_changed))
    # Relations are keyed by scoped fact IDs, never guessed from purpose labels.
    order = {'contemplated_decision': 0, 'expected_future_inflow': 1, 'planned_expense': 2, 'context': 3}
    for fid in sorted(reviews, key=lambda x: (order.get(facts[x].type, -1), x)):
        fact, review = facts[fid], reviews[fid]
        if fact.type in ('current_asset', 'earmarked_current_asset'):
            if fid not in mapped and fid not in outcomes:
                block(fid, 'Explicit position composition/account binding required')
            continue
        if fid not in metas or fid in outcomes:
            continue
        try:
            meta = metas[fid]
            ref = Ref(review.expected_ref.ref.id, review.expected_ref.ref.version + 1) if review.expected_ref else Ref(ids[fid], 1)
            common = (ref, root.owner_id, root.world_id)
            if fact.type == 'expected_future_inflow':
                record = FutureInflow(*common, _amount(fact, interpretation.default_currency),
                    fact.purpose or fact.label, meta.as_of, None, FutureInflowStatus.EXPECTED, meta)
            elif fact.type == 'planned_expense':
                amount = _amount(fact, interpretation.default_currency)
                funding = ()
                if review.funding_fact_id:
                    source = mapped.get(review.funding_fact_id)
                    if type(source) not in (AssetPosition, Earmark):
                        raise ValueError('Funding relationship needs an explicitly reviewed asset/allocation')
                    funding = (FundingLink(record_ref(source), amount),)
                record = PlannedExpense(*common, amount, fact.purpose or fact.label, meta.as_of,
                    funding, None, PlannedExpenseStatus.PLANNED, meta)
            elif fact.type == 'contemplated_decision':
                record = WorldDecision(*common, fact.purpose or fact.label, fact.label,
                    TypedRef('world_state', state_ref), DecisionStatus.EXPLORING, None,
                    _amount(fact, interpretation.default_currency) if fact.amount else None,
                    meta.as_of, (), meta)
            elif fact.type == 'monthly_essential_outflow':
                record = MonthlyEssentialOutflow(*common, _amount(fact, interpretation.default_currency), meta)
            else:
                if fact.amount is not None:
                    raise ValueError('Monetary context cannot be silently dropped or force-fit')
                if not review.decision_ids:
                    block(fid, 'Context has no explicit relevant decision relationship', 'context_only')
                    continue
                decisions = tuple(mapped.get(key) for key in review.decision_ids)
                if any(type(item) is not WorldDecision for item in decisions):
                    raise ValueError('Context relationship must name reviewed decisions')
                record = DecisionContext(*common, tuple(record_ref(d) for d in decisions),
                    fact.label, meta.as_of, meta)
            records.append((record, review.expected_ref)); mapped[fid] = record
        except (ValueError, TypeError) as error:
            disposition = 'unsupported' if fact.amount and (fact.amount.currency or interpretation.default_currency) not in (*[currency.value for currency in Currency], None) else 'needs_user_input'
            block(fid, str(error), disposition)

    for fid in reviews:
        if fid in mapped and fid not in outcomes:
            outcomes[fid] = FactOutcome(fid, 'ready', 'Representable after exact preview and authorization', (record_ref(mapped[fid]),))
        elif fid not in outcomes:
            block(fid, 'Fact is unresolved')
    for fid, outcome in sorted(outcomes.items()):
        if fid in reviews and outcome.disposition != 'ready':
            issue('fact/' + fid, IssueDisposition.UNSUPPORTED if outcome.disposition == 'unsupported' else IssueDisposition.NEEDS_USER_INPUT, outcome.reason)
    if asset_changed and not request.coverage_note:
        issue('coverage_required', IssueDisposition.NEEDS_USER_INPUT, 'State observations need explicit reported coverage')
    ops = []
    touched = {expected for _, expected in records if expected}
    if request.replacement_scope:
        # Retire now-empty scoped locations from the active view, preserving
        # their immutable account history. Cross-currency locations stay active.
        ops.extend(RetireRecord('retire/' + str(i), ref) for i, ref in enumerate(
            (*request.replacement_scope.earmark_refs, *request.replacement_scope.position_refs)) if ref not in touched)
        reused = {record_ref(a) for a in accounts.values()}
        for index, account_ref in enumerate(request.replacement_scope.account_refs):
            remaining = any(r.record_kind == 'asset_position' and snapshot.get(r).account_ref == account_ref
                            and r not in request.replacement_scope.position_refs for r in root.members)
            if account_ref not in reused and not remaining:
                ops.append(RetireRecord('retire-account/' + str(index), account_ref))
    ops.extend(PutRecord('put/' + str(i), record, expected) for i, (record, expected) in enumerate(records))
    changes = None
    if ops and not issues:
        touched = {op.expected_ref for op in ops if op.expected_ref}
        refs = tuple(dict.fromkeys(b.evidence.ref for b in bindings))
        changes = CandidateChangeSet(review_ref, root.owner_id, root.world_id, generation,
            root.ref, root.state_ref, root.plan_ref, snapshot.revision,
            ChangeMode.REPLACE_STATE if request.replacement_scope else ChangeMode.MERGE,
            tuple(ops), request.replacement_scope, tuple(r for r in root.members if r not in touched),
            (Ref('interpretation/' + review_ref.id, 1),),
            _metadata(refs, TemporalSpec.unknown('Reviewed bundle as-of varies by fact'), ObservationRole.CURRENT, at),
            request.coverage_note if asset_changed else None, (), at)
    return _bind_review(ReviewedChanges(review_ref, candidate.id, _digest(interpretation), generation, snapshot.revision,
        tuple(facts[fid] for fid in sorted(reviews)), request, tuple(bindings),
        tuple(outcomes[fid] for fid in sorted(outcomes)), tuple(issues), changes))


class AgentWorldBridge:
    """Opt-in service; no route enables it in the legacy demo.

    Uses the Understanding service's lock for candidate identity through commit.
    The existing WorldStore independently validates world revisions and consent.
    Callers must use the service lifecycle, not assign to its private state.
    """
    def __init__(self, understanding, world_store):
        self._understanding = understanding
        self._store = world_store
        self._candidate = None
        self._text = None
        self._review = None
        self._preview = None
        self._grant = None
        self._completed = None
        self._audit = ()

    @property
    def audit(self):
        return self._audit

    def export(self):
        """Pair F's replay with the minimal G evidence needed to explain writes.

        This produces data only, not a persistence/import/authorization service.
        Exporting F alone cannot supply source excerpts retained by this bridge.
        """
        from .world_replay import export_world_replay, serialize_world_replay
        with self._understanding.lock:
            for reviewed, receipt_ref in self._audit:
                if reviewed.change_set.candidate_refs != (Ref('reviewed-interpretation/' + reviewed.binding_digest, 1),):
                    raise Rejected('Review evidence binding changed')
                receipt = next((r for r in self._store.snapshot.receipts if r.ref == receipt_ref), None)
                if receipt is None or receipt.change_set_ref != reviewed.ref:
                    raise Rejected('Review audit must resolve its committed receipt')
            package = export_world_replay(self._store.snapshot, self._store.session_generation)
            return {'schema_version': 'finvis.agent-world-evidence.v0.1',
                    'world_replay': serialize_world_replay(package),
                    'review_audit': json.loads(review_json(self._audit))}

    def attach(self, submitted_text):
        """Bind the caller's transient original submission to current candidate."""
        with self._understanding.lock:
            candidate = self._understanding.candidate
            if not isinstance(submitted_text, str) or not submitted_text.strip() or len(submitted_text) > 20000:
                raise ValueError('Original submitted text required')
            if candidate is None:
                raise Rejected('No validated candidate')
            self._invalidate()
            self._candidate = replace(candidate, interpretation=CandidateInterpretation.parse(candidate.interpretation.as_dict()))
            self._text = submitted_text
            self._completed = None
            self._current()

    def _current(self):
        candidate = self._understanding.candidate
        if (self._candidate is None or candidate is None or candidate.id != self._candidate.id
            or candidate.status != 'awaiting_confirmation'
            or candidate.interpretation != self._candidate.interpretation
            or candidate.generation != self._candidate.generation or candidate.revision != self._candidate.revision
            or candidate.generation != self._store.session_generation
            or candidate.revision != self._store.snapshot.revision):
            raise StaleVersion('Candidate changed, closed, or world/session advanced')

    def _invalidate(self):
        if self._preview and self._completed is None:
            self._store.cancel(self._store.snapshot.world.owner_id, self._preview.ref)
        self._review = self._preview = self._grant = None

    def review(self, request, at):
        with self._understanding.lock:
            self._current()
            self._invalidate()
            self._review = reconcile(self._candidate, self._text, request, self._store.snapshot,
                self._store.session_generation, Ref('review/' + uuid4().hex, 1), at)
            return self._review

    def preview(self, actor, reviewed):
        with self._understanding.lock:
            self._current()
            if reviewed != self._review or reviewed.change_set is None:
                raise Rejected('Exact registered, resolved review required')
            if self._preview is None:
                self._preview = self._store.preview(actor, reviewed.change_set, Ref('preview/' + uuid4().hex, 1))
            elif actor != reviewed.change_set.owner_id:
                raise Rejected('Wrong actor')
            return self._preview

    def review_occurrence(self, request, at):
        """Reconcile one reviewed actual Candidate fact exclusively through E."""
        with self._understanding.lock:
            self._current()
            if type(request) is not OccurrenceReview:
                raise ValueError('Explicit typed occurrence review required')
            aware(at, 'review time')
            self._invalidate()
            ref = Ref('review/' + uuid4().hex, 1)
            snapshot = self._store.snapshot
            root = snapshot.world
            interpretation = self._candidate.interpretation
            actual_facts = {fact.id: fact for fact in interpretation.candidate_facts
                            if fact.type in ACTUAL_FACT_TYPES}
            fact = None
            if request.fact_id is not None:
                fact = actual_facts.get(request.fact_id)
                if fact is None:
                    raise ValueError('OccurrenceReview must name an actual Candidate fact')
                expected_kind = (OccurrenceKind.EXPENSE if fact.type == 'actual_expense'
                                 else OccurrenceKind.INFLOW)
                if request.kind is not expected_kind:
                    raise ValueError('Occurrence kind conflicts with the Candidate fact')
                if request.amount != _amount(fact, interpretation.default_currency):
                    raise ValueError('Occurrence amount must preserve Candidate evidence')
                if fact.timing_note and request.timing.source_text != fact.timing_note:
                    raise ValueError('Occurrence timing must retain Candidate timing source text')
                if fact.account is None and request.position_ref is not None:
                    raise ValueError('Unknown Candidate account cannot become a reviewed position')
                if request.position_ref is not None and not any(
                        selection.field == 'account' for selection in request.sources):
                    raise ValueError('Known occurrence position requires reviewed account evidence')
                if request.position_ref is not None:
                    position = snapshot.get(request.position_ref)
                    if type(position) is not AssetPosition:
                        raise ValueError('Occurrence review requires an asset position')
                    account = snapshot.get(position.account_ref)
                    if type(account) is not Account:
                        raise ValueError('Occurrence position must resolve to an account')
            elif actual_facts:
                raise ValueError('Exact actual Candidate fact review required')
            subject = fact.id if fact else 'reviewed_actual'
            bindings = _bindings(self._text, request.sources, subject, ref.id, root.owner_id, at)
            meta = _metadata(tuple(b.evidence.ref for b in bindings), request.timing,
                             ObservationRole.ACTUAL, at, request.expected_ref, request.correction_reason)
            if request.freshness:
                if request.freshness.assessed_at > at:
                    raise ValueError('Freshness assessment cannot be in the future')
                meta = replace(meta, freshness=request.freshness)
            occurrence_ref = Ref(request.expected_ref.ref.id, request.expected_ref.ref.version + 1) if request.expected_ref else Ref(ref.id + '/occurrence', 1)
            report = ActualOccurrence(occurrence_ref, root.owner_id, root.world_id,
                request.kind, request.amount, request.timing,
                (request.related_ref,) if request.related_ref else (), SettlementStatus.UNALLOCATED, meta,
                request.position_ref if request.kind is OccurrenceKind.EXPENSE else None,
                request.position_ref if request.kind is OccurrenceKind.INFLOW else None)
            coverage = None
            if request.basis and request.position_ref:
                coverage_bindings = _bindings(self._text, request.coverage_sources, 'coverage', ref.id, root.owner_id, at)
                bindings += coverage_bindings
                coverage_meta = replace(meta, evidence_refs=tuple(b.evidence.ref for b in coverage_bindings),
                                        corrects_ref=None, correction_reason=None)
                coverage = BalanceCoverage(record_ref(report), request.position_ref, request.basis,
                                           coverage_meta, request.coverage_rationale)
            operation = SettleOccurrence(ref.id, report, request.expected_ref, coverage,
                                        request.related_ref, request.earmark_ref, request.corrects_effect_ref)
            result = reconcile_settlement(snapshot, operation)
            changes = None
            if result.ready:
                changes = propose_settlement(snapshot, self._store.session_generation, operation, ref)
                changes = replace(changes, candidate_refs=(Ref('interpretation/' + ref.id, 1),),
                    observation=replace(changes.observation, evidence_refs=tuple(b.evidence.ref for b in bindings)))
            self._review = _bind_review(ReviewedChanges(ref, self._candidate.id, _digest(self._candidate.interpretation),
                self._candidate.generation, self._candidate.revision, (fact,) if fact else (), request, bindings,
                (FactOutcome(subject, 'ready' if result.ready else 'needs_user_input',
                             'Reviewed actual occurrence; Slice E determines settlement', (record_ref(report),)),),
                result.issues, changes))
            return self._review

    def authorize(self, actor, reviewed, preview, at):
        with self._understanding.lock:
            self._current()
            if reviewed != self._review or preview != self._preview or preview is None:
                raise Rejected('Exact reviewed preview required')
            self._grant = self._store.authorize(actor, preview, Ref('authorization/' + uuid4().hex, 1), at)
            return self._grant

    def execute(self, actor, reviewed, preview, grant, at):
        with self._understanding.lock:
            if reviewed != self._review or preview != self._preview or grant != self._grant or grant is None or actor != grant.actor:
                raise Rejected('Exact review, preview and registered authorization required')
            if self._completed is not None:
                if at != self._execution_at:
                    raise Rejected('Retry must preserve the original command timestamp')
                return self._completed
            self._current()
            receipt = self._store.execute(FinancialWorldCommand('agent/' + grant.ref.id, actor,
                reviewed.change_set, preview.ref, preview.digest, grant.ref, at))
            self._audit += ((reviewed, receipt.ref),)
            self._completed = receipt
            self._execution_at = at
            self._text = None
            self._understanding.candidate = replace(self._candidate, status='committed')
            return receipt

    def cancel(self, actor):
        with self._understanding.lock:
            if actor != self._store.snapshot.world.owner_id:
                raise Rejected('Wrong actor')
            if self._completed is not None:
                raise Rejected('Committed changes require a new reviewed correction')
            self._invalidate()
            if self._candidate and self._understanding.candidate and self._understanding.candidate.id == self._candidate.id:
                self._understanding.cancel(self._candidate.id, self._candidate.generation, self._candidate.revision)
            self._candidate = self._text = None

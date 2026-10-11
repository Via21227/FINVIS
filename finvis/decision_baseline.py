"""D1 derived Decision evidence. No financial writer, verdict or provider fallback.

Versioned artifacts are session-local, separate from durable FinancialWorld.
Exact record dependencies mirror I6.2: unrelated revisions do not invalidate;
changed/retired dependencies, context, evidence and generation do.
"""
from dataclasses import dataclass
from datetime import date, datetime, timezone
import hashlib
import json
from typing import Optional, Protocol, Tuple
from uuid import uuid4

from .agent_schema import CandidateAmount
from .domain import Currency, Ref
from .world_application import ContextKind, UserWorldAuthority
from .world_domain import sum_amount_estimates
from .world_planning import WorldDecision, PlannedExpense, PlannedExpenseStatus
from .world_values import AmountEstimate, AmountPrecision, TypedRef, money_from_major_units
from .reviewed_proposals import _wire


CATEGORIES = ('other', 'travel', 'flight', 'accommodation', 'dining', 'local_transport',
              'activity', 'camera', 'concert', 'phone', 'emergency', 'electronics', 'moving', 'training')


def text(value, optional=False):
    if optional and value is None:
        return None
    if type(value) is not str or not value.strip() or len(value) > 1000:
        raise ValueError('Bounded nonempty text required')
    return value.strip()


def fields(value, expected):
    if type(value) is not dict or set(value) != set(expected.split()):
        raise ValueError('Exact D1 fields required')
    return value


def strings(value):
    if type(value) is not list or len(value) > 16:
        raise ValueError('Bounded assumption list required')
    return tuple(text(s) for s in value)


def instant(value):
    result = datetime.fromisoformat(text(value).replace('Z', '+00:00'))
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError('Evidence timestamps require a timezone')
    return result


def amount(value):
    parsed = CandidateAmount.parse(value, 'baseline.amount')
    if parsed is None or parsed.currency not in ('CNY', 'SGD', 'EUR'):
        raise ValueError('Native supported currency required; no FX')
    currency = Currency(parsed.currency)
    if parsed.precision == 'unknown':
        return AmountEstimate.unknown('baseline_amount_unknown', currency)
    money = lambda v: money_from_major_units(v, currency)
    if parsed.precision == 'range':
        return AmountEstimate.range(money(parsed.range_min), money(parsed.range_max))
    return (AmountEstimate.approximate if parsed.precision == 'approximate'
            else AmountEstimate.exact_reported)(money(parsed.value))


def typed_ref(value):
    fields(value, 'record_kind ref')
    fields(value['ref'], 'id version')
    return TypedRef(text(value['record_kind']), Ref(text(value['ref']['id']), value['ref']['version']))


@dataclass(frozen=True)
class BaselineContext:
    category: str
    origin: Optional[str]
    destination: Optional[str]
    location: Optional[str]
    country_code: Optional[str]
    start_date: Optional[str]
    end_date: Optional[str]
    date_note: Optional[str]
    duration_days: Optional[int]
    nights: Optional[int]
    party_size: Optional[int]
    assumptions: Tuple[str, ...]

    @classmethod
    def parse(cls, value):
        fields(value, 'category origin destination location country_code start_date end_date date_note duration_days nights party_size assumptions')
        if value['category'] not in CATEGORIES:
            raise ValueError('Unknown semantic category')
        data = dict(value)
        for name in ('origin', 'destination', 'location', 'country_code', 'start_date', 'end_date', 'date_note'):
            data[name] = text(data[name], True)
        for name in ('duration_days', 'nights', 'party_size'):
            v = data[name]
            if v is not None and (type(v) is not int or v < (0 if name == 'nights' else 1) or v > 10000):
                raise ValueError('Invalid explicit duration or party size')
        if data['country_code'] is not None and (len(data['country_code']) != 2 or not data['country_code'].isascii() or not data['country_code'].isupper() or not data['country_code'].isalpha()):
            raise ValueError('Country cue requires a two-letter code')
        for name in ('start_date', 'end_date'):
            if data[name] is not None:
                if date.fromisoformat(data[name]).isoformat() != data[name]:
                    raise ValueError('ISO dates required')
        if data['end_date'] and not data['start_date']:
            raise ValueError('Date window needs its start')
        if data['end_date'] and data['end_date'] < data['start_date']:
            raise ValueError('Reversed date window')
        data['assumptions'] = strings(data['assumptions'])
        return cls(**data)

    def missing(self, categories):
        if self.category != 'travel':
            return ()
        required = ['destination', 'duration_days', 'party_size']
        if 'flight' in categories:
            required.append('origin')
        if 'accommodation' in categories:
            required.append('nights')
        missing = [key for key in required if getattr(self, key) is None]
        if self.start_date is None and self.date_note is None:
            missing.append('dates')
        return tuple(missing)


@dataclass(frozen=True)
class ExternalEstimate:
    """A bounded observation, not scraped prose, a booked item or Reality."""
    evidence_ref: str
    category: str
    source: str
    provider: str
    observed_amount: AmountEstimate
    observed_at: datetime
    retrieved_at: datetime
    valid_until: Optional[datetime]
    freshness: str
    context: BaselineContext
    assumptions: Tuple[str, ...]

    @classmethod
    def parse(cls, value, at):
        fields(value, 'evidence_ref category source provider observed_amount observed_at retrieved_at valid_until freshness context assumptions')
        if value['category'] not in CATEGORIES or value['freshness'] not in ('current', 'stale', 'unknown'):
            raise ValueError('Invalid evidence category or freshness')
        observed, retrieved = instant(value['observed_at']), instant(value['retrieved_at'])
        until = instant(value['valid_until']) if value['valid_until'] is not None else None
        if observed > retrieved or retrieved > at or (until is not None and until < observed):
            raise ValueError('Invalid evidence chronology')
        return cls(text(value['evidence_ref']), value['category'], text(value['source']), text(value['provider']),
                   amount(value['observed_amount']), observed, retrieved, until, value['freshness'],
                   BaselineContext.parse(value['context']), strings(value['assumptions']))

    def state(self, at):
        if self.freshness == 'stale' or (self.valid_until is not None and at >= self.valid_until):
            return 'stale'
        if self.freshness == 'unknown' or self.valid_until is None:
            return 'unknown'
        return 'current'


@dataclass(frozen=True)
class ResearchResult:
    status: str
    evidence: Tuple[ExternalEstimate, ...] = ()
    reason: str = 'research_provider_not_configured'


class EstimateResearchProvider(Protocol):
    def research(self, context: BaselineContext, category: str) -> ResearchResult:
        """Adapter owns bounded I/O. Results must pass ExternalEstimate validation."""
        ...


class UnavailableResearchProvider:
    def research(self, context, category):
        return ResearchResult('research_unavailable')


@dataclass(frozen=True)
class BaselineComponent:
    id: str
    label: str
    category: str
    basis: str
    observed_amount: AmountEstimate
    planning_amount: AmountEstimate
    assumptions: Tuple[str, ...]
    world_ref: Optional[TypedRef] = None
    external: Optional[ExternalEstimate] = None


@dataclass(frozen=True)
class DecisionBaseline:
    ref: Ref
    decision_ref: TypedRef
    owner_id: str
    world_id: str
    generation: str
    source_revision: int
    created_at: datetime
    context: BaselineContext
    components: Tuple[BaselineComponent, ...]
    dependencies: Tuple[Tuple[TypedRef, str], ...]
    linked_contexts: Tuple[TypedRef, ...]
    explicit_dependencies: Tuple[TypedRef, ...]
    scope_complete: bool
    buffer: Optional[AmountEstimate]
    buffer_reason: Optional[str]
    request_digest: str


def digest(value):
    return hashlib.sha256(json.dumps(_wire(value), ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def linked(snapshot, decision):
    return tuple(sorted((ref for ref in snapshot.world.members if ref.record_kind == 'decision_context'
                         and any(r.ref.id == decision.ref.id for r in snapshot.get(ref).decision_refs)),
                        key=lambda r: (r.ref.id, r.ref.version)))


class BaselineService:
    """Read-only authority consumer; caller serializes access with workflow lock.

    Retains immutable versions until service restart, bounded without eviction.
    Stable baseline ID + version is a future D2 input, never an additive rule.
    """
    def __init__(self, authority, research=None):
        if not isinstance(authority, UserWorldAuthority) or authority.context.kind is not ContextKind.USER:
            raise ValueError('User authority required')
        self.authority = authority
        self.research_provider = research or UnavailableResearchProvider()
        self.versions = {}

    def establish(self, request, at=None):
        at = at or datetime.now(timezone.utc)
        fields(request, 'decision_ref expected_version context components dependency_refs scope_complete buffer')
        read = self.authority.read()
        snapshot = read.snapshot
        decision_ref = typed_ref(request['decision_ref'])
        if snapshot is None or decision_ref not in snapshot.world.members or decision_ref.record_kind != 'world_decision':
            raise ValueError('Current reviewed User Decision required')
        decision = snapshot.get(decision_ref)
        context = BaselineContext.parse(request['context'])
        if type(request['scope_complete']) is not bool or type(request['expected_version']) is not int:
            raise ValueError('Explicit cost coverage and version required')
        previous = self.versions.get(decision_ref.ref.id, ())
        if request['expected_version'] != len(previous):
            raise ValueError('Baseline version changed; refresh before editing')
        if len(previous) >= 64 or (not previous and len(self.versions) >= 128):
            raise ValueError('Session baseline capacity reached')
        if type(request['components']) is not list or len(request['components']) > 32:
            raise ValueError('At most 32 explicit components')
        if type(request['dependency_refs']) is not list or len(request['dependency_refs']) > 32:
            raise ValueError('Bounded dependency set required')
        contexts = linked(snapshot, decision_ref)
        explicit_dependencies = tuple(typed_ref(r) for r in request['dependency_refs'])
        refs = {decision_ref, *contexts, *explicit_dependencies}
        components = []
        for raw in request['components']:
            fields(raw, 'id label category basis amount assumptions world_ref external')
            category, basis = raw['category'], raw['basis']
            if category not in CATEGORIES or basis not in ('user_assumption', 'world', 'external'):
                raise ValueError('Explicit component category and basis required')
            source_ref, external = None, None
            assumptions = strings(raw['assumptions'])
            if basis == 'world':
                if raw['amount'] is not None or raw['external'] is not None:
                    raise ValueError('World amounts are read from exact records, not caller prices')
                source_ref = typed_ref(raw['world_ref'])
                if source_ref not in snapshot.world.members or source_ref.record_kind not in ('world_decision', 'planned_expense'):
                    raise ValueError('Cost evidence must reference a current Decision or planned expense')
                record = snapshot.get(source_ref)
                if type(record) is PlannedExpense and record.status not in (PlannedExpenseStatus.PLANNED, PlannedExpenseStatus.PARTIALLY_REALIZED):
                    raise ValueError('Closed expenses are not expected future cost')
                observed = record.amount if type(record) is WorldDecision else record.expected_amount
                if observed is None:
                    raise ValueError('World cost amount is unknown')
                refs.add(source_ref)
            elif basis == 'external':
                if raw['amount'] is not None or raw['world_ref'] is not None:
                    raise ValueError('External price comes from its evidence')
                external = ExternalEstimate.parse(raw['external'], at)
                if external.category != category or external.context != context:
                    raise ValueError('Evidence must match the explicit planning context and category')
                observed = external.observed_amount
            else:
                if raw['world_ref'] is not None or raw['external'] is not None or not assumptions:
                    raise ValueError('User estimate requires an explicit assumption')
                observed = amount(raw['amount'])
            # Exact quotes remain intact as evidence, not guaranteed future cost.
            planning = AmountEstimate.approximate(observed.amount) if observed.precision is AmountPrecision.EXACT_REPORTED else observed
            components.append(BaselineComponent(text(raw['id']), text(raw['label']), category, basis,
                                               observed, planning, assumptions, source_ref, external))
        if len({c.id for c in components}) != len(components):
            raise ValueError('Duplicate component identity')
        external_refs = [c.external.evidence_ref for c in components if c.external]
        world_refs = [c.world_ref for c in components if c.world_ref]
        if len(set(external_refs)) != len(external_refs) or len(set(world_refs)) != len(world_refs):
            raise ValueError('A cost observation cannot be counted twice')
        for ref in world_refs:
            record = snapshot.get(ref)
            if type(record) is PlannedExpense and record.decision_ref in world_refs:
                raise ValueError('Linked Decision and expense cannot both be counted as cost')
        for old in previous:
            for c in old.components:
                if c.external:
                    for new in components:
                        if new.external and new.external.evidence_ref == c.external.evidence_ref and new.external != c.external:
                            raise ValueError('Changed evidence needs a new evidence reference')
        if any(r not in snapshot.world.members for r in refs):
            raise ValueError('Every dependency must be a current exact User record')
        buffer, reason = None, None
        if request['buffer'] is not None:
            fields(request['buffer'], 'amount reason')
            buffer, reason = amount(request['buffer']['amount']), text(request['buffer']['reason'])
        baseline = DecisionBaseline(Ref(previous[-1].ref.id if previous else 'baseline/' + uuid4().hex, len(previous)+1),
            decision_ref, snapshot.world.owner_id, snapshot.world.world_id, read.version.session_generation,
            snapshot.revision, at, context, tuple(components),
            tuple((r, digest(snapshot.get(r))) for r in sorted(refs, key=lambda r: (r.record_kind, r.ref.id))),
            contexts, explicit_dependencies, request['scope_complete'], buffer, reason, digest(request))
        self.versions[decision_ref.ref.id] = (*previous, baseline)
        return self.view(baseline, at)

    def view(self, baseline, at=None):
        at = at or datetime.now(timezone.utc)
        read = self.authority.read()
        snapshot = read.snapshot
        reasons = []
        if (read.version is None or read.version.session_generation != baseline.generation
                or read.context.owner_id != baseline.owner_id or read.context.world_id != baseline.world_id):
            reasons.append('authority_changed')
        elif any(r not in snapshot.world.members or digest(snapshot.get(r)) != h for r, h in baseline.dependencies):
            reasons.append('world_inputs_changed')
        elif linked(snapshot, baseline.decision_ref) != baseline.linked_contexts:
            reasons.append('decision_context_changed')
        latest = self.versions.get(baseline.decision_ref.ref.id, ())
        if latest and latest[-1].ref != baseline.ref:
            reasons.append('baseline_superseded')
        if any(c.external and c.external.state(at) == 'stale' for c in baseline.components):
            reasons.append('evidence_stale')
        world_freshness = []
        if snapshot is not None:
            world_freshness = [snapshot.get(r).observation.freshness.status.value for r, _ in baseline.dependencies
                               if r in snapshot.world.members]
        if 'stale' in world_freshness:
            reasons.append('world_evidence_stale')
        missing = baseline.context.missing({c.category for c in baseline.components})
        uncertain = any(c.planning_amount.precision is AmountPrecision.UNKNOWN or
                        c.external and c.external.state(at) == 'unknown' for c in baseline.components) or 'unknown' in world_freshness
        currencies = sorted({c.planning_amount.currency for c in baseline.components}, key=lambda c: c.value)
        totals = tuple(sum_amount_estimates((c.planning_amount for c in baseline.components
                                            if c.planning_amount.currency is currency), currency) for currency in currencies)
        # No synthetic zero total and no cross-currency aggregate.
        status = ('stale' if reasons else 'insufficient_evidence' if missing or not baseline.components
                  else 'partially_available' if uncertain or not baseline.scope_complete or any(t.precision is AmountPrecision.UNKNOWN for t in totals)
                  else 'estimate_available')
        from .reevaluation import dependency_details
        return {**_wire(baseline), 'status': status, 'stale_reasons': reasons, 'missing_context': list(missing),
            'dependency_details': dependency_details(baseline, snapshot),
            'estimated_cost_by_currency': _wire(totals), 'cost_scope': 'complete_user_report' if baseline.scope_complete else 'partial',
            'budget_buffer': {'status': 'user_supplied' if baseline.buffer else 'policy_required',
                              'amount': _wire(baseline.buffer), 'reason': baseline.buffer_reason},
            'recommended_budget': {'status': 'policy_required', 'amount': None, 'reason': 'budget_recommendation_policy_not_approved'},
            'research': {'status': 'research_unavailable', 'reason': 'research_provider_not_configured'},
            'retention': 'session_derived', 'financial_authority': False}

    def latest(self, decision_id):
        rows = self.versions.get(decision_id, ())
        return dict(self.view(rows[-1]), prior_versions=[_wire(b) for b in rows[:-1]]) if rows else None

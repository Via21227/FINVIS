"""Reviewed lifecycle evidence and derived arithmetic; no independent writer.

Presence of ExpenseFulfillment explicitly opts a PlannedExpense into gradual
realization. Legacy records keep their original one-time completion contract.
"""
from dataclasses import dataclass, fields, replace
from hashlib import sha256
import json
from typing import Optional, Tuple

from .domain import Ref
from .world_values import AmountEstimate, AmountPrecision, TypedRef


def _typed(ref, kind):
    if type(ref) is not TypedRef or ref.record_kind != kind:
        raise ValueError('Exact ' + kind + ' reference required')


def _fields(value):
    from .world_commands import validate_fields
    validate_fields(value)


@dataclass(frozen=True)
class ExpenseFulfillment:
    """An open budget/payment schedule, not another balance or liability."""
    occurrence_refs: Tuple[TypedRef, ...] = ()
    committed_amount: Optional[AmountEstimate] = None
    commitment_evidence: Tuple[Ref, ...] = ()

    def __post_init__(self):
        _fields(self)
        for ref in self.occurrence_refs:
            _typed(ref, 'actual_occurrence')
        if len({r.ref.id for r in self.occurrence_refs}) != len(self.occurrence_refs):
            raise ValueError('One current version per occurrence identity')
        if (self.committed_amount is not None) != bool(self.commitment_evidence):
            raise ValueError('Commitment needs explicit amount and evidence together')


@dataclass(frozen=True)
class ReplacementLink:
    original_payment_ref: TypedRef
    refund_refs: Tuple[TypedRef, ...]
    consumed_amount: AmountEstimate

    def __post_init__(self):
        _fields(self)
        _typed(self.original_payment_ref, 'actual_occurrence')
        for ref in self.refund_refs:
            _typed(ref, 'actual_occurrence')
        if len({r.ref.id for r in self.refund_refs}) != len(self.refund_refs):
            raise ValueError('Duplicate refund in replacement allocation')
        if self.consumed_amount.precision is not AmountPrecision.EXACT_REPORTED:
            raise ValueError('Replacement needs explicitly reported consumed allocation')


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


@dataclass(frozen=True)
class IfMaterializationEvidence:
    """Historical hypothetical evidence, never a grant or financial fact."""
    owner_id: str
    world_id: str
    source_generation: str
    if_id: str
    baseline_ref: Ref
    evidence_json: str
    evidence_sha256: str
    confirmation_refs: Tuple[Ref, ...]

    def __post_init__(self):
        _fields(self)
        from .persistence_evidence import strict_json
        if len(self.evidence_json.encode('utf-8')) > 131072:
            raise ValueError('If evidence exceeds retained capsule limit')
        data = strict_json(self.evidence_json)
        if (not self.confirmation_refs
                or _canonical(data) != self.evidence_json
                or sha256(self.evidence_json.encode('utf-8')).hexdigest() != self.evidence_sha256):
            raise ValueError('Bounded canonical If evidence and explicit confirmation required')
        if (type(data) is not dict or set(data) != {'if', 'evaluations'}
                or type(data['if']) is not dict or type(data['evaluations']) is not list
                or not data['evaluations'] or data['if'].get('id') != self.if_id):
            raise ValueError('If identity and retained evaluation history required')
        baseline = data['if'].get('baseline', {})
        if (baseline.get('ref') != {'id': self.baseline_ref.id, 'version': self.baseline_ref.version}
                or baseline.get('owner_id') != self.owner_id or baseline.get('world_id') != self.world_id
                or baseline.get('generation') != self.source_generation):
            raise ValueError('If evidence origin/baseline mismatch')


def capture_if_evidence(service, identity, confirmation_refs):
    """Capture an exact current D2 artifact; does not prepare/authorize a write."""
    from .reviewed_proposals import _wire
    line = service.lines[identity]
    service.baseline(_wire(line.baseline.ref))
    evidence = _canonical({'if': _wire(line), 'evaluations': _wire(service.evaluations[identity])})
    b = line.baseline
    return IfMaterializationEvidence(b.owner_id, b.world_id, b.generation, identity, b.ref,
        evidence, sha256(evidence.encode('utf-8')).hexdigest(), confirmation_refs)


@dataclass(frozen=True)
class OccurrenceLineage:
    refund_of_ref: Optional[TypedRef] = None
    replacement: Optional[ReplacementLink] = None
    if_evidence: Optional[IfMaterializationEvidence] = None

    def __post_init__(self):
        _fields(self)
        if self.refund_of_ref is not None:
            _typed(self.refund_of_ref, 'actual_occurrence')
        if self.refund_of_ref and self.replacement:
            raise ValueError('A refund is not a replacement payment')
        if not any((self.refund_of_ref, self.replacement, self.if_evidence)):
            raise ValueError('Empty lineage is not a new semantic record')


SCHEMA = 'finvis.expense-lifecycle.v0.1'
TYPES = {c.__name__: c for c in (ExpenseFulfillment, ReplacementLink, IfMaterializationEvidence, OccurrenceLineage)}


def encode(value):
    from .world_values import serialize_world_value
    if type(value) in TYPES.values():
        return dict(schema_version=SCHEMA, record_type=type(value).__name__,
                    value={f.name: encode(getattr(value, f.name)) for f in fields(value)})
    if value is None or type(value) in (str, int, bool): return value
    if type(value) is tuple: return [encode(v) for v in value]
    return serialize_world_value(value)


def decode(value):
    from .world_values import deserialize_world_value
    if value is None or type(value) in (str, int, bool): return value
    if type(value) is list: return tuple(decode(v) for v in value)
    if type(value) is not dict: raise ValueError('Invalid lifecycle value')
    if value.get('schema_version') != SCHEMA: return deserialize_world_value(value)
    if set(value) != {'schema_version', 'record_type', 'value'}:
        raise ValueError('Invalid lifecycle envelope')
    kind = TYPES.get(value['record_type']) if type(value['record_type']) is str else None
    data = value['value']
    if kind is None or type(data) is not dict or set(data) != {f.name for f in fields(kind)}:
        raise ValueError('Unknown lifecycle type/fields')
    return kind(**{k: decode(v) for k, v in data.items()})


def is_open(expense):
    from .world_planning import PlannedExpenseStatus
    return expense.status in (PlannedExpenseStatus.PLANNED, PlannedExpenseStatus.PARTIALLY_REALIZED)


def is_refund(occurrence):
    return occurrence.lineage is not None and occurrence.lineage.refund_of_ref is not None


def current_record(snapshot, ref):
    from .store import Rejected
    for r in snapshot.world.members:
        if (r.record_kind, r.ref.id) == (ref.record_kind, ref.ref.id): return snapshot.get(r)
    raise Rejected('Lifecycle dependency is not current')


def occurrences(snapshot):
    return tuple(snapshot.get(r) for r in snapshot.world.members if r.record_kind == 'actual_occurrence')


def refunds_for(snapshot, payment):
    return tuple(o for o in occurrences(snapshot) if is_refund(o) and o.lineage.refund_of_ref.ref.id == payment.ref.id)


def _bounds(amount):
    if amount.precision is AmountPrecision.EXACT_REPORTED:
        return amount.amount.minor_units, amount.amount.minor_units
    if amount.lower_bound is not None and amount.upper_bound is not None:
        return amount.lower_bound.minor_units, amount.upper_bound.minor_units
    raise ValueError('Refundable amount requires supported explicit bounds')


def _paid(snapshot, ref):
    from .world_planning import ActualOccurrence, OccurrenceKind, SettlementStatus
    old = snapshot.get(ref)
    paid = current_record(snapshot, ref)
    if (type(old) is not ActualOccurrence or old.kind is not OccurrenceKind.EXPENSE
            or old.settlement_status not in (SettlementStatus.APPLIED_TO_BALANCE, SettlementStatus.ALREADY_IN_BALANCE)
            or paid.kind is not OccurrenceKind.EXPENSE or is_refund(paid)
            or paid.settlement_status not in (SettlementStatus.APPLIED_TO_BALANCE, SettlementStatus.ALREADY_IN_BALANCE)):
        raise ValueError('Refund/replacement requires a prior authoritative payment')
    return paid


def validate_event(snapshot, report, old=None, prepare=False):
    """Validate current net lineage; caller stages money with unchanged E math."""
    from .world_commands import record_ref
    from .world_planning import OccurrenceKind, SettlementStatus
    lineage = report.lineage
    if old is not None and lineage != old.lineage:
        raise ValueError('Correction cannot change refund/replacement/If identity')
    if lineage is None: return
    if not report.observation.evidence_refs:
        raise ValueError('Lifecycle event needs reviewed evidence')
    refs = ([lineage.refund_of_ref] if lineage.refund_of_ref else [])
    if lineage.replacement: refs += [lineage.replacement.original_payment_ref, *lineage.replacement.refund_refs]
    for ref in refs:
        target = snapshot.get(ref)
        if (target.owner_id != report.owner_id or target.world_id != report.world_id
                or target.ref.id == report.ref.id or target.actual_amount.currency != report.actual_amount.currency
                or target.observation.recorded_at > report.observation.recorded_at):
            raise ValueError('Lifecycle ownership/currency/chronology conflict')
        if prepare and old is None and record_ref(current_record(snapshot, ref)) != ref:
            raise ValueError('Lifecycle target version is stale')
    if lineage.refund_of_ref:
        if report.kind is not OccurrenceKind.INFLOW:
            raise ValueError('Refund must be a forward inflow')
        paid = _paid(snapshot, lineage.refund_of_ref)
        refunds = [r for r in refunds_for(snapshot, paid) if r.ref.id != report.ref.id] + [report]
        if sum(_bounds(r.actual_amount)[1] for r in refunds) > _bounds(paid.actual_amount)[0]:
            raise ValueError('Cumulative refunds exceed proven refundable amount')
    if lineage.replacement:
        if report.kind is not OccurrenceKind.EXPENSE:
            raise ValueError('Replacement must be a new payment')
        link = lineage.replacement
        _paid(snapshot, link.original_payment_ref)
        # Allocation is a retained as-of assertion, not a ban on future refunds.
        paid = snapshot.get(link.original_payment_ref)
        refunds = tuple(snapshot.get(ref) for ref in link.refund_refs)
        if prepare and old is None and {r.ref.id for r in link.refund_refs} != {r.ref.id for r in refunds_for(snapshot, paid)}:
            raise ValueError('Replacement must enumerate the complete refund allocation')
        if (paid.actual_amount.precision is not AmountPrecision.EXACT_REPORTED
                or any(r.actual_amount.precision is not AmountPrecision.EXACT_REPORTED for r in refunds)
                or link.consumed_amount.currency != paid.actual_amount.currency
                or link.consumed_amount.amount.minor_units + sum(r.actual_amount.amount.minor_units for r in refunds)
                   != paid.actual_amount.amount.minor_units):
            raise ValueError('Replacement consumed/refunded allocation is unresolved')
        for ref in link.refund_refs:
            if not is_refund(snapshot.get(ref)) or snapshot.get(ref).lineage.refund_of_ref.ref.id != paid.ref.id:
                raise ValueError('Replacement refund belongs to another payment')
        for other in occurrences(snapshot):
            if (other.ref.id != report.ref.id and other.lineage and other.lineage.replacement
                    and other.lineage.replacement.original_payment_ref.ref.id == paid.ref.id):
                raise ValueError('Payment already has a replacement successor')
    if lineage.if_evidence:
        proof = lineage.if_evidence
        if (proof.owner_id, proof.world_id) != (report.owner_id, report.world_id) or is_refund(report):
            raise ValueError('If origin must match the realized World/payment')
        for other in occurrences(snapshot):
            prev = other.lineage.if_evidence if other.lineage else None
            if (other.ref.id != report.ref.id and prev and
                    (prev.source_generation, prev.if_id) == (proof.source_generation, proof.if_id)):
                raise ValueError('If already materialized; identify the same occurrence')
    if not prepare and report.settlement_status is SettlementStatus.UNALLOCATED:
        raise ValueError('Authoritative lifecycle linkage requires reviewed settlement')


def validate_fulfillment(snapshot, expense):
    from .world_commands import record_ref
    from .world_planning import OccurrenceKind, SettlementStatus
    if expense.fulfillment is None: return
    for ref in expense.fulfillment.occurrence_refs:
        occurrence = snapshot.get(ref)
        if (record_ref(current_record(snapshot, ref)) != ref
                or occurrence.settlement_status not in (SettlementStatus.APPLIED_TO_BALANCE, SettlementStatus.ALREADY_IN_BALANCE)
                or not any(r.record_kind == 'planned_expense' and r.ref.id == expense.ref.id for r in occurrence.related_entity_refs)
                or occurrence.actual_amount.currency != expense.expected_amount.currency
                or (occurrence.kind is not OccurrenceKind.EXPENSE and not is_refund(occurrence))):
            raise ValueError('Gradual plan must retain exact current reciprocal settled events')


def validate_plan_edit(old, new):
    """Generic reviewed plan edits cannot forge/remove realized membership."""
    from .world_planning import PlannedExpenseStatus as S
    if new.fulfillment is None:
        if old and old.fulfillment is not None: raise ValueError('Cannot discard gradual lifecycle')
        return
    if not new.observation.evidence_refs:
        raise ValueError('Gradual plan lifecycle requires reviewed evidence')
    expected = old.fulfillment.occurrence_refs if old and old.fulfillment else ()
    if new.fulfillment.occurrence_refs != expected:
        raise ValueError('Only settlement may change plan occurrence membership')
    if old and old.fulfillment is None and (old.status is not S.PLANNED or old.occurrence_ref is not None):
        raise ValueError('Cannot reinterpret an old completed/cancelled settlement')
    if old and old.fulfillment and old.status in (S.CANCELLED, S.COMPLETED) and new.status != old.status:
        raise ValueError('Closed gradual plan requires a new explicit plan, not reopening')


def signed_amount(terms, currency):
    """Derived planning difference; negative is over estimate, not invalid cash."""
    terms = tuple(terms)
    result = dict(currency=currency.value, precision='exact_reported', value_minor=0, lower_minor=None, upper_minor=None)
    if any(a.currency != currency for a, _ in terms): raise ValueError('No cross-currency arithmetic')
    if any(a.precision is AmountPrecision.UNKNOWN for a, _ in terms):
        return dict(result, precision='unknown', value_minor=None)
    if any(a.precision is AmountPrecision.RANGE for a, _ in terms):
        if any(a.precision is AmountPrecision.APPROXIMATE and a.lower_bound is None for a, _ in terms):
            return dict(result, precision='unknown', value_minor=None)
        lower = upper = 0
        for a, sign in terms:
            lo, hi = _bounds(a)
            lower += lo if sign > 0 else -hi
            upper += hi if sign > 0 else -lo
        return dict(result, precision='range', value_minor=None, lower_minor=lower, upper_minor=upper)
    result = dict(result, value_minor=sum(a.amount.minor_units*s for a,s in terms),
                  precision='approximate' if any(a.precision is AmountPrecision.APPROXIMATE for a,_ in terms) else 'exact_reported')
    if result['precision'] == 'approximate' and all(a.precision is AmountPrecision.EXACT_REPORTED or a.lower_bound is not None for a,_ in terms):
        bounds = [(_bounds(a), sign) for a,sign in terms]
        result.update(lower_minor=sum(lo if sign > 0 else -hi for (lo,hi),sign in bounds),
                      upper_minor=sum(hi if sign > 0 else -lo for (lo,hi),sign in bounds))
    return result


def expense_summary(snapshot, expense):
    """Read-only, latest corrected events once; original estimates stay in history."""
    from .world_planning import OccurrenceKind
    from .world_commands import record_ref
    refs = expense.fulfillment.occurrence_refs if expense.fulfillment else ((expense.occurrence_ref,) if expense.occurrence_ref else ())
    events = [snapshot.get(r) for r in refs]
    if expense.fulfillment is None and events:
        events += list(refunds_for(snapshot, events[0]))
    creation_order = {}
    for index, record in enumerate(snapshot.records):
        if record_ref(record).record_kind == 'actual_occurrence':
            creation_order.setdefault(record.ref.id, index)
    events.sort(key=lambda o: creation_order[o.ref.id])
    payments = [(o.actual_amount, 1) for o in events if o.kind is OccurrenceKind.EXPENSE]
    refunds = [(o.actual_amount, 1) for o in events if is_refund(o)]
    net = payments + [(a, -1) for a, _ in refunds]
    currency = expense.expected_amount.currency
    return dict(estimated=expense.expected_amount,
        committed=expense.fulfillment.committed_amount if expense.fulfillment else None,
        gross_actual=signed_amount(payments, currency), refunded=signed_amount(refunds, currency),
        net_actual=signed_amount(net, currency),
        remaining_planning=signed_amount([(expense.expected_amount, 1), *((a, -s) for a,s in net)], currency),
        status=expense.status.value, occurrence_refs=tuple(record_ref(o) for o in events))


def compatible_fields(value):
    """Omit new default fields from pre-expansion G evidence canonicalization."""
    defaults = {'PlannedExpense': ('fulfillment', None), 'ActualOccurrence': ('lineage', None),
                'SettleOccurrence': ('complete_plan', False), 'CandidateChangeSet': ('future_origin', None)}
    omitted = defaults.get(type(value).__name__)
    return tuple(f for f in fields(value) if not (omitted and f.name == omitted[0]
                                                  and getattr(value, f.name) is omitted[1]))

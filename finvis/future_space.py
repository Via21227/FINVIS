"""FS1 immutable exploration domain. No financial writer, inference or UI.

Containment, exclusive selection and evidence coverage are separate invariants.
All refs are scoped immutable versions; labels never establish identity.
"""
from dataclasses import dataclass, fields, replace
from datetime import datetime, date
from hashlib import sha256
from typing import Optional, Tuple

from .domain import Currency, Ref, Money
from .world_values import AmountEstimate, AmountPrecision, TypedRef
from .world_serialization import _encode as world_encode, _decode as world_decode
from .persistence_evidence import canonical, strict_json
from .if_lines import bucket
from .payment_amounts import PaymentPart, PaymentBasis, payment_planning, validate_payment_effects

SCHEMA = 'finvis.future-space.v1'
COMPILER = 'finvis.future-costs.v1'
KINDS = ('node', 'variable', 'option', 'reference', 'scenario', 'evaluation')
MAX_RECORDS = 8192


class FutureError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def require(ok, code):
    if not ok:
        raise FutureError(code)


def text(value, maximum=1000):
    require(type(value) is str and bool(value.strip()) and len(value) <= maximum, 'bounded_text_required')


def sequence(value, kind=None, maximum=256, unique=False):
    require(type(value) is tuple and len(value) <= maximum, 'bounded_tuple_required')
    if kind:
        require(all(type(x) is kind for x in value), 'typed_tuple_required')
    if unique:
        require(len(value) == len(set(value)), 'duplicate_values')


def instant(value):
    require(type(value) is datetime and value.tzinfo is not None and value.utcoffset() is not None,
            'aware_timestamp_required')


def fingerprint(value):
    return sha256(dumps(value).encode()).hexdigest()


@dataclass(frozen=True)
class FutureScope:
    context_id: str
    owner_id: str
    world_id: str
    store_id: str
    activation_id: str

    def __post_init__(self):
        for f in fields(self): text(getattr(self, f.name))
        require(self.context_id.startswith('user:') and len(self.context_id) > 5, 'user_scope_required')


@dataclass(frozen=True)
class FutureRef:
    kind: str
    id: str
    version: int

    def __post_init__(self):
        require(self.kind in KINDS, 'future_kind_unknown')
        text(self.id, 200)
        require(type(self.version) is int and self.version > 0, 'positive_version_required')


@dataclass(frozen=True)
class WorldDependency:
    ref: TypedRef
    digest: str

    def __post_init__(self):
        require(type(self.ref) is TypedRef, 'world_ref_required')
        require(type(self.digest) is str and len(self.digest) == 64 and all(c in '0123456789abcdef' for c in self.digest), 'digest_required')


@dataclass(frozen=True)
class ContextBinding:
    decision_ref: TypedRef
    context_refs: Tuple[TypedRef, ...]

    def __post_init__(self):
        require(type(self.decision_ref) is TypedRef and self.decision_ref.record_kind == 'world_decision', 'decision_ref_required')
        sequence(self.context_refs, TypedRef, unique=True)
        require(all(r.record_kind == 'decision_context' for r in self.context_refs), 'context_refs_required')


@dataclass(frozen=True)
class FutureEvidence:
    origin: str
    identity: str
    version: int
    generation: Optional[str]
    captured_at: datetime
    excerpt: str
    payload: Optional[str] = None
    freshness: str = 'current'
    expires_at: Optional[datetime] = None

    def __post_init__(self):
        require(self.origin in ('user_report', 'legacy_d1', 'legacy_d2', 'legacy_combination', 'manual_quote'), 'evidence_origin_unknown')
        text(self.identity, 200); text(self.excerpt)
        require(type(self.version) is int and self.version > 0, 'evidence_version_required')
        if self.generation is not None: text(self.generation, 200)
        instant(self.captured_at)
        require(self.freshness in ('current', 'unknown', 'stale'), 'freshness_required')
        if self.expires_at is not None:
            instant(self.expires_at)
            require(self.expires_at >= self.captured_at, 'evidence_chronology')
        if self.payload is not None:
            require(type(self.payload) is str and len(self.payload.encode()) <= 131072, 'evidence_capacity')
            require(canonical(strict_json(self.payload)) == self.payload, 'canonical_evidence_required')
        if self.origin.startswith('legacy_'):
            require(self.payload is not None and self.generation is not None, 'legacy_evidence_required')


@dataclass(frozen=True)
class FutureMeta:
    scope: FutureScope
    ref: FutureRef
    previous: Optional[FutureRef]
    recorded_at: datetime
    evidence: Tuple[FutureEvidence, ...]
    dependencies: Tuple[WorldDependency, ...] = ()
    contexts: Tuple[ContextBinding, ...] = ()
    archived: bool = False

    def __post_init__(self):
        require(type(self.scope) is FutureScope and type(self.ref) is FutureRef, 'scoped_ref_required')
        expected = None if self.ref.version == 1 else replace(self.ref, version=self.ref.version-1)
        require(self.previous == expected, 'contiguous_predecessor_required')
        instant(self.recorded_at)
        sequence(self.evidence, FutureEvidence, 32)
        require(bool(self.evidence), 'provenance_required')
        require(all(e.captured_at <= self.recorded_at for e in self.evidence), 'provenance_chronology')
        sequence(self.dependencies, WorldDependency, unique=True)
        require(len({d.ref for d in self.dependencies}) == len(self.dependencies), 'duplicate_world_dependency')
        sequence(self.contexts, ContextBinding, unique=True)
        require(type(self.archived) is bool, 'archive_flag_required')


@dataclass(frozen=True)
class FutureNode:
    meta: FutureMeta
    title: str
    node_kind: str
    occurrence: str
    parent: Optional[FutureRef] = None
    context: Tuple[str, ...] = ()
    active_when: Tuple[FutureRef, ...] = ()

    def __post_init__(self):
        check_meta(self.meta, 'node'); text(self.title); text(self.occurrence, 200)
        require(self.node_kind in ('event', 'component'), 'node_kind_required')
        require((self.node_kind == 'event') == (self.parent is None), 'component_parent_required')
        if self.parent: check_ref(self.parent, 'node')
        text_tuple(self.context); ref_tuple(self.active_when, 'option')


@dataclass(frozen=True)
class FutureVariable:
    meta: FutureMeta
    node: FutureRef
    question: str
    control: str
    reference_option: Optional[FutureRef] = None
    active_when: Tuple[FutureRef, ...] = ()

    def __post_init__(self):
        check_meta(self.meta, 'variable'); check_ref(self.node, 'node'); text(self.question)
        require(self.control in ('user_choice', 'external_outcome', 'planning_assumption'), 'control_kind_required')
        if self.reference_option: check_ref(self.reference_option, 'option')
        ref_tuple(self.active_when, 'option')


@dataclass(frozen=True)
class MoneyTiming:
    """Explicit movement date, never inferred from event identity or metadata."""
    precision: str
    value: Optional[str]
    source_text: str
    anchor_date: Optional[str] = None
    pending_checked_on: Optional[str] = None

    def __post_init__(self):
        text(self.source_text)
        require(self.precision in ('day', 'month', 'unknown'), 'money_timing_precision')
        if self.precision == 'unknown':
            require(self.value is None, 'money_timing_value')
        else:
            require(type(self.value) is str, 'money_timing_value')
            try:
                parsed = date.fromisoformat(self.value + '-01' if self.precision == 'month' else self.value)
                require((parsed.isoformat()[:7] if self.precision == 'month' else parsed.isoformat()) == self.value, 'money_timing_value')
            except ValueError:
                raise FutureError('money_timing_value')
        for value in (self.anchor_date, self.pending_checked_on):
            if value is not None:
                require(type(value) is str, 'money_timing_anchor')
                try: require(date.fromisoformat(value).isoformat() == value, 'money_timing_anchor')
                except ValueError: raise FutureError('money_timing_anchor')


@dataclass(frozen=True)
class FutureEffect:
    slot: str
    effect_type: str
    observed: AmountEstimate
    planning: AmountEstimate
    time_basis: str
    coverage: Tuple[str, ...]
    absence: bool = False
    money_timing: Optional[MoneyTiming] = None
    payment_basis: Optional[PaymentBasis] = None

    def __post_init__(self):
        require(self.money_timing is None or type(self.money_timing) is MoneyTiming, 'typed_money_timing_required')
        require(self.money_timing is None or self.time_basis == 'one_time', 'one_time_timing_required')
        text(self.slot, 200); text(self.time_basis, 200)
        require(self.effect_type in ('cost', 'expected_inflow'), 'effect_type_required')
        require(type(self.observed) is AmountEstimate and type(self.planning) is AmountEstimate, 'native_amount_required')
        require(self.observed.currency in tuple(Currency), 'native_currency_required')
        require(type(self.absence) is bool, 'absence_flag_required')
        expected = (AmountEstimate.approximate(self.observed.amount)
                    if self.observed.precision is AmountPrecision.EXACT_REPORTED and not self.absence else self.observed)
        if self.payment_basis is not None:
            require(type(self.payment_basis) is PaymentBasis, 'typed_payment_basis_required')
            require(self.time_basis == 'one_time' and not self.absence, 'payment_basis_one_time_required')
            require(self.effect_type == ('expected_inflow' if self.payment_basis.kind == 'refund' else 'cost'),
                    'payment_basis_direction_mismatch')
            try: expected = payment_planning(self.observed, self.payment_basis)
            except ValueError as error: raise FutureError(str(error)) from error
        require(self.planning == expected, 'planning_precision_mismatch')
        if self.absence:
            require(self.observed.precision is AmountPrecision.EXACT_REPORTED and self.observed.amount.minor_units == 0,
                    'absence_requires_explicit_zero')
        text_tuple(self.coverage); require(bool(self.coverage), 'effect_coverage_required')


@dataclass(frozen=True)
class FutureOption:
    meta: FutureMeta
    variable: FutureRef
    label: str
    effects: Tuple[FutureEffect, ...]
    assumptions: Tuple[str, ...]
    requires: Tuple[FutureRef, ...] = ()
    excludes: Tuple[FutureRef, ...] = ()

    def __post_init__(self):
        check_meta(self.meta, 'option'); check_ref(self.variable, 'variable'); text(self.label)
        sequence(self.effects, FutureEffect, 32)
        require(len({e.slot for e in self.effects}) == len(self.effects), 'duplicate_effect_slot')
        text_tuple(self.assumptions); require(bool(self.assumptions), 'assumptions_required')
        ref_tuple(self.requires, 'option'); ref_tuple(self.excludes, 'option')
        require(not set(self.requires) & set(self.excludes), 'contradictory_constraints')
        require(self.meta.ref not in (*self.requires, *self.excludes), 'self_constraint')


@dataclass(frozen=True)
class MembershipBinding:
    node: FutureRef
    members: Tuple[FutureRef, ...]

    def __post_init__(self):
        check_ref(self.node, 'node'); sequence(self.members, FutureRef, unique=True)
        require(all(r.kind in ('node', 'variable') for r in self.members), 'structural_members_required')


@dataclass(frozen=True)
class FutureSelection:
    roots: Tuple[FutureRef, ...]
    structure: Tuple[FutureRef, ...]
    options: Tuple[FutureRef, ...]
    memberships: Tuple[MembershipBinding, ...]
    scope_complete: bool
    unresolved: Tuple[str, ...] = ()

    def __post_init__(self):
        ref_tuple(self.roots, 'node'); require(bool(self.roots), 'declared_scope_required')
        sequence(self.structure, FutureRef, unique=True)
        require(all(r.kind in ('node', 'variable') for r in self.structure), 'structure_required')
        ref_tuple(self.options, 'option'); sequence(self.memberships, MembershipBinding)
        require(type(self.scope_complete) is bool, 'coverage_required'); text_tuple(self.unresolved)


@dataclass(frozen=True)
class ReferenceFrame:
    meta: FutureMeta
    label: str
    selection: FutureSelection

    def __post_init__(self):
        check_meta(self.meta, 'reference'); text(self.label)
        require(type(self.selection) is FutureSelection, 'selection_required')


@dataclass(frozen=True)
class SavedScenario:
    meta: FutureMeta
    name: str
    selection: FutureSelection
    reference: Optional[FutureRef] = None

    def __post_init__(self):
        check_meta(self.meta, 'scenario'); text(self.name)
        require(type(self.selection) is FutureSelection, 'selection_required')
        if self.reference: check_ref(self.reference, 'reference')


@dataclass(frozen=True)
class FutureEvaluation:
    meta: FutureMeta
    selection: FutureSelection
    reference: Optional[FutureRef]
    compiler: str
    result: str

    def __post_init__(self):
        check_meta(self.meta, 'evaluation')
        require(type(self.selection) is FutureSelection, 'selection_required')
        if self.reference: check_ref(self.reference, 'reference')
        require(self.compiler == COMPILER, 'unsupported_future_compiler')
        require(type(self.result) is str and len(self.result) <= 131072 and canonical(strict_json(self.result)) == self.result,
                'canonical_result_required')


def check_ref(ref, kind):
    require(type(ref) is FutureRef and ref.kind == kind, 'typed_future_ref_required')


def check_meta(meta, kind):
    require(type(meta) is FutureMeta and meta.ref.kind == kind, 'typed_future_metadata_required')


def ref_tuple(value, kind):
    sequence(value, FutureRef, unique=True)
    require(all(r.kind == kind for r in value), 'reference_kind_mismatch')


def text_tuple(value):
    sequence(value, str, 64, unique=True)
    for item in value: text(item)


RECORDS = (FutureNode, FutureVariable, FutureOption, ReferenceFrame, SavedScenario, FutureEvaluation)
TYPES = {t.__name__: t for t in (FutureScope, FutureRef, WorldDependency, ContextBinding, FutureEvidence,
    FutureMeta, FutureEffect, MoneyTiming, PaymentPart, PaymentBasis, MembershipBinding, FutureSelection, *RECORDS)}


def encode(value):
    if value is None or type(value) in (str, int, bool): return value
    if type(value) is tuple: return [encode(v) for v in value]
    if type(value) is datetime: return {'instant': value.isoformat()}
    if type(value) in (AmountEstimate, TypedRef, Ref, Money): return {'world_value': world_encode(value)}
    if type(value) in TYPES.values():
        return {'type': type(value).__name__, 'fields': {f.name: encode(getattr(value, f.name)) for f in fields(value) if not (type(value) is FutureEffect and f.name in ('money_timing', 'payment_basis') and getattr(value, f.name) is None)}}
    raise FutureError('unsupported_future_value')


def decode(value, depth=0, schema=SCHEMA):
    require(depth <= 40, 'future_payload_depth')
    if value is None or type(value) in (str, int, bool): return value
    if type(value) is list: return tuple(decode(x, depth+1, schema) for x in value)
    require(type(value) is dict, 'future_object_required')
    if set(value) == {'instant'}:
        result = datetime.fromisoformat(value['instant']); instant(result); return result
    if set(value) == {'world_value'}:
        result = world_decode(value['world_value'])
        require(type(result) in (AmountEstimate, TypedRef, Ref, Money), 'unsupported_world_value')
        return result
    require(set(value) == {'type', 'fields'} and type(value['type']) is str and value['type'] in TYPES, 'closed_future_codec')
    kind = TYPES[value['type']]
    require(schema != SCHEMA or kind is not MoneyTiming, 'future_schema_unknown')
    require(schema == 'finvis.future-space.v3' or kind not in (PaymentPart, PaymentBasis), 'future_schema_unknown')
    expected = {f.name for f in fields(kind)}
    if kind is FutureEffect and (schema == SCHEMA or 'money_timing' not in value['fields']):
        expected.remove('money_timing')
    if kind is FutureEffect and (schema != 'finvis.future-space.v3' or 'payment_basis' not in value['fields']):
        expected.remove('payment_basis')
    require(type(value['fields']) is dict and set(value['fields']) == expected, 'exact_future_fields')
    try: return kind(**{k: decode(v, depth+1, schema) for k, v in value['fields'].items()})
    except ValueError as error:
        if kind in (PaymentPart, PaymentBasis): raise FutureError(str(error)) from error
        raise


def has_timing(value):
    if type(value) is MoneyTiming: return True
    if type(value) is tuple: return any(has_timing(v) for v in value)
    if type(value) in TYPES.values(): return any(has_timing(getattr(value, f.name)) for f in fields(value))
    return False


def has_payment_basis(value):
    if type(value) in (PaymentPart, PaymentBasis): return True
    if type(value) is tuple: return any(has_payment_basis(v) for v in value)
    if type(value) in TYPES.values(): return any(has_payment_basis(getattr(value, f.name)) for f in fields(value))
    return False


def dumps(value):
    # Untimed historical records retain their exact v1 canonical bytes/hashes.
    schema = 'finvis.future-space.v3' if has_payment_basis(value) else 'finvis.future-space.v2' if has_timing(value) else SCHEMA
    return canonical({'schema_version': schema, 'value': encode(value)})


def loads(payload):
    require(type(payload) is str and len(payload.encode()) <= 2097152, 'future_payload_capacity')
    try:
        data = strict_json(payload)
        require(type(data) is dict and set(data) == {'schema_version', 'value'} and data['schema_version'] in (SCHEMA, 'finvis.future-space.v2', 'finvis.future-space.v3'), 'future_schema_unknown')
        result = decode(data['value'], schema=data['schema_version'])
        require(dumps(result) == payload, 'canonical_future_required')
        return result
    except (TypeError, KeyError, AttributeError, RecursionError) as error:
        raise FutureError('invalid_future_payload') from error


class FutureCatalog:
    """Immutable version index, rebuilt from verified Future operations."""
    def __init__(self, scope, records=()):
        require(type(scope) is FutureScope, 'scope_required')
        sequence(records, maximum=MAX_RECORDS)
        self.scope = scope
        self.records = records
        self.index = {}; self.latest = {}
        for row in records:
            require(type(row) in RECORDS and row.meta.scope == scope, 'foreign_future_record')
            ref = row.meta.ref
            require(ref not in self.index, 'duplicate_future_version')
            key = (ref.kind, ref.id)
            old = self.latest.get(key)
            require(row.meta.previous == (old.meta.ref if old else None), 'future_version_gap')
            if old:
                require(row.meta.recorded_at >= old.meta.recorded_at, 'future_chronology')
                self._same_identity(old, row)
            self.index[ref] = row; self.latest[key] = row
        occurrences={}
        for row in self.latest.values():
            if type(row) is FutureNode:
                key=(row.parent.id if row.parent else None,row.occurrence)
                require(key not in occurrences or occurrences[key]==row.meta.ref.id, 'duplicate_occurrence_identity')
                occurrences[key]=row.meta.ref.id
        for row in records:
            if type(row) not in (ReferenceFrame, SavedScenario, FutureEvaluation): self._references(row)
        self._cycles()
        for row in records:
            if type(row) in (ReferenceFrame, SavedScenario, FutureEvaluation): self._references(row)

    @staticmethod
    def _same_identity(old, row):
        require(type(old) is type(row), 'future_identity_kind_changed')
        if type(row) is FutureNode:
            require((old.node_kind, old.occurrence, old.parent and old.parent.id) ==
                    (row.node_kind, row.occurrence, row.parent and row.parent.id), 'node_identity_changed')
        elif type(row) is FutureVariable:
            require((old.node.id, old.control) == (row.node.id, row.control), 'variable_identity_changed')
        elif type(row) is FutureOption:
            require(old.variable.id == row.variable.id, 'option_identity_changed')

    def get(self, ref):
        require(type(ref) is FutureRef and ref in self.index, 'future_ref_missing')
        return self.index[ref]

    def _references(self, row):
        if type(row) is FutureNode:
            if row.parent: self.get(row.parent)
            for r in row.active_when: self.get(r)
        if type(row) is FutureVariable:
            self.get(row.node)
            if row.reference_option:
                require(self.get(row.reference_option).variable == row.meta.ref, 'reference_option_wrong_variable')
            for r in row.active_when: self.get(r)
        if type(row) is FutureOption:
            self.get(row.variable)
            for r in (*row.requires, *row.excludes): self.get(r)
        if type(row) in (ReferenceFrame, SavedScenario, FutureEvaluation):
            self.check_selection(row.selection)
            if getattr(row, 'reference', None): self.get(row.reference)
            if type(row) is FutureEvaluation:
                require(canonical(self.compile(row.selection, row.reference)) == row.result, 'future_evaluation_mismatch')

    def _cycles(self):
        # Iterative traversal gives a bounded, explicit rejection even for deep graphs.
        parents = {r: ((row.parent,) if row.parent else ()) for r, row in self.index.items() if type(row) is FutureNode}
        constraints = {r: row.requires for r, row in self.index.items() if type(row) is FutureOption}
        activation = {}
        for r,row in self.index.items():
            if type(row) is not FutureVariable: continue
            conditions=list(row.active_when); node=self.get(row.node); seen=set()
            while node is not None:
                require(node.meta.ref.id not in seen, 'future_cycle'); seen.add(node.meta.ref.id)
                conditions.extend(node.active_when); node=self.get(node.parent) if node.parent else None
            activation[r]=tuple(self.get(o).variable for o in conditions)
        for edges in (parents, constraints, activation):
            done = set()
            def visit(start):
                stack = [(start, False)]; active = set()
                while stack:
                    key, leaving = stack.pop()
                    if leaving: active.remove(key); done.add(key); continue
                    if key in done: continue
                    require(key not in active, 'future_cycle')
                    active.add(key); stack.append((key, True))
                    stack.extend((r, False) for r in edges.get(key, ()))
            for ref in edges: visit(ref)
        # Identity-level containment catches a reparent cycle across different versions.
        for row in self.latest.values():
            if type(row) is FutureNode:
                seen = set(); cursor = row
                while cursor is not None:
                    require(cursor.meta.ref.id not in seen, 'future_cycle')
                    seen.add(cursor.meta.ref.id)
                    cursor = self.get(cursor.parent) if cursor.parent else None
        # Activation must target outside the activated subtree, never itself/descendants.
        for row in self.index.values():
            if type(row) not in (FutureNode, FutureVariable): continue
            owner = row.meta.ref if type(row) is FutureNode else row.node
            for option_ref in row.active_when:
                target = self.get(self.get(option_ref).variable).node
                require(not self.is_descendant(target, owner) if type(row) is FutureNode else
                        self.get(option_ref).variable.id != row.meta.ref.id, 'activation_cycle')

    def is_descendant(self, ref, ancestor):
        seen=set()
        while ref is not None:
            require(ref.id not in seen, "future_cycle"); seen.add(ref.id)
            if ref.id == ancestor.id: return True
            ref = self.get(ref).parent
        return False

    def children(self, node):
        return tuple(sorted((r.meta.ref for r in self.latest.values()
            if (type(r) is FutureNode and r.parent and r.parent.id == node.id or
                type(r) is FutureVariable and r.node.id == node.id)), key=lambda r: (r.kind, r.id, r.version)))

    def select(self, roots, options, scope_complete=True, unresolved=()):
        """Build a transient exact structure; never retain a record."""
        ref_tuple(roots, 'node'); ref_tuple(options, 'option')
        structure = {}; memberships = []; pending = list(roots)
        while pending:
            r = pending.pop(); row = self.get(r)
            require(type(row) in (FutureNode, FutureVariable), 'structure_required')
            if r in structure: continue
            structure[r] = row
            if type(row) is FutureNode:
                members = self.children(r); memberships.append(MembershipBinding(r, members)); pending.extend(members)
        result = FutureSelection(roots, tuple(sorted(structure, key=lambda r:(r.kind,r.id,r.version))), options,
            tuple(sorted(memberships, key=lambda m:m.node.id)), scope_complete, unresolved)
        self.check_selection(result)
        return result

    def consumed_structure(self, selection):
        """Inactive branches remain pinned history but supply no current inputs."""
        chosen={self.get(r).variable.id:r for r in selection.options}
        result=[]
        for ref in selection.structure:
            item=self.get(ref)
            conditions=list(item.active_when)
            node=self.get(item.node) if type(item) is FutureVariable else item
            while node is not None:
                conditions.extend(node.active_when)
                node=self.get(node.parent) if node.parent else None
            if not any(self.get(r).variable.id in chosen and chosen[self.get(r).variable.id]!=r for r in conditions):
                result.append(ref)
        return tuple(result)

    def check_selection(self, selection):
        require(type(selection) is FutureSelection, 'selection_required')
        rows = {r: self.get(r) for r in selection.structure}
        require(set(selection.roots) <= set(rows), 'scope_not_in_structure')
        require(len({(r.kind,r.id) for r in rows}) == len(rows), 'competing_structure_versions')
        nodes = {r.id:r for r in rows if r.kind == 'node'}
        variables = {r.id:r for r in rows if r.kind == 'variable'}
        require(len({m.node for m in selection.memberships}) == len(selection.memberships), 'duplicate_membership')
        memberships = {m.node: m.members for m in selection.memberships}
        require(set(memberships) == {r for r in rows if r.kind == 'node'}, 'membership_closure_required')
        reached = set(selection.roots)
        pending = list(selection.roots)
        while pending:
            r = pending.pop()
            for child in memberships.get(r, ()):
                require(child in rows, 'member_not_in_structure')
                record = rows[child]
                parent = record.parent if type(record) is FutureNode else record.node
                require(parent and parent.id == r.id, 'membership_parent_mismatch')
                if child not in reached: reached.add(child); pending.append(child)
        require(reached == set(rows), 'unreachable_structure')
        chosen = {}; conflicts = []
        for r in selection.options:
            option = self.get(r)
            require(option.variable.id in variables, 'option_outside_scope')
            require(option.variable == variables[option.variable.id], 'option_structure_version_mismatch')
            if option.variable.id in chosen: conflicts.append('exclusive_options')
            chosen[option.variable.id] = option
        require(not conflicts, conflicts[0] if conflicts else 'exclusive_options')
        missing = list(selection.unresolved)
        def condition(refs):
            unknown=False
            for ref in refs:
                option = self.get(ref); actual = chosen.get(option.variable.id)
                if actual is None: unknown=True
                elif actual.meta.ref != ref: return False
            return None if unknown else True
        def active(variable):
            checks = [condition(variable.active_when)]; n = self.get(variable.node)
            while n is not None:
                checks.append(condition(n.active_when)); n = self.get(n.parent) if n.parent else None
            return False if False in checks else None if None in checks else True
        counted = []
        for key, ref in variables.items():
            var = self.get(ref); selected = chosen.get(key); state = active(var)
            if selected and state is False: raise FutureError('inactive_option_selected')
            if state is None: missing.append('activation_unknown:'+key)
            if selected is None:
                if state is not False: missing.append('selection_missing:'+key)
                continue
            for required in selected.requires:
                option = self.get(required); other = chosen.get(option.variable.id)
                if other is None: missing.append('prerequisite_missing:'+required.id)
                elif other.meta.ref != required: raise FutureError('prerequisite_conflict')
            if any(excluded.id == picked.id for excluded in selected.excludes for picked in selection.options):
                raise FutureError('excluded_options')
            for effect in selected.effects: counted.append((var.node, selected.meta.ref, effect))
        slots = set(); coverage = set()
        for node, ref, effect in counted:
            key = (effect.slot, effect.effect_type, effect.planning.currency, effect.time_basis)
            require(key not in slots, 'competing_effect_assignment'); slots.add(key)
            require(not coverage.intersection(effect.coverage), 'overlapping_effect_coverage'); coverage.update(effect.coverage)
        for i,(a,_,_) in enumerate(counted):
            for b,_,_ in counted[i+1:]:
                if a.id != b.id:
                    require(not self.is_descendant(a,b) and not self.is_descendant(b,a), 'bundle_child_overlap')
        try: validate_payment_effects(tuple(e for _, _, e in counted))
        except ValueError as error: raise FutureError(str(error)) from error
        return tuple(sorted(set(missing))), tuple(counted)

    def compile(self, selection, reference=None):
        missing, effects = self.check_selection(selection)
        original = ()
        if reference is not None:
            frame = self.get(reference)
            require(type(frame) is ReferenceFrame and set(frame.selection.roots) == set(selection.roots), 'reference_scope_mismatch')
            old_missing, original = self.check_selection(frame.selection)
            missing = tuple(sorted(set((*missing, *old_missing))))
        result = {'status':'needs_information' if missing or not selection.scope_complete else 'available',
                  'missing':list(missing), 'coverage':'complete_user_scope' if selection.scope_complete else 'partial',
                  'financial_authority':False, 'financial_metrics':'unavailable_policy_required', 'buckets':[]}
        groups = {(e.effect_type,e.planning.currency,e.time_basis) for _,_,e in (*effects,*original)}
        for typ,currency,time_basis in sorted(groups,key=lambda x:(x[0],x[1].value,x[2])):
            new = [(r,e) for _,r,e in effects if (e.effect_type,e.planning.currency,e.time_basis)==(typ,currency,time_basis)]
            old = [(r,e) for _,r,e in original if (e.effect_type,e.planning.currency,e.time_basis)==(typ,currency,time_basis)]
            total = bucket(((e.planning,1) for _,e in new),{currency}) if new else []
            # Shared immutable input terms cancel before range arithmetic.
            delta = [(e.planning,1) for r,e in new if (r,e) not in old] + [(e.planning,-1) for r,e in old if (r,e) not in new]
            result['buckets'].append(dict(effect_type=typ,currency=currency.value,time_basis=time_basis,
                known_selection_total=total, known_subtotal=bucket(((e.planning,1) for _,e in new if e.planning.precision is not AmountPrecision.UNKNOWN),{currency}) if any(e.planning.precision is not AmountPrecision.UNKNOWN for _,e in new) else [],
                delta=bucket(delta,{currency}) if reference else None))
            if any(e.planning.precision is AmountPrecision.UNKNOWN for _,e in new): result['status']='needs_information'
        return result


@dataclass(frozen=True)
class FutureOperation:
    operation_id: str
    scope: FutureScope
    expected_revision: int
    financial_revision: int
    action: str
    records: tuple
    recorded_at: datetime

    def __post_init__(self):
        text(self.operation_id, 200)
        require(type(self.scope) is FutureScope, 'scope_required')
        require(type(self.expected_revision) is int and self.expected_revision >= 0 and
                type(self.financial_revision) is int and self.financial_revision >= 0, 'expected_revisions_required')
        require(self.action in ('keep', 'revise', 'archive', 'save_scenario', 'evaluate_keep', 'import_legacy'), 'explicit_retention_action_required')
        sequence(self.records, maximum=256)
        require(bool(self.records) and all(type(r) in RECORDS for r in self.records), 'retained_records_required')
        require(all(r.meta.scope == self.scope for r in self.records), 'foreign_future_record')
        require(len({(r.meta.ref.kind,r.meta.ref.id) for r in self.records}) == len(self.records), 'one_version_per_operation')
        instant(self.recorded_at)
        require(all(r.meta.recorded_at <= self.recorded_at for r in self.records), 'operation_chronology')


TYPES[FutureOperation.__name__] = FutureOperation

"""Immutable Slice D protocol. No provider, parser, settlement or public writer.

Trusted callers stage complete typed records, never field paths or executable
text. WorldStore registers reviewed previews and exact, single-use consent.
"""

from dataclasses import dataclass, fields
from datetime import datetime
from enum import Enum
from typing import Optional, Tuple, Union, get_args, get_origin, get_type_hints

from .domain import Currency, RealitySource, Ref, aware, nonempty
from .world_values import AmountEstimate, ObservationMetadata, ObservationRole, TypedRef
from .world_domain import Account, AssetPosition, Earmark, WorldState
from .world_planning import (
    FutureInflow, PlannedExpense, ActualOccurrence, WorldDecision,
    DecisionContext, WorldPlan,
)
from .world_settlement import BalanceEffect, SettleOccurrence


@dataclass(frozen=True)
class LegacyPlanAnchor:
    """Read-only continuity marker for a migrated legacy plan predecessor."""

    ref: Ref
    owner_id: str
    world_id: str
    source_schema: str
    source_hash: str
    observation: ObservationMetadata

    def __post_init__(self):
        validate_fields(self)


@dataclass(frozen=True)
class MonthlyEssentialOutflow:
    """User's monthly observation, without an eligibility or coverage verdict."""
    ref: Ref
    owner_id: str
    world_id: str
    amount: AmountEstimate
    observation: ObservationMetadata
    cadence: str = 'monthly'
    coverage: str = 'user_reported_unverified'

    @property
    def label(self):
        return 'Monthly essential outflow'

    def __post_init__(self):
        validate_fields(self)
        if (self.amount.currency is None or self.cadence != 'monthly'
                or self.coverage != 'user_reported_unverified'
                or self.observation.role is not ObservationRole.CURRENT
                or self.observation.source is not RealitySource.USER_REPORT
                or not self.observation.evidence_refs):
            raise ValueError('Monthly essentials require a native-currency current user report')


Record = Union[Account, AssetPosition, Earmark, WorldState, FutureInflow,
               PlannedExpense, ActualOccurrence, WorldDecision, DecisionContext, WorldPlan,
               BalanceEffect, LegacyPlanAnchor, MonthlyEssentialOutflow]
EditableRecord = Union[Account, AssetPosition, Earmark, FutureInflow,
                       PlannedExpense, ActualOccurrence, WorldDecision, DecisionContext, MonthlyEssentialOutflow]
KINDS = {
    Account: 'account', AssetPosition: 'asset_position', Earmark: 'earmark',
    WorldState: 'world_state', FutureInflow: 'future_inflow',
    PlannedExpense: 'planned_expense', ActualOccurrence: 'actual_occurrence',
    WorldDecision: 'world_decision', DecisionContext: 'decision_context', WorldPlan: 'world_plan',
    BalanceEffect: 'balance_effect', LegacyPlanAnchor: 'legacy_world_plan',
    MonthlyEssentialOutflow: 'monthly_essential_outflow',
}


def record_ref(record: Record) -> TypedRef:
    if type(record) not in KINDS:
        raise ValueError('Unsupported world record')
    return TypedRef(KINDS[type(record)], record.ref)


def ref_key(ref: TypedRef) -> tuple:
    return ref.record_kind, ref.ref.id, ref.ref.version


def _matches(value, annotation) -> bool:
    origin, args = get_origin(annotation), get_args(annotation)
    if origin is Union:
        return any(_matches(value, arg) for arg in args)
    if origin is tuple:
        return type(value) is tuple and all(_matches(item, args[0]) for item in value)
    return type(value) is annotation


def validate_fields(value, signed_fields=()) -> None:
    """Closed protocol field types, including nested immutable tuples."""
    for name, annotation in get_type_hints(type(value)).items():
        item = getattr(value, name)
        if not _matches(item, annotation):
            raise ValueError(f'{type(value).__name__}.{name} has wrong type')
        if type(item) is str:
            nonempty(item, name)
        if type(item) is datetime:
            aware(item, name)
        if type(item) is int and item < 0 and name not in signed_fields:
            raise ValueError(f'{name} must be nonnegative')


def unique_refs(values: Tuple[TypedRef, ...]) -> None:
    keys = [(item.record_kind, item.ref.id) for item in values]
    if len(keys) != len(set(keys)):
        raise ValueError('Duplicate stable typed identity')


class ChangeMode(Enum):
    MERGE = 'merge'
    REPLACE_STATE = 'replace_state'
    CORRECT = 'correct'
    RESOLVE = 'resolve'


class IssueDisposition(Enum):
    READY = 'ready'
    NEEDS_USER_INPUT = 'needs_user_input'
    UNSUPPORTED = 'unsupported'
    CONFLICT = 'conflict'


@dataclass(frozen=True)
class ReconciliationIssue:
    code: str
    disposition: IssueDisposition
    rationale: str
    related_refs: Tuple[TypedRef, ...] = ()

    def __post_init__(self):
        validate_fields(self)


@dataclass(frozen=True)
class ReplacementScope:
    """Enumerated current observations, never a delete-all flag."""
    account_refs: Tuple[TypedRef, ...]
    currencies: Tuple[Currency, ...]
    position_refs: Tuple[TypedRef, ...]
    earmark_refs: Tuple[TypedRef, ...]

    def __post_init__(self):
        validate_fields(self)
        if not self.account_refs or not self.currencies:
            raise ValueError('Replacement needs explicit account and currency scope')
        for name, kind in (('account_refs', 'account'), ('position_refs', 'asset_position'), ('earmark_refs', 'earmark')):
            refs = getattr(self, name)
            unique_refs(refs)
            if any(item.record_kind != kind for item in refs):
                raise ValueError('Replacement reference kind mismatch')
            object.__setattr__(self, name, tuple(sorted(refs, key=ref_key)))
        if len(set(self.currencies)) != len(self.currencies):
            raise ValueError('Duplicate scope currency')
        object.__setattr__(self, 'currencies', tuple(sorted(self.currencies, key=lambda item: item.value)))


@dataclass(frozen=True)
class PutRecord:
    operation_id: str
    record: EditableRecord
    expected_ref: Optional[TypedRef] = None
    depends_on: Tuple[str, ...] = ()

    def __post_init__(self):
        validate_fields(self)
        if self.expected_ref is not None and (
            self.expected_ref.record_kind != record_ref(self.record).record_kind
            or self.expected_ref.ref.id != self.record.ref.id
        ):
            raise ValueError('Update must retain record type and stable identity')


@dataclass(frozen=True)
class RetireRecord:
    operation_id: str
    expected_ref: TypedRef
    depends_on: Tuple[str, ...] = ()

    def __post_init__(self):
        validate_fields(self)
        if self.expected_ref.record_kind not in ('account', 'asset_position', 'earmark'):
            raise ValueError('Other records close through explicit versioned lifecycle updates')


@dataclass(frozen=True)
class HistoryAction:
    """Bounded forward action, replayed from exact retained historical evidence."""
    operation_id: str
    action: str
    source_revision: int
    source_root: Ref
    source_digest: str
    target_ref: Optional[TypedRef]
    field: Optional[str]
    value: Union[AmountEstimate, str, None]
    reason: str
    depends_on: Tuple[str, ...] = ()

    def __post_init__(self):
        validate_fields(self)
        if self.action not in ('edit', 'remove', 'restore') or self.depends_on:
            raise ValueError('Unsupported history action')
        if len(self.source_digest) != 64 or any(c not in '0123456789abcdef' for c in self.source_digest):
            raise ValueError('Exact historical digest required')
        if self.action == 'restore' and any(v is not None for v in (self.target_ref, self.field, self.value)):
            raise ValueError('Restore is an exact whole-supported-world comparison')
        if self.action != 'restore' and self.target_ref is None:
            raise ValueError('Current target required')
        if self.action == 'remove' and (self.field is not None or self.value is not None):
            raise ValueError('Removal has no edited value')


Operation = Union[PutRecord, RetireRecord, SettleOccurrence, HistoryAction]


@dataclass(frozen=True)
class CandidateChangeSet:
    ref: Ref
    owner_id: str
    world_id: str
    session_generation: str
    expected_world_ref: Ref
    expected_state_ref: Ref
    expected_plan_ref: Ref
    expected_revision: int
    mode: ChangeMode
    operations: Tuple[Operation, ...]
    replacement_scope: Optional[ReplacementScope]
    retained_refs: Tuple[TypedRef, ...]
    candidate_refs: Tuple[Ref, ...]
    observation: ObservationMetadata
    coverage_note: Optional[str]
    issues: Tuple[ReconciliationIssue, ...]
    created_at: datetime
    future_origin: Optional[str] = None

    def __post_init__(self):
        validate_fields(self)
        if not self.operations:
            raise ValueError('Empty change set')
        if (self.mode is ChangeMode.REPLACE_STATE) != (self.replacement_scope is not None):
            raise ValueError('Exactly replace_state requires ReplacementScope')
        unique_refs(self.retained_refs)
        object.__setattr__(self, 'retained_refs', tuple(sorted(self.retained_refs, key=ref_key)))
        seen = set()
        for operation in self.operations:
            if operation.operation_id in seen or any(dep not in seen for dep in operation.depends_on):
                raise ValueError('Operation IDs/dependencies must be unique and ordered')
            if len(set(operation.depends_on)) != len(operation.depends_on):
                raise ValueError('Duplicate operation dependency')
            seen.add(operation.operation_id)
        if self.observation.recorded_at > self.created_at:
            raise ValueError('Change creation precedes provenance')


@dataclass(frozen=True)
class FinancialWorld:
    ref: Ref
    owner_id: str
    world_id: str
    state_ref: Ref
    plan_ref: Ref
    members: Tuple[TypedRef, ...]

    def __post_init__(self):
        validate_fields(self)
        unique_refs(self.members)
        if any(item.record_kind not in set(KINDS.values()) - {'world_state', 'world_plan'} for item in self.members):
            raise ValueError('Unsupported root member kind')
        object.__setattr__(self, 'members', tuple(sorted(self.members, key=ref_key)))


@dataclass(frozen=True)
class RecordChange:
    before: Optional[Record]
    after: Record
    leaves_current_scope: bool

    def __post_init__(self):
        validate_fields(self)


@dataclass(frozen=True)
class MutationPreview:
    ref: Ref
    change_set: CandidateChangeSet
    before_world: FinancialWorld
    after_world: FinancialWorld
    changes: Tuple[RecordChange, ...]
    retained_refs: Tuple[TypedRef, ...]
    pending: Tuple[ReconciliationIssue, ...]
    destructive: bool
    preserved_categories: Tuple[str, ...]

    def __post_init__(self):
        validate_fields(self)

    @property
    def digest(self) -> str:
        from .world_serialization import payload_digest
        return payload_digest(self)


@dataclass(frozen=True)
class WorldAuthorization:
    ref: Ref
    actor: str
    owner_id: str
    world_id: str
    preview_ref: Ref
    preview_digest: str
    change_set_ref: Ref
    change_set_digest: str
    expected_world_ref: Ref
    session_generation: str
    purpose: ChangeMode
    replacement_scope: Optional[ReplacementScope]
    granted_at: datetime

    def __post_init__(self):
        validate_fields(self)


@dataclass(frozen=True)
class FinancialWorldCommand:
    command_id: str
    actor: str
    change_set: CandidateChangeSet
    preview_ref: Ref
    preview_digest: str
    authorization_ref: Ref
    recorded_at: datetime

    def __post_init__(self):
        validate_fields(self)


@dataclass(frozen=True)
class WorldMutationEvent:
    ref: Ref
    command_id: str
    command_digest: str
    actor: str
    owner_id: str
    world_id: str
    session_generation: str
    change_set_ref: Ref
    change_set_digest: str
    authorization_ref: Ref
    preview_ref: Ref
    preview_digest: str
    before_world_ref: Ref
    after_world_ref: Ref
    before_state_ref: Ref
    after_state_ref: Ref
    before_plan_ref: Ref
    after_plan_ref: Ref
    inserted_refs: Tuple[TypedRef, ...]
    retired_refs: Tuple[TypedRef, ...]
    source_refs: Tuple[Ref, ...]
    pending: Tuple[ReconciliationIssue, ...]
    destructive: bool
    recorded_at: datetime

    def __post_init__(self):
        validate_fields(self)


@dataclass(frozen=True)
class WorldSnapshot:
    """Append-only records, roots, consent and receipts published together."""
    records: Tuple[Record, ...]
    worlds: Tuple[FinancialWorld, ...]
    authorizations: Tuple[WorldAuthorization, ...] = ()
    commands: Tuple[FinancialWorldCommand, ...] = ()
    receipts: Tuple[WorldMutationEvent, ...] = ()
    revision: int = 0

    def __post_init__(self):
        validate_fields(self)
        if not self.worlds:
            raise ValueError('Snapshot needs a root')
        keys = [record_ref(record) for record in self.records]
        if len(set(keys)) != len(keys):
            raise ValueError('Cannot overwrite an immutable record version')

    @property
    def world(self):
        return self.worlds[-1]

    def get(self, ref: TypedRef):
        from .store import Rejected
        for record in self.records:
            if record_ref(record) == ref:
                return record
        raise Rejected(f'Missing exact record: {ref}')


def typed_links(value):
    """Walk only immutable domain values to verify embedded typed relationships."""
    if type(value) is TypedRef:
        yield value
    elif type(value) is tuple:
        for item in value:
            yield from typed_links(item)
    elif hasattr(value, '__dataclass_fields__'):
        for field in fields(value):
            yield from typed_links(getattr(value, field.name))

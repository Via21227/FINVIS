"""Slice F deterministic legacy migration and Financial World replay.

Migration is a reviewed cutover proposal, never an automatic schema upgrade.
Replay reconstructs authoritative state by re-executing recorded D/E contracts;
decoding either format grants no authority.
"""

from dataclasses import dataclass, fields, replace
from datetime import datetime
from enum import Enum
import hashlib
import json
from threading import RLock
from typing import Optional, Tuple

from .domain import (
    Assumption, Confidence, Decision, DecisionOption, Evidence, ExecutionLog,
    FinancialAmount, FinancialState, Goal, IfLine, LivingPlan, Money,
    PlanAuthorization, Precision, Ref, aware, nonempty,
)
from .replay import structured
from .store import MemoryStore, Rejected, Snapshot
from .world_commands import (
    FinancialWorld, LegacyPlanAnchor, WorldAuthorization, WorldMutationEvent,
    WorldSnapshot, FinancialWorldCommand, record_ref, ref_key,
)
from .world_domain import (
    WorldState, deserialize_world_asset, serialize_world_asset,
)
from .world_planning import (
    PlanEvaluationStatus, PlannedExpense, PlannedExpenseStatus,
    PlanningRecordKind, WorldDecision, WorldPlan, deserialize_world_planning,
    planning_ref, serialize_world_planning,
)
from .world_serialization import (
    SCHEMA as MUTATION_SCHEMA, deserialize_world_mutation,
    payload_digest, serialize_world_mutation,
)
from .world_store import WorldStore, current_assets, validate_world
from .world_fulfillment import is_open
from .world_values import (
    AmountEstimate, ObservationMetadata, ObservationRole, TemporalSpec,
    TypedRef, deserialize_world_value, serialize_world_value,
)


REPLAY_SCHEMA = 'finvis.world-replay.v0.1'
MIGRATION_RULE_VERSION = 'finvis.legacy-phase2-to-world.v0.1'
LEGACY_SCHEMA = 'finvis.phase2-snapshot.v0.2'


class MigrationDisposition(Enum):
    MIGRATED = 'migrated'
    HISTORICAL_ONLY = 'historical_only'
    NON_EXECUTABLE = 'non_executable'
    UNREPRESENTED = 'unrepresented'


@dataclass(frozen=True)
class LegacyFinancialAmount:
    value: Money
    precision: Precision
    confidence: Confidence
    evidence_refs: Tuple[Ref, ...]
    assumption_refs: Tuple[Ref, ...]

    def __post_init__(self):
        if type(self.value) is not Money or type(self.precision) is not Precision:
            raise ValueError('Legacy financial amount requires Money and Precision')
        if type(self.confidence) is not Confidence:
            raise ValueError('Legacy financial amount requires Confidence')
        refs = (*self.evidence_refs, *self.assumption_refs)
        if (type(self.evidence_refs) is not tuple
            or type(self.assumption_refs) is not tuple
            or any(type(item) is not Ref for item in refs)
            or not refs):
            raise ValueError('Legacy financial amount requires immutable provenance refs')

    @classmethod
    def from_legacy(cls, value: FinancialAmount):
        if type(value) is not FinancialAmount:
            raise ValueError('Legacy amount must be FinancialAmount')
        return cls(value.value, value.precision, value.confidence,
                   value.evidence_refs, value.assumption_refs)


@dataclass(frozen=True)
class LegacyEmploymentAssessment:
    status: str
    uncertainty_ref: Ref
    monthly_income: Optional[LegacyFinancialAmount]
    salary_basis: Optional[str]
    accepted_on: Optional[str]
    employment_start_on: Optional[str]
    source_event_ref: Optional[Ref]

    def __post_init__(self):
        nonempty(self.status, 'employment status')
        if self.monthly_income is None:
            if any(value is not None for value in (
                    self.salary_basis, self.accepted_on,
                    self.employment_start_on, self.source_event_ref)):
                raise ValueError('Unresolved employment cannot claim accepted terms')
        elif self.salary_basis is None or self.accepted_on is None or self.source_event_ref is None:
            raise ValueError('Accepted employment assessment is incomplete')


@dataclass(frozen=True)
class LegacyLiquidityAssessment:
    """Legacy scalar facts retained outside account-derived asset authority."""

    ref: Ref
    owner_id: str
    world_id: str
    source_state_ref: Ref
    available_emergency_liquidity: LegacyFinancialAmount
    monthly_essential_outflow: LegacyFinancialAmount
    employment: Optional[LegacyEmploymentAssessment]
    goal_refs: Tuple[Ref, ...]
    active_for_legacy_calculations: bool
    limitation: str

    def __post_init__(self):
        nonempty(self.owner_id, 'owner_id')
        nonempty(self.world_id, 'world_id')
        nonempty(self.limitation, 'limitation')


@dataclass(frozen=True)
class MigrationMapping:
    source_kind: str
    source_ref: Ref
    source_ordinal: Optional[int]
    relation: str
    target_ref: Optional[TypedRef]
    disposition: MigrationDisposition
    rationale: str

    def __post_init__(self):
        for value, name in ((self.source_kind, 'source_kind'), (self.relation, 'relation'),
                            (self.rationale, 'rationale')):
            nonempty(value, name)
        if self.source_ordinal is not None and (type(self.source_ordinal) is not int or self.source_ordinal < 0):
            raise ValueError('source ordinal must be nonnegative')


@dataclass(frozen=True)
class MigrationIssue:
    code: str
    rationale: str
    source_refs: Tuple[Ref, ...] = ()

    def __post_init__(self):
        nonempty(self.code, 'code')
        nonempty(self.rationale, 'rationale')


@dataclass(frozen=True)
class MigrationManifest:
    ref: Ref
    source_schema: str
    source_hash: str
    rule_version: str
    owner_id: str
    world_id: str
    source_current_state_ref: Ref
    source_current_plan_ref: Ref
    target_world_ref: Ref
    mappings: Tuple[MigrationMapping, ...]
    unresolved: Tuple[MigrationIssue, ...]
    observation: ObservationMetadata

    def __post_init__(self):
        for value, name in ((self.source_schema, 'source_schema'),
                            (self.source_hash, 'source_hash'),
                            (self.rule_version, 'rule_version'),
                            (self.owner_id, 'owner_id'), (self.world_id, 'world_id')):
            nonempty(value, name)
        if self.observation.role is not ObservationRole.CURRENT:
            raise ValueError('Migration manifest observation must describe the cutover snapshot')
        ordered = tuple(sorted(self.mappings, key=_mapping_key))
        if ordered != self.mappings:
            raise ValueError('Migration mappings must be canonical')
        keys = [_mapping_key(item) for item in self.mappings]
        if len(keys) != len(set(keys)):
            raise ValueError('Migration mappings must not duplicate one source relation')
        if tuple(sorted(self.unresolved, key=lambda item: (item.code, tuple((r.id, r.version) for r in item.source_refs)))) != self.unresolved:
            raise ValueError('Migration issues must be canonical')


@dataclass(frozen=True)
class MigrationProposal:
    manifest: MigrationManifest
    initial_snapshot: WorldSnapshot
    legacy_assessment: LegacyLiquidityAssessment
    source_export: str

    def __post_init__(self):
        if _sha(self.source_export) != self.manifest.source_hash:
            raise ValueError('Migration source export/hash mismatch')
        if self.initial_snapshot.world.ref != self.manifest.target_world_ref:
            raise ValueError('Migration target root mismatch')
        if ((self.initial_snapshot.world.owner_id, self.initial_snapshot.world.world_id) !=
            (self.manifest.owner_id, self.manifest.world_id)):
            raise ValueError('Migration target owner/world mismatch')
        if self.legacy_assessment.source_state_ref != self.manifest.source_current_state_ref:
            raise ValueError('Migration assessment/source state mismatch')
        if ((self.legacy_assessment.owner_id, self.legacy_assessment.world_id) !=
            (self.manifest.owner_id, self.manifest.world_id)):
            raise ValueError('Migration assessment owner/world mismatch')
        validate_world(self.initial_snapshot)

    @property
    def digest(self):
        return replay_payload_digest(self)


@dataclass(frozen=True)
class MigrationPreview:
    ref: Ref
    manifest_ref: Ref
    proposal_digest: str
    source_hash: str
    target_world_ref: Ref
    unresolved: Tuple[MigrationIssue, ...]


@dataclass(frozen=True)
class MigrationAuthorization:
    ref: Ref
    actor: str
    owner_id: str
    world_id: str
    session_generation: str
    preview_ref: Ref
    proposal_digest: str
    source_hash: str
    target_world_ref: Ref
    granted_at: datetime

    def __post_init__(self):
        for value, name in ((self.actor, 'actor'), (self.owner_id, 'owner_id'),
                            (self.world_id, 'world_id'),
                            (self.session_generation, 'session_generation')):
            nonempty(value, name)
        aware(self.granted_at, 'granted_at')


@dataclass(frozen=True)
class MigrationEvidence:
    manifest: MigrationManifest
    preview: MigrationPreview
    authorization: MigrationAuthorization
    legacy_assessment: LegacyLiquidityAssessment
    source_export: str

    def __post_init__(self):
        if _sha(self.source_export) != self.manifest.source_hash:
            raise ValueError('Migration evidence source export/hash mismatch')
        if (self.preview.manifest_ref != self.manifest.ref
            or self.preview.source_hash != self.manifest.source_hash
            or self.authorization.preview_ref != self.preview.ref
            or self.authorization.proposal_digest != self.preview.proposal_digest
            or self.authorization.source_hash != self.manifest.source_hash):
            raise ValueError('Migration evidence chain is incoherent')


@dataclass(frozen=True)
class WorldReplayStep:
    command_schema: str
    authorization: WorldAuthorization
    command: FinancialWorldCommand
    receipt: WorldMutationEvent
    after_world: FinancialWorld
    introduced_records: Tuple[object, ...]

    def __post_init__(self):
        if self.command_schema != MUTATION_SCHEMA:
            raise ValueError('Unknown replay command schema')
        if self.command.authorization_ref != self.authorization.ref:
            raise ValueError('Replay command/authorization mismatch')
        if self.receipt.command_id != self.command.command_id:
            raise ValueError('Replay command/receipt mismatch')
        if self.receipt.after_world_ref != self.after_world.ref:
            raise ValueError('Replay receipt/root mismatch')
        if tuple(record_ref(item) for item in self.introduced_records) != self.receipt.inserted_refs:
            raise ValueError('Replay introduced records mismatch receipt')


@dataclass(frozen=True)
class WorldReplayPackage:
    ref: Ref
    session_generation: str
    initial_snapshot: WorldSnapshot
    steps: Tuple[WorldReplayStep, ...]
    final_world_ref: Ref
    final_state_ref: Ref
    final_plan_ref: Ref
    final_revision: int
    final_snapshot_digest: str
    final_derived_digest: str
    migration_evidence: Optional[MigrationEvidence] = None

    def __post_init__(self):
        nonempty(self.session_generation, 'session_generation')
        if self.initial_snapshot.revision or self.initial_snapshot.receipts or self.initial_snapshot.commands or self.initial_snapshot.authorizations:
            raise ValueError('Replay package must begin from an unmutated initial snapshot')
        if type(self.final_revision) is not int or self.final_revision < 0:
            raise ValueError('Final revision must be nonnegative')
        if self.migration_evidence is not None:
            evidence = self.migration_evidence
            if (evidence.manifest.target_world_ref != self.initial_snapshot.world.ref
                or evidence.preview.target_world_ref != self.initial_snapshot.world.ref
                or evidence.authorization.target_world_ref != self.initial_snapshot.world.ref
                or evidence.authorization.session_generation != self.session_generation):
                raise ValueError('Migration evidence does not bind replay initial authority')


class MigrationGate:
    """Process-local exact review/consent gate for one migration cutover."""

    def __init__(self, proposal: MigrationProposal, session_generation: str):
        if type(proposal) is not MigrationProposal:
            raise Rejected('Migration gate requires a validated proposal')
        nonempty(session_generation, 'session_generation')
        self._proposal = proposal
        self._generation = session_generation
        self._previews = {}
        self._authorizations = {}
        self._store = None
        self._evidence = None
        self._lock = RLock()

    def preview(self, actor: str, preview_ref: Ref) -> MigrationPreview:
        with self._lock:
            if actor != self._proposal.manifest.owner_id:
                raise Rejected('Migration actor/owner mismatch')
            value = MigrationPreview(preview_ref, self._proposal.manifest.ref,
                self._proposal.digest, self._proposal.manifest.source_hash,
                self._proposal.manifest.target_world_ref,
                self._proposal.manifest.unresolved)
            previous = self._previews.get(preview_ref)
            if previous is not None and previous != value:
                raise Rejected('Migration preview identity cannot be overwritten')
            self._previews[preview_ref] = value
            return value

    def authorize(self, actor: str, preview: MigrationPreview, authorization_ref: Ref,
                  granted_at: datetime) -> MigrationAuthorization:
        with self._lock:
            if self._previews.get(preview.ref) != preview:
                raise Rejected('Must authorize the exact registered migration preview')
            if actor != self._proposal.manifest.owner_id:
                raise Rejected('Migration actor/owner mismatch')
            aware(granted_at, 'granted_at')
            if granted_at < self._proposal.manifest.observation.recorded_at:
                raise Rejected('Migration authorization predates the proposal')
            value = MigrationAuthorization(authorization_ref, actor, actor,
                self._proposal.manifest.world_id, self._generation, preview.ref,
                preview.proposal_digest, preview.source_hash,
                preview.target_world_ref, granted_at)
            previous = self._authorizations.get(value.ref)
            if previous is not None and previous != value:
                raise Rejected('Migration authorization identity cannot be overwritten')
            self._authorizations[value.ref] = value
            return value

    def cutover(self, authorization: MigrationAuthorization) -> WorldStore:
        with self._lock:
            if self._store is not None:
                if self._evidence.authorization != authorization:
                    raise Rejected('Migration already cut over under different authorization')
                return self._store
            if self._authorizations.get(authorization.ref) != authorization:
                raise Rejected('Registered migration authorization required')
            preview = self._previews.get(authorization.preview_ref)
            manifest = self._proposal.manifest
            if (preview is None or authorization.actor != manifest.owner_id
                or authorization.owner_id != manifest.owner_id
                or authorization.world_id != manifest.world_id
                or authorization.session_generation != self._generation
                or authorization.proposal_digest != self._proposal.digest
                or authorization.source_hash != manifest.source_hash
                or authorization.target_world_ref != manifest.target_world_ref):
                raise Rejected('Migration authorization does not bind this exact proposal')
            self._store = WorldStore._restore_replay_initial(
                self._proposal.initial_snapshot, self._generation)
            self._evidence = MigrationEvidence(manifest, preview, authorization,
                self._proposal.legacy_assessment, self._proposal.source_export)
            return self._store

    @property
    def evidence(self) -> MigrationEvidence:
        if self._evidence is None:
            raise Rejected('Migration has not cut over')
        return self._evidence


def migrate_legacy_snapshot(snapshot: Snapshot, world_id: str,
                            manifest_ref: Ref, world_ref: Ref,
                            observation: ObservationMetadata) -> MigrationProposal:
    """Build a deterministic proposal from one complete validated legacy snapshot."""
    _validate_legacy_snapshot(snapshot)
    nonempty(world_id, 'world_id')
    if observation.role is not ObservationRole.CURRENT:
        raise Rejected('Migration observation must describe the reviewed current snapshot')
    legacy_refs = {record.ref for record in snapshot.records
                   if type(record) in (Evidence, Assumption)}
    if any(ref not in legacy_refs for ref in (*observation.evidence_refs, *observation.assumption_refs)):
        raise Rejected('Migration observation provenance must resolve in the legacy snapshot')

    source_export = _legacy_export(snapshot)
    source_hash = _sha(source_export)
    state = snapshot.get(FinancialState, snapshot.current_state_ref)
    plan = snapshot.get(LivingPlan, snapshot.current_plan_ref)
    owner_id = state.owner_id
    if plan.owner_id != owner_id:
        raise Rejected('Legacy current state/plan owner mismatch')

    state_versions = [item.ref.version for item in snapshot.records
                      if type(item) is FinancialState and item.ref.id == state.ref.id]
    target_state_ref = Ref(state.ref.id, max(state_versions) + 1)
    target_state = WorldState(target_state_ref, owner_id, world_id, (), (), (),
        observation,
        'Legacy aggregate liquidity is retained separately; account composition and total current assets remain unknown.')

    source_decision_refs = {plan.decision_ref, *(item.decision_ref for item in plan.planned_outflows)}
    legacy_decisions = tuple(snapshot.get(Decision, ref) for ref in sorted(source_decision_refs, key=_ref_key))
    decision_targets = {}
    for decision in legacy_decisions:
        versions = [item.ref.version for item in snapshot.records
                    if type(item) is Decision and item.ref.id == decision.ref.id]
        decision_targets[decision.ref] = Ref(decision.ref.id, max(versions) + 1)

    origin_rows = _planned_outflow_origins(snapshot, plan)
    expense_refs = tuple(Ref(
        'legacy-expense/{0}@{1}/{2}/{3}'.format(origin.ref.id, origin.ref.version,
                                                ordinal, outflow.decision_ref.id), 1)
        for outflow, origin, ordinal in origin_rows)
    expenses = tuple(PlannedExpense(
        expense_ref, owner_id, world_id, AmountEstimate.exact_reported(outflow.amount),
        'Migrated legacy planned outflow for decision ' + outflow.decision_ref.id,
        TemporalSpec.unknown('Legacy planned outflow has no occurrence date'), (),
        TypedRef(PlanningRecordKind.WORLD_DECISION.value, decision_targets[outflow.decision_ref]),
        PlannedExpenseStatus.PLANNED, replace(observation, role=ObservationRole.PLANNED))
        for (outflow, _, _), expense_ref in zip(origin_rows, expense_refs))

    decisions = []
    for decision in legacy_decisions:
        related = tuple(planning_ref(PlanningRecordKind.PLANNED_EXPENSE, expense.ref)
                        for expense, row in zip(expenses, origin_rows)
                        if row[0].decision_ref == decision.ref)
        amount = None
        option = None
        if decision.selected_option_ref is not None:
            option = snapshot.get(DecisionOption, decision.selected_option_ref)
            if option.planned_outflow is not None:
                amount = AmountEstimate.exact_reported(option.planned_outflow)
        decisions.append(WorldDecision(decision_targets[decision.ref], owner_id, world_id,
            decision.question, decision.question,
            TypedRef('world_state', target_state.ref), decision.status,
            decision.selected_option_ref, amount,
            TemporalSpec.unknown('Legacy decision timing was not represented'), related,
            replace(observation, role=ObservationRole.CONTEMPLATED)))
    decisions = tuple(decisions)

    plan_versions = [item.ref.version for item in snapshot.records
                     if type(item) is LivingPlan and item.ref.id == plan.ref.id]
    target_plan_ref = Ref(plan.ref.id, max(plan_versions) + 1)
    anchor = LegacyPlanAnchor(plan.ref, owner_id, world_id, LEGACY_SCHEMA,
                              source_hash, replace(observation, role=ObservationRole.PLANNED))
    target_plan = WorldPlan(target_plan_ref, owner_id, world_id,
        TypedRef('world_state', target_state.ref), TypedRef('legacy_world_plan', plan.ref),
        tuple(planning_ref(PlanningRecordKind.WORLD_DECISION, item.ref) for item in decisions),
        tuple(planning_ref(PlanningRecordKind.PLANNED_EXPENSE, item.ref) for item in expenses),
        plan.allocations, plan.assumption_refs, plan.status, PlanEvaluationStatus.PENDING,
        replace(observation, role=ObservationRole.PLANNED))

    members = tuple(record_ref(item) for item in (*decisions, *expenses))
    root = FinancialWorld(world_ref, owner_id, world_id, target_state.ref,
                          target_plan.ref, members)
    records = tuple(sorted((anchor, target_state, *decisions, *expenses, target_plan),
                           key=lambda item: ref_key(record_ref(item))))
    initial = WorldSnapshot(records, (root,))
    validate_world(initial)

    employment = None
    if state.employment is not None:
        offer = state.employment.offer
        employment = LegacyEmploymentAssessment(
            state.employment.status.value, state.employment.uncertainty_ref,
            LegacyFinancialAmount.from_legacy(offer.monthly_income) if offer else None,
            offer.salary_basis.value if offer else None,
            offer.accepted_on.isoformat() if offer else None,
            offer.employment_start_on.isoformat() if offer and offer.employment_start_on else None,
            state.employment.source_event_ref,
        )
    assessment = LegacyLiquidityAssessment(
        Ref('legacy-liquidity/' + state.ref.id, 1), owner_id, world_id, state.ref,
        LegacyFinancialAmount.from_legacy(state.available_emergency_liquidity),
        LegacyFinancialAmount.from_legacy(state.monthly_essential_outflow),
        employment, state.goal_refs, True,
        'This is legacy emergency-liquidity assessment data, not an Account, AssetPosition, total-assets value, or payment source.')

    mappings = _migration_mappings(snapshot, plan, target_state, target_plan,
                                   decisions, expenses, origin_rows)
    unresolved = tuple(sorted((
        MigrationIssue('account_composition_unknown',
            'Legacy aggregate liquidity does not identify any account, position, ownership, or payment source.', (state.ref,)),
        MigrationIssue('current_asset_total_unknown',
            'Legacy emergency-eligible liquidity cannot be promoted to total current assets.', (state.ref,)),
        MigrationIssue('if_line_successor_unrepresented',
            'Legacy If Line history remains in the source export; no new-schema If Line successor exists.',
            tuple(item.ref for item in snapshot.records if type(item) is IfLine)),
        MigrationIssue('legacy_authority_closed',
            'Legacy authorizations are historical evidence and cannot authorize Financial World mutation.',
            tuple(item.ref for item in snapshot.records if type(item) is PlanAuthorization)),
        MigrationIssue('payment_source_unknown',
            'Migrated planned outflows have no inferred source or destination.',
            (plan.ref,) if plan.planned_outflows else ()),
    ), key=lambda item: (item.code, tuple(_ref_key(ref) for ref in item.source_refs))))
    manifest = MigrationManifest(manifest_ref, LEGACY_SCHEMA, source_hash,
        MIGRATION_RULE_VERSION, owner_id, world_id, state.ref, plan.ref,
        world_ref, mappings, unresolved, observation)
    return MigrationProposal(manifest, initial, assessment, source_export)


def _history_parts(snapshot):
    """Exact initial closure and ordered F steps, also used by v0.2."""
    validate_world(snapshot)
    if not (len(snapshot.authorizations) == len(snapshot.commands) == len(snapshot.receipts) == snapshot.revision) or len(snapshot.worlds) != snapshot.revision + 1:
        raise Rejected("Incomplete replay history")
    inserted = tuple(ref for receipt in snapshot.receipts for ref in receipt.inserted_refs)
    if len(set(inserted)) != len(inserted):
        raise Rejected('A replay record version was introduced more than once')
    inserted_set = set(inserted)
    initial_records = tuple(item for item in snapshot.records if record_ref(item) not in inserted_set)
    if len(initial_records) + len(inserted) != len(snapshot.records):
        raise Rejected('Replay receipts do not account for immutable record history')
    initial = WorldSnapshot(initial_records, (snapshot.worlds[0],))

    steps = []
    for index, (authorization, command, receipt) in enumerate(zip(
            snapshot.authorizations, snapshot.commands, snapshot.receipts)):
        records = tuple(snapshot.get(ref) for ref in receipt.inserted_refs)
        steps.append(WorldReplayStep(MUTATION_SCHEMA, authorization, command,
                                     receipt, snapshot.worlds[index + 1], records))
    return initial, tuple(steps)


def export_world_replay(snapshot: WorldSnapshot, session_generation: Optional[str] = None,
                        migration_evidence: Optional[MigrationEvidence] = None,
                        package_ref: Optional[Ref] = None) -> WorldReplayPackage:
    """Export a complete explicit package from process-local immutable history."""
    if type(snapshot) is not WorldSnapshot:
        raise Rejected('Replay export requires a WorldSnapshot')
    validate_world(snapshot)
    if not (len(snapshot.authorizations) == len(snapshot.commands) == len(snapshot.receipts)):
        raise Rejected('Replay history arrays are incomplete')
    if len(snapshot.worlds) != len(snapshot.receipts) + 1 or snapshot.revision != len(snapshot.receipts):
        raise Rejected('Replay roots/revision do not match successful receipts')
    generations = {item.session_generation for item in snapshot.receipts}
    if len(generations) > 1:
        raise Rejected('One replay package cannot span session generations')
    generation = session_generation or (next(iter(generations)) if generations else None)
    if generation is None and migration_evidence is not None:
        generation = migration_evidence.authorization.session_generation
    nonempty(generation, 'session_generation')
    if generations and generations != {generation}:
        raise Rejected('Replay session generation mismatch')

    initial, steps = _history_parts(snapshot)
    WorldStore._restore_replay_initial(initial, generation)
    ref = package_ref or Ref('world-replay/' + snapshot.world.world_id, 1)
    return WorldReplayPackage(ref, generation, initial, tuple(steps),
        snapshot.world.ref, snapshot.world.state_ref, snapshot.world.plan_ref,
        snapshot.revision, payload_digest(snapshot), _derived_digest(snapshot),
        migration_evidence)


def _replay_step(store, step):
    """Replay one original D/E/History step; no live storage side effects."""
    if step.command_schema != MUTATION_SCHEMA:
        raise Rejected('Unknown replay command schema')
    preview = store.preview(step.command.actor, step.command.change_set,
                            step.receipt.preview_ref)
    if preview.digest != step.receipt.preview_digest:
        raise Rejected('Recomputed replay preview differs from receipt')
    authorization = store.authorize(step.authorization.actor, preview,
        step.authorization.ref, step.authorization.granted_at)
    if authorization != step.authorization:
        raise Rejected('Recomputed replay authorization differs from history')
    receipt = store.execute(step.command)
    if receipt != step.receipt or store.snapshot.world != step.after_world:
        raise Rejected('Replayed receipt/root differs from history')
    if tuple(store.snapshot.get(ref) for ref in receipt.inserted_refs) != step.introduced_records:
        raise Rejected('Replayed immutable records differ from history')


def replay_world(package: WorldReplayPackage) -> WorldSnapshot:
    """Re-execute every recorded contract and fail closed on any drift."""
    if type(package) is not WorldReplayPackage:
        raise Rejected('Replay requires a validated WorldReplayPackage')
    store = WorldStore._restore_replay_initial(package.initial_snapshot,
                                               package.session_generation)
    for step in package.steps:
        _replay_step(store, step)
    result = store.snapshot
    if ((result.world.ref, result.world.state_ref, result.world.plan_ref, result.revision) !=
        (package.final_world_ref, package.final_state_ref,
         package.final_plan_ref, package.final_revision)):
        raise Rejected('Replay final authority pointers differ')
    if payload_digest(result) != package.final_snapshot_digest:
        raise Rejected('Replay final snapshot differs')
    if _derived_digest(result) != package.final_derived_digest:
        raise Rejected('Replay derived outputs differ')
    return result


def _validate_legacy_snapshot(snapshot: Snapshot) -> None:
    if type(snapshot) is not Snapshot:
        raise Rejected('Migration requires a complete legacy Snapshot')
    MemoryStore._validate(snapshot)
    records = tuple(item for item in snapshot.records if hasattr(item, 'ref'))
    refs = {(type(item), item.ref) for item in records}
    if len(refs) != len(records):
        raise Rejected('Legacy snapshot contains duplicate record versions')
    state = snapshot.get(FinancialState, snapshot.current_state_ref)
    plan = snapshot.get(LivingPlan, snapshot.current_plan_ref)
    if plan.state_ref != state.ref or plan.owner_id != state.owner_id:
        raise Rejected('Legacy current state/plan are incoherent')
    if snapshot.latest(FinancialState, state.ref.id) != state or snapshot.latest(LivingPlan, plan.ref.id) != plan:
        raise Rejected('Legacy current pointers must select latest identity versions')
    for ref in state.goal_refs:
        snapshot.get(Goal, ref)
    for allocation in plan.allocations:
        snapshot.get(Goal, allocation.goal_ref)
    decision_refs = {plan.decision_ref, *(item.decision_ref for item in plan.planned_outflows)}
    for ref in decision_refs:
        decision = snapshot.get(Decision, ref)
        if snapshot.latest(Decision, ref.id) != decision:
            raise Rejected('Legacy current plan references a historical decision')
        if decision.selected_option_ref is not None:
            option = snapshot.get(DecisionOption, decision.selected_option_ref)
            if option.decision_ref.id != decision.ref.id:
                raise Rejected('Legacy selected option belongs to another decision')
    if any(item.owner_id != state.owner_id for item in snapshot.records if hasattr(item, 'owner_id')):
        raise Rejected('Legacy snapshot mixes owners')


def _planned_outflow_origins(snapshot: Snapshot, current_plan: LivingPlan):
    chain = sorted((item for item in snapshot.records
                    if type(item) is LivingPlan and item.ref.id == current_plan.ref.id),
                   key=lambda item: item.ref.version)
    rows = []
    for ordinal, outflow in enumerate(current_plan.planned_outflows):
        matching = [plan for plan in chain
                    if len(plan.planned_outflows) > ordinal
                    and plan.planned_outflows[ordinal] == outflow]
        if not matching:
            raise Rejected('Legacy planned outflow has no stable origin')
        origin = matching[0]
        rows.append((outflow, origin, ordinal))
    return tuple(rows)


def _migration_mappings(snapshot, current_plan, state, plan, decisions, expenses, origins):
    values = []
    current_state = snapshot.get(FinancialState, snapshot.current_state_ref)
    values.append(MigrationMapping('FinancialState', current_state.ref, None, 'successor',
        TypedRef('world_state', state.ref), MigrationDisposition.MIGRATED,
        'Current legacy state maps to an account-empty successor plus a separate aggregate assessment.'))
    for source in sorted((item for item in snapshot.records if type(item) is LivingPlan), key=lambda item: _ref_key(item.ref)):
        if source.ref == current_plan.ref:
            values.append(MigrationMapping('LivingPlan', source.ref, None, 'historical_anchor',
                TypedRef('legacy_world_plan', source.ref), MigrationDisposition.HISTORICAL_ONLY,
                'Current legacy plan is the explicit predecessor anchor.'))
            values.append(MigrationMapping('LivingPlan', source.ref, None, 'successor',
                TypedRef('world_plan', plan.ref), MigrationDisposition.MIGRATED,
                'New-schema plan continues above the highest legacy version.'))
        else:
            values.append(MigrationMapping('LivingPlan', source.ref, None, 'history', None,
                MigrationDisposition.HISTORICAL_ONLY,
                'Earlier legacy plan version remains readable in the source export.'))
    target_by_id = {item.ref.id: item for item in decisions}
    for source in sorted((item for item in snapshot.records if type(item) is Decision), key=lambda item: _ref_key(item.ref)):
        target = target_by_id.get(source.ref.id)
        current = snapshot.latest(Decision, source.ref.id)
        values.append(MigrationMapping('Decision', source.ref, None,
            'successor' if source == current and target else 'history',
            TypedRef('world_decision', target.ref) if source == current and target else None,
            MigrationDisposition.MIGRATED if source == current and target else MigrationDisposition.HISTORICAL_ONLY,
            'Current referenced decision maps once; older versions remain historical.'))
    plans = tuple(sorted((item for item in snapshot.records if type(item) is LivingPlan),
                         key=lambda item: _ref_key(item.ref)))
    for (outflow, origin, ordinal), expense in zip(origins, expenses):
        for source in plans:
            if (source.ref.id == current_plan.ref.id
                and len(source.planned_outflows) > ordinal
                and source.planned_outflows[ordinal] == outflow):
                is_origin = source.ref == origin.ref
                values.append(MigrationMapping('PlannedOutflow', source.ref, ordinal,
                    'economic_intent_origin' if is_origin else 'economic_intent_carried',
                    TypedRef('planned_expense', expense.ref), MigrationDisposition.MIGRATED,
                    'The original and every carried copy map to the same single planned obligation.'))
    for source in sorted((item for item in snapshot.records if type(item) is IfLine), key=lambda item: _ref_key(item.ref)):
        values.append(MigrationMapping('IfLine', source.ref, None, 'history', None,
            MigrationDisposition.UNREPRESENTED,
            'If Line remains immutable legacy history; Slice F invents no successor schema.'))
    for source in sorted((item for item in snapshot.records if type(item) is PlanAuthorization), key=lambda item: _ref_key(item.ref)):
        values.append(MigrationMapping('PlanAuthorization', source.ref, None, 'authority', None,
            MigrationDisposition.NON_EXECUTABLE,
            'Legacy consent remains historical and grants no Financial World authority.'))
    for source in sorted((item for item in snapshot.records if type(item) is ExecutionLog), key=lambda item: _ref_key(item.ref)):
        values.append(MigrationMapping('ExecutionLog', source.ref, None, 'receipt', None,
            MigrationDisposition.HISTORICAL_ONLY,
            'Legacy receipt remains valid evidence for its original immutable versions.'))
    return tuple(sorted(values, key=_mapping_key))


def _mapping_key(value):
    target = value.target_ref
    return (value.source_kind, value.source_ref.id, value.source_ref.version,
            -1 if value.source_ordinal is None else value.source_ordinal,
            value.relation, '' if target is None else target.record_kind,
            '' if target is None else target.ref.id,
            0 if target is None else target.ref.version)


def _legacy_export(snapshot):
    return json.dumps(structured(snapshot), ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False)


def _derived_digest(snapshot):
    assets = current_assets(snapshot)
    live = tuple(snapshot.get(ref) for ref in snapshot.world.members)
    payload = {
        'current_assets': [serialize_world_asset(item) for item in assets.current_assets_by_currency()],
        'earmarked_assets': [serialize_world_asset(item) for item in assets.earmarked_assets_by_currency()],
        'unallocated_assets': [serialize_world_asset(item) for item in assets.unallocated_assets_by_currency()],
        'open_expenses': [serialize_world_planning(item) for item in live if type(item) is PlannedExpense and is_open(item)],
        'effects': [serialize_world_mutation(item) for item in live if record_ref(item).record_kind == 'balance_effect'],
        'pending': [serialize_world_mutation(item) for item in validate_world(snapshot)],
    }
    return _sha(json.dumps(payload, ensure_ascii=False, sort_keys=True,
                           separators=(',', ':'), allow_nan=False))


def _ref_key(value):
    return value.id, value.version


def _sha(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


CUSTOM_TYPES = {kind.__name__: kind for kind in (
    LegacyFinancialAmount, LegacyEmploymentAssessment, LegacyLiquidityAssessment,
    MigrationMapping, MigrationIssue, MigrationManifest, MigrationProposal,
    MigrationPreview, MigrationAuthorization, MigrationEvidence,
    WorldReplayStep, WorldReplayPackage,
)}


def _encode(value):
    if type(value) in CUSTOM_TYPES.values():
        return {'schema_version': REPLAY_SCHEMA, 'record_type': type(value).__name__,
                'value': {field.name: _encode(getattr(value, field.name)) for field in fields(value)}}
    if type(value) is MigrationDisposition:
        return {'schema_version': REPLAY_SCHEMA, 'migration_disposition': value.value}
    if value is None or type(value) in (str, int, bool):
        return value
    if type(value) is tuple:
        return [_encode(item) for item in value]
    if type(value) is Precision:
        return {'schema_version': REPLAY_SCHEMA, 'legacy_precision': value.value}
    if type(value) is datetime:
        return {'schema_version': REPLAY_SCHEMA, 'datetime': value.isoformat()}
    try:
        return serialize_world_mutation(value)
    except (TypeError, ValueError):
        pass
    for serializer in (serialize_world_asset, serialize_world_planning, serialize_world_value):
        try:
            return serializer(value)
        except TypeError:
            pass
    raise ValueError('Unsupported replay value: ' + type(value).__name__)


def _decode(value):
    if value is None or type(value) in (str, int, bool):
        return value
    if type(value) is list:
        return tuple(_decode(item) for item in value)
    if type(value) is not dict:
        raise ValueError('Replay value must be a JSON object/value')
    if value.get('schema_version') == REPLAY_SCHEMA:
        if set(value) == {'schema_version', 'migration_disposition'}:
            return MigrationDisposition(value['migration_disposition'])
        if set(value) == {'schema_version', 'legacy_precision'}:
            return Precision(value['legacy_precision'])
        if set(value) == {'schema_version', 'datetime'}:
            parsed = datetime.fromisoformat(value['datetime'])
            aware(parsed, 'datetime')
            return parsed
        _object(value, {'schema_version', 'record_type', 'value'}, 'replay envelope')
        kind = CUSTOM_TYPES.get(value['record_type'])
        if kind is None:
            raise ValueError('Unknown replay record type')
        raw = value['value']
        _object(raw, {field.name for field in fields(kind)}, kind.__name__)
        return kind(**{field.name: _decode(raw[field.name]) for field in fields(kind)})
    try:
        return deserialize_world_mutation(value)
    except (TypeError, ValueError, KeyError):
        pass
    for deserializer in (deserialize_world_asset, deserialize_world_planning, deserialize_world_value):
        try:
            return deserializer(value)
        except (TypeError, ValueError, KeyError):
            pass
    raise ValueError('Unknown nested replay schema')


def serialize_world_replay(value):
    if type(value) not in CUSTOM_TYPES.values():
        raise TypeError('Unsupported world replay type')
    return _encode(value)


def deserialize_world_replay(value):
    result = _decode(value)
    if type(result) not in CUSTOM_TYPES.values():
        raise ValueError('Top-level value is not a world replay type')
    return result


def dumps_world_replay(value):
    return json.dumps(serialize_world_replay(value), ensure_ascii=False,
                      sort_keys=True, separators=(',', ':'), allow_nan=False)


def loads_world_replay(payload):
    if not isinstance(payload, str):
        raise ValueError('Replay payload must be text')
    return deserialize_world_replay(json.loads(
        payload, object_pairs_hook=_strict_pairs,
        parse_constant=lambda value: (_ for _ in ()).throw(ValueError('Nonfinite JSON'))))


def replay_payload_digest(value):
    return _sha(dumps_world_replay(value))


def _strict_pairs(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError('Duplicate JSON key')
        value[key] = item
    return value


def _object(value, keys, name):
    if type(value) is not dict or set(value) != keys:
        raise ValueError(name + ' has unexpected fields')
    return value

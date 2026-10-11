"""Slice D single-process authority boundary, independent of the legacy writer.

Like MemoryStore, stages immutable history under RLock and publishes once.
Registered consent is transient until included in the successful publication.
This is neither production authentication nor durable crash recovery.
"""

from dataclasses import replace
from threading import RLock

from .domain import PlanStatus, Ref, aware, nonempty
from .lifecycle import next_ref, owner
from .store import Rejected, StaleVersion
from .world_commands import (
    Account, AssetPosition, Earmark, WorldState, WorldPlan, FutureInflow,
    PlannedExpense, ActualOccurrence, WorldDecision, DecisionContext,
    CandidateChangeSet, ChangeMode, FinancialWorld, FinancialWorldCommand,
    IssueDisposition, MutationPreview, PutRecord, RecordChange, ReconciliationIssue,
    RetireRecord, WorldAuthorization, WorldMutationEvent, WorldSnapshot,
    record_ref, ref_key, typed_links,
)
from .world_domain import WorldAssetBundle, RecordLifecycle, EarmarkLifecycle
from .world_planning import (
    WorldPlanningBundle, PlanEvaluationStatus, PlannedExpenseStatus,
    FutureInflowStatus, OccurrenceKind, SettlementStatus,
)
from .world_values import ObservationRole, TypedRef
from .world_serialization import payload_digest
from .world_fulfillment import is_open, is_refund, validate_event, validate_fulfillment, validate_plan_edit
from .world_settlement import BalanceEffect, SettleOccurrence, expand_settlement, validate_effect


def _key(ref):
    return ref.record_kind, ref.ref.id


def _pending(code, rationale, refs=()):
    # READY means the record write is not blocked; the calculation stays pending.
    return ReconciliationIssue(code, IssueDisposition.READY, rationale, refs)


def initial_world(bundle: WorldPlanningBundle, world_ref: Ref) -> WorldSnapshot:
    """Trusted bootstrap of a new-schema world only; never migrates Phase 2."""
    if type(bundle) is not WorldPlanningBundle or type(world_ref) is not Ref or world_ref.version != 1:
        raise Rejected('Bootstrap requires a validated Slice C bundle and v1 root')
    members = (*bundle.assets.accounts, *bundle.assets.positions, *bundle.assets.earmarks,
               *bundle.future_inflows, *bundle.planned_expenses, *bundle.actual_occurrences,
               *bundle.decisions, *bundle.contexts)
    state, plan = bundle.assets.state, bundle.plan
    if state.ref.version != 1 or plan.ref.version != 1 or plan.previous_plan_ref is not None:
        raise Rejected('Bootstrap requires initial state and plan, not a history import')
    root = FinancialWorld(world_ref, state.owner_id, state.world_id, state.ref, plan.ref,
                          tuple(record_ref(item) for item in members))
    records = tuple(sorted((*members, state, plan), key=lambda item: ref_key(record_ref(item))))
    result = WorldSnapshot(records, (root,))
    validate_world(result)
    return result


def current_assets(snapshot):
    root = snapshot.world
    state = snapshot.get(TypedRef('world_state', root.state_ref))
    return WorldAssetBundle(state,
        tuple(snapshot.get(ref) for ref in state.account_refs),
        tuple(snapshot.get(ref) for ref in state.position_refs),
        tuple(snapshot.get(ref) for ref in state.earmark_refs))


def validate_world(snapshot, newly_written=()):
    """Resolve live membership and retained historical dependencies explicitly."""
    root = snapshot.world
    live = set(root.members)
    monthly = [snapshot.get(ref).amount.currency for ref in live
               if ref.record_kind == 'monthly_essential_outflow']
    if len(monthly) != len(set(monthly)):
        raise Rejected('One current monthly essentials report per currency; review an exact correction')
    index = {record_ref(item): item for item in snapshot.records}
    for item in snapshot.records:
        if item.owner_id != root.owner_id or item.world_id != root.world_id:
            raise Rejected('Every record must share world and owner')
        metadata = item.snapshot_observation if type(item) is WorldState else item.observation
        if metadata.corrects_ref is not None:
            previous_ref = TypedRef(record_ref(item).record_kind, metadata.corrects_ref)
            if (previous_ref not in index or metadata.corrects_ref.id != item.ref.id
                or metadata.corrects_ref.version != item.ref.version - 1):
                raise Rejected('Correction lineage must resolve to the prior record version')
    assets = current_assets(snapshot)  # Slice B conservation is the same authority.
    expected_assets = set((*assets.state.account_refs, *assets.state.position_refs, *assets.state.earmark_refs))
    if {ref for ref in live if ref.record_kind in ('account', 'asset_position', 'earmark')} != expected_assets:
        raise Rejected('Root/state asset membership mismatch')
    state_ref = TypedRef('world_state', root.state_ref)
    plan = snapshot.get(TypedRef('world_plan', root.plan_ref))
    if plan.source_state_ref != state_ref or plan.status is PlanStatus.SUPERSEDED:
        raise Rejected('Current world must have one coherent current plan')
    for ref in live:
        snapshot.get(ref)
        versions = [candidate.ref.version for candidate in index if _key(candidate) == _key(ref)]
        if ref.ref.version != max(versions):
            raise Rejected('Current membership cannot reuse an older version')
    pending = []
    for record in (*(snapshot.get(ref) for ref in root.members), plan):
        if type(record) is BalanceEffect:
            validate_effect(snapshot, record)
            continue  # Effect references deliberately pin historical balance versions.
        for link in typed_links(record):
            target = snapshot.get(link)
            if link not in live and link not in (state_ref, record_ref(plan)):
                historical_settlement = (
                    type(record) is ActualOccurrence and record.balance_effect_ref is not None
                    and link in (record.source_position_ref, record.destination_position_ref)
                ) or (
                    type(record) is PlannedExpense and record.status is PlannedExpenseStatus.COMPLETED
                    and record.occurrence_ref is not None
                    and snapshot.get(record.occurrence_ref).balance_effect_ref is not None
                    and link in tuple(f.source_ref for f in record.funding_links)
                ) or (
                    type(record) is FutureInflow and record.status is FutureInflowStatus.REALIZED
                    and snapshot.get(record.realization_ref).balance_effect_ref is not None
                    and link == record.destination_ref
                ) or (
                    type(record) is PlannedExpense and record.fulfillment is not None
                    and bool(record.fulfillment.occurrence_refs)
                    and link in tuple(f.source_ref for f in record.funding_links)
                ) or (
                    type(record) is ActualOccurrence and record.balance_effect_ref is not None
                    and (link in tuple(typed_links(record.lineage)) or
                         link in record.related_entity_refs and type(target) is PlannedExpense and target.fulfillment is not None)
                )
                if historical_settlement:
                    continue
                if record_ref(record) in newly_written and not (
                    type(record) is WorldPlan and link == record.previous_plan_ref
                ):
                    raise Rejected('New relationship references a noncurrent version')
                if type(record) is not WorldPlan or link != record.previous_plan_ref:
                    pending.append(_pending('historical_dependency',
                        'Retained input is historical; current funding/evaluation is unresolved', (record_ref(record), link)))
        if type(record) is FutureInflow and record.destination_ref is not None:
            destination = snapshot.get(record.destination_ref)
            if destination.currency is not record.expected_amount.currency:
                raise Rejected('Future destination currency mismatch')
        if type(record) is PlannedExpense:
            validate_fulfillment(snapshot, record)
            for link in record.funding_links:
                source = snapshot.get(link.source_ref)
                if type(source) is Earmark:
                    source = snapshot.get(source.position_ref)
                if source.currency is not link.amount.currency or source.currency is not record.expected_amount.currency:
                    raise Rejected('Expense funding currency mismatch')
        if type(record) is ActualOccurrence:
            validate_event(snapshot, record)
            if record.settlement_status is not SettlementStatus.UNALLOCATED:
                if record.settlement_status not in (SettlementStatus.APPLIED_TO_BALANCE, SettlementStatus.ALREADY_IN_BALANCE):
                    raise Rejected('Unsupported occurrence reversal/supersession')
                effect = snapshot.get(record.balance_effect_ref)
                if type(effect) is not BalanceEffect or effect.occurrence_ref != record_ref(record):
                    raise Rejected('Settled occurrence must have its exact effect')
            else:
                pending.append(_pending('unsettled_occurrence', 'Occurrence has no reconciled balance effect', (record_ref(record),)))
            for position_ref in (record.source_position_ref, record.destination_position_ref):
                if position_ref and snapshot.get(position_ref).currency is not record.actual_amount.currency:
                    raise Rejected('Occurrence currency mismatch')
            resolved = [link for link in record.related_entity_refs if link.record_kind in ('future_inflow', 'planned_expense')]
            if len(resolved) > 1:
                raise Rejected('Split or multiple realizations are unsupported')
            for link in resolved:
                related = snapshot.get(link)
                if type(related) is PlannedExpense and related.fulfillment is not None:
                    if record_ref(record) not in related.fulfillment.occurrence_refs or (record.kind is not OccurrenceKind.EXPENSE and not is_refund(record)):
                        raise Rejected('Gradual occurrence must have reciprocal event membership')
                else:
                    reciprocal = related.realization_ref if type(related) is FutureInflow else related.occurrence_ref
                    expected_kind = OccurrenceKind.INFLOW if type(related) is FutureInflow else OccurrenceKind.EXPENSE
                    if reciprocal != record_ref(record) or record.kind is not expected_kind:
                        raise Rejected('Occurrence must reciprocally resolve the appropriate lifecycle')
        occurrence_ref = (record.realization_ref if type(record) is FutureInflow else
                          record.occurrence_ref if type(record) is PlannedExpense else None)
        if occurrence_ref:
            occurrence = snapshot.get(occurrence_ref)
            expected_kind = OccurrenceKind.INFLOW if type(record) is FutureInflow else OccurrenceKind.EXPENSE
            if occurrence.kind is not expected_kind or record_ref(record) not in occurrence.related_entity_refs:
                raise Rejected('Lifecycle and occurrence must match reciprocally')
            if occurrence.actual_amount.currency is not record.expected_amount.currency:
                raise Rejected('Expected/actual currency mismatch')
    open_expenses = {ref for ref in live if ref.record_kind == 'planned_expense'
                     and is_open(snapshot.get(ref))}
    if set(plan.planned_expense_refs) != open_expenses:
        raise Rejected('Plan must name exactly the still-planned expenses')
    if any(ref not in live for ref in plan.supporting_decision_refs):
        raise Rejected('Plan must name current decision versions')
    if pending and plan.evaluation_status is PlanEvaluationStatus.CURRENT:
        raise Rejected('Unresolved dependencies cannot claim current evaluation')
    return tuple(pending)


class WorldStore:
    """Only execute publishes world changes. No unrestricted transact method."""

    def __init__(self, initial: WorldSnapshot, session_generation: str, before_publish=None):
        if type(initial) is not WorldSnapshot or initial.revision or len(initial.worlds) != 1 or initial.receipts or initial.commands or initial.authorizations:
            raise Rejected('Only trusted initial worlds supported; restoration belongs to Slice F')
        initial_refs = {*initial.world.members, TypedRef('world_state', initial.world.state_ref),
                        TypedRef('world_plan', initial.world.plan_ref)}
        if (initial.world.ref.version != 1 or any(item.ref.version != 1 for item in initial.records)
            or {record_ref(item) for item in initial.records} != initial_refs):
            raise Rejected('Bootstrap cannot import hidden history or existing versions')
        nonempty(session_generation, 'session_generation')
        validate_world(initial)
        self._snapshot = initial
        self._generation = session_generation
        self._lock = RLock()
        self._previews = {}
        self._authorizations = {}
        self._cancelled = set()
        self._before_publish = before_publish

    @classmethod
    def _restore_replay_initial(cls, initial, session_generation, before_publish=None):
        """Create authority only from Slice F's validated initial replay boundary."""
        if (type(initial) is not WorldSnapshot or initial.revision
            or len(initial.worlds) != 1 or initial.receipts
            or initial.commands or initial.authorizations):
            raise Rejected('Replay restoration requires one unmutated initial world')
        nonempty(session_generation, 'session_generation')
        validate_world(initial)
        required = {*initial.world.members,
                    TypedRef('world_state', initial.world.state_ref),
                    TypedRef('world_plan', initial.world.plan_ref)}
        pending = list(required)
        while pending:
            record = initial.get(pending.pop())
            for link in typed_links(record):
                if link not in required:
                    required.add(link)
                    pending.append(link)
        if {record_ref(item) for item in initial.records} != required:
            raise Rejected('Replay initial world contains unreferenced hidden history')
        value = cls.__new__(cls)
        value._snapshot = initial
        value._generation = session_generation
        value._lock = RLock()
        value._previews = {}
        value._authorizations = {}
        value._cancelled = set()
        value._before_publish = before_publish
        return value

    @property
    def snapshot(self):
        with self._lock:
            return self._snapshot

    @property
    def session_generation(self):
        return self._generation

    def preview(self, actor, change_set, preview_ref):
        with self._lock:
            if type(change_set) is not CandidateChangeSet or type(preview_ref) is not Ref:
                raise Rejected('Only deterministic change sets may be previewed')
            self._preconditions(actor, change_set)
            candidate, changes, pending = self._stage(change_set)
            value = MutationPreview(preview_ref, change_set, self._snapshot.world,
                candidate.world, changes, change_set.retained_refs, (*change_set.issues, *pending),
                change_set.mode is ChangeMode.REPLACE_STATE or any(change.leaves_current_scope or
                    (type(change.after) is BalanceEffect and change.after.direction != 0) for change in changes),
                ('audit_history', 'prior_worlds_states_plans', 'goals_and_if_lines', 'unmentioned_world_members'))
            existing = self._previews.get(preview_ref)
            if existing is not None and existing.digest != value.digest:
                raise Rejected('Preview identity cannot be overwritten')
            for previous in self._previews.values():
                if previous.change_set.ref == change_set.ref and payload_digest(previous.change_set) != payload_digest(change_set):
                    raise Rejected('Change-set identity cannot be overwritten')
            if preview_ref in self._cancelled:
                raise Rejected('Cancelled preview cannot be reopened')
            self._previews[preview_ref] = value
            return value

    def authorize(self, actor, preview, authorization_ref, granted_at):
        with self._lock:
            if (type(preview) is not MutationPreview or preview.ref not in self._previews
                or self._previews[preview.ref].digest != preview.digest):
                raise Rejected('Must authorize the exact registered preview')
            if preview.ref in self._cancelled:
                raise Rejected('Preview is cancelled')
            self._preconditions(actor, preview.change_set)
            aware(granted_at, 'granted_at')
            if granted_at < preview.change_set.created_at:
                raise Rejected('Authorization predates preview')
            change = preview.change_set
            value = WorldAuthorization(authorization_ref, actor, change.owner_id, change.world_id,
                preview.ref, preview.digest, change.ref, payload_digest(change), change.expected_world_ref,
                change.session_generation, change.mode, change.replacement_scope, granted_at)
            all_grants = (*self._authorizations.values(), *self._snapshot.authorizations)
            if any(grant.ref == value.ref and payload_digest(grant) != payload_digest(value) for grant in all_grants):
                raise Rejected('Authorization identity cannot be overwritten')
            if any(grant.ref == value.ref for grant in self._snapshot.authorizations):
                raise Rejected('Authorization is consumed')
            self._authorizations[value.ref] = value
            return value

    def cancel(self, actor, preview_ref):
        with self._lock:
            preview = self._previews.get(preview_ref)
            if preview is None:
                raise Rejected('Unknown preview')
            owner(actor, preview.change_set.owner_id)
            if any(receipt.preview_ref == preview_ref for receipt in self._snapshot.receipts):
                raise Rejected('Committed mutation needs a new reviewed correction')
            self._cancelled.add(preview_ref)

    def execute(self, command):
        with self._lock:
            if type(command) is not FinancialWorldCommand:
                raise Rejected('Only FinancialWorldCommand may execute')
            before = self._snapshot
            change = command.change_set
            owner(command.actor, before.world.owner_id)
            if change.owner_id != before.world.owner_id or change.world_id != before.world.world_id:
                raise Rejected('Wrong command world/owner')
            if change.session_generation != self._generation:
                raise StaleVersion('Session generation changed')
            for previous, receipt in zip(before.commands, before.receipts):
                if previous.command_id == command.command_id:
                    if payload_digest(previous) != payload_digest(command):
                        raise Rejected('Command ID was reused with changed content')
                    return receipt
            self._preconditions(command.actor, change)
            preview = self._previews.get(command.preview_ref)
            grant = self._authorizations.get(command.authorization_ref)
            if preview is None or grant is None or command.preview_ref in self._cancelled:
                raise Rejected('Registered, uncancelled preview and authorization required')
            if any(item.ref == grant.ref for item in before.authorizations):
                raise Rejected('Authorization already consumed')
            if (preview.change_set != change or preview.digest != command.preview_digest
                or grant.preview_ref != preview.ref or grant.preview_digest != preview.digest
                or grant.change_set_ref != change.ref or grant.change_set_digest != payload_digest(change)
                or grant.actor != command.actor or grant.owner_id != change.owner_id
                or grant.world_id != change.world_id or grant.session_generation != self._generation
                or grant.expected_world_ref != change.expected_world_ref
                or grant.purpose is not change.mode or grant.replacement_scope != change.replacement_scope):
                raise Rejected('Authorization does not bind this exact preview/change set')
            if command.recorded_at < grant.granted_at or (before.receipts and command.recorded_at < before.receipts[-1].recorded_at):
                raise Rejected('Execution chronology is invalid')
            candidate, changes, pending = self._stage(change)
            if (candidate.world != preview.after_world or changes != preview.changes
                or (*change.issues, *pending) != preview.pending):
                raise Rejected('Execution differs from reviewed preview')
            receipt = WorldMutationEvent(Ref('world-mutation/' + command.command_id, 1),
                command.command_id, payload_digest(command), command.actor, change.owner_id, change.world_id,
                change.session_generation, change.ref, payload_digest(change), grant.ref, preview.ref, preview.digest,
                before.world.ref, candidate.world.ref, before.world.state_ref, candidate.world.state_ref,
                before.world.plan_ref, candidate.world.plan_ref,
                tuple(record_ref(item.after) for item in changes),
                tuple(record_ref(item.before) for item in changes if item.leaves_current_scope),
                change.observation.evidence_refs, preview.pending, preview.destructive, command.recorded_at)
            candidate = replace(candidate, authorizations=(*before.authorizations, grant),
                commands=(*before.commands, command), receipts=(*before.receipts, receipt), revision=before.revision + 1)
            if candidate.records[:len(before.records)] != before.records:
                raise Rejected('Publication cannot alter history')
            validate_world(candidate, tuple(record_ref(item.after) for item in changes if not item.leaves_current_scope))
            if self._before_publish:
                self._before_publish(candidate)
            self._publish(candidate)
            return receipt

    def _publish(self, candidate):
        """Publication seam; durable subclasses accept on disk before exposing RAM."""
        self._snapshot = candidate

    def _preconditions(self, actor, change):
        before = self._snapshot
        owner(actor, before.world.owner_id)
        if change.owner_id != before.world.owner_id or change.world_id != before.world.world_id:
            raise Rejected('Wrong owner/world')
        if (change.session_generation != self._generation or change.expected_world_ref != before.world.ref
            or change.expected_state_ref != before.world.state_ref or change.expected_plan_ref != before.world.plan_ref
            or change.expected_revision != before.revision):
            raise StaleVersion('World/state/plan/revision/session changed; regenerate preview')
        if before.receipts and change.created_at < before.receipts[-1].recorded_at:
            raise Rejected('Change predates current world')
        if any(issue.disposition is not IssueDisposition.READY for issue in change.issues):
            raise Rejected('Unresolved operation blocks the whole change set')

    def _stage(self, change):
        before = self._snapshot
        if change.future_origin is not None:
            from .future_adoption import validate_origin
            validate_origin(before, change)
        from .world_commands import HistoryAction
        if any(type(op) is HistoryAction for op in change.operations):
            from .history_actions import expand_history
            if len(change.operations) != 1:
                raise Rejected('One exact history action per command')
            operations, scope, retained, coverage = expand_history(before, change.session_generation,
                change.operations[0], change.created_at)
            if (scope != change.replacement_scope or retained != change.retained_refs or
                    coverage != change.coverage_note or change.mode is not
                    (ChangeMode.REPLACE_STATE if scope else ChangeMode.CORRECT)):
                raise Rejected('History action scope differs from deterministic expansion')
            change = replace(change, operations=operations)
        settlement = any(type(op) is SettleOccurrence for op in change.operations)
        effect = None
        operations = change.operations
        if settlement:
            if len(operations) != 1 or change.mode not in (ChangeMode.RESOLVE, ChangeMode.CORRECT):
                raise Rejected('One settlement per exact resolve/correct command')
            if (change.mode is ChangeMode.CORRECT) != (operations[0].corrects_effect_ref is not None):
                raise Rejected('Settlement correction purpose mismatch')
            operations, effect = expand_settlement(before, operations[0])
        live = { _key(ref): before.get(ref) for ref in before.world.members }
        history = {record_ref(item): item for item in before.records}
        touched = set()
        changes = []
        scope = change.replacement_scope
        if scope:
            self._validate_scope(scope)
        for operation in operations:
            expected = operation.expected_ref
            target = record_ref(operation.record) if type(operation) is PutRecord else expected
            key = _key(target)
            if key in touched:
                raise Rejected('A record can change only once per command')
            touched.add(key)
            old = live.get(key)
            if not settlement and type(operation) is PutRecord:
                if type(operation.record) is PlannedExpense:
                    validate_plan_edit(old, operation.record)
                if type(operation.record) is ActualOccurrence and (
                    operation.record.settlement_status is not SettlementStatus.UNALLOCATED
                    or (old is not None and old.settlement_status is not SettlementStatus.UNALLOCATED)
                ):
                    raise Rejected('Only reviewed settlement can write settled occurrences')
            if (scope and old is not None and type(old) is Account
                and expected not in scope.account_refs):
                raise Rejected('Account mutation outside replacement scope')
            if expected is None:
                if old is not None or any(_key(ref) == key for ref in history):
                    raise Rejected('Create requires absence from current and historical identity')
                if operation.record.ref.version != 1 or operation.record.observation.corrects_ref is not None:
                    raise Rejected('Create requires initial version without fabricated correction')
            elif old is None or record_ref(old) != expected:
                raise StaleVersion('Expected entity version is no longer current')
            if type(operation) is RetireRecord:
                if not scope or expected not in (*scope.account_refs, *scope.position_refs, *scope.earmark_refs):
                    raise Rejected('Retirement requires explicit replacement scope')
                lifecycle = EarmarkLifecycle.RELEASED if type(old) is Earmark else RecordLifecycle.RETIRED
                new = replace(old, ref=next_ref(old.ref), lifecycle=lifecycle,
                              observation=self._metadata(change, old, ObservationRole.CURRENT))
                del live[key]
                changes.append(RecordChange(old, new, True))
            else:
                new = operation.record
                if new.owner_id != change.owner_id or new.world_id != change.world_id:
                    raise Rejected('Record owner/world mismatch')
                if new.observation.recorded_at > change.created_at:
                    raise Rejected('Record provenance is after change creation')
                if old is not None:
                    if new.ref != next_ref(old.ref) or new.observation.corrects_ref != old.ref:
                        raise Rejected('Update requires next version and exact correction lineage')
                    if new.observation.recorded_at < old.observation.recorded_at:
                        raise Rejected('Correction chronology is invalid')
                    if not settlement:
                        self._validate_lifecycle(old, new)
                    if scope and type(old) in (AssetPosition, Earmark) and expected not in (*scope.position_refs, *scope.earmark_refs):
                        raise Rejected('Asset update outside replacement scope')
                if type(new) in (Account, AssetPosition) and new.lifecycle is not RecordLifecycle.ACTIVE:
                    raise Rejected('Inactive assets use explicit retirement')
                if type(new) is Earmark and new.lifecycle is not EarmarkLifecycle.ACTIVE:
                    raise Rejected('Released earmarks use explicit retirement')
                live[key] = new
                changes.append(RecordChange(old, new, False))
        if scope:
            if any(_key(ref) not in touched for ref in (*scope.position_refs, *scope.earmark_refs)):
                raise Rejected('Replacement must explicitly account for every scoped observation')
            for item in changes:
                if type(item.after) is AssetPosition and not item.leaves_current_scope:
                    if item.after.currency not in scope.currencies:
                        raise Rejected('New position currency outside explicit replacement scope')
                    allowed_accounts = {_key(ref) for ref in scope.account_refs} | {
                        _key(record_ref(delta.after)) for delta in changes if type(delta.after) is Account and delta.before is None}
                    if _key(item.after.account_ref) not in allowed_accounts:
                        raise Rejected('New position account outside replacement scope')
                if type(item.after) is Earmark and not item.leaves_current_scope:
                    pool = live.get(_key(item.after.position_ref))
                    allowed_pools = {_key(ref) for ref in scope.position_refs} | {
                        _key(record_ref(delta.after)) for delta in changes
                        if type(delta.after) is AssetPosition and delta.before is None}
                    if pool is None or _key(item.after.position_ref) not in allowed_pools:
                        raise Rejected('Earmark outside replacement position scope')
        if effect is not None:
            key = _key(record_ref(effect))
            old_effect = live.get(key)
            if old_effect is not None and effect.previous_effect_ref != record_ref(old_effect):
                raise Rejected('Effect must supersede its exact current version')
            touched.add(key)
            live[key] = effect
            changes.append(RecordChange(old_effect, effect, False))
        retained = tuple(sorted((ref for ref in before.world.members if _key(ref) not in touched), key=ref_key))
        if retained != change.retained_refs:
            raise Rejected('Explicit retained scope does not match unchanged members')
        records = (*before.records, *(item.after for item in changes))
        state = before.get(TypedRef('world_state', before.world.state_ref))
        asset_changed = any(type(item.after) in (Account, AssetPosition, Earmark) for item in changes)
        if asset_changed:
            if change.observation.role is not ObservationRole.CURRENT or not change.coverage_note:
                raise Rejected('Asset mutation requires current snapshot provenance and explicit coverage')
            new_state = replace(state, ref=next_ref(state.ref),
                account_refs=tuple(record_ref(item) for item in live.values() if type(item) is Account),
                position_refs=tuple(record_ref(item) for item in live.values() if type(item) is AssetPosition),
                earmark_refs=tuple(record_ref(item) for item in live.values() if type(item) is Earmark),
                snapshot_observation=replace(change.observation, corrects_ref=state.ref, correction_reason='Reviewed state observation update'),
                coverage_note=change.coverage_note)
            changes.append(RecordChange(state, new_state, False))
            records += (new_state,)
            state = new_state
        elif change.coverage_note is not None:
            raise Rejected('Coverage cannot change without a state observation update')
        plan = before.get(TypedRef('world_plan', before.world.plan_ref))
        new_plan = replace(plan, ref=next_ref(plan.ref), source_state_ref=record_ref(state),
            previous_plan_ref=record_ref(plan), evaluation_status=PlanEvaluationStatus.PENDING,
            supporting_decision_refs=tuple(record_ref(live[_key(ref)]) for ref in plan.supporting_decision_refs),
            planned_expense_refs=tuple(record_ref(item) for item in live.values()
                if type(item) is PlannedExpense and is_open(item)),
            observation=self._metadata(change, plan, ObservationRole.PLANNED))
        changes.append(RecordChange(plan, new_plan, False))
        records += (new_plan,)
        root = replace(before.world, ref=next_ref(before.world.ref), state_ref=state.ref,
                       plan_ref=new_plan.ref, members=tuple(record_ref(item) for item in live.values()))
        candidate = replace(before, records=records, worlds=(*before.worlds, root))
        try:
            pending = validate_world(candidate, tuple(record_ref(item.after) for item in changes if not item.leaves_current_scope))
        except (ValueError, KeyError, AttributeError) as error:
            raise Rejected(str(error)) from error
        pending += (_pending('evaluation_pending', 'Relevant financial evaluations require review; no advice emitted', (record_ref(new_plan),)),)
        if effect is not None and effect.earmark_after_ref is not None:
            pending += (_pending('residual_earmark_release_pending',
                'Remaining allocation is preserved; any release requires a separate exact reviewed allocation change',
                (effect.earmark_after_ref,)),)
        return candidate, tuple(changes), pending

    def _validate_scope(self, scope):
        assets = current_assets(self._snapshot)
        if any(ref not in assets.state.account_refs for ref in scope.account_refs):
            raise Rejected('Replacement account scope is not current')
        positions = tuple(item for item in assets.positions if item.account_ref in scope.account_refs and item.currency in scope.currencies)
        refs = {record_ref(item) for item in positions}
        earmarks = {record_ref(item) for item in assets.earmarks if item.position_ref in refs}
        if set(scope.position_refs) != refs or set(scope.earmark_refs) != earmarks:
            raise Rejected('Replacement scope omits or invents current observations')

    @staticmethod
    def _metadata(change, old, role):
        return replace(change.observation, role=role, corrects_ref=old.ref,
                       correction_reason='Exact reviewed world mutation')

    @staticmethod
    def _validate_lifecycle(old, new):
        if type(old) is FutureInflow and old.status is FutureInflowStatus.REALIZED and new.status is not FutureInflowStatus.REALIZED:
            raise Rejected('Cannot cancel or reopen a realized receipt')
        if type(old) is PlannedExpense and old.status is PlannedExpenseStatus.COMPLETED and new.status is not PlannedExpenseStatus.COMPLETED:
            raise Rejected('Cannot erase a completed occurrence')
        if type(old) is FutureInflow and old.status is FutureInflowStatus.REALIZED and old.realization_ref != new.realization_ref:
            raise Rejected('Receipt correction belongs to later occurrence correction support')
        if type(old) is PlannedExpense and old.status is PlannedExpenseStatus.COMPLETED and old.occurrence_ref != new.occurrence_ref:
            raise Rejected('Purchase correction belongs to later occurrence correction support')
        if type(old) in (AssetPosition, Earmark):
            link = 'account_ref' if type(old) is AssetPosition else 'position_ref'
            previous, updated = getattr(old, link), getattr(new, link)
            if _key(previous) != _key(updated):
                raise Rejected('Changing an owned pool association requires a separately reviewed new identity')
        if type(old) is AssetPosition and (old.currency != new.currency or old.partition_key != new.partition_key):
            raise Rejected('Position identity cannot silently switch currency/partition')

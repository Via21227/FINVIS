"""FS1 same-store exploration persistence and minimal Python application port.

No HTTP/default-UI surface. The financial store supplies its lock, transaction,
verified snapshot and stable identity; Future saves do not execute World commands.
"""
from dataclasses import replace
from datetime import datetime, timezone
from hashlib import sha256
import sqlite3

from .future_space import (FutureScope, FutureCatalog, FutureOperation, FutureNode,
    FutureVariable, FutureOption, ReferenceFrame, SavedScenario, FutureEvaluation,
    FutureError, FutureRef, FutureMeta, WorldDependency, ContextBinding, FutureEvidence,
    COMPILER, RECORDS, require, dumps, loads, fingerprint)
from .world_values import TypedRef
from .persistence_evidence import canonical, strict_json
from .decision_baseline import digest as world_digest, linked

EMPTY_DIGEST = sha256(b'finvis.future-memory.v1:empty').hexdigest()


def scope_for(store):
    return FutureScope(store.context.context_id, store.context.owner_id, store.context.world_id, *store.storage_identity)


def financial_bindings(record, snapshot):
    """Check retained original evidence, not just today's display values."""
    for dep in record.meta.dependencies:
        try: original = snapshot.get(dep.ref)
        except Exception: raise FutureError('world_evidence_missing') from None
        require(world_digest(original) == dep.digest, 'world_evidence_digest_mismatch')
    for binding in record.meta.contexts:
        snapshot.get(binding.decision_ref)
        for ref in binding.context_refs:
            row = snapshot.get(ref)
            require(any(r.ref.id == binding.decision_ref.ref.id for r in row.decision_refs), 'context_binding_mismatch')


def validate_operation(catalog, operation, financial_snapshot):
    require(type(operation) is FutureOperation and operation.scope == catalog.scope, 'future_operation_scope')
    require(operation.financial_revision == financial_snapshot.revision, 'financial_basis_changed')
    for row in operation.records:
        financial_bindings(row, financial_snapshot)
        old = catalog.latest.get((row.meta.ref.kind, row.meta.ref.id))
        if operation.action == 'archive':
            require(old is not None and not old.meta.archived and row.meta.archived, 'archive_requires_live_version')
            expected = replace(old, meta=replace(old.meta, ref=row.meta.ref, previous=old.meta.ref,
                recorded_at=row.meta.recorded_at, archived=True))
            require(row == expected, 'archive_cannot_change_meaning')
        else:
            require(not row.meta.archived, 'archive_action_required')
            if old: require(not old.meta.archived, 'archived_identity_requires_explicit_future_contract')
        if operation.action == 'keep': require(old is None, 'keep_is_new_retention')
        if operation.action == 'revise': require(old is not None, 'revise_requires_previous')
        if old and type(row) is FutureEvaluation:
            require((row.selection,row.reference,row.compiler,row.result)!=(old.selection,old.reference,old.compiler,old.result),
                    'future_evaluation_unchanged')
    updated = FutureCatalog(catalog.scope, (*catalog.records, *operation.records))
    if operation.action != 'archive':
        for row in operation.records:
            if type(row) in (ReferenceFrame, SavedScenario, FutureEvaluation):
                # Verify the exact structural closure at the save point. Recovery
                # replays this check against that historical Future prefix.
                expected = updated.select(row.selection.roots, row.selection.options,
                    row.selection.scope_complete, row.selection.unresolved)
                require(expected == row.selection, 'selection_structure_changed')
                for ref in (*row.selection.structure, *row.selection.options):
                    require(not updated.get(ref).meta.archived, 'archived_input')
            if type(row) is SavedScenario:
                require(operation.action in ('save_scenario', 'import_legacy', 'revise'), 'explicit_scenario_save_required')
            if type(row) is FutureEvaluation:
                require(operation.action in ('evaluate_keep', 'import_legacy'), 'explicit_evaluation_retention_required')
    return updated


def acknowledgement(operation, revision, previous):
    payload = dumps(operation)
    digest = sha256(payload.encode()).hexdigest()
    chain = sha256((previous+digest).encode()).hexdigest()
    return dict(schema_version='finvis.future-ack.v1', operation_id=operation.operation_id,
                revision=revision, operation_digest=digest, previous_digest=previous, head_digest=chain,
                financial_authority=False, record_refs=[dict(kind=r.meta.ref.kind,id=r.meta.ref.id,
                    version=r.meta.ref.version) for r in operation.records])


def verify_memory(store, snapshot):
    """Replay every retained Future operation; no wall-clock validity at recovery."""
    from .world_history_replay import snapshot_prefix
    scope = scope_for(store)
    catalog = FutureCatalog(scope)
    rows = store._db.execute('SELECT revision,operation_id,payload,acknowledgement FROM future_operations ORDER BY revision').fetchall()
    require(len(rows) <= 8192, 'future_operation_capacity')
    require([r[0] for r in rows] == list(range(1,len(rows)+1)), 'future_operation_gap')
    previous = EMPTY_DIGEST; expected_versions = []
    operations = {}
    for revision, identity, payload, ack in rows:
        operation = loads(payload)
        require(type(operation) is FutureOperation and operation.operation_id == identity and
                operation.expected_revision == revision-1, 'future_operation_index_mismatch')
        require(operation.financial_revision <= snapshot.revision, 'future_financial_basis_missing')
        catalog = validate_operation(catalog, operation, snapshot_prefix(snapshot,operation.financial_revision))
        expected = acknowledgement(operation,revision,previous)
        require(ack == canonical(expected), 'future_ack_mismatch')
        previous = expected['head_digest']; operations[identity]=(payload,expected)
        for row in operation.records:
            ref=row.meta.ref
            expected_versions.append((ref.kind,ref.id,ref.version,revision,dumps(row),fingerprint(row)))
    actual = store._db.execute('SELECT kind,identity,version,introduced_revision,payload,digest FROM future_versions ORDER BY introduced_revision,rowid').fetchall()
    require(actual == expected_versions, 'future_version_index_mismatch')
    require(store._db.execute('SELECT singleton,revision,digest FROM future_head').fetchall() == [(1,len(rows),previous)], 'future_head_mismatch')
    return catalog, operations, previous


def retain(store, operation):
    from .persistent_world import StorageUnavailable, StorageBusy
    with store._lock:
        store._ready()
        require(type(operation) is FutureOperation, 'typed_future_operation_required')
        # Reverify both namespaces and current published authority before lookup.
        snapshot = store._verified_current()
        catalog, operations, head = verify_memory(store, snapshot)
        encoded = dumps(operation)
        require(loads(encoded)==operation, 'future_roundtrip_required')
        prior = operations.get(operation.operation_id)
        if prior is not None:
            require(prior[0] == encoded, 'future_idempotency_conflict')
            return dict(prior[1])
        require(operation.expected_revision == len(operations), 'future_head_changed')
        validate_operation(catalog, operation, snapshot)
        require(len(operations) < 8192, 'future_operation_capacity')
        revision = len(operations)+1
        ack = acknowledgement(operation,revision,head)
        try:
            store._checkpoint('future_before_transaction')
            store._db.execute('BEGIN IMMEDIATE')
            store._check_head(snapshot); store._effective_configuration()
            require(store._db.execute('SELECT revision,digest FROM future_head WHERE singleton=1').fetchone() == (operation.expected_revision,head), 'future_head_changed')
            store._db.execute('INSERT INTO future_operations VALUES (?,?,?,?)',
                              (revision,operation.operation_id,encoded,canonical(ack)))
            for row in operation.records:
                ref=row.meta.ref
                store._db.execute('INSERT INTO future_versions VALUES (?,?,?,?,?,?)',
                    (ref.kind,ref.id,ref.version,revision,dumps(row),fingerprint(row)))
                store._checkpoint('future_record_written')
            require(store._db.execute('UPDATE future_head SET revision=?,digest=? WHERE singleton=1 AND revision=? AND digest=?',
                (revision,ack['head_digest'],operation.expected_revision,head)).rowcount == 1, 'future_head_changed')
            require(not store._db.execute('PRAGMA foreign_key_check').fetchall(), 'future_foreign_key_failure')
            store._checkpoint('future_before_commit')
            store._db.execute('COMMIT')
            store._checkpoint('future_after_commit')
            # No competing writable cache: all reads rebuild from verified SQLite.
            return ack
        except BaseException as error:
            if store._db.in_transaction: store._db.execute('ROLLBACK')
            if not isinstance(error, Exception): raise
            try:
                recovered=store._verified_current()
                _, recovered_ops, _=verify_memory(store,recovered)
                found=recovered_ops.get(operation.operation_id)
                if found:
                    require(found[0]==encoded, 'future_idempotency_conflict')
                    return dict(found[1])
            except Exception:
                store._unavailable=True
                raise StorageUnavailable('Future save outcome requires verified reopen') from None
            if isinstance(error,sqlite3.OperationalError): raise StorageBusy('Future durable write unavailable; no commit found') from None
            if isinstance(error,sqlite3.Error): raise StorageUnavailable('Future durable transaction rejected') from None
            raise


def related_refs(catalog, row):
    """Only consumed branches; unselected alternatives are deliberately absent."""
    refs = set(); pending=[row.meta.ref]
    while pending:
        ref=pending.pop()
        if ref in refs: continue
        refs.add(ref); item=catalog.get(ref)
        if type(item) is FutureOption: pending.extend((item.variable,*item.requires))
        elif type(item) is FutureVariable: pending.append(item.node); pending.extend(item.active_when)
        elif type(item) is FutureNode:
            if item.parent: pending.append(item.parent)
            pending.extend(item.active_when)
        elif type(item) in (SavedScenario,ReferenceFrame,FutureEvaluation):
            pending.extend((*catalog.consumed_structure(item.selection),*item.selection.options))
            if getattr(item,'reference',None):pending.append(item.reference)
    return refs


def validity(catalog, row, snapshot, at=None):
    at=at or datetime.now(timezone.utc)
    reasons=[]; affected=[]; missing=[]
    current_refs=(*snapshot.world.members, TypedRef("world_state",snapshot.world.state_ref), TypedRef("world_plan",snapshot.world.plan_ref))
    for ref in sorted(related_refs(catalog,row),key=lambda r:(r.kind,r.id,r.version)):
        item=catalog.get(ref); local=[]
        latest=catalog.latest[(ref.kind,ref.id)]
        if latest.meta.ref != ref: local.append('future_version_changed')
        if latest.meta.archived: local.append('future_archived')
        for dep in item.meta.dependencies:
            if dep.ref not in current_refs or world_digest(snapshot.get(dep.ref))!=dep.digest:
                local.append('world_input_changed')
            else:
                obs=getattr(snapshot.get(dep.ref),'observation',None)
                if obs and obs.freshness.status.value=='stale':local.append('world_evidence_stale')
                if obs and obs.freshness.status.value=='unknown':missing.append('world_evidence_unknown')
        for context in item.meta.contexts:
            if linked(snapshot,context.decision_ref)!=context.context_refs:local.append('decision_context_changed')
        for e in item.meta.evidence:
            if e.freshness=='unknown':missing.append('evidence_freshness_unknown')
            if e.freshness=='stale' or e.expires_at is not None and at>=e.expires_at:local.append('evidence_stale')
        if type(item) in (SavedScenario,ReferenceFrame,FutureEvaluation):
            consumed=catalog.consumed_structure(item.selection)
            for binding in item.selection.memberships:
                if binding.node in consumed and catalog.children(binding.node)!=binding.members:local.append('scope_membership_changed')
        if local:
            reasons.extend(local);affected.append(dict(kind=ref.kind,id=ref.id,version=ref.version,reasons=sorted(set(local))))
    if type(row) in (SavedScenario,ReferenceFrame,FutureEvaluation):
        missing.extend(catalog.check_selection(row.selection)[0])
        if not row.selection.scope_complete:missing.append('scope_incomplete')
        if catalog.compile(row.selection,getattr(row,'reference',None))['status']=='needs_information':missing.append('amount_or_context_missing')
    status='stale' if reasons else 'needs_information' if missing else 'current'
    return dict(status=status,reasons=sorted(set(reasons)),affected=affected,missing=sorted(set(missing)),
                financial_authority=False,retention='durable_future_memory')


class FutureMemoryService:
    """Minimal application port; all mutation is an explicit FutureOperation.

    No implicit store creation, save-on-click, adoption, financial command or model.
    """
    def __init__(self,store):
        from .persistent_world import PersistentWorldStore
        require(isinstance(store,PersistentWorldStore), 'durable_user_store_required')
        self.store=store

    @property
    def scope(self): return scope_for(self.store)

    def read(self):
        with self.store._lock:
            self.store._ready(); snapshot=self.store._verified_current()
            catalog,operations,_=verify_memory(self.store,snapshot)
            return catalog,len(operations),snapshot

    def prepare(self,identity,records,action,at=None):
        with self.store._lock:
            catalog,revision,snapshot=self.read()
            op=FutureOperation(identity,self.scope,revision,snapshot.revision,action,tuple(records),at or datetime.now(timezone.utc))
            require(loads(dumps(op))==op, 'future_roundtrip_required')
            validate_operation(catalog,op,snapshot)
            return op

    def retain(self,operation): return retain(self.store,operation)

    def lookup(self,identity):
        with self.store._lock:
            catalog,_,snapshot=self.read()
            _,operations,_=verify_memory(self.store,snapshot)
            found=operations.get(identity)
            return dict(found[1]) if found else None

    def view(self,ref,at=None):
        catalog,revision,snapshot=self.read();row=catalog.get(ref)
        return dict(record=row,future_revision=revision,**validity(catalog,row,snapshot,at))

    def inspect(self,selection,reference=None):
        catalog,_,snapshot=self.read()
        # Pure derived preview; callers inspect validity separately. Stale inputs
        # never produce a current numerical comparison.
        selected=[catalog.get(r) for r in (*catalog.consumed_structure(selection),*selection.options)]
        if reference: selected.append(catalog.get(reference))
        statuses=[validity(catalog,r,snapshot) for r in selected]
        if any(s['status']=='stale' for s in statuses):
            return dict(status='stale',effects=None,financial_authority=False)
        consumed=catalog.consumed_structure(selection)
        for binding in selection.memberships:
            if binding.node in consumed and catalog.children(binding.node)!=binding.members:
                return dict(status='stale',effects=None,financial_authority=False)
        result=catalog.compile(selection,reference)
        if any(s['status']=='needs_information' for s in statuses):
            result['status']='needs_information'
            result['missing']=sorted(set(result['missing']+[reason for s in statuses for reason in s['missing']]))
        return result

    def evaluation(self,meta,selection,reference=None):
        catalog,_,snapshot=self.read()
        inspected=self.inspect(selection,reference)
        require(inspected['status']!='stale','stale_evaluation')
        return FutureEvaluation(meta,selection,reference,COMPILER,canonical(catalog.compile(selection,reference)))

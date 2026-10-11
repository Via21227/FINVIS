"""I4 receipt-first application of an exact I3 User World authorization.

The sole financial writer remains WorldStore.execute.  Operation identities and
timestamps bind exact retries. P2 resolves committed operations from P1 storage;
no projection or workflow registry becomes a second financial authority.
"""

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Dict, Optional

from .i3_workflow import I3Error, I3Workflow
from .reviewed_proposals import ProposalStatus, _wire
from .store import Rejected, StaleVersion
from .world_commands import FinancialWorldCommand, WorldMutationEvent
from .world_projection import projection_data
from .world_serialization import payload_digest
from .persistent_world import PersistentWorldStore, StorageUnavailable
from .persistence_evidence import CommitEvidence


@dataclass(frozen=True)
class _Operation:
    command: FinancialWorldCommand
    candidate_id: str
    proposal_id: str
    review_digest: str
    preview_id: str
    preview_digest: str
    authorization_id: str
    evidence: Optional[CommitEvidence] = None


class I4Workflow(I3Workflow):
    """A narrow executable continuation of I3, never an interpretation path."""

    def __init__(self, authority, understanding, world_store):
        super().__init__(authority, understanding, world_store)
        self._operations: Dict[str, _Operation] = {}
        self._latest_authorization_id: Optional[str] = None
        from .history_workflow import HistoryWorkflow
        self.history_actions = HistoryWorkflow(self)
        from .decision_baseline import BaselineService
        self.baselines = BaselineService(authority)
        from .if_lines import IfLineService
        self.if_lines = IfLineService(self.baselines)
        from .reevaluation import EvaluationRefresh
        self.evaluations = EvaluationRefresh(self.baselines, self.if_lines)
        from .reality_routing import RealityRouting
        self.reality_routing = RealityRouting(self)
        from .simple_planning import SimplePlanning
        self.simple_planning = SimplePlanning(self)
        from .plan_reality import PlanReality
        self.plan_reality = PlanReality(self)
        from .compound_workflow import CompoundWorkflow
        self.compound = CompoundWorkflow(self)

    def stop_interpretation(self, request_id):
        with self._lock:
            result = super().stop_interpretation(request_id)
            entry = self._interpret_requests.get(request_id)
            if result == 'too_late' and entry and entry['status'] == 'ready' and entry['candidate_id'] is None:
                entry['status'] = 'cancelled'
                if self.compound.active == request_id:
                    self.compound.invalidate()
                return 'cancelled'
            return result

    def interpret(self, text, request_id=None, provider_call=None, *, route=False):
        from .compound_binding import needs_compound
        if route and needs_compound(text):
            return self.compound.interpret(text, request_id, provider_call)
        if self.compound.active:
            self.compound.invalidate(preserve_future=True)
        result = super().interpret(text, request_id, provider_call,
                                   self.reality_routing.route if route else None)
        with self._lock:
            self._latest_authorization_id = None
        return result

    def cancel(self):
        with self._lock:
            if (self._latest_authorization_id and
                    self._receipt(self._operations[self._latest_authorization_id])):
                raise I3Error('committed_requires_new_review')
            return super().cancel()

    @property
    def durable(self):
        return isinstance(self.store, PersistentWorldStore)

    def _load_operation(self, authorization_id):
        operation = self._operations.get(authorization_id)
        if operation is None and self.durable:
            try:
                saved = self.store.operation(authorization_id)
            except StorageUnavailable:
                raise I3Error('execution_outcome_unknown', 503) from None
            if saved is not None:
                command, evidence = saved['command'], saved['evidence']
                operation = _Operation(command, evidence['candidate_id'],
                    'future_adoption' if command.change_set.future_origin is not None else 'history' if evidence['origin'] == 'history' else 'lifecycle' if evidence['origin']=='domain' and command.authorization_ref.id.startswith('lifecycle/authorization/') else evidence['proposal_id'],
                    payload_digest(command.change_set) if evidence['origin'] in ('history','domain') else evidence['review_digest'],
                    command.preview_ref.id, command.preview_digest, command.authorization_ref.id)
                self._operations[authorization_id] = operation
        return operation

    def _receipt(self, operation):
        if self.durable:
            try:
                self.store.lookup(operation.command.command_id, payload_digest(operation.command))
            except StorageUnavailable:
                raise I3Error('execution_outcome_unknown', 503) from None
        snapshot = self.store.snapshot
        for revision, receipt in enumerate(snapshot.receipts, 1):
            if receipt.command_id == operation.command.command_id:
                if (receipt.command_digest != payload_digest(operation.command)
                        or receipt.authorization_ref != operation.command.authorization_ref
                        or receipt.preview_ref != operation.command.preview_ref
                        or receipt.preview_digest != operation.command.preview_digest):
                    raise I3Error('receipt_binding_failed')
                return receipt, revision
        return None

    def _status(self, operation, recovered=False):
        completed = self._receipt(operation)
        if completed is None:
            return {'status': 'authorized_not_committed',
                    'authorization_id': operation.authorization_id,
                    'command_id': operation.command.command_id}
        receipt, revision = completed
        snapshot = self.store.snapshot
        if (snapshot.revision < revision or
                snapshot.world.world_id != receipt.world_id or
                snapshot.world.owner_id != receipt.owner_id or
                snapshot.authorizations[revision - 1].ref != receipt.authorization_ref):
            raise I3Error('receipt_binding_failed')
        return {'status': 'receipt_recovered' if recovered else 'applied',
                'authorization_id': operation.authorization_id,
                'command_id': operation.command.command_id,
                'candidate_id': operation.candidate_id,
                'proposal_id': operation.proposal_id,
                'review_digest': operation.review_digest,
                'preview_id': operation.preview_id,
                'preview_digest': operation.preview_digest,
                'receipt': _wire(receipt), 'receipt_revision': revision,
                'current_revision': snapshot.revision,
                'changed_records': len(receipt.inserted_refs),
                'retired_records': len(receipt.retired_refs)}

    def operation_status(self, authorization_id):
        with self._lock:
            operation = self._load_operation(authorization_id)
            if operation is not None:
                return self._status(operation)
            if self._grant is not None and self._grant.ref.id == authorization_id:
                return {'status': 'authorized_not_started',
                        'authorization_id': authorization_id}
            pending = self.history_actions.pending
            if pending and pending['grant'] is not None and pending['grant'].ref.id == authorization_id:
                return {'status': 'authorized_not_started', 'authorization_id': authorization_id}
            raise I3Error('operation_unknown', 404)

    def _check_exact_authorization(self, proposal_id, preview_id, digest, authorization_id):
        if (self._proposal is None or self._preview is None or self._grant is None
                or self._proposal.proposal_id != proposal_id
                or self._preview.ref.id != preview_id
                or self._preview.digest != digest
                or self._grant.ref.id != authorization_id):
            raise I3Error('authorization_stale')
        try:
            read = self.authority.read(self._proposal.session.world_version)
        except StaleVersion:
            raise I3Error('world_changed') from None
        if read.snapshot is not self.store.snapshot:
            raise I3Error('world_changed')
        if self.proposals.status(self.context, proposal_id).status is not ProposalStatus.READY_FOR_PROPOSAL:
            raise I3Error('proposal_stale')
        candidate = self.understanding.candidate
        if (candidate is None or candidate.status != 'awaiting_confirmation'
                or candidate.id != self._proposal.session.candidate_id
                or candidate.generation != self._proposal.session.candidate_generation
                or candidate.revision != self._proposal.session.candidate_revision):
            raise I3Error('candidate_stale')
        if (self._grant.preview_ref != self._preview.ref
                or self._grant.preview_digest != self._preview.digest
                or self._grant.change_set_ref != self._proposal.reviewed.change_set.ref
                or self._grant.change_set_digest != payload_digest(self._proposal.reviewed.change_set)
                or self._grant.expected_world_ref != read.snapshot.world.ref
                or self._grant.actor != self.context.owner_id
                or self._grant.session_generation != read.version.session_generation
                or self._grant.replacement_scope != self._proposal.reviewed.change_set.replacement_scope):
            raise I3Error('authorization_stale')
        return candidate

    def apply(self, proposal_id, preview_id, digest, authorization_id):
        """Execute once, or recover the immutable receipt of an exact retry."""
        with self._lock:
            operation = self._load_operation(authorization_id)
            if operation is not None:
                if (operation.proposal_id != proposal_id or operation.preview_id != preview_id
                        or operation.preview_digest != digest):
                    raise I3Error('authorization_stale')
                if self._receipt(operation):
                    self._latest_authorization_id = authorization_id
                    self._mark_committed(operation)
                    return self._status(operation, recovered=True)
            candidate = self._check_exact_authorization(
                proposal_id, preview_id, digest, authorization_id)
            if operation is None:
                change_set = self._proposal.reviewed.change_set
                command = FinancialWorldCommand(
                    'i4/' + authorization_id, self.context.owner_id, change_set,
                    self._preview.ref, self._preview.digest, self._grant.ref,
                    max(datetime.now(timezone.utc), self._grant.granted_at))
                operation = _Operation(command, candidate.id, proposal_id,
                    self._proposal.review_digest, preview_id, digest, authorization_id,
                    CommitEvidence(self._proposal.reviewed, proposal_id) if self.durable else None)
                self._operations[authorization_id] = operation
            self._latest_authorization_id = authorization_id
            self._execute_command(operation)
            self._latest_authorization_id = authorization_id
            self._mark_committed(operation)
            return self._status(operation)

    def _execute_command(self, operation):
        """Common atomic execution and receipt-first recovery for both entry points."""
        before = self.store.snapshot
        try:
            receipt = (self.store.execute(operation.command, operation.evidence) if self.durable
                       else self.store.execute(operation.command))
            if type(receipt) is not WorldMutationEvent:
                raise I3Error('execution_outcome_unknown', 503)
        except StorageUnavailable:
            # P1 alone proves whether a failure around COMMIT wrote anything.
            if not self._receipt(operation):
                raise I3Error('execution_failed_before_commit', 503) from None
        except StaleVersion:
            if not self._receipt(operation):
                raise I3Error('world_changed') from None
        except Rejected:
            if not self._receipt(operation):
                raise I3Error('execution_rejected') from None
        except Exception:
            if not self._receipt(operation):
                if self.durable or self.store.snapshot is before:
                    raise I3Error('execution_failed_before_commit', 503) from None
                raise I3Error('execution_outcome_unknown', 503) from None

    def _mark_committed(self, operation):
        candidate = self.understanding.candidate
        if candidate is not None and candidate.id == operation.candidate_id:
            with self.understanding.lock:
                if self.understanding.candidate is candidate and candidate.status == 'awaiting_confirmation':
                    self.understanding.candidate = replace(candidate, status='committed')

    def home(self):
        """Read a fresh I1 projection, including after a committed operation."""
        read = self.authority.read()
        if not read.initialized:
            return {'initialized': False, 'version': None, 'projection': None}
        activity = [
            {'revision': revision, 'recorded_at': receipt.recorded_at.isoformat(),
             'inserted_count': len(receipt.inserted_refs),
             'retired_count': len(receipt.retired_refs),
             'destructive': receipt.destructive}
            for revision, receipt in enumerate(read.snapshot.receipts, 1)
        ]
        return {'initialized': True, 'version': _wire(read.version),
                'projection': projection_data(read.projection), 'activity': activity,
                'lifecycle': self.plan_reality.summary(read),
                'retention': 'durable_local' if self.durable else 'process_local'}

    def history(self, revision=None):
        """Verified, read-only history from one fresh User authority publication."""
        from .world_history import history_view
        with self._lock:
            read = self.authority.read()
            result = history_view(read, revision, replay_package=self.store.export_replay() if self.durable else None)
            if result['detail']:
                if self.durable and revision:
                    receipt = read.snapshot.receipts[revision - 1]
                    saved = self.store.operation(receipt.authorization_ref.id)
                    evidence = saved['evidence']
                    detail = result['detail']
                    detail['source_detail_available'] = evidence['origin'] == 'reviewed'
                    detail['committed_evidence'] = evidence
                result['detail']['actions'] = self.history_actions.inspect(read, revision)
            return result

    def view(self):
        with self._lock:
            result = super().view()
            if self._latest_authorization_id:
                operation = self._operations[self._latest_authorization_id]
                status = self._status(operation)
                result['operation'] = status
                if status['status'] in ('applied', 'receipt_recovered'):
                    result['stage'] = 'applied'
            return result

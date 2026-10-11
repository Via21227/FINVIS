"""History entry point into D consent and shared I4 receipt recovery."""
from datetime import datetime, timezone
from uuid import uuid4
from .domain import Ref
from .history_actions import historical, key, availability, proposal, FIELDS
from .i3_workflow import I3Error, _preview_view
from .reviewed_proposals import _wire
from .store import Rejected, StaleVersion
from .world_commands import HistoryAction, FinancialWorldCommand, record_ref
from .world_serialization import payload_digest
from .world_values import AmountEstimate, AmountPrecision, money_from_major_units


class HistoryWorkflow:
    def __init__(self, workflow):
        self.w, self.pending = workflow, None

    def intent(self, snapshot, revision, action, target=None, field=None, value=None, reason='Review history action'):
        past = historical(snapshot, self.w.store.session_generation, revision)
        return HistoryAction('history/action', action, revision, past.world.ref,
                             payload_digest(past), target, field, value, reason)

    def inspect(self, read, revision):
        snapshot = read.snapshot
        past = historical(snapshot, read.version.session_generation, revision)
        binding = {'current': _wire(read.version), 'source_revision': revision,
                   'source_root': _wire(past.world.ref), 'source_digest': payload_digest(past)}
        current = {key(ref): snapshot.get(ref) for ref in snapshot.world.members}
        records = []
        def check(intent):
            change = proposal(snapshot, read.version.session_generation, intent,
                              datetime.now(timezone.utc), 'history/check')
            self.w.store._stage(change)  # Pure staging, no registration or publication.
        for ref in past.world.members:
            record = current.get(key(ref))
            if record is None:
                records.append({'source_ref': _wire(ref), 'ref': None, 'fields': [],
                                'remove': False, 'reason': 'retired_identity_requires_reactivation'})
                continue
            editable, removable, reason = availability(snapshot, record)
            editable = [f for f in editable if getattr(record, f) is not None]
            if removable:
                try:
                    check(self.intent(snapshot, revision, 'remove', record_ref(record)))
                except (Rejected, ValueError, KeyError) as error:
                    removable, reason = False, str(error)
            records.append({'source_ref': _wire(ref), 'ref': _wire(record_ref(record)),
                'label': next((getattr(record, f) for f in ('label','purpose','subject','statement') if getattr(record, f, None)), None),
                'fields': [{'name': f, 'value': _wire(getattr(record, f))} for f in editable],
                'remove': removable, 'reason': reason})
        restore = {'available': False, 'reason': 'already_matches'}
        try:
            check(self.intent(snapshot, revision, 'restore'))
            restore = {'available': True, 'reason': ''}
        except (Rejected, ValueError, KeyError) as error:
            restore['reason'] = str(error)
        return {'binding': binding, 'records': records, 'restore': restore}

    def prepare(self, payload):
        with self.w._lock:
            if set(payload) != {'binding', 'action', 'target', 'field', 'value', 'reason'}:
                raise I3Error('history_input_invalid', 400)
            read = self.w.authority.read()
            binding = payload['binding']
            if type(binding) is not dict or set(binding) != {'current', 'source_revision', 'source_root', 'source_digest'}:
                raise I3Error('history_input_invalid', 400)
            if binding['current'] != _wire(read.version):
                raise I3Error('world_changed')
            past = historical(read.snapshot, read.version.session_generation, binding['source_revision'])
            if binding['source_root'] != _wire(past.world.ref) or binding['source_digest'] != payload_digest(past):
                raise I3Error('history_evidence_changed')
            reason = payload['reason']
            if type(reason) is not str or not reason.strip() or len(reason) > 1000:
                raise I3Error('history_reason_required', 400)
            target = None
            if payload['target'] is not None:
                target = next((ref for ref in read.snapshot.world.members if _wire(ref) == payload['target']), None)
                if target is None:
                    raise I3Error('history_target_stale')
            value = payload['value']
            if payload['action'] == 'edit':
                if target is None:
                    raise I3Error('history_target_stale')
                record = read.snapshot.get(target)
                if payload['field'] not in FIELDS.get(type(record), ()):
                    raise I3Error('unsupported_field', 400)
                old = getattr(record, payload['field'])
                if type(old) is AmountEstimate:
                    value = self.amount(value, old.currency)
            intent = self.intent(read.snapshot, binding['source_revision'], payload['action'],
                                 target, payload['field'], value, reason.strip())
            try:
                change = proposal(read.snapshot, read.version.session_generation, intent,
                                  datetime.now(timezone.utc), 'history/change/' + uuid4().hex)
                preview = self.w.store.preview(self.w.context.owner_id, change, Ref('history/preview/' + uuid4().hex, 1))
            except (Rejected, ValueError, KeyError) as error:
                raise I3Error(str(error), 409) from None
            if self.pending:
                try:
                    self.w.store.cancel(self.w.context.owner_id, self.pending['preview'].ref)
                except Rejected:
                    pass
            self.pending = {'version': read.version, 'preview': preview, 'grant': None}
            return self.view()

    @staticmethod
    def amount(value, currency):
        if type(value) is not dict or set(value) != {'precision', 'value', 'lower', 'upper', 'reason'}:
            raise I3Error('history_input_invalid', 400)
        precision = AmountPrecision(value['precision'])
        money = lambda text: money_from_major_units(text, currency)
        if precision is AmountPrecision.EXACT_REPORTED:
            return AmountEstimate.exact_reported(money(value['value']))
        if precision is AmountPrecision.APPROXIMATE:
            return AmountEstimate.approximate(money(value['value']),
                money(value['lower']) if value['lower'] else None, money(value['upper']) if value['upper'] else None)
        if precision is AmountPrecision.RANGE:
            return AmountEstimate.range(money(value['lower']), money(value['upper']))
        return AmountEstimate.unknown(value['reason'], currency)

    def exact(self, preview_id, digest):
        p = self.pending
        if p is None or p['preview'].ref.id != preview_id or p['preview'].digest != digest:
            raise I3Error('preview_stale')
        try:
            self.w.authority.read(p['version'])
        except StaleVersion:
            raise I3Error('world_changed') from None
        return p

    def authorize(self, preview_id, digest):
        with self.w._lock:
            p = self.exact(preview_id, digest)
            if p['grant'] is None:
                p['grant'] = self.w.store.authorize(self.w.context.owner_id, p['preview'],
                    Ref('history/authorization/' + uuid4().hex, 1), datetime.now(timezone.utc))
            return self.view()

    def apply(self, preview_id, digest, authorization_id):
        from .i4_commit import _Operation
        with self.w._lock:
            operation = self.w._load_operation(authorization_id)
            if operation:
                if operation.proposal_id != 'history' or operation.preview_id != preview_id or operation.preview_digest != digest:
                    raise I3Error('authorization_stale')
                if self.w._receipt(operation):
                    return self.w._status(operation, recovered=True)
            p = self.exact(preview_id, digest)
            if p['grant'] is None or p['grant'].ref.id != authorization_id:
                raise I3Error('authorization_stale')
            preview, grant = p['preview'], p['grant']
            if operation is None:
                command = FinancialWorldCommand('i4/' + authorization_id, self.w.context.owner_id,
                    preview.change_set, preview.ref, preview.digest, grant.ref,
                    max(datetime.now(timezone.utc), grant.granted_at))
                operation = _Operation(command, '', 'history', payload_digest(preview.change_set),
                                       preview_id, digest, authorization_id)
                self.w._operations[authorization_id] = operation
            self.w._execute_command(operation)
            return self.w._status(operation)

    def cancel(self):
        with self.w._lock:
            if self.pending:
                self.w.store.cancel(self.w.context.owner_id, self.pending['preview'].ref)
                self.pending = None
            return self.view()

    def view(self):
        if self.pending is None:
            return {'stage': 'idle'}
        p = self.pending
        intent = p['preview'].change_set.operations[0]
        try:
            self.w.authority.read(p['version'])
            stage = 'authorized' if p['grant'] else 'preview'
        except StaleVersion:
            stage = 'stale'
        return {'stage': stage, 'action': intent.action, 'reason': intent.reason,
            'source_revision': intent.source_revision, 'source_root': _wire(intent.source_root),
            'source_digest': intent.source_digest, 'world_revision': p['version'].revision,
            'preview': _preview_view(p['preview'], self.w.store.snapshot),
            'authorization': {'id': p['grant'].ref.id} if p['grant'] else None}

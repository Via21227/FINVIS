"""S2 read-only User World history over verified D/F authority."""

from .store import Rejected
from .world_commands import WorldSnapshot, HistoryAction, record_ref
from .world_projection import ProjectionError, project_world, projection_data
from .world_replay import export_world_replay, replay_world
from .reviewed_proposals import _wire


_SYSTEM_KINDS = {'world_state', 'world_plan'}


def _prefix(package, revision):
    steps = package.steps[:revision]
    return WorldSnapshot(
        (*package.initial_snapshot.records,
         *(record for step in steps for record in step.introduced_records)),
        (package.initial_snapshot.world, *(step.after_world for step in steps)),
        tuple(step.authorization for step in steps),
        tuple(step.command for step in steps),
        tuple(step.receipt for step in steps), revision)


def _amount(record):
    if record is None:
        return None
    for name in ('balance', 'allocation', 'expected_amount', 'amount', 'actual_amount'):
        value = getattr(record, name, None)
        if value is not None and hasattr(value, 'precision'):
            return _wire(value)
    return None


def _subject(record, before, after):
    kind = record_ref(record).record_kind
    for name in ('label', 'purpose', 'subject', 'statement'):
        value = getattr(record, name, None)
        if value:
            return value
    if kind == 'actual_occurrence':
        return record.kind.value
    if kind in ('asset_position', 'balance_effect'):
        position = (record if kind == 'asset_position' else
                    after.get(record.position_after_ref))
        for snapshot in (after, before):
            try:
                account = snapshot.get(position.account_ref)
                return account.label + (' · ' + position.currency.value if kind == 'asset_position' else '')
            except Rejected:
                continue
    return ''


def _changes(before, after, receipt):
    live = {(ref.record_kind, ref.ref.id): ref for ref in before.world.members}
    rows = []
    system = []
    for ref in receipt.inserted_refs:
        record = after.get(ref)
        old_ref = live.get((ref.record_kind, ref.ref.id))
        old = before.get(old_ref) if old_ref is not None else None
        if ref.record_kind in _SYSTEM_KINDS:
            system.append({'kind': ref.record_kind,
                           'before_ref': _wire(old_ref) if old_ref else None,
                           'after_ref': _wire(ref)})
            continue
        observation = getattr(record, 'observation', None)
        retired = old_ref in receipt.retired_refs
        rows.append({'kind': ref.record_kind,
                     'subject': _subject(record, before, after),
                     'before': _amount(old), 'after': _amount(record),
                     'retired': retired,
                     'before_status': getattr(getattr(old, 'status', None), 'value', None),
                     'after_status': getattr(getattr(record, 'status', None), 'value', None),
                     'text_changes': [{'before': getattr(old, f), 'after': getattr(record, f)}
                         for f in ('label','purpose','subject','question','statement')
                         if old is not None and hasattr(record, f) and getattr(old, f) != getattr(record, f)],
                     'corrected': bool(not retired and old and observation and observation.corrects_ref),
                     'correction_reason': observation.correction_reason if observation else None,
                     'observed_at': observation.observed_at.isoformat() if observation and observation.observed_at else None,
                     'as_of': _wire(observation.as_of) if observation else None,
                     'effect_direction': record.direction if ref.record_kind == 'balance_effect' else None,
                     'before_ref': _wire(old_ref) if old_ref else None,
                     'after_ref': _wire(ref)})
        if getattr(record, 'lineage', None) is not None:
            rows[-1]['lineage'] = _wire(record.lineage)
            rows[-1]['event_semantics'] = ('refund' if record.lineage.refund_of_ref else
                                           'replacement' if record.lineage.replacement else 'if_materialization')
        if getattr(record, 'fulfillment', None) is not None:
            rows[-1]['fulfillment'] = _wire(record.fulfillment)
    command=next((c for c in after.commands if c.command_id==receipt.command_id),None)
    if command and command.change_set.future_origin is not None:
        for row in rows:
            row['corrected']=False
            row['intended_choice']=True
    return rows, system


def history_view(read, revision=None, *, replay_package=None):
    """One captured I1 read; no WorldStore method or live authority mutation."""
    if not read.initialized:
        return {'initialized': False, 'current_revision': None, 'entries': [],
                'detail': None, 'retention': 'process_local'}
    snapshot = read.snapshot
    if revision is not None and (type(revision) is not int or revision < 0 or revision > snapshot.revision):
        raise ValueError('Historical revision is unavailable')
    try:
        if replay_package is None:
            package = export_world_replay(snapshot, read.version.session_generation)
            verified = replay_world(package)
        else:
            from .world_history_replay import replay_history
            package = replay_package
            verified = replay_history(package)
        if verified != snapshot:
            raise ProjectionError('history_replay_mismatch')
        entries = []
        for index in range(snapshot.revision, 0, -1):
            receipt = package.steps[index - 1].receipt
            before, after = _prefix(package, index - 1), _prefix(package, index)
            rows, _ = _changes(before, after, receipt)
            entries.append({'revision': index,
                            'recorded_at': receipt.recorded_at.isoformat(),
                            'change_count': len(rows),
                            'destructive': receipt.destructive,
                            'has_correction': any(row['corrected'] for row in rows),
                            'has_occurrence': any(row['kind'] == 'actual_occurrence' for row in rows)})
            if package.steps[index - 1].command.change_set.future_origin is not None:
                entries[-1]['intended_choice']=True
            action = package.steps[index - 1].command.change_set.operations[0]
            if type(action) is HistoryAction:
                entries[-1]['history_action'] = action.action
        if entries:
            entries.append({'revision': 0, 'recorded_at': None, 'change_count': 0,
                            'destructive': False, 'has_correction': False,
                            'has_occurrence': False})
        detail = None
        if revision is not None:
            historical = _prefix(package, revision)
            if replay_package is None:
                verified = replay_world(export_world_replay(
                    historical, read.version.session_generation))
            else:
                from .world_history_replay import export_history_replay
                epochs = tuple(e for e in package.epochs if e.start_revision <= revision)
                verified = replay_history(export_history_replay(historical, epochs, package.migration_evidence))
            if verified != historical:
                raise ProjectionError('history_prefix_mismatch')
            rows, system = ([], [])
            receipt = None
            if revision:
                receipt = package.steps[revision - 1].receipt
                rows, system = _changes(_prefix(package, revision - 1), historical, receipt)
            detail = {'revision': revision,
                      'is_current': revision == snapshot.revision,
                      'recorded_at': receipt.recorded_at.isoformat() if receipt else None,
                      'destructive': receipt.destructive if receipt else False,
                      'rows': rows, 'system_changes': system,
                      'source_detail_available': False,
                      'receipt_ref': _wire(receipt.ref) if receipt else None,
                      'source_refs': _wire(receipt.source_refs) if receipt else [],
                      'root_ref': _wire(historical.world.ref),
                      'projection': projection_data(project_world(historical))}
            if revision:
                origin=package.steps[revision - 1].command.change_set.future_origin
                if origin is not None:
                    from .future_adoption import decode_origin, FutureAdoption
                    p,c,sel,adopted,_=decode_origin(origin)
                    detail['future_origin']=FutureAdoption.describe(c,sel,adopted,p['gradual'])
                action = package.steps[revision - 1].command.change_set.operations[0]
                if type(action) is HistoryAction:
                    detail['history_action'] = _wire(action)
        return {'initialized': True, 'current_revision': snapshot.revision,
                'entries': entries, 'detail': detail, 'retention': 'durable_local' if replay_package is not None else 'process_local'}
    except (Rejected, ValueError, TypeError, KeyError, AttributeError) as error:
        raise ProjectionError('history_unavailable') from error

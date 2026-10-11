"""Bounded history intent compiler. D remains the only writer; F replays intent."""
from dataclasses import fields, is_dataclass, replace

from .domain import Ref
from .store import Rejected, StaleVersion
from .world_commands import (MonthlyEssentialOutflow, HistoryAction, PutRecord, RetireRecord, ReplacementScope,
    CandidateChangeSet, ChangeMode, record_ref, ref_key, typed_links)
from .world_domain import Account, AssetPosition, Earmark
from .world_planning import (FutureInflow, FutureInflowStatus, PlannedExpense,
    PlannedExpenseStatus, WorldDecision, DecisionContext, ActualOccurrence)
from .world_settlement import BalanceEffect
from .world_values import (AmountEstimate, ObservationRole, TypedRef,
    FreshnessAssessment, FreshnessStatus)
from .world_serialization import payload_digest

FIELDS = {MonthlyEssentialOutflow: ('amount',), Account: ('label',), AssetPosition: ('balance',),
          Earmark: ('allocation', 'purpose'), FutureInflow: ('expected_amount', 'purpose'),
          PlannedExpense: ('expected_amount', 'purpose'),
          WorldDecision: ('amount', 'subject', 'question'), DecisionContext: ('statement',)}
ASSETS = (Account, AssetPosition, Earmark)


def key(ref):
    return ref.record_kind, ref.ref.id


def historical(snapshot, generation, revision):
    from .world_history_replay import snapshot_prefix
    if type(revision) is not int or not 0 <= revision <= snapshot.revision:
        raise Rejected('history_revision_invalid')
    return snapshot_prefix(snapshot, revision)


def protected(record):
    return (type(record) in (ActualOccurrence, BalanceEffect) or
            type(record) is FutureInflow and record.status is FutureInflowStatus.REALIZED or
            type(record) is PlannedExpense and (record.status is PlannedExpenseStatus.COMPLETED or
                record.fulfillment is not None and bool(record.fulfillment.occurrence_refs)))


def settlement_keys(snapshot):
    result = set()
    for ref in snapshot.world.members:
        record = snapshot.get(ref)
        if protected(record):
            result.update(key(link) for link in typed_links(record))
    for ref in snapshot.world.members:
        if ref.record_kind == 'asset_position' and key(ref) in result:
            result.add(key(snapshot.get(ref).account_ref))
    return result


def availability(snapshot, record):
    if protected(record) or key(record_ref(record)) in settlement_keys(snapshot):
        return (), False, 'protected_reality'
    editable = FIELDS.get(type(record), ())
    if type(record) is FutureInflow and record.status is not FutureInflowStatus.EXPECTED:
        return (), False, 'closed_record'
    if type(record) is PlannedExpense and record.status is not PlannedExpenseStatus.PLANNED:
        return (), False, 'closed_record'
    removable = type(record) in (*ASSETS, FutureInflow, PlannedExpense)
    return editable, removable, '' if removable else 'unsupported_retirement'


def metadata(template, old, intent, at):
    # Correct historical meaning, never assert that old money is current today.
    return replace(template.observation, recorded_at=at,
        evidence_refs=tuple(dict.fromkeys((*template.observation.evidence_refs, intent.source_root))),
        corrects_ref=old.ref, correction_reason=intent.reason,
        freshness=FreshnessAssessment(FreshnessStatus.UNKNOWN,
            'History correction preserves evidence time; current freshness is not asserted', at))


def _rewrite(value, mapping):
    if type(value) is TypedRef:
        return mapping.get(key(value), value)
    if type(value) is tuple:
        return tuple(_rewrite(item, mapping) for item in value)
    if is_dataclass(value):
        return replace(value, **{f.name: _rewrite(getattr(value, f.name), mapping)
                                  for f in fields(value)})
    return value


def _meaning(record):
    """Compare reported meaning, ignoring only identity-version/provenance."""
    def normalize(value):
        if type(value) is TypedRef:
            return key(value)
        if type(value) is tuple:
            return tuple(normalize(item) for item in value)
        if is_dataclass(value):
            return tuple((f.name, normalize(getattr(value, f.name))) for f in fields(value)
                         if f.name not in ('ref', 'observation'))
        return value
    return normalize(record)


def expand_history(snapshot, generation, intent, at):
    past = historical(snapshot, generation, intent.source_revision)
    if past.world.ref != intent.source_root or payload_digest(past) != intent.source_digest:
        raise Rejected('history_evidence_changed')
    live = {key(ref): snapshot.get(ref) for ref in snapshot.world.members}
    old_live = {key(ref): past.get(ref) for ref in past.world.members}
    changed, retired = {}, set()
    if intent.action == 'restore':
        if intent.source_revision == snapshot.revision:
            raise Rejected('already_matches')
        if intent.source_revision == 0 and any(record_ref(r).record_kind == 'legacy_world_plan' for r in past.records):
            raise Rejected('legacy_restore_unsupported')
        current_plan = snapshot.get(TypedRef('world_plan', snapshot.world.plan_ref))
        past_plan = past.get(TypedRef('world_plan', past.world.plan_ref))
        if (any(getattr(current_plan, f) != getattr(past_plan, f)
                for f in ('status', 'goal_allocations', 'assumption_refs')) or
                {key(r) for r in current_plan.supporting_decision_refs} !=
                {key(r) for r in past_plan.supporting_decision_refs}):
            raise Rejected('plan_intent_restore_unsupported')
        # No rewind through an occurrence, settlement or settled correction.
        for k in set(live) | set(old_live):
            a, b = live.get(k), old_live.get(k)
            if (a is not None and protected(a) or b is not None and protected(b)) and a != b:
                raise Rejected('protected_reality')
        for k, desired in old_live.items():
            if k not in live:
                raise Rejected('retired_identity_requires_reactivation')
            current = live[k]
            if protected(current):
                continue
            if type(current) is WorldDecision and (
                    current.status is not desired.status or current.selected_option_ref != desired.selected_option_ref):
                raise Rejected('decision_intent_restore_unsupported')
            if _meaning(current) != _meaning(desired):
                if type(current) not in FIELDS:
                    raise Rejected('unsupported_restore_record')
                changed[k] = desired
        for k, current in live.items():
            if k not in old_live:
                if type(current) in ASSETS:
                    retired.add(k)
                elif type(current) is FutureInflow:
                    if current.status is not FutureInflowStatus.CANCELLED:
                        changed[k] = replace(current, status=FutureInflowStatus.CANCELLED)
                elif type(current) is PlannedExpense:
                    if current.status is not PlannedExpenseStatus.CANCELLED:
                        changed[k] = replace(current, status=PlannedExpenseStatus.CANCELLED)
                else:
                    raise Rejected('unsupported_retirement')
    else:
        k = key(intent.target_ref)
        if k not in old_live or live.get(k) is None or record_ref(live[k]) != intent.target_ref:
            raise StaleVersion('history_target_stale')
        current = live[k]
        editable, removable, reason = availability(snapshot, current)
        if intent.action == 'edit':
            if intent.field not in editable:
                raise Rejected(reason or 'unsupported_field')
            original = getattr(current, intent.field)
            if intent.field in ('balance', 'allocation', 'expected_amount', 'amount'):
                if type(intent.value) is not AmountEstimate:
                    raise Rejected('invalid_amount')
                currency = original.currency if original is not None else None
                if currency is None or intent.value.currency is not currency:
                    raise Rejected('currency_change_unsupported')
            elif type(intent.value) is not str or not intent.value.strip():
                raise Rejected('invalid_text')
            if original == intent.value:
                raise Rejected('already_matches')
            changed[k] = replace(current, **{intent.field: intent.value})
        else:
            if not removable:
                raise Rejected(reason or 'unsupported_retirement')
            if type(current) in ASSETS:
                retired.add(k)
                if type(current) is Account:
                    retired.update(key(ref) for ref in snapshot.world.members
                        if ref.record_kind == 'asset_position' and key(snapshot.get(ref).account_ref) == k)
                retired.update(key(ref) for ref in snapshot.world.members
                    if ref.record_kind == 'earmark' and key(snapshot.get(ref).position_ref) in retired)
            elif type(current) is FutureInflow:
                changed[k] = replace(current, status=FutureInflowStatus.CANCELLED)
            else:
                changed[k] = replace(current, status=PlannedExpenseStatus.CANCELLED)
    scope = None
    if retired:
        accounts = {record_ref(live[k]) for k in retired if k[0] == 'account'}
        currencies = set()
        for k in retired:
            record = live[k]
            if type(record) is Earmark:
                record = snapshot.get(record.position_ref)
            if type(record) is AssetPosition:
                accounts.add(record.account_ref)
                currencies.add(record.currency)
            elif type(record) is Account:
                currencies.update(snapshot.get(ref).currency for ref in snapshot.world.members
                    if ref.record_kind == 'asset_position' and snapshot.get(ref).account_ref == record_ref(record))
        if not currencies:
            raise Rejected('empty_account_scope')
        positions = tuple(ref for ref in snapshot.world.members if ref.record_kind == 'asset_position'
            and snapshot.get(ref).account_ref in accounts and snapshot.get(ref).currency in currencies)
        marks = tuple(ref for ref in snapshot.world.members if ref.record_kind == 'earmark'
                      and snapshot.get(ref).position_ref in positions)
        scope = ReplacementScope(tuple(accounts), tuple(currencies), positions, marks)
        for ref in (*positions, *marks):
            if key(ref) not in retired:
                changed.setdefault(key(ref), snapshot.get(ref))
    affected = set(changed) | retired
    if affected & settlement_keys(snapshot):
        raise Rejected('protected_reality')
    # No inferred removal cascade outside the explicitly displayed asset closure.
    for k, current in live.items():
        if k not in retired and any(key(link) in retired for link in typed_links(changed.get(k, current))):
            raise Rejected('dependent_records')
    # Advance dependent live references atomically. Historical evidence stays pinned.
    while True:
        added = set()
        for k, current in live.items():
            if k in affected:
                continue
            if any(key(link) in changed for link in typed_links(current)):
                if protected(current):
                    raise Rejected('protected_reality')
                if type(current) not in FIELDS:
                    raise Rejected('dependent_records')
                changed[k] = current
                added.add(k)
        if not added:
            break
        affected |= added
    if not affected:
        raise Rejected('already_matches')
    mapping = {k: record_ref(r) for k, r in live.items() if k not in retired}
    mapping.update({k: TypedRef(k[0], Ref(r.ref.id, r.ref.version + 1))
                    for k, r in live.items() if k in changed})
    asset_changed = any(k[0] in ('account', 'asset_position', 'earmark') for k in affected)
    state_ref = snapshot.world.state_ref
    mapping[('world_state', state_ref.id)] = TypedRef('world_state',
        Ref(state_ref.id, state_ref.version + int(asset_changed)))
    operations = []
    for k in sorted(retired):
        operations.append(RetireRecord('history/retire/' + k[0] + '/' + k[1], record_ref(live[k])))
    for k, template in sorted(changed.items()):
        old = live[k]
        updated = _rewrite(template, mapping)
        updated = replace(updated, ref=mapping[k].ref, observation=metadata(template, old, intent, at))
        operations.append(PutRecord('history/put/' + k[0] + '/' + k[1], updated, record_ref(old)))
    retained = tuple(sorted((ref for ref in snapshot.world.members if key(ref) not in affected), key=ref_key))
    coverage = 'Historical reported subset only; no completeness or current freshness assertion.' if asset_changed else None
    return tuple(operations), scope, retained, coverage


def proposal(snapshot, generation, intent, at, identity):
    operations, scope, retained, coverage = expand_history(snapshot, generation, intent, at)
    state = snapshot.get(TypedRef('world_state', snapshot.world.state_ref))
    observation = replace(state.snapshot_observation, recorded_at=at, role=ObservationRole.CURRENT,
        evidence_refs=(intent.source_root,), corrects_ref=None, correction_reason=None,
        freshness=FreshnessAssessment(FreshnessStatus.UNKNOWN, 'History is not a current observation', at))
    return CandidateChangeSet(Ref(identity, 1), snapshot.world.owner_id, snapshot.world.world_id,
        generation, snapshot.world.ref, snapshot.world.state_ref, snapshot.world.plan_ref,
        snapshot.revision, ChangeMode.REPLACE_STATE if scope else ChangeMode.CORRECT,
        (intent,), scope, retained, (intent.source_root,), observation, coverage, (), at)

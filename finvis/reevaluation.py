"""D3 explicit, session-derived refresh. No command/authorization/write port.

Refresh exact dependencies, never a revision-wide invalidation or replanner.
Stage the entire derived publication; retain immutable prior evaluations.
"""
from copy import copy, deepcopy
from dataclasses import replace
from datetime import datetime, timezone

from .decision_baseline import digest, fields, linked, text, typed_ref
from .domain import Ref
from .if_lines import IfError
from .reviewed_proposals import _wire
from .store import Rejected
from .world_planning import FutureInflowStatus, PlannedExpenseStatus
from .world_values import AmountEstimate, AmountPrecision


def current_ref(snapshot, old):
    return next((r for r in snapshot.world.members
                 if r.record_kind == old.record_kind and r.ref.id == old.ref.id), None)


def label(record):
    return next((getattr(record, k) for k in ('purpose', 'subject', 'label', 'name')
                 if getattr(record, k, None)), None)


def dependency_details(baseline, snapshot):
    """Labels and raw reported context only: no derived financial eligibility."""
    rows = []
    if snapshot is None:
        return rows
    for ref, fingerprint in baseline.dependencies:
        now = current_ref(snapshot, ref)
        try:
            old = snapshot.get(ref)
        except Rejected:
            # A different recovered authority session need not retain this
            # volatile artifact's evidence. Show unavailable, never a read crash.
            old = None
        record = snapshot.get(now) if now else None
        changed = now != ref or record is None or digest(record) != fingerprint
        status = getattr(getattr(record, 'status', None), 'value', None)
        rows.append(dict(label=label(record or old), record_kind=ref.record_kind,
                         changed=changed, reason=('dependency_removed' if now is None else
                         'inflow_cancelled' if changed and ref.record_kind == 'future_inflow' and status == 'cancelled'
                         else 'dependency_changed' if changed else None),
                         status=status, amount=_wire(next((getattr(record, k) for k in
                         ('expected_amount', 'amount', 'balance') if getattr(record, k, None) is not None), None))))
    return rows


def compatible(old, new):
    # Ref/observation and reported amount may change; interpretation/context may not.
    def normalize(value):
        if isinstance(value, dict):
            if set(value) == {'record_kind', 'ref'}:
                return (value['record_kind'], value['ref']['id'])
            return {k: normalize(v) for k, v in value.items()}
        if isinstance(value, list):
            return [normalize(v) for v in value]
        return value
    def meaning(record):
        return {k: normalize(v) for k, v in _wire(record).items()
                if k not in ('ref', 'observation', 'amount', 'expected_amount')}
    return meaning(old) == meaning(new)


class EvaluationRefresh:
    def __init__(self, baselines, lines):
        self.baselines, self.lines = baselines, lines

    def refresh(self, request):
        fields(request, 'decision_id baseline_ref')
        identity = text(request['decision_id'])
        fields(request['baseline_ref'], 'id version')
        versions = self.baselines.versions.get(identity, ())
        if not versions or _wire(versions[-1].ref) != request['baseline_ref']:
            raise IfError('baseline_changed')
        original = versions[-1]
        read = self.baselines.authority.read()
        snapshot = read.snapshot
        view = self.baselines.view(original)
        if (snapshot is None or 'authority_changed' in view['stale_reasons']):
            raise IfError('authority_changed')
        # External unknown remains explicitly partial, expired evidence blocks refresh.
        if any(c.external and c.external.state(datetime.now(timezone.utc)) == 'stale'
               for c in original.components):
            raise IfError('evidence_stale')
        staged_base = copy(self.baselines)
        staged_base.versions = dict(self.baselines.versions)
        baseline = original
        if view['status'] == 'stale':
            if len(versions) >= 64:
                raise IfError('session_capacity')
            refs = {}
            for old, _ in original.dependencies:
                now = current_ref(snapshot, old)
                if now is None:
                    raise IfError('dependency_removed')
                if old.record_kind in ('world_decision', 'decision_context') and not compatible(snapshot.get(old), snapshot.get(now)):
                    raise IfError('context_review_required')
                refs[old] = now
            decision = refs[original.decision_ref]
            contexts = linked(snapshot, decision)
            if set(contexts) != {refs[r] for r in original.linked_contexts}:
                raise IfError('context_review_required')
            components = []
            for component in original.components:
                if component.world_ref:
                    ref = refs[component.world_ref]
                    record = snapshot.get(ref)
                    if ref.record_kind == 'planned_expense' and record.status not in (PlannedExpenseStatus.PLANNED, PlannedExpenseStatus.PARTIALLY_REALIZED):
                        raise IfError('cost_no_longer_planned')
                    if not compatible(snapshot.get(component.world_ref), record):
                        raise IfError('context_review_required')
                    observed = record.amount if ref.record_kind == 'world_decision' else record.expected_amount
                    if observed is None:
                        raise IfError('cost_unknown')
                    planning = AmountEstimate.approximate(observed.amount) if observed.precision is AmountPrecision.EXACT_REPORTED else observed
                    component = replace(component, world_ref=ref, observed_amount=observed, planning_amount=planning)
                components.append(component)
            baseline = replace(original, ref=Ref(original.ref.id, len(versions)+1), decision_ref=decision,
                source_revision=snapshot.revision, created_at=datetime.now(timezone.utc), components=tuple(components),
                dependencies=tuple((r, digest(snapshot.get(r))) for r in refs.values()), linked_contexts=contexts,
                explicit_dependencies=tuple(refs[r] for r in original.explicit_dependencies),
                request_digest=digest(dict(refresh_from=_wire(original.ref), revision=snapshot.revision)))
            staged_base.versions[identity] = (*versions, baseline)
            if staged_base.view(baseline)['status'] not in ('estimate_available', 'partially_available'):
                raise IfError('baseline_not_current')
        staged = copy(self.lines)
        staged.baselines = staged_base
        staged.lines = dict(self.lines.lines)
        staged.unresolved = dict(self.lines.unresolved)
        staged.evaluations = deepcopy(self.lines.evaluations)
        affected = []
        for key, line in self.lines.lines.items():
            if line.baseline.decision_ref.ref.id != identity or self.lines.validity(line)[0]:
                continue
            if len(staged.evaluations[key]) >= 64:
                raise IfError('session_capacity')
            reason = None
            if line.baseline.ref != original.ref:
                reason = staged.unresolved.get(key, 'context_review_required')
            refs, inflows = [], []
            for old, _ in line.extra_dependencies:
                now = current_ref(snapshot, old)
                if now is None:
                    reason = 'dependency_removed'; break
                record = snapshot.get(now)
                if record.status is not FutureInflowStatus.EXPECTED:
                    reason = ('hypothesis_already_realized' if record.status is FutureInflowStatus.CANCELLED
                              and line.kind == 'remove' and line.target == 'inflow:'+old.ref.id
                              else 'required_inflow_unavailable')
                    break
                if not compatible(snapshot.get(old), record):
                    reason = 'context_review_required'; break
                refs.append((now, digest(record)))
                inflows.append(('inflow:'+now.ref.id, record.expected_amount))
            if reason:
                staged.unresolved[key] = reason
            else:
                staged.unresolved.pop(key, None)
                staged.lines[key] = replace(line, baseline=baseline, extra_dependencies=tuple(refs), inflows=tuple(inflows))
            # Avoid repeated unresolved attempts manufacturing identical versions.
            candidate = staged.view(staged.lines[key], audit=False)
            if staged.evaluations[key][-1]['result'] != candidate:
                staged.record(key, candidate, snapshot.revision)
                affected.append(key)
        for key, combo in staged.combinations.items():
            if set(combo['line_ids']) & set(affected):
                if len(staged.evaluations[key]) >= 64:
                    raise IfError('session_capacity')
                staged.record(key, staged.combination_view(combo, audit=False), snapshot.revision)
        if self.baselines.authority.read().version != read.version:
            raise IfError('world_changed')
        # Atomic session publication under the application workflow lock.
        self.baselines.versions = staged_base.versions
        self.lines.lines = staged.lines
        self.lines.unresolved = staged.unresolved
        self.lines.evaluations = staged.evaluations
        return dict(baseline=self.baselines.latest(identity), scenarios=self.lines.listing(identity),
                    refreshed=affected, financial_authority=False)

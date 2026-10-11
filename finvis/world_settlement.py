"""Pure Slice E reconciliation. Only WorldStore.execute can publish its results."""

from dataclasses import dataclass, replace
from enum import Enum
from typing import Optional, Tuple

from .domain import Money, Ref
from .lifecycle import next_ref
from .store import Rejected
from .world_values import AmountEstimate, AmountPrecision, ObservationMetadata, ObservationRole, TypedRef, FreshnessStatus
from .world_domain import AssetPosition, Earmark, WorldAssetBundle, sum_amount_estimates, _subtract_estimates
from .world_fulfillment import is_refund, is_open, validate_event, current_record
from .world_planning import ActualOccurrence, OccurrenceKind, SettlementStatus, PlannedExpense, PlannedExpenseStatus, FutureInflow, FutureInflowStatus


class BalanceBasis(Enum):
    PRIOR = 'apply_to_prior_balance'
    INCLUDED = 'already_in_reported_balance'


@dataclass(frozen=True)
class BalanceCoverage:
    occurrence_ref: TypedRef
    position_ref: TypedRef
    basis: BalanceBasis
    observation: ObservationMetadata
    rationale: str

    def __post_init__(self):
        from .world_commands import validate_fields
        validate_fields(self)
        if self.occurrence_ref.record_kind != 'actual_occurrence' or self.position_ref.record_kind != 'asset_position':
            raise ValueError('Coverage needs exact occurrence and position refs')
        if not self.observation.evidence_refs or self.observation.role is not ObservationRole.ACTUAL:
            raise ValueError('Coverage needs actual reviewed evidence')


@dataclass(frozen=True)
class SettleOccurrence:
    """One reviewed occurrence, one position, optionally one expectation/allocation.

    The report is UNALLOCATED even for correction; its expected_ref names the
    exact old occurrence. Correction must additionally name its old effect.
    Unknown position/coverage is representable, but cannot become a command.
    """
    operation_id: str
    report: ActualOccurrence
    expected_ref: Optional[TypedRef]
    coverage: Optional[BalanceCoverage]
    related_ref: Optional[TypedRef]
    earmark_ref: Optional[TypedRef]
    corrects_effect_ref: Optional[TypedRef] = None
    depends_on: Tuple[str, ...] = ()
    complete_plan: bool = False

    def __post_init__(self):
        from .world_commands import validate_fields
        validate_fields(self)
        if self.report.settlement_status is not SettlementStatus.UNALLOCATED:
            raise ValueError('Settlement input must be an unallocated report')
        for ref, kinds in ((self.expected_ref, ('actual_occurrence',)),
                           (self.related_ref, ('planned_expense', 'future_inflow')),
                           (self.earmark_ref, ('earmark',)), (self.corrects_effect_ref, ('balance_effect',))):
            if ref is not None and ref.record_kind not in kinds:
                raise ValueError('Wrong settlement reference kind')


@dataclass(frozen=True)
class BalanceEffect:
    ref: Ref
    owner_id: str
    world_id: str
    occurrence_ref: TypedRef
    position_before_ref: TypedRef
    position_after_ref: TypedRef
    # Amount is nonnegative; direction supplies its sign. Correction is the
    # net adjustment, while occurrence.actual_amount retains the full report.
    amount: AmountEstimate
    direction: int
    coverage: BalanceCoverage
    previous_effect_ref: Optional[TypedRef]
    earmark_before_ref: Optional[TypedRef]
    earmark_after_ref: Optional[TypedRef]
    observation: ObservationMetadata

    def __post_init__(self):
        from .world_commands import validate_fields
        if type(self.direction) is not int or self.direction not in (-1, 0, 1):
            raise ValueError('Effect direction must be -1, 0 or 1')
        validate_fields(self, signed_fields=('direction',))
        if self.occurrence_ref.record_kind != 'actual_occurrence' or any(
            ref.record_kind != 'asset_position' for ref in (self.position_before_ref, self.position_after_ref)
        ):
            raise ValueError('Effect reference kinds are invalid')
        if self.position_before_ref.ref.id != self.position_after_ref.ref.id:
            raise ValueError('One effect cannot transfer between positions')
        for ref, kind in ((self.previous_effect_ref, 'balance_effect'),
                          (self.earmark_before_ref, 'earmark'), (self.earmark_after_ref, 'earmark')):
            if ref is not None and ref.record_kind != kind:
                raise ValueError('Wrong effect relationship kind')


@dataclass(frozen=True)
class SettlementReconciliation:
    operation: SettleOccurrence
    issues: Tuple['ReconciliationIssue', ...]

    def __post_init__(self):
        from .world_commands import ReconciliationIssue
        if type(self.operation) is not SettleOccurrence or type(self.issues) is not tuple or any(type(i) is not ReconciliationIssue for i in self.issues):
            raise ValueError('Invalid reconciliation')

    @property
    def ready(self):
        return not self.issues


def reconcile_settlement(snapshot, operation):
    """No matching by prose, date or amount; no mutation and no implicit consent."""
    from .world_commands import ReconciliationIssue, IssueDisposition
    if type(operation) is not SettleOccurrence:
        raise Rejected('Expected typed settlement operation')
    issues = []
    report = operation.report
    position = report.source_position_ref if report.kind is OccurrenceKind.EXPENSE else report.destination_position_ref
    for missing, code in ((position is None, 'source_unknown' if report.kind is OccurrenceKind.EXPENSE else 'destination_unknown'),
                          (operation.coverage is None, 'balance_coverage_unknown'),
                          (report.actual_amount.precision is AmountPrecision.UNKNOWN, 'actual_amount_unknown')):
        if missing:
            issues.append(ReconciliationIssue(code, IssueDisposition.NEEDS_USER_INPUT, 'Explicit reviewed settlement input is missing'))
    if not issues:
        try:
            expand_settlement(snapshot, operation)
        except ValueError as error:
            issues.append(ReconciliationIssue('settlement_conflict', IssueDisposition.CONFLICT, str(error)))
    return SettlementReconciliation(operation, tuple(issues))


def _current(snapshot, ref):
    if ref not in snapshot.world.members:
        raise Rejected('Settlement dependency is not the exact current version')
    return snapshot.get(ref)


def _metadata(report, old, role):
    return replace(report.observation, role=role, corrects_ref=old.ref,
                   correction_reason='Reviewed settlement/correction')


def _apply(balance, amount, direction):
    if balance.currency != amount.currency:
        raise Rejected('Cross-currency settlement is unsupported')
    if direction == 0:
        return balance
    result = (sum_amount_estimates((balance, amount), balance.currency) if direction > 0
              else _subtract_estimates(balance, amount, balance.currency))
    if result.precision is AmountPrecision.UNKNOWN:
        raise Rejected('Settlement arithmetic is unresolved; explicit supported bounds required')
    return result


def _known_later_effects(snapshot, old_effect, position):
    """Do not adjust through a later independently reported balance."""
    from .world_commands import record_ref
    cursor = old_effect.position_after_ref
    while cursor != record_ref(position):
        effects = [r for r in snapshot.records if type(r) is BalanceEffect and
                   r.position_before_ref == cursor and r.position_after_ref != cursor]
        if len(effects) != 1:
            raise Rejected('Later balance coverage is unresolved; correction cannot rebase')
        cursor = effects[0].position_after_ref
        if cursor.ref.version > position.ref.version:
            raise Rejected('Correction basis is not current')


def _known_allocation_basis(snapshot, old, current):
    """Only unchanged reserve rebindings from known later effects are safe."""
    cursor = old
    while cursor.ref != current.ref:
        following = snapshot.get(TypedRef('earmark', next_ref(cursor.ref)))
        if (following.allocation, following.purpose, following.role, following.emergency_use_designated) != (
            cursor.allocation, cursor.purpose, cursor.role, cursor.emergency_use_designated
        ) or not any(type(effect) is BalanceEffect and effect.position_before_ref == cursor.position_ref
                     and effect.position_after_ref == following.position_ref
                     and effect.position_before_ref != effect.position_after_ref for effect in snapshot.records):
            raise Rejected('Later allocation basis is not a known unchanged rebind')
        cursor = following


def expand_settlement(snapshot, operation):
    """Return typed writes and one effect, never publish. Called again under D lock."""
    from .world_commands import PutRecord, record_ref
    report, coverage = operation.report, operation.coverage
    position_ref = report.source_position_ref if report.kind is OccurrenceKind.EXPENSE else report.destination_position_ref
    if position_ref is None or coverage is None:
        raise Rejected('Source/destination and reviewed balance coverage are required')
    position = _current(snapshot, position_ref)
    if type(position) is not AssetPosition or coverage.position_ref != position_ref or coverage.occurrence_ref != record_ref(report):
        raise Rejected('Coverage must bind this exact report and balance observation')
    if (not report.observation.evidence_refs or report.observation.freshness.status is not FreshnessStatus.CURRENT
        or position.observation.freshness.status is not FreshnessStatus.CURRENT
        or coverage.observation.freshness.status is not FreshnessStatus.CURRENT):
        raise Rejected('Current reviewed occurrence/balance/coverage evidence is required')
    if coverage.observation.recorded_at > report.observation.recorded_at:
        raise Rejected('Coverage evidence is later than settlement report')
    if report.owner_id != snapshot.world.owner_id or report.world_id != snapshot.world.world_id:
        raise Rejected('Settlement owner/world mismatch')
    if report.actual_amount.currency != position.currency or report.actual_amount.precision is AmountPrecision.UNKNOWN:
        raise Rejected('Settlement requires known same-currency amount')
    old = _current(snapshot, operation.expected_ref) if operation.expected_ref else None
    if old:
        if report.ref != next_ref(old.ref) or report.observation.corrects_ref != old.ref or old.kind != report.kind:
            raise Rejected('Occurrence correction must retain kind and next-version lineage')
        if report.observation.recorded_at < old.observation.recorded_at:
            raise Rejected('Occurrence correction chronology is invalid')
    elif report.ref.version != 1 or report.observation.corrects_ref is not None or any(
        type(r) is ActualOccurrence and r.ref.id == report.ref.id for r in snapshot.records
    ):
        raise Rejected('New occurrence must have a fresh v1 identity')
    validate_event(snapshot, report, old, prepare=True)
    related = _current(snapshot, operation.related_ref) if operation.related_ref else None
    gradual = type(related) is PlannedExpense and related.fulfillment is not None
    if operation.complete_plan and (not gradual or old is not None or is_refund(report)):
        raise Rejected('Explicit completion requires a new payment on a gradual plan')
    if is_refund(report):
        original = snapshot.get(report.lineage.refund_of_ref)
        origin_plans = [r for r in original.related_entity_refs if r.record_kind == 'planned_expense']
        origin = current_record(snapshot, origin_plans[0]) if origin_plans else None
        if origin is not None and origin.fulfillment is not None:
            if related != origin:
                raise Rejected('Refund must retain original gradual plan membership')
        elif related is not None:
            raise Rejected('Legacy/standalone refund must not realize another expectation')
    if report.lineage and report.lineage.replacement:
        original = snapshot.get(report.lineage.replacement.original_payment_ref)
        origin_plans = [r for r in original.related_entity_refs if r.record_kind == 'planned_expense']
        origin = current_record(snapshot, origin_plans[0]) if origin_plans else None
        if origin is not None and origin.fulfillment is not None and related != origin:
            raise Rejected('Replacement must retain original gradual component membership')
    report_lifecycle_refs = tuple(r for r in report.related_entity_refs if r.record_kind in ('planned_expense', 'future_inflow'))
    if report_lifecycle_refs != (() if related is None else (operation.related_ref,)):
        raise Rejected('Explicit related entity must match report; splits are unsupported')
    if old:
        previous_links = {(r.record_kind, r.ref.id) for r in old.related_entity_refs if r.record_kind in ('planned_expense', 'future_inflow')}
        if previous_links != {(r.record_kind, r.ref.id) for r in report_lifecycle_refs}:
            raise Rejected('Cannot detach or switch an occurrence lifecycle relationship')
    if related:
        expected_type = PlannedExpense if report.kind is OccurrenceKind.EXPENSE or is_refund(report) else FutureInflow
        if type(related) is not expected_type or related.expected_amount.currency != position.currency:
            raise Rejected('Related lifecycle/currency mismatch')
        if gradual:
            if old is None and not is_refund(report) and not is_open(related):
                raise Rejected('Closed gradual plan cannot receive a new payment')
            if old is not None and record_ref(old) not in related.fulfillment.occurrence_refs:
                raise Rejected('Correction must belong to this exact gradual plan')
        else:
            if related.status in (PlannedExpenseStatus.CANCELLED, FutureInflowStatus.CANCELLED):
                raise Rejected('Cancelled expectation requires separate evidenced reinstatement')
            linked = related.occurrence_ref if type(related) is PlannedExpense else related.realization_ref
            if linked is not None and (old is None or linked != record_ref(old)):
                raise Rejected('Expectation already realized; partial/multiple settlements unsupported')
    mark = _current(snapshot, operation.earmark_ref) if operation.earmark_ref else None
    if mark and (report.kind is not OccurrenceKind.EXPENSE or mark.position_ref != position_ref):
        raise Rejected('Earmark must belong to this exact expense source')
    # FundingLink is intention, not evidence of the eventual payment source.
    # This explicit operation names the actual reserve; a different choice is
    # reviewable in the exact preview and never inferred from the old plan.
    direction = -1 if report.kind is OccurrenceKind.EXPENSE else 1
    adjustment = report.actual_amount
    prior_effect = None
    if operation.corrects_effect_ref:
        if old is None or old.balance_effect_ref != operation.corrects_effect_ref or old.settlement_status is not SettlementStatus.APPLIED_TO_BALANCE:
            raise Rejected('Correction must name the exact previously applied effect')
        prior_effect = snapshot.get(operation.corrects_effect_ref)
        if coverage.basis is not BalanceBasis.PRIOR or prior_effect.position_after_ref.ref.id != position.ref.id:
            raise Rejected('Wrong-source/inclusive-basis correction needs separate coverage design')
        _known_later_effects(snapshot, prior_effect, position)
        if any(a.precision is not AmountPrecision.EXACT_REPORTED for a in (report.actual_amount, old.actual_amount, position.balance)):
            raise Rejected('Uncertain correction difference requires new coverage reconciliation')
        old_mark = snapshot.get(prior_effect.earmark_after_ref) if prior_effect.earmark_after_ref else None
        if (old_mark is None) != (mark is None) or (mark and (mark.ref.id != old_mark.ref.id or mark.allocation != old_mark.allocation)):
            raise Rejected('Changed allocation basis requires explicit reconciliation')
        if mark:
            _known_allocation_basis(snapshot, old_mark, mark)
        difference = (report.actual_amount.amount.minor_units - old.actual_amount.amount.minor_units) * direction
        direction = (difference > 0) - (difference < 0)
        adjustment = AmountEstimate.exact_reported(Money(abs(difference), position.currency))
    elif old and old.settlement_status is not SettlementStatus.UNALLOCATED:
        raise Rejected('Occurrence already settled; use an explicit correction')
    if coverage.basis is BalanceBasis.INCLUDED:
        direction = 0
        adjustment = AmountEstimate.exact_reported(Money(0, position.currency))
        if mark:
            raise Rejected('Inclusive balance requires separately reconciled earmarks; no inferred second consumption')
    updated_position = position
    writes = []
    if direction:
        updated_position = replace(position, ref=next_ref(position.ref),
            balance=_apply(position.balance, adjustment, direction),
            observation=_metadata(report, position, ObservationRole.CURRENT))
        writes.append(PutRecord(operation.operation_id + '/position', updated_position, record_ref(position)))
    mark_after = None
    # Rebind all exact position refs, preserving unrelated allocation values and
    # purpose. Only the explicitly named reserve is consumed or corrected.
    for ref in snapshot.world.members:
        item = snapshot.get(ref)
        if type(item) is not Earmark or item.position_ref != position_ref:
            continue
        if not direction:
            if item == mark:
                mark_after = item
            continue
        allocation = _apply(item.allocation, adjustment, direction) if item == mark else item.allocation
        updated = replace(item, ref=next_ref(item.ref), position_ref=record_ref(updated_position),
            allocation=allocation, observation=_metadata(report, item, ObservationRole.CURRENT))
        writes.append(PutRecord(operation.operation_id + '/' + item.ref.id, updated, ref))
        if item == mark:
            mark_after = updated
    # Effect identity advances independently when an unallocated occurrence had
    # prior report revisions; only successful effects form this version chain.
    effect_ref = next_ref(prior_effect.ref) if prior_effect else Ref('balance-effect/' + report.ref.id, 1)
    linked_refs = report.related_entity_refs
    if related:
        new_related_ref = TypedRef(operation.related_ref.record_kind, next_ref(related.ref))
        linked_refs = tuple(new_related_ref if ref == operation.related_ref else ref for ref in linked_refs)
    settled = replace(report, related_entity_refs=linked_refs,
        source_position_ref=record_ref(updated_position) if report.kind is OccurrenceKind.EXPENSE else None,
        destination_position_ref=record_ref(updated_position) if report.kind is OccurrenceKind.INFLOW else None,
        settlement_status=SettlementStatus.APPLIED_TO_BALANCE if coverage.basis is BalanceBasis.PRIOR else SettlementStatus.ALREADY_IN_BALANCE,
        balance_effect_ref=TypedRef('balance_effect', effect_ref))
    if related:
        kwargs = dict(ref=next_ref(related.ref), observation=_metadata(report, related, related.observation.role))
        if gradual:
            prior = related.fulfillment.occurrence_refs
            refs = tuple(record_ref(settled) if r == operation.expected_ref else r for r in prior) if old else (*prior, record_ref(settled))
            status = related.status
            if old is None and not is_refund(report):
                status = PlannedExpenseStatus.COMPLETED if operation.complete_plan else PlannedExpenseStatus.PARTIALLY_REALIZED
            kwargs.update(status=status, fulfillment=replace(related.fulfillment, occurrence_refs=refs))
        elif type(related) is PlannedExpense:
            # Funding intention remains historical audit evidence, not another debit.
            kwargs.update(status=PlannedExpenseStatus.COMPLETED, occurrence_ref=record_ref(settled))
        else:
            kwargs.update(status=FutureInflowStatus.REALIZED, realization_ref=record_ref(settled))
        writes.append(PutRecord(operation.operation_id + '/lifecycle', replace(related, **kwargs), record_ref(related)))
    writes.append(PutRecord(operation.operation_id + '/occurrence', settled, operation.expected_ref))
    metadata = replace(report.observation, corrects_ref=prior_effect.ref if prior_effect else None,
                       correction_reason='Supersedes prior settled amount effect' if prior_effect else None)
    effect = BalanceEffect(effect_ref, report.owner_id, report.world_id, record_ref(settled),
        position_ref, record_ref(updated_position), adjustment, direction, coverage,
        record_ref(prior_effect) if prior_effect else None, record_ref(mark) if mark else None,
        record_ref(mark_after) if mark_after else None, metadata)
    # Validate the complete resulting allocation graph before calling it ready.
    from .world_store import current_assets
    assets = current_assets(snapshot)
    replacements = {(record_ref(op.record).record_kind, op.record.ref.id): op.record for op in writes}
    def updated(values):
        return tuple(replacements.get((record_ref(v).record_kind, v.ref.id), v) for v in values)
    positions, marks = updated(assets.positions), updated(assets.earmarks)
    state = replace(assets.state, position_refs=tuple(record_ref(p) for p in positions),
                    earmark_refs=tuple(record_ref(m) for m in marks))
    WorldAssetBundle(state, assets.accounts, positions, marks)
    return tuple(writes), effect


def propose_settlement(snapshot, session_generation, operation, change_ref):
    """Build D's exact change set. Caller must preview, authorize and execute it."""
    from .world_commands import CandidateChangeSet, ChangeMode, record_ref
    reconciliation = reconcile_settlement(snapshot, operation)
    if not reconciliation.ready:
        raise Rejected('; '.join(issue.code + ': ' + issue.rationale for issue in reconciliation.issues))
    writes, effect = expand_settlement(snapshot, operation)
    touched = {(record_ref(op.record).record_kind, op.record.ref.id) for op in writes}
    touched.add(('balance_effect', effect.ref.id))
    retained = tuple(ref for ref in snapshot.world.members if (ref.record_kind, ref.ref.id) not in touched)
    assets_changed = any(type(op.record) in (AssetPosition, Earmark) for op in writes)
    return CandidateChangeSet(change_ref, snapshot.world.owner_id, snapshot.world.world_id, session_generation,
        snapshot.world.ref, snapshot.world.state_ref, snapshot.world.plan_ref, snapshot.revision,
        ChangeMode.CORRECT if operation.corrects_effect_ref else ChangeMode.RESOLVE,
        (operation,), None, retained, (), replace(operation.report.observation, role=ObservationRole.CURRENT),
        'Reviewed occurrence settlement against explicit balance coverage' if assets_changed else None,
        (), operation.report.observation.recorded_at)


def validate_effect(snapshot, effect):
    """Audit the local arithmetic and pinned coverage, including zero posting."""
    from .world_commands import record_ref
    occurrence = snapshot.get(effect.occurrence_ref)
    before = snapshot.get(effect.position_before_ref)
    after = snapshot.get(effect.position_after_ref)
    if type(occurrence) is not ActualOccurrence or type(before) is not AssetPosition or type(after) is not AssetPosition:
        raise Rejected('Invalid balance effect endpoints')
    if occurrence.balance_effect_ref != record_ref(effect) or effect.coverage.occurrence_ref != effect.occurrence_ref or effect.coverage.position_ref != effect.position_before_ref:
        raise Rejected('Effect, occurrence and coverage must bind exactly')
    position = occurrence.source_position_ref if occurrence.kind is OccurrenceKind.EXPENSE else occurrence.destination_position_ref
    if position != effect.position_after_ref:
        raise Rejected('Occurrence must pin the resulting balance observation')
    amount, direction = occurrence.actual_amount, -1 if occurrence.kind is OccurrenceKind.EXPENSE else 1
    if effect.previous_effect_ref:
        previous = snapshot.get(effect.previous_effect_ref)
        old = snapshot.get(previous.occurrence_ref)
        if (effect.ref != next_ref(previous.ref) or occurrence.observation.corrects_ref != old.ref
            or previous.position_after_ref.ref.id != before.ref.id):
            raise Rejected('Effect correction lineage mismatch')
        if amount.precision is not AmountPrecision.EXACT_REPORTED or old.actual_amount.precision is not AmountPrecision.EXACT_REPORTED:
            raise Rejected('Uncertain effect correction')
        delta = (amount.amount.minor_units - old.actual_amount.amount.minor_units) * direction
        amount = AmountEstimate.exact_reported(Money(abs(delta), before.currency))
        direction = (delta > 0) - (delta < 0)
    if effect.coverage.basis is BalanceBasis.INCLUDED:
        if effect.previous_effect_ref is not None or occurrence.settlement_status is not SettlementStatus.ALREADY_IN_BALANCE:
            raise Rejected('Inclusive effect cannot silently adjust an old posting')
        amount, direction = AmountEstimate.exact_reported(Money(0, before.currency)), 0
    elif occurrence.settlement_status is not SettlementStatus.APPLIED_TO_BALANCE:
        raise Rejected('Prior balance effect must be applied')
    if effect.amount != amount or effect.direction != direction or after.balance != _apply(before.balance, amount, direction):
        raise Rejected('Balance effect arithmetic mismatch')
    if after.ref != (next_ref(before.ref) if direction else before.ref):
        raise Rejected('Balance effect version mismatch')
    if (effect.earmark_before_ref is None) != (effect.earmark_after_ref is None):
        raise Rejected('Allocation effect must retain both endpoints')
    if effect.earmark_before_ref:
        old_mark, new_mark = snapshot.get(effect.earmark_before_ref), snapshot.get(effect.earmark_after_ref)
        if (occurrence.kind is not OccurrenceKind.EXPENSE or old_mark.ref.id != new_mark.ref.id
            or old_mark.position_ref != record_ref(before) or new_mark.position_ref != record_ref(after)
            or new_mark.allocation != _apply(old_mark.allocation, amount, direction)):
            raise Rejected('Allocation effect mismatch')

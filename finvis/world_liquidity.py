"""Read-only pending adapter. No emergency eligibility policy or engine output."""

from dataclasses import dataclass
from typing import Tuple

from .domain import Currency
from .world_values import AmountEstimate, AmountPrecision, FreshnessStatus, TypedRef
from .world_commands import ReconciliationIssue, IssueDisposition, record_ref
from .world_planning import ActualOccurrence, SettlementStatus
from .world_domain import AccessStatus


@dataclass(frozen=True)
class PendingLiquidity:
    currency: Currency
    value: AmountEstimate
    input_refs: Tuple[TypedRef, ...]
    reasons: Tuple[ReconciliationIssue, ...]


def pending_liquidity(snapshot, currency):
    from .world_store import current_assets
    if type(currency) is not Currency:
        raise ValueError('Only existing native currencies supported')
    assets = current_assets(snapshot)
    positions = tuple(p for p in assets.positions if p.currency is currency)
    refs = tuple(record_ref(p) for p in positions)
    reasons = [ReconciliationIssue('eligibility_policy_pending', IssueDisposition.NEEDS_USER_INPUT,
        'Emergency designation and unallocated balance do not establish financial eligibility', refs)]
    for p in positions:
        if p.balance.precision is AmountPrecision.UNKNOWN or p.observation.freshness.status is not FreshnessStatus.CURRENT:
            reasons.append(ReconciliationIssue('balance_unknown_or_stale', IssueDisposition.NEEDS_USER_INPUT,
                'Current balance support is incomplete', (record_ref(p),)))
        if p.access.status is not AccessStatus.ACCESSIBLE:
            reasons.append(ReconciliationIssue('access_unresolved_or_restricted', IssueDisposition.NEEDS_USER_INPUT,
                'Access must be reviewed before any eligibility conclusion', (record_ref(p),)))
    for ref in snapshot.world.members:
        value = snapshot.get(ref)
        if type(value) is ActualOccurrence and value.settlement_status is SettlementStatus.UNALLOCATED and value.actual_amount.currency is currency:
            refs += (ref,)
            reasons.append(ReconciliationIssue('unsettled_occurrence', IssueDisposition.NEEDS_USER_INPUT,
                'Relevant actual occurrence has unresolved balance coverage', (ref,)))
    return PendingLiquidity(currency, AmountEstimate.unknown('Emergency eligibility remains unresolved', currency),
                            refs, tuple(reasons))

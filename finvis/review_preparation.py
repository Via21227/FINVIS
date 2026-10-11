"""S4 pure, conservative preparation of existing G review evidence.

This module has no provider, proposal, preview or writer dependency. A prepared
component is evidence for a later explicit ReviewRequest, never an approval.
"""

from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation
from enum import Enum
import hashlib
import re
from typing import Optional, Tuple

from .agent_reconciliation import AccountReview, FactReview, PositionReview, SourceSelection
from .agent_schema import CandidateInterpretation
from .agent_understanding import CandidateEnvelope
from .store import StaleVersion
from .world_application import ContextKind, ReadVersion, UserWorldRead
from .world_domain import EarmarkRole


class PreparationStatus(Enum):
    AUTO_VERIFIED = 'auto_verified'
    CLARIFICATION_REQUIRED = 'clarification_required'
    UNRESOLVED = 'unresolved'
    UNSUPPORTED = 'unsupported'


RULE_VERSION = 's4-0.2'


@dataclass(frozen=True)
class PreparedFact:
    fact_id: str
    status: PreparationStatus
    reason: str
    sources: Tuple[SourceSelection, ...] = ()
    excerpt: Optional[str] = None
    excerpt_sha256: Optional[str] = None
    extraction_verified: bool = False
    review: Optional[FactReview] = None


@dataclass(frozen=True)
class PreparationIssue:
    status: PreparationStatus
    code: str


@dataclass(frozen=True)
class PreparedReview:
    candidate_id: str
    version: ReadVersion
    rule_version: str
    source_sha256: str
    facts: Tuple[PreparedFact, ...]
    accounts: Tuple[AccountReview, ...]
    positions: Tuple[PositionReview, ...]
    issues: Tuple[PreparationIssue, ...]

    @property
    def fact_reviews(self):
        return tuple(item.review for item in self.facts if item.review is not None)


_NUMBERS = re.compile(r'(?<![\d.,])(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?![\d,])')
_CURRENCIES = {
    'CNY': re.compile(r'(?<![A-Za-z])(?:CNY|RMB)(?![A-Za-z])|人民币|[¥￥]|(?<![新欧美日港澳加韩台])元', re.I),
    'SGD': re.compile(r'(?<![A-Za-z])SGD(?![A-Za-z])|S\$|新币|新元', re.I),
    'EUR': re.compile(r'(?<![A-Za-z])(?:EUR|euros?)(?![A-Za-z])|€|欧元', re.I),
}
_APPROX = re.compile(r'≈|~|about|around|roughly|approximately|约|大概|左右|估计', re.I)
_UNKNOWN = re.compile(r'unknown|not known|unspecified|未知|不清楚|未确定', re.I)
_RANGE = re.compile(r'\d\s*(?:-|–|—|至|到|~)\s*\d|between\b.+\band\b|介于.+之间', re.I)
_NEGATION = re.compile(r'\b(?:not|never|no longer)\b|没有|尚未|未曾|并未', re.I)
_CONDITIONAL = re.compile(r'\b(?:if|would|suppose|hypothetically)\b|如果|假如|假设', re.I)
_OTHER_OWNER = re.compile(r"\b(?:her|his|their|your)\b|\b(?:mother|father|friend|partner)'s\b|她的|他的|他们的|朋友的|妈妈的|爸爸的", re.I)
_TEMPORAL = {
    'monthly_essential_outflow': re.compile(r'monthly essential|essential.+(?:per month|monthly)|每月必要|月度必要|必要月支出', re.I),
    'current_asset': re.compile(r'\bbalance\b|\bcurrent(?:ly)?\b|\bI have\b|余额|现有|目前|持有', re.I),
    'earmarked_current_asset': re.compile(r'reserved|set aside|earmark|allocated|预留|专款|备用金|留作', re.I),
    'expected_future_inflow': re.compile(r'expect|will receive|due to receive|pending refund|预计|将收到|待到账|未来收入', re.I),
    'planned_expense': re.compile(r'plan to|planning to|planning estimate|budget for|will buy|计划|预算|准备买|打算购买', re.I),
    'actual_expense': re.compile(r'\bpaid\b|\bspent\b|\bcharged\b|已支付|已花|扣款|付款了', re.I),
    'actual_inflow': re.compile(r'\breceived\b|\barrived\b|\bdeposited\b|已收到|到账|入账', re.I),
    'contemplated_decision': re.compile(r'considering|thinking about|deciding whether|考虑|权衡|要不要', re.I),
}


def _spans(text):
    start = 0
    for index, char in enumerate(text):
        if char in '。！？!?;；\n' or (char == '.' and
                not (index and index + 1 < len(text) and text[index - 1].isdigit() and text[index + 1].isdigit())):
            if text[start:index + 1].strip():
                yield start, index + 1, text[start:index + 1]
            start = index + 1
    if text[start:].strip():
        yield start, len(text), text[start:]


def _contains(haystack, needle):
    if needle[0].isascii() and needle[0].isalnum():
        left = r'(?<!\w)'
    else:
        left = ''
    if needle[-1].isascii() and needle[-1].isalnum():
        right = r'(?!\w)'
    else:
        right = ''
    return bool(re.search(left + re.escape(needle) + right, haystack, re.I))


def _amount_supported(fact, default_currency, excerpt):
    amount = fact.amount
    if amount is None:
        return (not _NUMBERS.search(excerpt), 'unexpected_numeric_evidence')
    currency = amount.currency or default_currency
    if currency not in _CURRENCIES:
        return False, 'unsupported_currency'
    found = {code for code, marker in _CURRENCIES.items() if marker.search(excerpt)}
    if found != {currency}:
        return False, 'currency_evidence_ambiguous'
    numbers = []
    try:
        numbers = [Decimal(match.group().replace(',', '')) for match in _NUMBERS.finditer(excerpt)]
        if amount.precision == 'unknown':
            return (not numbers and bool(_UNKNOWN.search(excerpt)), 'unknown_amount_not_explicit')
        if amount.precision == 'range':
            expected = [Decimal(amount.range_min), Decimal(amount.range_max)]
            return (numbers == expected and bool(_RANGE.search(excerpt)), 'range_evidence_ambiguous')
        expected = Decimal(amount.value)
        if amount.precision == 'approximate' and not _APPROX.search(excerpt):
            return False, 'approximation_not_explicit'
        if amount.precision == 'exact' and _APPROX.search(excerpt):
            return False, 'precision_conflict'
        return (numbers == [expected], 'amount_evidence_ambiguous')
    except (InvalidOperation, TypeError, ValueError):
        return False, 'amount_evidence_ambiguous'


def _extract(fact, interpretation, spans, labels):
    matches = [span for span in spans if _contains(span[2], fact.label)]
    # Repeated account mentions in allocations do not create a second balance
    # observation. Narrow by role only, never cherry-pick a matching amount from
    # two contradictory balance statements.
    if len(matches) > 1 and fact.type in _TEMPORAL:
        compatible = [span for span in matches if _TEMPORAL[fact.type].search(span[2])]
        if compatible:
            unexplained = [span for span in matches if span not in compatible and not any(
                cue.search(span[2]) for kind, cue in _TEMPORAL.items() if kind != fact.type)]
            matches = compatible + unexplained
    if len(matches) != 1:
        return PreparedFact(fact.id, PreparationStatus.CLARIFICATION_REQUIRED,
                            'source_excerpt_not_unique')
    start, end, excerpt = matches[0]
    source = (SourceSelection('fact', start, end),)
    digest = hashlib.sha256(excerpt.encode('utf-8')).hexdigest()
    base = PreparedFact(fact.id, PreparationStatus.CLARIFICATION_REQUIRED, '', source,
                        excerpt, digest)
    account_labels = {other.id for other in interpretation.candidate_facts
                      if other.type == 'current_asset' and other.label == fact.account}
    if any(other != fact.id and other not in account_labels and _contains(excerpt, label)
           for other, label in labels.items()):
        return replace(base, reason='multiple_facts_in_excerpt')
    if fact.account and not _contains(excerpt, fact.account):
        return replace(base, reason='account_not_in_excerpt')
    if fact.earmark and not _contains(excerpt, fact.earmark):
        return replace(base, reason='earmark_not_in_excerpt')
    if fact.purpose and fact.type == 'earmarked_current_asset' and not _contains(excerpt, fact.purpose):
        return replace(base, reason='purpose_not_in_excerpt')
    valid, reason = _amount_supported(fact, interpretation.default_currency, excerpt)
    if not valid:
        status = PreparationStatus.UNSUPPORTED if reason == 'unsupported_currency' else PreparationStatus.CLARIFICATION_REQUIRED
        return replace(base, status=status, reason=reason)
    if _NEGATION.search(excerpt):
        return replace(base, reason='negated_or_unresolved_timing')
    if _CONDITIONAL.search(excerpt) or _OTHER_OWNER.search(excerpt):
        return replace(base, reason='conditional_or_other_owner')
    # A type cue is not enough if the same excerpt also asserts an incompatible
    # temporal role. Keep such extraction visible for human resolution.
    competing = {'current_asset': ('expected_future_inflow', 'actual_expense', 'actual_inflow'),
                 'expected_future_inflow': ('current_asset', 'actual_inflow'),
                 'planned_expense': ('actual_expense',),
                 'actual_expense': ('planned_expense',),
                 'actual_inflow': ('expected_future_inflow',)}
    if any(_TEMPORAL[other].search(excerpt) for other in competing.get(fact.type, ())):
        return replace(base, reason='conflicting_temporal_evidence')
    cue = _TEMPORAL.get(fact.type)
    if cue and not cue.search(excerpt):
        return replace(base, reason='temporal_role_not_explicit')
    if fact.type == 'context' and fact.amount is not None:
        return replace(base, status=PreparationStatus.UNSUPPORTED, reason='monetary_context_unsupported')
    if fact.type in ('actual_expense', 'actual_inflow'):
        return replace(base, reason='settlement_review_required', extraction_verified=True)
    if fact.type == 'context':
        return replace(base, reason='decision_relationship_required', extraction_verified=True)
    if fact.type == 'earmarked_current_asset' and re.search(r'emergency|紧急|应急', excerpt, re.I):
        return replace(base, reason='earmark_role_requires_review', extraction_verified=True)
    return replace(base, status=PreparationStatus.AUTO_VERIFIED, reason='exact_source_binding',
                   extraction_verified=True, review=FactReview(fact.id, source,
                   earmark_role=EarmarkRole.OTHER))


def prepare_review(candidate: CandidateEnvelope, submitted_text: str,
                   read: UserWorldRead) -> PreparedReview:
    """Prepare only source-grounded G components from one exact I1 User read."""
    if type(read) is not UserWorldRead or read.context.kind is not ContextKind.USER or not read.initialized:
        raise ValueError('An initialized I1 User read is required')
    if (type(candidate) is not CandidateEnvelope or type(read.version) is not ReadVersion or
            read.snapshot is None or read.version.context != read.context or
            read.snapshot.revision != read.version.revision or
            read.snapshot.world.owner_id != read.context.owner_id or
            read.snapshot.world.world_id != read.context.world_id):
        raise ValueError('Candidate and I1 read must have complete User World identity')
    if (candidate.status != 'awaiting_confirmation' or
            candidate.generation != read.version.session_generation or
            candidate.revision != read.version.revision):
        raise StaleVersion('Candidate and User World version do not match')
    if type(submitted_text) is not str or not submitted_text.strip():
        raise ValueError('Exact submitted text is required')
    interpretation = CandidateInterpretation.parse(candidate.interpretation.as_dict())
    spans = tuple(_spans(submitted_text))
    facts = {fact.id: fact for fact in interpretation.candidate_facts}
    labels = {fid: fact.label for fid, fact in facts.items()}
    items = {fid: _extract(fact, interpretation, spans, labels) for fid, fact in sorted(facts.items())}
    if interpretation.conflicts or interpretation.assumptions:
        items = {fid: replace(item, status=PreparationStatus.UNRESOLVED,
                              reason='candidate_conflict_or_assumption', review=None)
                 for fid, item in items.items()}
    accounts, positions = [], []
    groups = {}
    for fact in facts.values():
        if fact.type in ('current_asset', 'earmarked_current_asset') and fact.account:
            currency = (fact.amount.currency or interpretation.default_currency) if fact.amount else None
            groups.setdefault((fact.account, currency), []).append(fact)
    for (label, currency), group in sorted(groups.items()):
        balances = sorted((fact for fact in group if fact.type == 'current_asset'), key=lambda fact: fact.id)
        allocations = sorted((fact for fact in group if fact.type == 'earmarked_current_asset'), key=lambda fact: fact.id)
        all_facts = balances + allocations
        if any(items[fact.id].status is not PreparationStatus.AUTO_VERIFIED for fact in all_facts):
            continue
        reason = None
        if len(balances) != 1:
            reason = 'balance_composition_required'
        # A natural-language label is not an authoritative account identity.
        # Any existing account needs an explicit reviewed identity decision.
        matches = [ref for ref in read.snapshot.world.members if ref.record_kind == 'account']
        if matches:
            reason = 'account_identity_ambiguous'
        if reason:
            for fact in all_facts:
                items[fact.id] = replace(items[fact.id], status=PreparationStatus.CLARIFICATION_REQUIRED,
                                         reason=reason, review=None)
            continue
        key = 'account:' + label
        account_source = SourceSelection('account', items[balances[0].id].sources[0].start,
                                         items[balances[0].id].sources[0].end)
        if not any(account.label == label for account in accounts):
            accounts.append(AccountReview(key, label, read.context.owner_id,
                                          (account_source,)))
        positions.append(PositionReview('position:' + label + ':' + currency, key,
                                        (balances[0].id,), tuple(fact.id for fact in allocations)))

    issues = []
    def issue(status, code):
        issues.append(PreparationIssue(status, code))
    if interpretation.conflicts or interpretation.assumptions:
        issue(PreparationStatus.UNRESOLVED, 'candidate_conflict_or_assumption')
    if interpretation.unsupported_requests:
        issue(PreparationStatus.UNSUPPORTED, 'unsupported_request')
    if interpretation.destructive_actions or 'replace_financial_state' in interpretation.user_intents:
        issue(PreparationStatus.CLARIFICATION_REQUIRED, 'replacement_scope_required')
    if 'correct_information' in interpretation.user_intents:
        issue(PreparationStatus.CLARIFICATION_REQUIRED, 'correction_target_required')
    if interpretation.uncertainties:
        issue(PreparationStatus.CLARIFICATION_REQUIRED, 'uncertainty_review_required')
    if interpretation.clarification_questions:
        issue(PreparationStatus.CLARIFICATION_REQUIRED, 'candidate_clarification_required')
    if any(fact.type == 'current_asset' and items[fact.id].status is PreparationStatus.AUTO_VERIFIED
           for fact in facts.values()):
        issue(PreparationStatus.CLARIFICATION_REQUIRED, 'reported_coverage_required')
    return PreparedReview(candidate.id, read.version, RULE_VERSION,
                          hashlib.sha256(submitted_text.encode('utf-8')).hexdigest(),
                          tuple(items[fid] for fid in sorted(items)), tuple(accounts),
                          tuple(positions), tuple(issues))

"""Provisional v0.2 schemas for accepted FINVIS concepts.

Records are data; lifecycle.py validates and executes the scoped Phase 2 commands.
No generic reconciliation or calibrated financial judgments are implemented.
"""

from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from typing import Optional, Tuple


def nonempty(value: str, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be nonempty text")


def whole(value: int, name: str, minimum: int = 0) -> None:
    if type(value) is not int or value < minimum:
        raise ValueError(f"{name} must be an integer >= {minimum}")


def typed_tuple(values: tuple, item_type: type, name: str) -> None:
    if not isinstance(values, tuple) or any(not isinstance(v, item_type) for v in values):
        raise ValueError(f"{name} must be an immutable tuple of {item_type.__name__}")


def aware(value: datetime, name: str) -> None:
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError(f"{name} must include a timezone")


def enum_value(value: Enum, kind: type) -> None:
    if not isinstance(value, kind):
        raise ValueError(f"Expected {kind.__name__}")


@dataclass(frozen=True)
class Ref:
    """Identity plus immutable version; changing content requires a new version."""

    id: str
    version: int

    def __post_init__(self) -> None:
        nonempty(self.id, "id")
        whole(self.version, "version", 1)


class Currency(Enum):
    # Native currencies use 100 minor units; no implicit conversion.
    SGD = "SGD"
    CNY = "CNY"
    EUR = "EUR"


@dataclass(frozen=True)
class Money:
    """Signed integer minor units. SGD and CNY both use 100 minor units/unit."""

    minor_units: int
    currency: Currency

    def __post_init__(self) -> None:
        if type(self.minor_units) is not int:
            raise ValueError("Money requires integer minor units; floats/bools are rejected")
        enum_value(self.currency, Currency)

    def _check(self, other: "Money") -> None:
        if not isinstance(other, Money) or self.currency is not other.currency:
            raise ValueError("Money arithmetic requires the same explicit currency")

    def __add__(self, other: "Money") -> "Money":
        self._check(other)
        return Money(self.minor_units + other.minor_units, self.currency)

    def __sub__(self, other: "Money") -> "Money":
        self._check(other)
        return Money(self.minor_units - other.minor_units, self.currency)


class RealitySource(Enum):
    USER_REPORT = "user_report"
    USER_PROVIDED_EVIDENCE = "user_provided_evidence"
    CONNECTED_SOURCE = "connected_source"
    EXTERNAL_PUBLIC_SOURCE = "external_public_source"


class Precision(Enum):
    EXACT_REPORTED = "exact_reported"
    APPROXIMATE = "approximate"
    UNSPECIFIED = "unspecified"


class ConfidenceAssessment(Enum):
    UNASSESSED = "unassessed"
    TENTATIVE = "tentative"
    SUPPORTED = "supported"


@dataclass(frozen=True)
class Confidence:
    """Provisional vocabulary, not ordered, scored or inferred from source type."""

    assessment: ConfidenceAssessment
    rationale: str

    def __post_init__(self) -> None:
        enum_value(self.assessment, ConfidenceAssessment)
        nonempty(self.rationale, "confidence rationale")


@dataclass(frozen=True)
class FinancialAmount:
    """A selected scalar input with provenance; exact reporting is not certainty."""

    value: Money
    precision: Precision
    confidence: Confidence
    evidence_refs: Tuple[Ref, ...]
    assumption_refs: Tuple[Ref, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.value, Money) or not isinstance(self.confidence, Confidence):
            raise ValueError("FinancialAmount needs typed money and confidence")
        enum_value(self.precision, Precision)
        typed_tuple(self.evidence_refs, Ref, "evidence_refs")
        typed_tuple(self.assumption_refs, Ref, "assumption_refs")
        if not self.evidence_refs and not self.assumption_refs:
            raise ValueError("A financial input needs evidence or an explicit assumption")


@dataclass(frozen=True)
class Evidence:
    ref: Ref
    owner_id: str
    source: RealitySource
    captured_at: datetime
    source_locator: str
    decision_relevant_excerpt: str
    # A locator never confers permission to fetch it. No fetching exists here.

    def __post_init__(self) -> None:
        nonempty(self.owner_id, "owner_id")
        enum_value(self.source, RealitySource)
        aware(self.captured_at, "captured_at")
        nonempty(self.source_locator, "source_locator")
        nonempty(self.decision_relevant_excerpt, "decision_relevant_excerpt")


class AssumptionStatus(Enum):
    PROVISIONAL = "provisional"
    ACCEPTED_FOR_SCENARIO = "accepted_for_scenario"
    SUPERSEDED = "superseded"


@dataclass(frozen=True)
class Assumption:
    ref: Ref
    statement: str
    rationale: str
    scope: str
    status: AssumptionStatus
    review_trigger: str

    def __post_init__(self) -> None:
        for name in ("statement", "rationale", "scope", "review_trigger"):
            nonempty(getattr(self, name), name)
        enum_value(self.status, AssumptionStatus)


class GoalProtection(Enum):
    HARD = "hard"
    SOFT = "soft"
    FLEXIBLE = "flexible"


@dataclass(frozen=True)
class Goal:
    ref: Ref
    owner_id: str
    title: str
    target_amount: Money
    target_date: date
    protection: GoalProtection

    def __post_init__(self) -> None:
        nonempty(self.owner_id, "owner_id")
        nonempty(self.title, "title")
        whole(self.target_amount.minor_units, "target_amount")
        enum_value(self.protection, GoalProtection)
        if type(self.target_date) is not date:
            raise ValueError("target_date must be a calendar date")


class SalaryBasis(Enum):
    UNSPECIFIED = "unspecified"
    GROSS = "gross"
    NET = "net"


@dataclass(frozen=True)
class EmploymentOffer:
    monthly_income: FinancialAmount
    salary_basis: SalaryBasis
    accepted_on: date
    employment_start_on: Optional[date] = None

    def __post_init__(self) -> None:
        whole(self.monthly_income.value.minor_units, "monthly income")
        enum_value(self.salary_basis, SalaryBasis)
        if type(self.accepted_on) is not date:
            raise ValueError("accepted_on must be a date")
        if self.employment_start_on is not None and type(self.employment_start_on) is not date:
            raise ValueError("employment_start_on must be a date")


class EmploymentStatus(Enum):
    NO_CONFIRMED_OFFER = "no_confirmed_offer"
    ACCEPTED_OFFER = "accepted_offer"


@dataclass(frozen=True)
class EmploymentState:
    status: EmploymentStatus
    uncertainty_ref: Ref
    offer: Optional[EmploymentOffer] = None
    source_event_ref: Optional[Ref] = None

    def __post_init__(self) -> None:
        enum_value(self.status, EmploymentStatus)
        if self.status is EmploymentStatus.ACCEPTED_OFFER:
            if self.offer is None or self.source_event_ref is None:
                raise ValueError("Accepted employment requires offer terms and event provenance")
        elif self.offer is not None or self.source_event_ref is not None:
            raise ValueError("Unresolved employment cannot claim accepted terms")


@dataclass(frozen=True)
class FinancialState:
    """Partial decision-sufficient snapshot, not a complete balance sheet.

    Liquidity eligibility is supplied explicitly; this model does not infer it
    from account balances or subtract earmarks itself.
    """

    ref: Ref
    owner_id: str
    as_of: date
    available_emergency_liquidity: FinancialAmount
    monthly_essential_outflow: FinancialAmount
    goal_refs: Tuple[Ref, ...] = ()
    employment: Optional[EmploymentState] = None

    def __post_init__(self) -> None:
        nonempty(self.owner_id, "owner_id")
        if type(self.as_of) is not date:
            raise ValueError("as_of must be a calendar date")
        left = self.available_emergency_liquidity.value
        right = self.monthly_essential_outflow.value
        left._check(right)
        whole(left.minor_units, "available emergency liquidity")
        whole(right.minor_units, "monthly essential outflow")
        typed_tuple(self.goal_refs, Ref, "goal_refs")


class InterpretationOutcome(Enum):
    INFER = "infer"
    ASK = "ask"
    BRANCH_RANGE = "branch_range"
    KEEP_UNRESOLVED = "keep_unresolved"


@dataclass(frozen=True)
class EventInterpretation:
    """Candidate meaning, distinct from the evidence and the resulting event."""

    ref: Ref
    owner_id: str
    evidence_refs: Tuple[Ref, ...]
    candidate_meaning: str
    outcome: InterpretationOutcome
    confidence: Confidence
    rationale: str

    def __post_init__(self) -> None:
        nonempty(self.owner_id, "owner_id")
        typed_tuple(self.evidence_refs, Ref, "evidence_refs")
        if not self.evidence_refs:
            raise ValueError("An interpretation needs evidence")
        nonempty(self.candidate_meaning, "candidate_meaning")
        nonempty(self.rationale, "rationale")
        enum_value(self.outcome, InterpretationOutcome)


@dataclass(frozen=True)
class FinancialEvent:
    """Recorded occurrence; construction does not accept its meaning or apply it."""

    ref: Ref
    owner_id: str
    occurred_on: date
    recorded_at: datetime
    description: str
    interpretation_ref: Ref
    confidence: Confidence
    affected_state_ref: Optional[Ref] = None
    employment_offer: Optional[EmploymentOffer] = None
    resolves_assumption_ref: Optional[Ref] = None

    def __post_init__(self) -> None:
        nonempty(self.owner_id, "owner_id")
        nonempty(self.description, "description")
        aware(self.recorded_at, "recorded_at")
        if type(self.occurred_on) is not date:
            raise ValueError("occurred_on must be a calendar date")


class DecisionStatus(Enum):
    EXPLORING = "exploring"
    DEFERRED = "deferred"
    SELECTED = "selected"


@dataclass(frozen=True)
class DecisionOption:
    """A user-controllable alternative, not an uncertain future outcome."""

    ref: Ref
    decision_ref: Ref
    label: str
    description: str
    goal_change: Optional["DelayGoalChange"] = None
    planned_outflow: Optional[Money] = None

    def __post_init__(self) -> None:
        nonempty(self.label, "label")
        nonempty(self.description, "description")


@dataclass(frozen=True)
class Decision:
    ref: Ref
    owner_id: str
    question: str
    state_ref: Ref
    status: DecisionStatus
    selected_option_ref: Optional[Ref] = None

    def __post_init__(self) -> None:
        nonempty(self.owner_id, "owner_id")
        nonempty(self.question, "question")
        enum_value(self.status, DecisionStatus)
        if (self.status is DecisionStatus.SELECTED) != (self.selected_option_ref is not None):
            raise ValueError("Exactly a selected decision must name the user's selected option")


class IfLineStatus(Enum):
    ACTIVE = "active"
    ARCHIVED = "archived"


@dataclass(frozen=True)
class IfLine:
    ref: Ref
    decision_ref: Ref
    state_ref: Ref
    label: str
    assumption_refs: Tuple[Ref, ...]
    status: IfLineStatus
    archive_reason: Optional[str] = None
    unemployment_months: Optional[int] = None
    employment_uncertainty_ref: Optional[Ref] = None
    resolution_event_ref: Optional[Ref] = None

    def __post_init__(self) -> None:
        nonempty(self.label, "label")
        typed_tuple(self.assumption_refs, Ref, "assumption_refs")
        if not self.assumption_refs:
            raise ValueError("If Lines must expose assumptions")
        enum_value(self.status, IfLineStatus)
        if self.status is IfLineStatus.ARCHIVED:
            nonempty(self.archive_reason, "archive_reason")
        elif self.archive_reason is not None:
            raise ValueError("Active If Lines cannot carry an archive reason")
        if self.unemployment_months is not None:
            whole(self.unemployment_months, "unemployment_months", 1)
            if self.employment_uncertainty_ref not in self.assumption_refs:
                raise ValueError("An unemployment line must identify its driving assumption")
        if self.resolution_event_ref is not None and self.status is not IfLineStatus.ARCHIVED:
            raise ValueError("A resolved line must no longer be active")


class PlanStatus(Enum):
    # Lifecycle only. Financial condition and On Track assessments remain separate.
    DRAFT = "draft"
    ADOPTED = "adopted"
    SUPERSEDED = "superseded"


@dataclass(frozen=True)
class GoalAllocation:
    goal_ref: Ref
    monthly_contribution: Money

    def __post_init__(self) -> None:
        whole(self.monthly_contribution.minor_units, "monthly contribution")


@dataclass(frozen=True)
class PlannedOutflow:
    decision_ref: Ref
    amount: Money

    def __post_init__(self) -> None:
        whole(self.amount.minor_units, "planned outflow")


@dataclass(frozen=True)
class LivingPlan:
    ref: Ref
    owner_id: str
    state_ref: Ref
    decision_ref: Ref
    assumption_refs: Tuple[Ref, ...]
    allocations: Tuple[GoalAllocation, ...]
    status: PlanStatus
    previous_plan_ref: Optional[Ref] = None
    planned_outflows: Tuple[PlannedOutflow, ...] = ()

    def __post_init__(self) -> None:
        nonempty(self.owner_id, "owner_id")
        typed_tuple(self.assumption_refs, Ref, "assumption_refs")
        typed_tuple(self.allocations, GoalAllocation, "allocations")
        typed_tuple(self.planned_outflows, PlannedOutflow, "planned_outflows")
        enum_value(self.status, PlanStatus)
        if len({a.goal_ref.id for a in self.allocations}) != len(self.allocations):
            raise ValueError("A plan cannot allocate to duplicate versions of the same goal")
        if self.previous_plan_ref is not None:
            if self.previous_plan_ref.id != self.ref.id or self.previous_plan_ref.version >= self.ref.version:
                raise ValueError("Previous plan must be an earlier version of the same plan")


@dataclass(frozen=True)
class DelayGoalChange:
    """Typed goal-delay payload; lifecycle.py supplies the scoped executor."""

    goal_ref: Ref
    previous_target_date: date
    proposed_target_date: date

    def __post_init__(self) -> None:
        if type(self.previous_target_date) is not date or type(self.proposed_target_date) is not date:
            raise ValueError("Goal dates must be calendar dates")
        if self.proposed_target_date <= self.previous_target_date:
            raise ValueError("A delay must move the target date later")


@dataclass(frozen=True)
class PlanAction:
    ref: Ref
    owner_id: str
    decision_ref: Ref
    expected_plan_ref: Ref
    change: DelayGoalChange
    rationale: str
    option_ref: Optional[Ref] = None
    planned_outflow: Optional[Money] = None

    def __post_init__(self) -> None:
        nonempty(self.owner_id, "owner_id")
        nonempty(self.rationale, "rationale")
        if not isinstance(self.change, DelayGoalChange):
            raise ValueError("Foundation PlanAction supports only a proposed goal delay")


@dataclass(frozen=True)
class PlanAuthorization:
    """Scoped consent record; existence is not authentication or permission to run."""

    ref: Ref
    owner_id: str
    action_ref: Ref
    expected_plan_ref: Ref
    granted_at: datetime

    def __post_init__(self) -> None:
        nonempty(self.owner_id, "owner_id")
        aware(self.granted_at, "granted_at")


class ExecutionOutcome(Enum):
    APPLIED = "applied"
    REJECTED = "rejected"


@dataclass(frozen=True)
class ExecutionLog:
    """Receipt committed atomically by the Phase 2 in-memory command service."""

    ref: Ref
    action_ref: Ref
    authorization_ref: Optional[Ref]
    before_plan_ref: Ref
    after_plan_ref: Optional[Ref]
    recorded_at: datetime
    outcome: ExecutionOutcome
    reason: str
    decision_ref: Optional[Ref] = None
    option_ref: Optional[Ref] = None
    before_goal_ref: Optional[Ref] = None
    after_goal_ref: Optional[Ref] = None
    before_state_ref: Optional[Ref] = None
    after_state_ref: Optional[Ref] = None
    changed_fields: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        aware(self.recorded_at, "recorded_at")
        nonempty(self.reason, "reason")
        enum_value(self.outcome, ExecutionOutcome)
        typed_tuple(self.changed_fields, str, "changed_fields")
        if self.outcome is ExecutionOutcome.APPLIED:
            if self.authorization_ref is None or self.after_plan_ref is None:
                raise ValueError("An applied receipt needs consent and a resulting plan version")
            if self.after_plan_ref.id != self.before_plan_ref.id or self.after_plan_ref.version <= self.before_plan_ref.version:
                raise ValueError("An applied receipt must advance the same plan's version")
        elif self.after_plan_ref is not None:
            raise ValueError("A rejected receipt must not claim a resulting plan")

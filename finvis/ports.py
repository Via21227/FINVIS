"""Replaceable provisional boundaries. Phase 2 supplies one materiality policy."""

from dataclasses import dataclass
from enum import Enum
from typing import Protocol, Tuple

from .domain import FinancialEvent, FinancialState, LivingPlan, Ref


class MaterialityDisposition(Enum):
    REEVALUATE = "reevaluate"
    RECORD_ONLY = "record_only"
    NEEDS_UNDERSTANDING = "needs_understanding"


@dataclass(frozen=True)
class MaterialityAssessment:
    disposition: MaterialityDisposition
    rationale: str
    considered_event_refs: Tuple[Ref, ...]
    policy_ref: Ref


class MaterialityPolicy(Protocol):
    def assess(
        self, before: FinancialState, after: FinancialState,
        relevant_history: Tuple[FinancialEvent, ...], plan: LivingPlan,
    ) -> MaterialityAssessment:
        """History supports accumulated change; no universal threshold chosen."""
        ...


class LayerOutcome(Enum):
    NOT_EVALUATED = "not_evaluated"
    MEETS_RULE = "meets_rule"
    FAILS_RULE = "fails_rule"
    INSUFFICIENT_UNDERSTANDING = "insufficient_understanding"


@dataclass(frozen=True)
class LayerFinding:
    outcome: LayerOutcome
    rule_refs: Tuple[Ref, ...]
    rationale: str


@dataclass(frozen=True)
class FinancialJudgment:
    solvency: LayerFinding
    resilience: LayerFinding
    trade_off: LayerFinding
    # No scalar aggregate, overall score or rule implementation.


class FinancialRuleSet(Protocol):
    def evaluate(self, state: FinancialState, plan: LivingPlan) -> FinancialJudgment:
        """Professor-reviewed financial policies belong behind this boundary."""
        ...

"""Validated, non-authoritative semantic output for Agent Understanding v0.1."""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Mapping, Optional, Tuple


SCHEMA_VERSION = "finvis.agent-understanding.v0.1"
FACT_TYPES = {
    "current_asset", "earmarked_current_asset", "expected_future_inflow",
    "planned_expense", "actual_expense", "actual_inflow",
    "contemplated_decision", "context", "monthly_essential_outflow",
}
ACTUAL_FACT_TYPES = {"actual_expense", "actual_inflow"}
PRECISIONS = {"exact", "approximate", "range", "unknown"}
TEMPORAL_STATUSES = {"current", "future", "planned", "actual", "contemplated", "context"}
INTENTS = {
    "replace_financial_state", "update_financial_state", "correct_information",
    "evaluate_decision", "provide_context", "ask_question",
}
DESTRUCTIVE_ACTIONS = {"replace_financial_state", "clear_financial_state"}


class InterpretationError(ValueError):
    """The provider output cannot safely become a candidate interpretation."""

    def __init__(self, message: str, *, category: str = "semantic_validation",
                 path: str = "$", rule: Optional[str] = None,
                 repairable: bool = False):
        super().__init__(message)
        self.category = category
        self.path = path
        self.rule = rule or message
        self.repairable = repairable


def interpretation_json_schema() -> Dict[str, Any]:
    def amount_object(precision: str, value_type: Any,
                      lower_type: Any, upper_type: Any) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "currency": {"type": ["string", "null"]},
                "value": {"type": value_type},
                "range_min": {"type": lower_type},
                "range_max": {"type": upper_type},
                "precision": {"type": "string", "enum": [precision]},
            },
            "required": ["currency", "value", "range_min", "range_max", "precision"],
            "additionalProperties": False,
        }

    # Keep this within the simple object/array/type/enum/anyOf subset exercised
    # by the local OpenAI-compatible structured-output runtime. The same four
    # branches are still rechecked by CandidateAmount.parse after generation.
    amount = {"anyOf": [
        {"type": "null"},
        amount_object("exact", "string", "null", "null"),
        amount_object("approximate", "string", "null", "null"),
        amount_object("range", "null", "string", "string"),
        amount_object("unknown", "null", "null", "null"),
    ]}
    fact = {
        "type": "object",
        "properties": {
            "id": {"type": "string"},
            "type": {"type": "string", "enum": sorted(FACT_TYPES)},
            "label": {"type": "string"},
            "account": {"type": ["string", "null"]},
            "amount": amount,
            "earmark": {"type": ["string", "null"]},
            "purpose": {"type": ["string", "null"]},
            "temporal_status": {"type": "string", "enum": sorted(TEMPORAL_STATUSES)},
            "timing_note": {"type": ["string", "null"]},
            "source": {"type": "string", "enum": ["user_report"]},
        },
        "required": ["id", "type", "label", "account", "amount", "earmark", "purpose",
                     "temporal_status", "timing_note", "source"],
        "additionalProperties": False,
        "anyOf": [
            {"properties": {"type": {"enum": ["monthly_essential_outflow"]},
                            "temporal_status": {"enum": ["current"]}}},
            {"properties": {"type": {"enum": ["current_asset"]},
                            "temporal_status": {"enum": ["current"]}}},
            {"properties": {"type": {"enum": ["earmarked_current_asset"]},
                            "temporal_status": {"enum": ["current"]}}},
            {"properties": {"type": {"enum": ["expected_future_inflow"]},
                            "temporal_status": {"enum": ["future"]}}},
            {"properties": {"type": {"enum": ["planned_expense"]},
                            "temporal_status": {"enum": ["planned"]}}},
            {"properties": {"type": {"enum": ["actual_expense"]},
                            "temporal_status": {"enum": ["actual"]}}},
            {"properties": {"type": {"enum": ["actual_inflow"]},
                            "temporal_status": {"enum": ["actual"]}}},
            {"properties": {"type": {"enum": ["contemplated_decision"]},
                            "temporal_status": {"enum": ["contemplated"]}}},
            {"properties": {"type": {"enum": ["context"]},
                            "temporal_status": {"enum": ["context"]}}},
        ],
    }
    return {
        "type": "object",
        "properties": {
            "schema_version": {"type": "string", "enum": [SCHEMA_VERSION]},
            "language": {"type": "string", "enum": ["en", "zh-CN", "mixed", "unknown"]},
            "user_intents": {"type": "array", "items": {"type": "string", "enum": sorted(INTENTS)}},
            "default_currency": {"type": ["string", "null"]},
            "candidate_facts": {"type": "array", "items": fact},
            "destructive_actions": {"type": "array", "items": {"type": "string", "enum": sorted(DESTRUCTIVE_ACTIONS)}},
            "assumptions": {"type": "array", "items": {"type": "string"}},
            "uncertainties": {"type": "array", "items": {"type": "string"}},
            "conflicts": {"type": "array", "items": {"type": "string"}},
            "clarification_questions": {"type": "array", "items": {"type": "string"}},
            "unsupported_requests": {"type": "array", "items": {"type": "string"}},
            "user_question": {"type": ["string", "null"]},
            "interpretation_notes": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["schema_version", "language", "user_intents", "default_currency",
                     "candidate_facts", "destructive_actions", "assumptions", "uncertainties",
                     "conflicts", "clarification_questions", "unsupported_requests",
                     "user_question", "interpretation_notes"],
        "additionalProperties": False,
    }


def _text(value: Any, name: str, nullable: bool = False) -> Optional[str]:
    if nullable and value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise InterpretationError(f"{name} must be non-empty text")
    return value.strip()


def _string_list(value: Any, name: str, allowed=None) -> Tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
        raise InterpretationError(f"{name} must be a list of non-empty strings")
    result = tuple(item.strip() for item in value)
    if allowed is not None and any(item not in allowed for item in result):
        raise InterpretationError(f"{name} contains an unsupported value")
    return result


def _decimal(value: Any, name: str) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str):
        raise InterpretationError(f"{name} must be a decimal string or null")
    try:
        parsed = Decimal(value)
    except InvalidOperation as error:
        raise InterpretationError(f"{name} is not a decimal") from error
    if not parsed.is_finite() or parsed < 0:
        raise InterpretationError(f"{name} must be finite and non-negative")
    return value


@dataclass(frozen=True)
class CandidateAmount:
    currency: Optional[str]
    value: Optional[str]
    range_min: Optional[str]
    range_max: Optional[str]
    precision: str

    @classmethod
    def parse(cls, value: Any, name: str) -> Optional["CandidateAmount"]:
        if value is None:
            return None
        required = {"currency", "value", "range_min", "range_max", "precision"}
        if not isinstance(value, dict) or set(value) != required:
            raise InterpretationError(f"{name} has invalid fields")
        currency = _text(value["currency"], f"{name}.currency", nullable=True)
        precision = value["precision"]
        if precision not in PRECISIONS:
            raise InterpretationError(f"{name}.precision is unsupported")
        amount = cls(currency, _decimal(value["value"], f"{name}.value"),
                     _decimal(value["range_min"], f"{name}.range_min"),
                     _decimal(value["range_max"], f"{name}.range_max"), precision)
        if precision in {"exact", "approximate"} and (amount.value is None or amount.range_min is not None or amount.range_max is not None):
            repairable = amount.value is not None
            raise InterpretationError(
                f"{name} scalar precision requires value only",
                category="amount_representation", path=name,
                rule=f"{precision} requires non-null value and null range_min/range_max",
                repairable=repairable)
        if precision == "range" and (amount.value is not None or amount.range_min is None or amount.range_max is None):
            repairable = amount.range_min is not None and amount.range_max is not None
            raise InterpretationError(
                f"{name} range precision requires both bounds",
                category="amount_representation", path=name,
                rule="range requires null value and non-null range_min/range_max",
                repairable=repairable)
        if precision == "unknown" and any((amount.value, amount.range_min, amount.range_max)):
            raise InterpretationError(
                f"{name} unknown precision cannot contain a value",
                category="amount_representation", path=name,
                rule="unknown requires null value, range_min and range_max",
                repairable=True)
        return amount

    def as_dict(self):
        return dict(currency=self.currency, value=self.value, range_min=self.range_min,
                    range_max=self.range_max, precision=self.precision)


@dataclass(frozen=True)
class CandidateFact:
    id: str
    type: str
    label: str
    account: Optional[str]
    amount: Optional[CandidateAmount]
    earmark: Optional[str]
    purpose: Optional[str]
    temporal_status: str
    timing_note: Optional[str]
    source: str

    @classmethod
    def parse(cls, value: Any, index: int) -> "CandidateFact":
        required = {"id", "type", "label", "account", "amount", "earmark", "purpose",
                    "temporal_status", "timing_note", "source"}
        if not isinstance(value, dict) or set(value) != required:
            raise InterpretationError(f"candidate_facts[{index}] has invalid fields")
        fact_type, temporal = value["type"], value["temporal_status"]
        if fact_type not in FACT_TYPES or temporal not in TEMPORAL_STATUSES or value["source"] != "user_report":
            raise InterpretationError(f"candidate_facts[{index}] has invalid semantic enums")
        expected_temporal = {
            "monthly_essential_outflow": "current",
            "current_asset": "current", "earmarked_current_asset": "current",
            "expected_future_inflow": "future", "planned_expense": "planned",
            "actual_expense": "actual", "actual_inflow": "actual",
            "contemplated_decision": "contemplated", "context": "context",
        }[fact_type]
        if temporal != expected_temporal:
            raise InterpretationError(
                f"candidate_facts[{index}] conflicts with its temporal type",
                category="fact_temporal_status",
                path=f"candidate_facts[{index}].temporal_status",
                rule=f"{fact_type} requires temporal_status={expected_temporal}",
                repairable=True)
        if fact_type == 'monthly_essential_outflow' and (value['amount'] is None or value['account'] is not None or value['earmark'] is not None):
            raise InterpretationError('Monthly essential outflow requires an amount and no account or earmark')
        return cls(_text(value["id"], "fact.id"), fact_type, _text(value["label"], "fact.label"),
                   _text(value["account"], "fact.account", True),
                   CandidateAmount.parse(value["amount"], f"candidate_facts[{index}].amount"),
                   _text(value["earmark"], "fact.earmark", True), _text(value["purpose"], "fact.purpose", True),
                   temporal, _text(value["timing_note"], "fact.timing_note", True), "user_report")

    def as_dict(self):
        return dict(id=self.id, type=self.type, label=self.label, account=self.account,
                    amount=self.amount.as_dict() if self.amount else None, earmark=self.earmark,
                    purpose=self.purpose, temporal_status=self.temporal_status,
                    timing_note=self.timing_note, source=self.source)


@dataclass(frozen=True)
class CandidateInterpretation:
    language: str
    user_intents: Tuple[str, ...]
    default_currency: Optional[str]
    candidate_facts: Tuple[CandidateFact, ...]
    destructive_actions: Tuple[str, ...]
    assumptions: Tuple[str, ...]
    uncertainties: Tuple[str, ...]
    conflicts: Tuple[str, ...]
    clarification_questions: Tuple[str, ...]
    unsupported_requests: Tuple[str, ...]
    user_question: Optional[str]
    interpretation_notes: Tuple[str, ...]

    @classmethod
    def parse(cls, value: Any) -> "CandidateInterpretation":
        required = set(interpretation_json_schema()["required"])
        if not isinstance(value, dict) or set(value) != required or value.get("schema_version") != SCHEMA_VERSION:
            raise InterpretationError("interpretation has invalid top-level fields or version")
        language = value["language"]
        if language not in {"en", "zh-CN", "mixed", "unknown"}:
            raise InterpretationError("language is unsupported")
        facts = value["candidate_facts"]
        if not isinstance(facts, list) or len(facts) > 100:
            raise InterpretationError("candidate_facts must be a bounded list")
        parsed = tuple(CandidateFact.parse(item, index) for index, item in enumerate(facts))
        ids = [fact.id for fact in parsed]
        if len(ids) != len(set(ids)):
            raise InterpretationError("candidate fact IDs must be unique")
        currency = _text(value["default_currency"], "default_currency", True)
        return cls(language, _string_list(value["user_intents"], "user_intents", INTENTS), currency,
                   parsed, _string_list(value["destructive_actions"], "destructive_actions", DESTRUCTIVE_ACTIONS),
                   _string_list(value["assumptions"], "assumptions"),
                   _string_list(value["uncertainties"], "uncertainties"),
                   _string_list(value["conflicts"], "conflicts"),
                   _string_list(value["clarification_questions"], "clarification_questions"),
                   _string_list(value["unsupported_requests"], "unsupported_requests"),
                   _text(value["user_question"], "user_question", True),
                   _string_list(value["interpretation_notes"], "interpretation_notes"))

    def as_dict(self) -> Dict[str, Any]:
        return dict(schema_version=SCHEMA_VERSION, language=self.language,
                    user_intents=list(self.user_intents), default_currency=self.default_currency,
                    candidate_facts=[fact.as_dict() for fact in self.candidate_facts],
                    destructive_actions=list(self.destructive_actions), assumptions=list(self.assumptions),
                    uncertainties=list(self.uncertainties), conflicts=list(self.conflicts),
                    clarification_questions=list(self.clarification_questions),
                    unsupported_requests=list(self.unsupported_requests), user_question=self.user_question,
                    interpretation_notes=list(self.interpretation_notes))


def reconcile_candidate(interpretation: CandidateInterpretation) -> Dict[str, Any]:
    """Describe safe commit support without changing the Phase 2 financial domain."""
    changes = []
    reason = "current_core_cannot_represent_account_earmark_timing_precision_safely"
    for fact in interpretation.candidate_facts:
        changes.append({"fact_id": fact.id, "committable": False, "reason": reason})
    return {
        "operation": "replace_financial_state" if interpretation.destructive_actions else "update_financial_state",
        "requires_confirmation": True,
        "committable_count": 0,
        "candidate_only_count": len(changes),
        "changes": changes,
    }

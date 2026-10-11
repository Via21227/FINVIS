"""Explicit live LM Studio evaluation; normal tests never call a model server."""

import argparse
from collections import Counter
from dataclasses import replace
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path

from .agent_providers import AgentProviderConfig, LMStudioProvider, ProviderError
from .agent_understanding import AgentUnderstandingService


ROOT = Path(__file__).resolve().parent.parent


def _decimal_equal(actual, expected):
    if actual is None or expected is None:
        return actual is expected
    try:
        return Decimal(str(actual)) == Decimal(str(expected))
    except InvalidOperation:
        return False


def _text_equal(actual, expected):
    if actual is None or expected is None:
        return actual is expected
    return " ".join(actual.casefold().split()) == " ".join(expected.casefold().split())


def _amount_matches(actual, expected, *, precision=False):
    if expected is None:
        return actual is None
    if actual is None:
        return False
    if "currency" in expected and actual.currency != expected["currency"]:
        return False
    for field in ("value", "range_min", "range_max"):
        if field in expected and not _decimal_equal(getattr(actual, field), expected[field]):
            return False
    return not precision or "precision" not in expected or actual.precision == expected["precision"]


def _fact_matches(fact, expected, *, classification=False, temporal=False,
                  precision=False, relationship=False):
    if "amount" in expected and not _amount_matches(
            fact.amount, expected["amount"], precision=precision):
        return False
    allowed_accounts = expected.get("allowed_accounts")
    if allowed_accounts is not None:
        if not any(_text_equal(fact.account, value) for value in allowed_accounts):
            return False
    elif "account" in expected and not _text_equal(fact.account, expected["account"]):
        return False
    if classification:
        allowed = expected.get("allowed_types")
        if allowed is None and "type" in expected:
            allowed = [expected["type"]]
        if allowed is not None and fact.type not in allowed:
            return False
    if temporal:
        allowed = expected.get("allowed_temporal_statuses")
        if allowed is None and "temporal_status" in expected:
            allowed = [expected["temporal_status"]]
        if allowed is not None and fact.temporal_status not in allowed:
            return False
    if relationship:
        for field in ("earmark", "purpose"):
            if field in expected and not _text_equal(getattr(fact, field), expected[field]):
                return False
    return True


def _matching(specifications, facts, predicate):
    """Return a one-to-one match without depending on fixture or fact order."""
    specifications = tuple(specifications)
    facts = tuple(facts)

    def search(index, available):
        if index == len(specifications):
            return ()
        for fact_index in available:
            if predicate(facts[fact_index], specifications[index]):
                rest = search(index + 1, available - {fact_index})
                if rest is not None:
                    return (fact_index,) + rest
        return None

    return search(0, set(range(len(facts))))


def _inventory_matches(specifications, facts, predicate, expected):
    matched = _matching(specifications, facts, predicate)
    if matched is None:
        return False
    if expected.get("allow_additional_facts", False):
        return True
    allowed = set(expected.get("allowed_additional_types", ()))
    unmatched = set(range(len(facts))) - set(matched)
    return all(facts[index].type in allowed for index in unmatched)


def _legacy_score(interpretation, expected):
    """Compatibility for older evaluation fixtures without content-level facts."""
    actual_types = [fact.type for fact in interpretation.candidate_facts]
    expected_types = expected.get("types", [])
    classification = Counter(actual_types) == Counter(expected_types)
    temporal = all(fact.temporal_status == {
        "current_asset":"current", "earmarked_current_asset":"current",
        "expected_future_inflow":"future", "planned_expense":"planned",
        "actual_expense":"actual", "actual_inflow":"actual",
        "contemplated_decision":"contemplated", "context":"context",
    }[fact.type] for fact in interpretation.candidate_facts)
    precision = all(fact.amount is None or fact.amount.precision in {
        "exact", "approximate", "range", "unknown"}
        for fact in interpretation.candidate_facts)
    earmarks = all(fact.earmark or fact.purpose for fact in interpretation.candidate_facts
                   if fact.type == "earmarked_current_asset")
    return classification, classification, temporal, precision, earmarks


def score(candidate, expected):
    interpretation = candidate.interpretation
    facts = interpretation.candidate_facts
    specifications = expected.get("facts")
    if specifications is None:
        extraction, classification, temporal, precision, earmarks = _legacy_score(
            interpretation, expected)
    else:
        extraction = _inventory_matches(
            specifications, facts, lambda fact, item: _fact_matches(fact, item),
            expected)
        classification = _inventory_matches(
            specifications, facts,
            lambda fact, item: _fact_matches(fact, item, classification=True),
            expected)
        temporal = _matching(
            specifications, facts,
            lambda fact, item: _fact_matches(fact, item, temporal=True)) is not None
        precision = _matching(
            specifications, facts,
            lambda fact, item: _fact_matches(fact, item, precision=True)) is not None
        earmark_specs = [item for item in specifications
                         if item.get("type") == "earmarked_current_asset"
                         or "earmarked_current_asset" in item.get("allowed_types", ())]
        emitted_earmarks = [fact for fact in facts if fact.type == "earmarked_current_asset"]
        earmarks = len(earmark_specs) == len(emitted_earmarks) and _matching(
            earmark_specs, emitted_earmarks,
            lambda fact, item: _fact_matches(
                fact, item, classification=True, relationship=True)) is not None

        for excluded in expected.get("unrepresentable_facts", []):
            represented = [fact for fact in facts if _fact_matches(fact, excluded)]
            forbidden_types = set(excluded.get("forbidden_types", ()))
            forbidden_temporal = set(excluded.get("forbidden_temporal_statuses", ()))
            if any(fact.type in forbidden_types for fact in represented):
                classification = False
            if any(fact.temporal_status in forbidden_temporal for fact in represented):
                temporal = False
            if represented and any(not _fact_matches(fact, excluded, precision=True)
                                   for fact in represented):
                precision = False

    destructive = bool(interpretation.destructive_actions) == expected.get("destructive", False)
    clarification_stage = expected.get("clarification_stage")
    if clarification_stage is None:
        clarification_stage = "extraction" if expected.get("clarification") else "none"
    clarification = (bool(interpretation.clarification_questions)
                     if clarification_stage == "extraction" else True)
    unsupported_stage = expected.get("unsupported_stage")
    if unsupported_stage is None:
        unsupported_stage = "extraction" if expected.get("unsupported") else "none"
    unsupported = bool(interpretation.unsupported_requests) == (unsupported_stage == "extraction")
    return {"schema_valid": True, "semantic_extraction": extraction,
            "financial_classification": classification, "temporal_integrity": temporal,
            "precision_integrity": precision, "earmark_integrity": earmarks,
            "destructive_action": destructive, "clarification": clarification,
            "unsupported_action_discipline": unsupported,
            "currency": interpretation.default_currency == expected.get("default_currency")}


def run(model, base_config=None):
    cases = json.loads((ROOT / "inputs/agent_eval_cases.json").read_text())
    config = replace(base_config or AgentProviderConfig.from_env(), provider="lmstudio", lmstudio_model=model)
    service = AgentUnderstandingService(LMStudioProvider(config))
    rows = []
    for case in cases:
        try:
            candidate = service.interpret(case["text"], "evaluation", 0)
            rows.append({"case": case["id"], "model": candidate.model,
                         "latency_ms": candidate.latency_ms, "dimensions": score(candidate, case["expect"])})
        except ProviderError as error:
            rows.append({"case": case["id"], "model": model, "error": error.category,
                         "diagnostics": error.diagnostics,
                         "dimensions": {"schema_valid": False}})
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", action="append", required=True,
                        help="Exact LM Studio model ID; repeat to compare models")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    config = AgentProviderConfig.from_env()
    report = {"kind":"FINVIS Agent Understanding v0.1 live evaluation",
              "provider":"lmstudio",
              "runtime":{"reasoning_effort":config.lmstudio_reasoning_effort,
                         "max_output_tokens":config.max_output_tokens,
                         "timeout_seconds":config.timeout_seconds},
              "models":args.model,
              "results":[row for model in args.model for row in run(model, config)]}
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(rendered)
    print(rendered, end="")


if __name__ == "__main__":
    main()

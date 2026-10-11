"""Actual-occurrence Candidate semantics and reviewed Slice G/E routing."""

from copy import deepcopy
from datetime import date
import unittest

from finvis.agent_eval import score
from finvis.agent_reconciliation import FactReview, OccurrenceReview, ReviewRequest
from finvis.agent_schema import (CandidateInterpretation, InterpretationError,
    SCHEMA_VERSION, interpretation_json_schema)
from finvis.agent_understanding import AgentUnderstandingService, SYSTEM_PROMPT
from finvis.domain import Currency
from finvis.world_commands import record_ref
from finvis.world_planning import OccurrenceKind
from finvis.world_settlement import BalanceBasis, SettleOccurrence
from finvis.world_store import current_assets
from finvis.world_values import (AmountEstimate, FreshnessAssessment,
    FreshnessStatus, TemporalSpec, money_from_major_units)
from tests.test_agent_reconciliation import AT, finish, setup, source
from tests.test_agent_understanding import SequenceProvider, schema_accepts
from tests.test_world_assets import exact
from tests.test_world_mutation import store


TEMPORAL = {
    "actual_expense":"actual", "actual_inflow":"actual",
    "planned_expense":"planned", "expected_future_inflow":"future",
}


def candidate_fact(kind, value="125", *, account="Old wallet",
                   currency="CNY", precision="exact", label="Bicycle repair",
                   timing="yesterday"):
    return {"id":"occurrence", "type":kind, "label":label, "account":account,
            "amount":{"currency":currency, "value":str(value),
                      "range_min":None, "range_max":None, "precision":precision},
            "earmark":None, "purpose":label, "temporal_status":TEMPORAL[kind],
            "timing_note":timing, "source":"user_report"}


def candidate_raw(kind, **kwargs):
    language = kwargs.pop("language", "en")
    currency = kwargs.get("currency", "CNY")
    return {"schema_version":SCHEMA_VERSION, "language":language,
            "user_intents":["update_financial_state"], "default_currency":currency,
            "candidate_facts":[candidate_fact(kind, **kwargs)],
            "destructive_actions":[], "assumptions":[], "uncertainties":[],
            "conflicts":[], "clarification_questions":[],
            "unsupported_requests":[], "user_question":None,
            "interpretation_notes":[]}


def selections(text, fact_text, account_text=None):
    result = list(source(text, fact_text))
    if account_text is not None:
        result.extend(source(text, account_text, "account"))
    return tuple(result)


def actual_review(text, kind, *, position_ref=None, amount_value="125",
                  precision="exact", account_text=None, basis=BalanceBasis.PRIOR,
                  fact_id="occurrence", timing_text="yesterday"):
    fact_text = text.split(" Coverage:")[0]
    amount = (AmountEstimate.exact_reported(
                  money_from_major_units(str(amount_value), Currency.CNY))
              if precision == "exact" else
              AmountEstimate.approximate(
                  money_from_major_units(str(amount_value), Currency.CNY)))
    coverage_text = text.split("Coverage: ", 1)[1] if "Coverage: " in text else None
    return OccurrenceReview(
        kind=kind, amount=amount,
        timing=TemporalSpec.known_date(date(2026, 9, 21), timing_text),
        sources=selections(text, fact_text, account_text),
        position_ref=position_ref,
        basis=basis if position_ref is not None else None,
        coverage_sources=source(text, coverage_text, "coverage") if coverage_text else (),
        coverage_rationale=coverage_text,
        freshness=FreshnessAssessment(
            FreshnessStatus.CURRENT, "Explicitly reviewed current report", AT),
        fact_id=fact_id,
    )


class ActualOccurrenceCandidateTests(unittest.TestCase):
    def test_actual_paid_expense_has_native_actual_role(self):
        parsed = CandidateInterpretation.parse(candidate_raw("actual_expense"))
        fact = parsed.candidate_facts[0]
        self.assertEqual((fact.type, fact.temporal_status),
                         ("actual_expense", "actual"))

    def test_actual_received_inflow_has_native_actual_role(self):
        parsed = CandidateInterpretation.parse(candidate_raw(
            "actual_inflow", label="Freelance payment"))
        fact = parsed.candidate_facts[0]
        self.assertEqual((fact.type, fact.temporal_status),
                         ("actual_inflow", "actual"))

    def test_wire_schema_accepts_actual_pairs_and_rejects_force_fit_status(self):
        for kind in ("actual_expense", "actual_inflow"):
            with self.subTest(kind=kind):
                raw = candidate_raw(kind)
                self.assertTrue(schema_accepts(raw, interpretation_json_schema()))
                raw["candidate_facts"][0]["temporal_status"] = "planned"
                self.assertFalse(schema_accepts(raw, interpretation_json_schema()))

    def test_model_visible_contract_names_actual_and_account_direction(self):
        for text in ("actual_expense", "actual_inflow", "already moved",
                     "reported source", "reported destination",
                     "Never force actual evidence into planned or future"):
            self.assertIn(text, SYSTEM_PROMPT)

    def test_actual_provider_output_remains_non_authoritative_candidate(self):
        service = AgentUnderstandingService(SequenceProvider([
            candidate_raw("actual_expense")]))
        envelope = service.interpret(
            "I paid 125 CNY yesterday from Old wallet.", "generation", 0)
        self.assertEqual(envelope.status, "awaiting_confirmation")
        self.assertEqual(envelope.proposed_change["committable_count"], 0)
        self.assertEqual(envelope.interpretation.candidate_facts[0].type,
                         "actual_expense")

    def test_planned_and_future_facts_remain_distinct(self):
        planned = CandidateInterpretation.parse(candidate_raw(
            "planned_expense", timing="next month"))
        future = CandidateInterpretation.parse(candidate_raw(
            "expected_future_inflow", label="Salary", timing="next week"))
        self.assertEqual(planned.candidate_facts[0].temporal_status, "planned")
        self.assertEqual(future.candidate_facts[0].temporal_status, "future")

    def test_ambiguous_or_conflicting_actual_status_fails_closed(self):
        raw = candidate_raw("actual_expense")
        raw["candidate_facts"][0]["temporal_status"] = "planned"
        with self.assertRaises(InterpretationError):
            CandidateInterpretation.parse(raw)

    def test_approximate_actual_amount_preserves_precision(self):
        parsed = CandidateInterpretation.parse(candidate_raw(
            "actual_expense", value="42.5", precision="approximate",
            label="Groceries"))
        self.assertEqual(parsed.candidate_facts[0].amount.precision, "approximate")
        self.assertEqual(parsed.candidate_facts[0].amount.value, "42.5")

    def test_english_and_chinese_actual_expenses_share_role(self):
        cases = (
            candidate_raw("actual_expense", label="Medical bill", language="en"),
            candidate_raw("actual_expense", label="家庭采购", language="zh-CN"),
        )
        self.assertEqual(
            [CandidateInterpretation.parse(raw).candidate_facts[0].type for raw in cases],
            ["actual_expense", "actual_expense"])

    def test_english_and_chinese_actual_inflows_share_role(self):
        cases = (
            candidate_raw("actual_inflow", label="Reimbursement", language="en"),
            candidate_raw("actual_inflow", label="房租退款", language="zh-CN"),
        )
        self.assertEqual(
            [CandidateInterpretation.parse(raw).candidate_facts[0].type for raw in cases],
            ["actual_inflow", "actual_inflow"])

    def test_labels_categories_and_scaled_amounts_do_not_change_role(self):
        facts = [
            candidate_fact("actual_expense", "9", label="Groceries"),
            candidate_fact("actual_expense", "930", label="Laptop"),
            candidate_fact("actual_expense", "71", label="Travel"),
        ]
        for index, item in enumerate(facts):
            item["id"] = "occurrence-" + str(index)
        raw = candidate_raw("actual_expense")
        raw["candidate_facts"] = list(reversed(facts))
        parsed = CandidateInterpretation.parse(raw)
        self.assertEqual({fact.type for fact in parsed.candidate_facts},
                         {"actual_expense"})
        self.assertEqual({fact.amount.value for fact in parsed.candidate_facts},
                         {"9", "930", "71"})

    def test_corrected_evaluator_accepts_actual_and_rejects_force_fit(self):
        expected = {"facts":[{
            "type":"actual_expense", "account":"Old wallet",
            "amount":{"currency":"CNY", "value":"125", "precision":"exact"},
            "temporal_status":"actual"}],
            "default_currency":"CNY", "destructive":False,
            "clarification_stage":"settlement", "unsupported_stage":"none"}
        actual = type("Envelope", (), {"interpretation":
            CandidateInterpretation.parse(candidate_raw("actual_expense"))})()
        wrong_raw = candidate_raw("planned_expense")
        wrong = type("Envelope", (), {"interpretation":
            CandidateInterpretation.parse(wrong_raw)})()
        self.assertTrue(all(score(actual, expected).values()))
        result = score(wrong, expected)
        self.assertTrue(result["semantic_extraction"])
        self.assertFalse(result["financial_classification"])
        self.assertFalse(result["temporal_integrity"])


class ActualOccurrenceBridgeTests(unittest.TestCase):
    def setUp(self):
        self.target = store()
        self.position = current_assets(self.target.snapshot).positions[0]

    def prepare(self, kind, *, known=True, precision="exact", value="125",
                language="en", label=None):
        account = "Old wallet" if known else None
        label = label or ("Bicycle repair" if kind == "actual_expense"
                          else "Freelance invoice")
        movement = ("I paid {} CNY yesterday for {} from Old wallet."
                    if kind == "actual_expense" else
                    "I received {} CNY yesterday for {} into Old wallet.")
        movement = movement.format(value, label)
        if not known:
            movement = movement.replace(" from Old wallet", "").replace(
                " into Old wallet", "")
        coverage = "The current balance was before this occurrence."
        text = movement + " Coverage: " + coverage
        raw = candidate_raw(kind, value=value, account=account,
                            precision=precision, label=label,
                            language=language)
        _, bridge, target = setup(raw, text, self.target)
        review = actual_review(
            text,
            OccurrenceKind.EXPENSE if kind == "actual_expense"
            else OccurrenceKind.INFLOW,
            position_ref=record_ref(self.position) if known else None,
            amount_value=value, precision=precision,
            account_text="Old wallet" if known else None,
        )
        return bridge, target, review

    def test_known_source_expense_routes_through_slice_e_without_direct_mutation(self):
        bridge, target, request = self.prepare("actual_expense")
        before = target.snapshot
        reviewed = bridge.review_occurrence(request, AT)
        self.assertIs(target.snapshot, before)
        self.assertIs(type(reviewed.change_set.operations[0]), SettleOccurrence)
        self.assertEqual(reviewed.facts[0].type, "actual_expense")
        finish(bridge, reviewed)
        self.assertEqual(current_assets(target.snapshot).positions[0].balance,
                         exact(575))

    def test_known_destination_inflow_routes_through_slice_e(self):
        bridge, target, request = self.prepare("actual_inflow")
        reviewed = bridge.review_occurrence(request, AT)
        self.assertIs(type(reviewed.change_set.operations[0]), SettleOccurrence)
        finish(bridge, reviewed)
        self.assertEqual(current_assets(target.snapshot).positions[0].balance,
                         exact(825))

    def test_unknown_source_and_destination_remain_unresolved(self):
        for kind in ("actual_expense", "actual_inflow"):
            with self.subTest(kind=kind):
                bridge, target, request = self.prepare(kind, known=False)
                before = target.snapshot
                reviewed = bridge.review_occurrence(request, AT)
                self.assertIsNone(reviewed.change_set)
                self.assertTrue(reviewed.issues)
                self.assertIs(target.snapshot, before)

    def test_candidate_account_cannot_be_invented_or_redirected(self):
        bridge, target, request = self.prepare("actual_expense", known=False)
        invented = deepcopy(request)
        invented = OccurrenceReview(
            kind=invented.kind, amount=invented.amount, timing=invented.timing,
            sources=invented.sources, position_ref=record_ref(self.position),
            basis=BalanceBasis.PRIOR, freshness=invented.freshness,
            fact_id=invented.fact_id)
        with self.assertRaisesRegex(ValueError, "Unknown Candidate account"):
            bridge.review_occurrence(invented, AT)
        self.assertEqual(target.snapshot.revision, 0)

    def test_account_relationship_requires_explicit_account_evidence(self):
        bridge, target, request = self.prepare("actual_expense")
        missing_account_source = OccurrenceReview(
            kind=request.kind, amount=request.amount, timing=request.timing,
            sources=tuple(item for item in request.sources if item.field != "account"),
            position_ref=request.position_ref, basis=request.basis,
            coverage_sources=request.coverage_sources,
            coverage_rationale=request.coverage_rationale,
            freshness=request.freshness, fact_id=request.fact_id)
        with self.assertRaisesRegex(ValueError, "reviewed account evidence"):
            bridge.review_occurrence(missing_account_source, AT)

    def test_amount_kind_and_timing_cannot_change_during_review(self):
        mutations = (
            ("amount", lambda r: OccurrenceReview(
                kind=r.kind, amount=exact(126), timing=r.timing,
                sources=r.sources, position_ref=r.position_ref, basis=r.basis,
                coverage_sources=r.coverage_sources,
                coverage_rationale=r.coverage_rationale, freshness=r.freshness,
                fact_id=r.fact_id)),
            ("kind", lambda r: OccurrenceReview(
                kind=OccurrenceKind.INFLOW, amount=r.amount, timing=r.timing,
                sources=r.sources, position_ref=r.position_ref, basis=r.basis,
                coverage_sources=r.coverage_sources,
                coverage_rationale=r.coverage_rationale, freshness=r.freshness,
                fact_id=r.fact_id)),
            ("timing", lambda r: OccurrenceReview(
                kind=r.kind, amount=r.amount,
                timing=TemporalSpec.known_date(date(2026,9,21), "last week"),
                sources=r.sources, position_ref=r.position_ref, basis=r.basis,
                coverage_sources=r.coverage_sources,
                coverage_rationale=r.coverage_rationale, freshness=r.freshness,
                fact_id=r.fact_id)),
        )
        for name, mutate in mutations:
            with self.subTest(field=name):
                bridge, target, request = self.prepare("actual_expense")
                with self.assertRaises(ValueError):
                    bridge.review_occurrence(mutate(request), AT)
                self.assertEqual(target.snapshot.revision, 0)

    def test_actual_fact_cannot_use_generic_fact_review_path(self):
        bridge, target, _ = self.prepare("actual_expense")
        text = bridge._text
        reviewed = bridge.review(ReviewRequest((
            FactReview("occurrence", source(text, text.split(" Coverage:")[0])),
        )), AT)
        self.assertIsNone(reviewed.change_set)
        self.assertEqual(reviewed.outcomes[0].disposition, "needs_user_input")
        self.assertEqual(target.snapshot.revision, 0)

    def test_actual_provenance_binds_candidate_fact_and_user_report(self):
        bridge, _, request = self.prepare("actual_inflow")
        reviewed = bridge.review_occurrence(request, AT)
        self.assertEqual(reviewed.outcomes[0].fact_id, "occurrence")
        self.assertEqual(reviewed.facts[0].source, "user_report")
        report = reviewed.change_set.operations[0].report
        self.assertTrue(report.observation.evidence_refs)

    def test_approximate_occurrence_reaches_review_without_precision_upgrade(self):
        bridge, _, request = self.prepare(
            "actual_expense", known=False, precision="approximate",
            value="42.5", label="Groceries")
        reviewed = bridge.review_occurrence(request, AT)
        self.assertIsNone(reviewed.change_set)
        self.assertEqual(reviewed.facts[0].amount.precision, "approximate")

    def test_fact_order_does_not_authorize_unreviewed_actual(self):
        raw = candidate_raw("actual_expense")
        second = candidate_fact("actual_inflow", "38", account=None,
                                label="Reimbursement", timing="today")
        second["id"] = "second"
        raw["candidate_facts"] = [second, raw["candidate_facts"][0]]
        text = ("I received 38 CNY today as reimbursement. "
                "I paid 125 CNY yesterday for Bicycle repair from Old wallet. "
                "Coverage: The current balance was before this occurrence.")
        _, bridge, target = setup(raw, text, self.target)
        request = actual_review(
            text, OccurrenceKind.EXPENSE,
            position_ref=record_ref(self.position), account_text="Old wallet")
        reviewed = bridge.review_occurrence(request, AT)
        self.assertEqual(tuple(f.id for f in reviewed.facts), ("occurrence",))
        self.assertIs(type(reviewed.change_set.operations[0]), SettleOccurrence)
        self.assertEqual(target.snapshot.revision, 0)


if __name__ == "__main__":
    unittest.main()

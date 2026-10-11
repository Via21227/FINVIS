import json
import io
import os
import socket
import threading
from contextlib import contextmanager
from http.client import HTTPResponse, IncompleteRead, RemoteDisconnected
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.request import Request

from finvis.agent_providers import (AgentProviderConfig, DeterministicFallback,
                                    LMStudioProvider, LocalModelInfo, OpenAIProvider, ProviderError,
                                    configured_provider, _read_json)
from finvis.agent_schema import (CandidateInterpretation, InterpretationError, SCHEMA_VERSION,
                                 interpretation_json_schema)
from finvis.agent_understanding import AgentUnderstandingService, SYSTEM_PROMPT
from finvis.application import AppError, DemoApplication


ROOT = Path(__file__).resolve().parents[1]


def ready_lmstudio(config):
    provider = LMStudioProvider(config)
    model = provider.model or 'local'
    provider.discover_model_info = lambda: (LocalModelInfo(model, model, 'MLX · 4bit', True),)
    return provider


def realistic_output():
    def amount(value, precision="exact"):
        return {"currency":"CNY", "value":str(value), "range_min":None, "range_max":None, "precision":precision}
    def fact(identifier, kind, label, value=None, precision="exact", account=None,
             earmark=None, purpose=None, timing=None):
        temporal = {"current_asset":"current", "earmarked_current_asset":"current",
                    "expected_future_inflow":"future", "planned_expense":"planned",
                    "contemplated_decision":"contemplated", "context":"context"}[kind]
        return {"id":identifier, "type":kind, "label":label, "account":account,
                "amount":amount(value, precision) if value is not None else None,
                "earmark":earmark, "purpose":purpose, "temporal_status":temporal,
                "timing_note":timing, "source":"user_report"}
    return {
        "schema_version":SCHEMA_VERSION, "language":"zh-CN",
        "user_intents":["replace_financial_state","evaluate_decision","provide_context","ask_question"],
        "default_currency":"CNY",
        "candidate_facts":[
            fact("icbc", "current_asset", "工商银行", 9000, account="工商银行"),
            fact("emergency", "earmarked_current_asset", "支付宝备用金", 5000, account="支付宝", earmark="备用金"),
            fact("phones-fund", "earmarked_current_asset", "支付宝手机资金", 20000, account="支付宝", earmark="手机"),
            fact("rent-refund", "expected_future_inflow", "租房退款", 13000, "approximate", timing="毕业后"),
            fact("gift", "expected_future_inflow", "奶奶赠予", 10000, "approximate", timing="未来"),
            fact("phones", "planned_expense", "新手机", 18000, "approximate", purpose="为自己和母亲买手机"),
            fact("gloves", "contemplated_decision", "Winning 16oz 拳套", 6000, "approximate", purpose="回国打实战"),
            fact("boxing", "context", "在新加坡练拳击一年"),
        ],
        "destructive_actions":["replace_financial_state"], "assumptions":[],
        "uncertainties":[], "conflicts":[],
        "clarification_questions":["你回国后的每月必要支出大约是多少？"],
        "unsupported_requests":[], "user_question":"买拳套在财务上是否合理？",
        "interpretation_notes":[],
    }


class SequenceProvider:
    name = "test-local"
    def __init__(self, values):
        self.values=list(values); self.calls=0; self.requests=[]
    def complete(self, system_prompt, user_text):
        self.calls += 1
        self.requests.append((system_prompt, user_text))
        value = self.values.pop(0)
        return value, {"provider":self.name,"model":"fixture-model","latency_ms":7}


def generic_output():
    return {
        "schema_version":SCHEMA_VERSION, "language":"en",
        "user_intents":["update_financial_state","evaluate_decision"],
        "default_currency":"SGD",
        "candidate_facts":[
            {"id":"reserve", "type":"current_asset", "label":"Transit reserve",
             "account":"Daily wallet",
             "amount":{"currency":"SGD", "value":"840.50", "range_min":None,
                       "range_max":None, "precision":"exact"},
             "earmark":None, "purpose":None, "temporal_status":"current",
             "timing_note":None, "source":"user_report"},
            {"id":"course", "type":"contemplated_decision", "label":"Evening course",
             "account":None,
             "amount":{"currency":"SGD", "value":"1200", "range_min":None,
                       "range_max":None, "precision":"approximate"},
             "earmark":None, "purpose":"professional study", "temporal_status":"contemplated",
             "timing_note":"later this year", "source":"user_report"},
        ],
        "destructive_actions":[], "assumptions":[], "uncertainties":[], "conflicts":[],
        "clarification_questions":["What are the monthly essential expenses?"],
        "unsupported_requests":[], "user_question":"Can I consider the course?",
        "interpretation_notes":[],
    }


def schema_accepts(value, schema):
    if "anyOf" in schema and not any(schema_accepts(value, branch) for branch in schema["anyOf"]):
        return False
    if "enum" in schema and value not in schema["enum"]:
        return False
    allowed_types = schema.get("type")
    if allowed_types is not None:
        allowed_types = allowed_types if isinstance(allowed_types, list) else [allowed_types]
        matches = {"null": value is None,
                   "object": isinstance(value, dict),
                   "array": isinstance(value, list),
                   "string": isinstance(value, str)}
        if not any(matches.get(kind, False) for kind in allowed_types):
            return False
    if isinstance(value, dict):
        properties = schema.get("properties", {})
        if any(field not in value for field in schema.get("required", [])):
            return False
        if schema.get("additionalProperties") is False and any(field not in properties for field in value):
            return False
        if any(not schema_accepts(item, properties[field])
               for field, item in value.items() if field in properties):
            return False
    if isinstance(value, list) and "items" in schema:
        if any(not schema_accepts(item, schema["items"]) for item in value):
            return False
    return True


class AgentSchemaTests(unittest.TestCase):
    def test_realistic_prompt_schema_preserves_semantic_types(self):
        value = CandidateInterpretation.parse(realistic_output())
        self.assertEqual(value.default_currency, "CNY")
        self.assertEqual([fact.type for fact in value.candidate_facts], [
            "current_asset","earmarked_current_asset","earmarked_current_asset",
            "expected_future_inflow","expected_future_inflow","planned_expense",
            "contemplated_decision","context"])
        self.assertEqual(value.candidate_facts[3].amount.precision, "approximate")
        self.assertEqual(value.candidate_facts[3].temporal_status, "future")
        self.assertEqual(value.candidate_facts[1].earmark, "备用金")

    def test_temporal_conflict_fails_closed(self):
        value = realistic_output(); value["candidate_facts"][3]["temporal_status"] = "current"
        with self.assertRaises(InterpretationError): CandidateInterpretation.parse(value)

    def test_unknown_or_extra_fields_fail_closed(self):
        value = realistic_output(); value["authoritative_runway"] = "safe"
        with self.assertRaises(InterpretationError): CandidateInterpretation.parse(value)

    def test_approximate_value_remains_approximate(self):
        value = CandidateInterpretation.parse(realistic_output())
        gloves = next(f for f in value.candidate_facts if f.id == "gloves")
        self.assertEqual((gloves.amount.value, gloves.amount.precision), ("6000", "approximate"))

    def test_wire_schema_rejects_scalar_with_range_bounds(self):
        value = generic_output()
        value["candidate_facts"][0]["amount"].update(range_min="800", range_max="900")
        self.assertFalse(schema_accepts(value, interpretation_json_schema()))

    def test_wire_schema_accepts_each_existing_amount_representation(self):
        cases = (
            {"currency":"SGD", "value":"840.50", "range_min":None,
             "range_max":None, "precision":"exact"},
            {"currency":"SGD", "value":"1200", "range_min":None,
             "range_max":None, "precision":"approximate"},
            {"currency":"SGD", "value":None, "range_min":"700",
             "range_max":"950", "precision":"range"},
            {"currency":None, "value":None, "range_min":None,
             "range_max":None, "precision":"unknown"},
        )
        for amount in cases:
            with self.subTest(precision=amount["precision"]):
                value = generic_output(); value["candidate_facts"][0]["amount"] = amount
                self.assertTrue(schema_accepts(value, interpretation_json_schema()))
                self.assertEqual(CandidateInterpretation.parse(value).candidate_facts[0].amount.precision,
                                 amount["precision"])

    def test_wire_schema_constrains_fact_type_and_status_pairs(self):
        value = generic_output(); value["candidate_facts"][1]["temporal_status"] = "planned"
        self.assertFalse(schema_accepts(value, interpretation_json_schema()))
        with self.assertRaises(InterpretationError):
            CandidateInterpretation.parse(value)

    def test_contemplated_decision_and_planned_expense_stay_distinct(self):
        contemplated = generic_output()
        self.assertTrue(schema_accepts(contemplated, interpretation_json_schema()))
        planned = generic_output()
        fact = planned["candidate_facts"][1]
        fact.update(type="planned_expense", temporal_status="planned", label="Confirmed course fee")
        self.assertTrue(schema_accepts(planned, interpretation_json_schema()))
        fact["temporal_status"] = "contemplated"
        self.assertFalse(schema_accepts(planned, interpretation_json_schema()))


class AgentServiceTests(unittest.TestCase):
    def test_valid_provider_output_creates_candidate_only_preview(self):
        service = AgentUnderstandingService(SequenceProvider([json.dumps(realistic_output())]))
        candidate = service.interpret("真实长文本", "g1", 3)
        self.assertEqual(candidate.status, "awaiting_confirmation")
        self.assertTrue(candidate.proposed_change["requires_confirmation"])
        self.assertEqual(candidate.proposed_change["committable_count"], 0)
        self.assertEqual(candidate.proposed_change["candidate_only_count"], 8)

    def test_invalid_output_has_one_bounded_repair_then_fails(self):
        first = generic_output(); first["candidate_facts"][0]["amount"].update(range_min="800", range_max="900")
        second = generic_output(); second["candidate_facts"][0]["amount"].update(range_min="810", range_max="910")
        provider = SequenceProvider([first, second])
        with self.assertRaisesRegex(ProviderError, "schema validation") as raised:
            AgentUnderstandingService(provider).interpret("text", "g1", 0)
        self.assertEqual(provider.calls, 2)
        self.assertEqual(raised.exception.diagnostics['attempts'], 2)
        self.assertEqual(raised.exception.diagnostics['stage'], 'candidate_validation')

    def test_repair_can_succeed_on_second_attempt(self):
        invalid = generic_output(); invalid["candidate_facts"][0]["amount"].update(range_min="800", range_max="900")
        provider = SequenceProvider([invalid, generic_output()])
        candidate = AgentUnderstandingService(provider).interpret("text", "g1", 0)
        self.assertEqual(candidate.model, "fixture-model")
        self.assertEqual(provider.calls, 2)

    def test_malformed_json_fails_closed_without_unverifiable_repair(self):
        provider = SequenceProvider(["not json", generic_output()])
        with self.assertRaises(ProviderError) as raised:
            AgentUnderstandingService(provider).interpret("text", "g1", 0)
        self.assertEqual(provider.calls, 1)
        self.assertEqual(raised.exception.diagnostics["attempts"], 1)

    def test_confirm_and_cancel_require_matching_candidate_and_version(self):
        service = AgentUnderstandingService(SequenceProvider([realistic_output()]))
        candidate = service.interpret("text", "g1", 0)
        with self.assertRaises(ProviderError): service.confirm(candidate.id, "wrong", 0)
        cancelled = service.cancel(candidate.id, "g1", 0)
        self.assertEqual(cancelled.status, "cancelled")
        with self.assertRaises(ProviderError): service.confirm(candidate.id, "g1", 0)

    def test_prompt_forbids_authoritative_financial_conclusions(self):
        for term in ("runway", "affordability", "resilience", "financial verdict"):
            self.assertIn(term, SYSTEM_PROMPT)

    def test_prompt_states_amount_and_fact_construction_rules(self):
        for term in ("exact/approximate use value", "range uses null value",
                     "unknown uses null", "planned_expense uses planned",
                     "contemplated_decision uses contemplated"):
            self.assertIn(term, SYSTEM_PROMPT)

    def test_semantic_repair_receives_exact_actionable_feedback(self):
        invalid = generic_output()
        invalid["candidate_facts"][0]["amount"].update(range_min="800", range_max="900")
        repaired = generic_output()
        provider = SequenceProvider([invalid, repaired])
        candidate = AgentUnderstandingService(provider).interpret("I have a transit reserve.", "g2", 4)
        self.assertEqual(provider.calls, 2)
        self.assertEqual(candidate.interpretation.candidate_facts[0].amount.value, "840.50")
        system_prompt, user_payload = provider.requests[1]
        payload = json.loads(user_payload)
        self.assertIn("Repair exactly one invalid CandidateInterpretation", system_prompt)
        self.assertEqual(payload["invalid_candidate"], invalid)
        self.assertEqual(payload["validation_error"]["category"], "amount_representation")
        self.assertEqual(payload["validation_error"]["path"], "candidate_facts[0].amount")
        self.assertEqual(payload["validation_error"]["rule"],
                         "exact requires non-null value and null range_min/range_max")

    def test_repair_cannot_invent_or_change_financial_evidence(self):
        invalid = generic_output()
        invalid["candidate_facts"][0]["amount"].update(range_min="800", range_max="900")
        invented = generic_output(); invented["candidate_facts"][0]["account"] = "Invented account"
        provider = SequenceProvider([invalid, invented])
        service = AgentUnderstandingService(provider)
        with self.assertRaises(ProviderError) as raised:
            service.interpret("I have a transit reserve.", "g2", 4)
        self.assertEqual(provider.calls, 2)
        self.assertEqual(raised.exception.diagnostics["attempts"], 2)
        self.assertIsNone(service.candidate)

    def test_repair_cannot_erase_destructive_or_clarification_signals(self):
        invalid = generic_output()
        invalid["destructive_actions"] = ["replace_financial_state"]
        invalid["candidate_facts"][0]["amount"].update(range_min="800", range_max="900")
        erased = generic_output()
        provider = SequenceProvider([invalid, erased])
        with self.assertRaises(ProviderError):
            AgentUnderstandingService(provider).interpret("Replace my records.", "g2", 4)
        self.assertEqual(provider.calls, 2)

    def test_missing_numeric_evidence_is_not_repaired_by_invention(self):
        invalid = generic_output()
        invalid["candidate_facts"][0]["amount"]["value"] = None
        provider = SequenceProvider([invalid, generic_output()])
        with self.assertRaises(ProviderError) as raised:
            AgentUnderstandingService(provider).interpret("The amount was not stated.", "g2", 4)
        self.assertEqual(provider.calls, 1)
        self.assertEqual(raised.exception.diagnostics["attempts"], 1)


class ProviderBoundaryTests(unittest.TestCase):
    def test_lmstudio_is_default_and_models_are_runtime_configuration(self):
        with patch.dict(os.environ, {}, clear=True):
            config = AgentProviderConfig.from_env()
        self.assertEqual(config.provider, "lmstudio")
        self.assertEqual(config.lmstudio_base_url, "http://127.0.0.1:1234/v1")
        self.assertIsNone(config.lmstudio_model)
        self.assertEqual(config.lmstudio_reasoning_effort, "none")
        self.assertEqual(config.max_output_tokens, 1536)
        self.assertEqual(config.timeout_seconds, 90.0)

    def test_lmstudio_runtime_controls_are_bounded_and_configurable(self):
        with patch.dict(os.environ, {"FINVIS_AGENT_REASONING_EFFORT":"",
                                     "FINVIS_AGENT_MAX_OUTPUT_TOKENS":"99999",
                                     "FINVIS_AGENT_TIMEOUT_SECONDS":"999"}, clear=True):
            config = AgentProviderConfig.from_env()
        self.assertIsNone(config.lmstudio_reasoning_effort)
        self.assertEqual(config.max_output_tokens, 4096)
        self.assertEqual(config.timeout_seconds, 120.0)

    def test_lmstudio_sends_runtime_controls_and_final_content_only(self):
        response = {"choices":[{"finish_reason":"stop", "message":{
            "content":json.dumps(realistic_output()), "reasoning_content":"must not be used"}}]}
        with patch("finvis.agent_providers.urlopen") as opened:
            opened.return_value.__enter__.return_value.read.return_value = json.dumps(response).encode()
            provider = ready_lmstudio(AgentProviderConfig(lmstudio_model="local-gemma",
                                                           lmstudio_reasoning_effort="none",
                                                           max_output_tokens=1536))
            content, metadata = provider.complete("system", "input")
        body = json.loads(opened.call_args.args[0].data.decode())
        self.assertEqual(body["reasoning_effort"], "none")
        self.assertEqual(body["max_tokens"], 1536)
        self.assertEqual(content, json.dumps(realistic_output()))
        self.assertEqual(metadata["model"], "local-gemma")

    def test_lmstudio_omits_reasoning_control_when_explicitly_disabled(self):
        response = {"choices":[{"finish_reason":"stop", "message":{"content":"{}"}}]}
        with patch("finvis.agent_providers.urlopen") as opened:
            opened.return_value.__enter__.return_value.read.return_value = json.dumps(response).encode()
            ready_lmstudio(AgentProviderConfig(lmstudio_model="local", lmstudio_reasoning_effort=None)).complete("s", "u")
        body = json.loads(opened.call_args.args[0].data.decode())
        self.assertNotIn("reasoning_effort", body)

    def test_lmstudio_length_truncation_and_reasoning_only_response_fail_closed(self):
        truncated = {"choices":[{"finish_reason":"length", "message":{"content":"{\"partial\":"}}]}
        reasoning_only = {"choices":[{"finish_reason":"stop", "message":{"content":"", "reasoning_content":"{}"}}]}
        for response, category in ((truncated, "truncated_response"), (reasoning_only, "empty_final_content")):
            with self.subTest(category=category), patch("finvis.agent_providers.urlopen") as opened:
                opened.return_value.__enter__.return_value.read.return_value = json.dumps(response).encode()
                with self.assertRaisesRegex(ProviderError, "Provider response") as raised:
                    ready_lmstudio(AgentProviderConfig(lmstudio_model="local")).complete("s", "u")
                self.assertEqual(raised.exception.category, category)

    def test_lmstudio_timeout_is_not_mislabeled_as_unavailable(self):
        for error in (TimeoutError(), socket.timeout(), URLError(TimeoutError()), URLError(socket.timeout())):
            with self.subTest(error=type(error).__name__), patch("finvis.agent_providers.urlopen", side_effect=error):
                with self.assertRaisesRegex(ProviderError, "timed out") as raised:
                    LMStudioProvider(AgentProviderConfig(lmstudio_model="local")).complete("s", "u")
                self.assertEqual(raised.exception.category, "timeout")
                self.assertEqual(raised.exception.diagnostics['stage'], 'connect_or_headers')

    def test_lmstudio_rejects_non_loopback_endpoint(self):
        with self.assertRaisesRegex(ProviderError, "loopback"):
            LMStudioProvider(AgentProviderConfig(lmstudio_base_url="https://example.com/v1"))

    def test_openai_requires_explicit_provider_key_and_model(self):
        provider = OpenAIProvider(AgentProviderConfig(provider="openai"), api_key=None)
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ProviderError): provider.complete("system", "financial text")

    def test_no_automatic_cloud_fallback_exists(self):
        with patch.dict(os.environ, {"FINVIS_AGENT_PROVIDER":"deterministic"}, clear=True):
            self.assertIsInstance(configured_provider(), DeterministicFallback)


@contextmanager
def stalled_http_response(send_headers):
    release = threading.Event()
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_): pass
        def do_GET(self):
            if send_headers:
                self.send_response(200)
                self.send_header('Content-Length', '2')
                self.end_headers()
                self.wfile.flush()
            release.wait(3)
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield Request('http://127.0.0.1:%s/' % server.server_port)
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        thread.join()


class ProviderTransportTests(unittest.TestCase):
    def test_real_python_socket_timeout_before_headers_and_during_body(self):
        for headers, stage in ((False, 'connect_or_headers'), (True, 'read_body')):
            with self.subTest(stage=stage), stalled_http_response(headers) as request:
                with self.assertRaises(ProviderError) as raised:
                    _read_json(request, .05)
                self.assertEqual(raised.exception.category, 'timeout')
                self.assertEqual(raised.exception.diagnostics['stage'], stage)

    def test_connection_reset_eof_and_generic_os_error_remain_distinct(self):
        cases = [(URLError(ConnectionRefusedError(61, 'private')), 'connection_error'),
                 (ConnectionRefusedError(), 'connection_error'),
                 (ConnectionResetError(), 'transport_interrupted'),
                 (URLError(BrokenPipeError()), 'transport_interrupted'),
                 (RemoteDisconnected('private'), 'transport_interrupted'),
                 (IncompleteRead(b'private'), 'transport_interrupted'),
                 (EOFError(), 'transport_interrupted'),
                 (OSError(1, 'private'), 'provider_unavailable')]
        for error, category in cases:
            with self.subTest(category=category), patch('finvis.agent_providers.urlopen', side_effect=error):
                with self.assertRaises(ProviderError) as raised:
                    _read_json(Request('http://127.0.0.1/'), 60)
                self.assertEqual(raised.exception.category, category)
                self.assertNotIn('private', str(raised.exception)+json.dumps(raised.exception.diagnostics))

    def test_http_errors_keep_status_without_recording_response_body(self):
        for code, category in ((400, 'provider_http_error'), (404, 'model_not_found'), (500, 'server_error')):
            body = io.BytesIO(b'private')
            error = HTTPError('http://127.0.0.1/', code, 'private', {}, body)
            with self.subTest(code=code), patch('finvis.agent_providers.urlopen', side_effect=error):
                with self.assertRaises(ProviderError) as raised: _read_json(Request('http://127.0.0.1/'), 60)
                self.assertEqual(raised.exception.category, category)
                self.assertEqual(raised.exception.diagnostics['http_status'], code)
                self.assertTrue(body.closed)
                self.assertNotIn('private', json.dumps(raised.exception.diagnostics))

    def test_body_failure_classes_and_short_content_length(self):
        cases = [(b'', None, 'empty_response'), (b'{', None, 'malformed_response'),
                 (b'[]', None, 'malformed_response'), (b'\xff', None, 'malformed_response'),
                 (b'{"error":{"message":"private"}}', None, 'server_error'),
                 (b'{}', 5, 'transport_interrupted')]
        for raw, remaining, category in cases:
            with self.subTest(category=category), patch('finvis.agent_providers.urlopen') as opened:
                response = opened.return_value.__enter__.return_value
                response.read.return_value = raw
                response.length = remaining
                with self.assertRaises(ProviderError) as raised: _read_json(Request('http://127.0.0.1/'), 60)
                self.assertEqual(raised.exception.category, category)
                self.assertNotIn('private', json.dumps(raised.exception.diagnostics))

    def test_bad_envelopes_fail_closed_without_attribute_errors(self):
        for data in ({'choices':[None]}, {'choices':[{'message':None}]}, {'choices':[]}, {'choices':None}):
            with self.subTest(data=data), patch('finvis.agent_providers._read_json', return_value=data):
                with self.assertRaises(ProviderError) as raised:
                    ready_lmstudio(AgentProviderConfig(lmstudio_model='local')).complete('s','u')
                self.assertEqual(raised.exception.category, 'malformed_response')

    def test_real_http_parser_rejects_valid_json_in_an_incomplete_body(self):
        class WireSocket:
            def makefile(self, *_):
                return io.BytesIO(b'HTTP/1.1 200 OK\r\nContent-Length: 7\r\n\r\n{}')
        response = HTTPResponse(WireSocket())
        response.begin()
        with patch('finvis.agent_providers.urlopen', return_value=response):
            with self.assertRaises(ProviderError) as raised: _read_json(Request('http://127.0.0.1/'), 60)
        self.assertEqual(raised.exception.category, 'transport_interrupted')

    def test_missing_or_empty_final_content_never_uses_reasoning(self):
        for message in ({}, {'content':None}, {'content':''}, {'reasoning_content':'private'}):
            with self.subTest(message=message), patch('finvis.agent_providers._read_json', return_value={
                    'choices':[{'finish_reason':'stop','message':message}]}):
                with self.assertRaises(ProviderError) as raised:
                    ready_lmstudio(AgentProviderConfig(lmstudio_model='local')).complete('s','u')
                self.assertEqual(raised.exception.category, 'empty_final_content')
                self.assertNotIn('private', json.dumps(raised.exception.diagnostics))

    def test_transport_failure_never_causes_a_repair_or_candidate(self):
        for category in ('timeout', 'connection_error', 'server_error', 'transport_interrupted'):
            with self.subTest(category=category), patch('finvis.agent_providers.LMStudioProvider.complete',
                    side_effect=ProviderError(category, 'closed')) as completed:
                service = AgentUnderstandingService(LMStudioProvider(AgentProviderConfig(lmstudio_model='local')))
                with self.assertRaises(ProviderError) as raised: service.interpret('text','g',0)
                self.assertEqual(completed.call_count, 1)
                self.assertEqual(raised.exception.diagnostics['attempts'], 1)
                self.assertIsNone(service.candidate)

    def test_repair_failure_is_serial_and_retains_transport_category(self):
        invalid = generic_output()
        invalid['candidate_facts'][0]['amount'].update(range_min='800', range_max='900')
        with patch('finvis.agent_providers.LMStudioProvider.complete', side_effect=[
                (invalid, {}), ProviderError('timeout','closed')]) as completed:
            service = AgentUnderstandingService(LMStudioProvider(AgentProviderConfig(lmstudio_model='local')))
            with self.assertRaises(ProviderError) as raised: service.interpret('text','g',0)
            self.assertEqual(completed.call_count, 2)
            self.assertEqual(raised.exception.category, 'timeout')
            self.assertEqual(raised.exception.diagnostics['attempts'], 2)
            self.assertIsNone(service.candidate)

    def test_eval_preserves_error_diagnostics_without_running_a_model(self):
        from finvis.agent_eval import run
        with patch('finvis.agent_eval.LMStudioProvider') as provider:
            provider.return_value.complete.side_effect = ProviderError('timeout', 'closed', {'stage':'connect_or_headers'})
            rows = run('fixture')
        self.assertEqual(len(rows), 15)
        self.assertTrue(all(row['error']=='timeout' and row['diagnostics']['attempts']==1 for row in rows))
        self.assertNotIn('text', rows[0])


class CandidateLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.app = DemoApplication(agent_provider=SequenceProvider([realistic_output(), realistic_output()]))
        self.expected = lambda: {"generation":self.app.view()["generation"], "revision":self.app.view()["revision"]}

    def test_interpretation_does_not_mutate_authoritative_state(self):
        before = self.app.view()
        candidate = self.app.interpret_agent_input("真实长文本", self.expected())
        after = self.app.view()
        self.assertEqual(before["revision"], after["revision"])
        self.assertEqual(before["audit"], after["audit"])
        self.assertEqual(candidate["status"], "awaiting_confirmation")
        class FailingProvider:
            name = "failed-local"
            def complete(self, *_): raise ProviderError("unavailable", "closed")
        failed_app = DemoApplication(agent_provider=FailingProvider())
        failed_before = failed_app.view()
        expected = {key:failed_before[key] for key in ("generation","revision")}
        with self.assertRaises(AppError): failed_app.interpret_agent_input("真实长文本", expected)
        self.assertEqual(failed_before["audit"], failed_app.view()["audit"])

    def test_confirm_authorizes_zero_unsafe_commits(self):
        before = self.app.view()
        candidate = self.app.interpret_agent_input("真实长文本", self.expected())
        confirmed = self.app.resolve_agent_candidate("confirm", candidate["id"], self.expected())
        after = self.app.view()
        self.assertEqual(confirmed["status"], "confirmed_no_commit")
        self.assertEqual((before["state"], before["plan"], before["revision"]),
                         (after["state"], after["plan"], after["revision"]))

    def test_cancel_preserves_authoritative_state(self):
        before = self.app.view()
        candidate = self.app.interpret_agent_input("真实长文本", self.expected())
        cancelled = self.app.resolve_agent_candidate("cancel", candidate["id"], self.expected())
        self.assertEqual(cancelled["status"], "cancelled")
        self.assertEqual(before["audit"], self.app.view()["audit"])

    def test_reset_discards_candidate_and_restores_canonical_story(self):
        self.app.interpret_agent_input("真实长文本", self.expected())
        self.app.command("reset", self.expected())
        self.assertIsNone(self.app.view()["agent_candidate"])
        self.assertEqual(self.app.view()["plan"]["ref"]["version"], 1)

    def test_eval_suite_has_fifteen_meaningful_cases(self):
        cases = json.loads((ROOT / "inputs/agent_eval_cases.json").read_text())
        self.assertEqual(len(cases), 15)
        self.assertIn("realistic-long-zh", {case["id"] for case in cases})
        self.assertIn("adversarial-schema", {case["id"] for case in cases})


if __name__ == "__main__": unittest.main()

import json
from pathlib import Path
import unittest
from unittest.mock import patch
from urllib.error import URLError

from finvis.agent_providers import (
    AgentProviderConfig, LMStudioProvider, LocalAIConnection, LocalModelInfo,
    ProviderError,
)
from finvis.application import DemoApplication
from tests.test_agent_understanding import SequenceProvider, realistic_output


ROOT = Path(__file__).resolve().parents[1]


def model(identifier, loaded=True, display=None):
    return LocalModelInfo(identifier, display or identifier, "MLX · 4bit", loaded)


def connection(models=(), selected=None):
    provider = LMStudioProvider(AgentProviderConfig(lmstudio_model=selected))
    provider.discover_model_info = lambda: tuple(models)
    return LocalAIConnection(provider=provider)


class LocalModelDiscoveryTests(unittest.TestCase):
    def test_management_endpoint_uses_same_safe_loopback_origin(self):
        provider = LMStudioProvider(AgentProviderConfig(
            lmstudio_base_url="http://localhost:4567/v1"))
        self.assertEqual(provider.management_url, "http://localhost:4567/api/v1/models")

    def test_native_discovery_filters_embeddings_and_preserves_loaded_state(self):
        response = {"models": [
            {"key": "vendor/ready", "display_name": "Ready", "type": "llm",
             "format": "mlx", "quantization": {"name": "4bit"},
             "loaded_instances": [{"id": "ready"}]},
            {"key": "vendor/cold", "display_name": "Cold", "type": "llm",
             "format": "gguf", "loaded_instances": []},
            {"key": "vendor/embed", "type": "embedding", "format": "gguf",
             "loaded_instances": [{"id": "embed"}]},
        ]}
        with patch("finvis.agent_providers._read_json", return_value=response):
            models = LMStudioProvider(AgentProviderConfig()).discover_model_info()
        self.assertEqual([item.id for item in models], ["vendor/ready", "vendor/cold"])
        self.assertEqual([item.loaded for item in models], [True, False])
        self.assertEqual(models[0].runtime, "MLX · 4bit")

    def test_discover_models_returns_only_loaded_language_models(self):
        provider = LMStudioProvider(AgentProviderConfig())
        provider.discover_model_info = lambda: (model("one"), model("two", False))
        self.assertEqual(provider.discover_models(), ("one",))

    def test_malformed_native_envelope_fails_closed(self):
        for response in ({}, {"models": {}}, {"models": [{"type": "llm"}]}):
            with self.subTest(response=response), patch(
                    "finvis.agent_providers._read_json", return_value=response):
                with self.assertRaises(ProviderError) as raised:
                    LMStudioProvider(AgentProviderConfig()).discover_model_info()
                self.assertEqual(raised.exception.category, "malformed_response")

    def test_non_loopback_provider_remains_rejected(self):
        with self.assertRaises(ProviderError):
            LMStudioProvider(AgentProviderConfig(
                lmstudio_base_url="http://192.168.1.8:1234/v1"))


class LocalAIStateTests(unittest.TestCase):
    def test_server_offline_is_distinct(self):
        manager = connection()
        manager.provider.discover_model_info = lambda: (_ for _ in ()).throw(
            ProviderError("connection_error", "closed"))
        status = manager.refresh()
        self.assertEqual((status["state"], status["connection"]),
                         ("server_offline", "disconnected"))

    def test_server_online_with_no_loaded_model(self):
        status = connection((model("cold", False),)).refresh()
        self.assertEqual(status["state"], "no_model_loaded")
        self.assertEqual(status["connection"], "connected")
        self.assertFalse(status["requests_enabled"])

    def test_exactly_one_loaded_model_is_auto_selected(self):
        status = connection((model("only"), model("cold", False))).refresh()
        self.assertEqual(status["selected_model"], "only")
        self.assertEqual(status["state"], "ready")
        self.assertTrue(status["requests_enabled"])

    def test_multiple_loaded_models_require_selection(self):
        status = connection((model("alpha"), model("beta"))).refresh()
        self.assertEqual(status["state"], "selection_required")
        self.assertIsNone(status["selected_model"])

    def test_explicit_selection_becomes_visible_and_ready(self):
        manager = connection((model("alpha"), model("beta")))
        manager.refresh()
        status = manager.select("beta")
        self.assertEqual(status["selected_model"], "beta")
        self.assertEqual(status["state"], "ready")

    def test_selected_model_disappearing_never_falls_back(self):
        manager = connection((model("alpha"), model("beta")), selected="beta")
        self.assertEqual(manager.refresh()["state"], "ready")
        manager.provider.discover_model_info = lambda: (model("alpha"),)
        status = manager.refresh()
        self.assertEqual(status["state"], "configured_model_not_found")
        self.assertEqual(status["selected_model"], "beta")

    def test_selected_discovered_but_unloaded_is_distinct(self):
        status = connection((model("alpha", False),), selected="alpha").refresh()
        self.assertEqual(status["state"], "selected_model_unavailable")

    def test_refresh_detects_newly_loaded_model_without_restart(self):
        manager = connection((model("alpha", False),))
        self.assertEqual(manager.refresh()["state"], "no_model_loaded")
        manager.provider.discover_model_info = lambda: (model("alpha"),)
        self.assertEqual(manager.refresh()["state"], "ready")
        self.assertEqual(manager.status()["selected_model"], "alpha")

    def test_malformed_discovery_is_provider_error(self):
        manager = connection()
        manager.provider.discover_model_info = lambda: (_ for _ in ()).throw(
            ProviderError("malformed_response", "bad"))
        status = manager.refresh()
        self.assertEqual((status["state"], status["error_category"]),
                         ("provider_error", "malformed_response"))

    def test_busy_is_observable_and_disables_requests(self):
        manager = connection((model("alpha"),), selected="alpha")
        manager.refresh()
        manager.begin_request()
        status = manager.status()
        self.assertEqual(status["state"], "busy")
        self.assertTrue(status["busy"])
        self.assertFalse(status["requests_enabled"])
        manager.end_request()
        self.assertEqual(manager.status()["state"], "ready")

    def test_selection_is_rejected_while_request_is_in_flight(self):
        manager = connection((model("alpha"), model("beta")), selected="alpha")
        manager.refresh()
        manager.begin_request()
        with self.assertRaises(ProviderError) as raised:
            manager.select("beta")
        self.assertEqual(raised.exception.category, "provider_busy")
        self.assertEqual(manager.status()["selected_model"], "alpha")

    def test_long_model_identifier_is_preserved_without_truncation(self):
        identifier = "organization/" + "very-long-compatible-model-" * 12
        status = connection((model(identifier),)).refresh()
        self.assertEqual(status["models"][0]["id"], identifier)
        self.assertEqual(status["selected_model"], identifier)

    def test_safe_diagnostics_exclude_prompt_and_exception_text(self):
        manager = connection()
        manager.provider.discover_model_info = lambda: (_ for _ in ()).throw(
            ProviderError("provider_unavailable", "PRIVATE FINANCIAL PROMPT"))
        serialized = json.dumps(manager.refresh())
        self.assertIn("provider_unavailable", serialized)
        self.assertNotIn("PRIVATE FINANCIAL PROMPT", serialized)
        self.assertEqual(manager.endpoint, "127.0.0.1:1234")

    def test_no_automatic_cloud_fallback_after_local_failure(self):
        manager = connection()
        manager.provider.discover_model_info = lambda: (_ for _ in ()).throw(
            ProviderError("connection_error", "closed"))
        manager.refresh()
        self.assertIsInstance(manager.provider, LMStudioProvider)
        self.assertEqual(manager.provider.name, "lmstudio")


class LocalAIApplicationBoundaryTests(unittest.TestCase):
    def test_model_selection_does_not_mutate_financial_world(self):
        manager = connection((model("alpha"), model("beta")))
        app = DemoApplication(agent_provider=SequenceProvider([realistic_output()]),
                              local_ai=manager)
        before = app.view()
        app.select_local_ai_model("beta")
        after = app.view()
        self.assertEqual(after["revision"], before["revision"])
        self.assertEqual(after["audit"], before["audit"])
        self.assertEqual(after["plan"], before["plan"])

    def test_model_selection_does_not_mutate_existing_candidate(self):
        manager = connection((model("alpha"), model("beta")), selected="alpha")
        app = DemoApplication(agent_provider=SequenceProvider([realistic_output()]),
                              local_ai=manager)
        before = app.view()
        candidate = app.interpret_agent_input(
            "真实长文本", {"generation": before["generation"],
                         "revision": before["revision"]})
        app.select_local_ai_model("beta")
        self.assertEqual(app.view()["agent_candidate"], candidate)

    def test_not_ready_provider_fails_before_candidate_creation(self):
        manager = connection((model("alpha"), model("beta")))
        app = DemoApplication(agent_provider=SequenceProvider([realistic_output()]),
                              local_ai=manager)
        view = app.view()
        with self.assertRaisesRegex(Exception, "not ready"):
            app.interpret_agent_input("text", {
                "generation": view["generation"], "revision": view["revision"]})
        self.assertIsNone(app.view()["agent_candidate"])
        self.assertEqual(app.view()["revision"], view["revision"])

    def test_ready_real_application_path_creates_review_only_candidate(self):
        manager = connection((model("alpha"),), selected="alpha")
        app = DemoApplication(agent_provider=SequenceProvider([realistic_output()]),
                              local_ai=manager)
        view = app.view()
        candidate = app.interpret_agent_input("真实长文本", {
            "generation": view["generation"], "revision": view["revision"]})
        self.assertEqual(candidate["status"], "awaiting_confirmation")
        self.assertEqual(candidate["proposed_change"]["committable_count"], 0)
        self.assertEqual(app.view()["revision"], view["revision"])


class LocalAIFrontendContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.shared = (ROOT / "web/shared.js").read_text()
        cls.desktop = (ROOT / "web/desktop.html").read_text()
        cls.mobile = (ROOT / "web/mobile.html").read_text()
        cls.i18n = (ROOT / "web/i18n.js").read_text()
        cls.css = (ROOT / "web/experience.css").read_text()

    def test_selection_persistence_uses_local_configuration_not_financial_state(self):
        self.assertIn("finvis-local-model", self.shared)
        self.assertIn("localStorage.setItem(LOCAL_MODEL_KEY", self.shared)
        self.assertNotIn("localModel", (ROOT / "finvis/domain.py").read_text())

    def test_refresh_and_selection_use_dedicated_local_ai_routes(self):
        self.assertIn("/api/local-ai/status", self.shared)
        self.assertIn("/api/local-ai/select", self.shared)

    def test_english_status_and_actionable_messages_exist(self):
        for value in ("LM Studio offline", "No model loaded", "Choose a model",
                      "Selected model unavailable", "Ready"):
            self.assertIn(value, self.i18n)

    def test_simplified_chinese_status_and_actionable_messages_exist(self):
        for value in ("LM Studio 离线", "没有已加载模型", "请选择模型",
                      "已选模型不可用", "已就绪"):
            self.assertIn(value, self.i18n)

    def test_desktop_exposes_compact_status_and_connection_dialog(self):
        self.assertIn('id="local-ai-trigger"', self.desktop)
        self.assertIn('id="local-ai-dialog"', self.desktop)
        self.assertIn('id="local-ai-models"', self.desktop)

    def test_mobile_exposes_same_logical_connection_controls(self):
        self.assertIn('id="local-ai-trigger"', self.mobile)
        self.assertIn('id="local-ai-dialog"', self.mobile)
        self.assertIn('id="local-ai-refresh"', self.mobile)

    def test_long_model_ids_have_explicit_wrap_contract(self):
        self.assertIn("overflow-wrap:anywhere", self.css)
        self.assertIn(".local-model-row", self.css)

    def test_primary_ui_does_not_render_raw_errors_prompts_or_secrets(self):
        self.assertNotIn("OPENAI_API_KEY", self.shared + self.desktop + self.mobile)
        self.assertNotIn("exception", self.desktop.lower() + self.mobile.lower())
        self.assertNotIn("candidate content", self.desktop.lower() + self.mobile.lower())

    def test_ui_states_cover_required_truthful_categories(self):
        for state in ("server_offline", "no_model_loaded", "ready",
                      "selection_required", "selected_model_unavailable",
                      "configured_model_not_found", "busy", "provider_error"):
            self.assertIn("localAI.state." + state, self.i18n)

    def test_unreachable_finvis_status_is_not_reported_as_lm_studio_failure(self):
        # A retained browser tab may outlive its local FINVIS Host. This state
        # must not claim LM Studio was checked or that no model was loaded.
        self.assertIn("state:'app_unavailable'", self.shared)
        self.assertIn("error_category:'app_status_unavailable'", self.shared)
        self.assertIn("status.state==='app_unavailable'?'localAI.notChecked'", self.shared)
        self.assertNotIn("state:'provider_error',connection:'error',provider:'LM Studio',endpoint:null", self.shared)
        for phrase in ("FINVIS unavailable", "FINVIS could not check the loaded local models.",
                       "FINVIS 不可用", "FINVIS 暂时无法检查已加载的本地模型。"):
            self.assertIn(phrase, self.i18n)

    def test_current_ui_keeps_candidate_review_only_boundary_visible(self):
        self.assertIn("reviewable Candidate", self.i18n)
        self.assertIn("待审阅候选", self.i18n)
        self.assertIn("agent.previewBoundary", self.i18n)


if __name__ == "__main__":
    unittest.main()

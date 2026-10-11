"""Provider adapters for local-first Agent Understanding; no financial logic."""

from dataclasses import dataclass
from datetime import datetime, timezone
from http.client import HTTPException
import json
import os
import socket
from threading import RLock
import time
from typing import Any, Dict, Optional, Protocol, Sequence, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from .agent_schema import interpretation_json_schema


class ProviderError(RuntimeError):
    def __init__(self, category: str, message: str, diagnostics=None):
        super().__init__(message)
        self.category = category
        # Only code-generated metadata belongs here, never exception messages,
        # provider bodies, financial input or reasoning content.
        self.diagnostics = dict(diagnostics or {})


class AgentModelProvider(Protocol):
    name: str
    def complete(self, system_prompt: str, user_text: str) -> Tuple[Any, Dict[str, Any]]: ...


@dataclass(frozen=True)
class LocalModelInfo:
    id: str
    display_name: str
    runtime: str
    loaded: bool

    def as_dict(self):
        return {"id": self.id, "display_name": self.display_name,
                "runtime": self.runtime, "loaded": self.loaded}


@dataclass(frozen=True)
class AgentProviderConfig:
    provider: str = "lmstudio"
    lmstudio_base_url: str = "http://127.0.0.1:1234/v1"
    lmstudio_model: Optional[str] = None
    openai_model: Optional[str] = None
    timeout_seconds: float = 90.0
    lmstudio_reasoning_effort: Optional[str] = "none"
    max_output_tokens: int = 1536

    @classmethod
    def from_env(cls):
        try:
            timeout = float(os.environ.get("FINVIS_AGENT_TIMEOUT_SECONDS", "90"))
        except ValueError:
            timeout = 90.0
        try:
            max_output_tokens = int(os.environ.get("FINVIS_AGENT_MAX_OUTPUT_TOKENS", "1536"))
        except ValueError:
            max_output_tokens = 1536
        reasoning_effort = os.environ.get("FINVIS_AGENT_REASONING_EFFORT", "none").strip() or None
        return cls(provider=os.environ.get("FINVIS_AGENT_PROVIDER", "lmstudio").strip().lower(),
                   lmstudio_base_url=os.environ.get("FINVIS_LMSTUDIO_BASE_URL", "http://127.0.0.1:1234/v1").rstrip("/"),
                   lmstudio_model=os.environ.get("FINVIS_LMSTUDIO_MODEL") or None,
                   openai_model=os.environ.get("FINVIS_OPENAI_MODEL") or None,
                   timeout_seconds=max(1.0, min(timeout, 120.0)),
                   lmstudio_reasoning_effort=reasoning_effort,
                   max_output_tokens=max(64, min(max_output_tokens, 4096)))


def _read_json(request: Request, timeout: float) -> Dict[str, Any]:
    started = time.monotonic()
    stage = "connect_or_headers"

    def failure(category, message, error=None, **metadata):
        diagnostics = dict(stage=stage, elapsed_ms=round((time.monotonic() - started) * 1000),
                           timeout_seconds=timeout, **metadata)
        if error is not None:
            diagnostics["exception_type"] = type(error).__name__
            if isinstance(getattr(error, "errno", None), int):
                diagnostics["errno"] = error.errno
        return ProviderError(category, message, diagnostics)

    try:
        with urlopen(request, timeout=timeout) as response:
            stage = "read_body"
            raw = response.read(1_048_577)
            if len(raw) > 1_048_576:
                raise failure("response_too_large", "Provider response exceeded the safe limit")
            # HTTPResponse.read(amt) can silently return a short body on EOF.
            remaining = getattr(response, "length", None)
            if isinstance(remaining, int) and remaining > 0:
                raise failure("transport_interrupted", "Provider response body was interrupted")
            if not raw:
                raise failure("empty_response", "Provider returned an empty response body")
            stage = "decode_json"
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise failure("malformed_response", "Provider response must be a JSON object")
            if data.get("error") is not None:
                raise failure("server_error", "Provider returned an explicit error")
            return data
    except ProviderError:
        raise
    except HTTPError as error:
        category = ("model_not_found" if error.code == 404 else
                    "server_error" if error.code >= 500 else "provider_http_error")
        error.close()
        raise failure(category, f"Provider returned HTTP {error.code}", error, http_status=error.code) from None
    except (socket.timeout, TimeoutError) as error:
        # socket.timeout is not a TimeoutError subclass on supported Python 3.9.
        raise failure("timeout", "Provider request timed out", error) from None
    except URLError as error:
        if isinstance(error.reason, (socket.timeout, TimeoutError)):
            raise failure("timeout", "Provider request timed out", error.reason) from None
        if isinstance(error.reason, (ConnectionResetError, ConnectionAbortedError, BrokenPipeError)):
            raise failure("transport_interrupted", "Provider connection was interrupted", error.reason) from None
        raise failure("connection_error", "Provider connection failed", error.reason) from None
    except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError, EOFError, HTTPException) as error:
        raise failure("transport_interrupted", "Provider response transport was interrupted", error) from None
    except ConnectionError as error:
        raise failure("connection_error", "Provider connection failed", error) from None
    except OSError as error:
        raise failure("provider_unavailable", "Provider transport is unavailable", error) from None
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise failure("malformed_response", "Provider returned malformed JSON", error) from None


def _local_base_url(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ProviderError("unsafe_configuration", "LM Studio endpoint must use local loopback HTTP")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ProviderError("unsafe_configuration", "LM Studio endpoint is invalid")
    return value.rstrip("/")


class LMStudioProvider:
    name = "lmstudio"
    def __init__(self, config: AgentProviderConfig):
        self.base_url = _local_base_url(config.lmstudio_base_url)
        parsed = urlsplit(self.base_url)
        self.management_url = f"{parsed.scheme}://{parsed.netloc}/api/v1/models"
        self.model = config.lmstudio_model
        self.timeout = config.timeout_seconds
        self.reasoning_effort = config.lmstudio_reasoning_effort
        self.max_output_tokens = config.max_output_tokens

    def discover_model_info(self) -> Tuple[LocalModelInfo, ...]:
        """Read LM Studio's loaded-instance state, not its broad catalog."""
        data = _read_json(Request(self.management_url, headers={"Accept": "application/json"}),
                          min(self.timeout, 5.0))
        models = data.get("models")
        if not isinstance(models, list):
            raise ProviderError("malformed_response", "Model discovery response is invalid")
        result = []
        for item in models:
            if not isinstance(item, dict) or item.get("type") != "llm":
                continue
            identity = item.get("key")
            instances = item.get("loaded_instances")
            if not isinstance(identity, str) or not identity or not isinstance(instances, list):
                raise ProviderError("malformed_response", "Model discovery response is invalid")
            display_name = item.get("display_name")
            if not isinstance(display_name, str) or not display_name:
                display_name = identity
            runtime = item.get("format")
            runtime = runtime.upper() if isinstance(runtime, str) and runtime else "Local"
            quantization = item.get("quantization")
            if isinstance(quantization, dict) and isinstance(quantization.get("name"), str):
                runtime += " · " + quantization["name"]
            result.append(LocalModelInfo(identity, display_name, runtime, bool(instances)))
        return tuple(result)

    def discover_models(self) -> Tuple[str, ...]:
        return tuple(model.id for model in self.discover_model_info() if model.loaded)

    def selected_model(self) -> str:
        discovered = self.discover_model_info()
        loaded = tuple(model.id for model in discovered if model.loaded)
        discovered_ids = {model.id for model in discovered}
        if self.model:
            if self.model in loaded:
                return self.model
            category = ("selected_model_unavailable" if self.model in discovered_ids
                        else "configured_model_not_found")
            raise ProviderError(category, "The selected local model is not loaded")
        if not loaded:
            raise ProviderError("no_model_loaded", "No local language model is loaded")
        if len(loaded) != 1:
            raise ProviderError("model_not_configured", "Set FINVIS_LMSTUDIO_MODEL when multiple models are loaded")
        self.model = loaded[0]
        return loaded[0]

    def complete(self, system_prompt: str, user_text: str) -> Tuple[Any, Dict[str, Any]]:
        return self._complete_schema(system_prompt, user_text, interpretation_json_schema(), "finvis_candidate_interpretation")

    def complete_explanation(self, system_prompt, user_text, schema):
        return self._complete_schema(system_prompt, user_text, schema, "finvis_grounded_explanation")

    def _complete_schema(self, system_prompt, user_text, schema, schema_name):
        model = self.selected_model()
        body = {
            "model": model,
            "messages": [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_text}],
            "temperature": 0,
            "max_tokens": self.max_output_tokens,
            "response_format": {"type": "json_schema", "json_schema": {
                "name": schema_name, "strict": True,
                "schema": schema,
            }},
        }
        if self.reasoning_effort is not None:
            body["reasoning_effort"] = self.reasoning_effort
        request = Request(self.base_url + "/chat/completions", data=json.dumps(body).encode(), method="POST",
                          headers={"Content-Type": "application/json", "Accept": "application/json"})
        started = time.monotonic()
        data = _read_json(request, self.timeout)
        latency = round((time.monotonic() - started) * 1000)
        diagnostics = {"stage": "final_content", "elapsed_ms": latency}
        try:
            choice = data["choices"][0]
            if not isinstance(choice, dict):
                raise TypeError()
            if choice.get("finish_reason") == "length":
                raise ProviderError("truncated_response", "Provider response reached the output token limit", diagnostics)
            message = choice["message"]
            if not isinstance(message, dict):
                raise TypeError()
            if message.get("refusal"):
                raise ProviderError("provider_refusal", "Provider refused the interpretation request", diagnostics)
            content = message.get("content")
        except (KeyError, IndexError, TypeError):
            raise ProviderError("malformed_response", "Provider response did not contain message content", diagnostics) from None
        if not isinstance(content, str) or not content.strip():
            raise ProviderError("empty_final_content", "Provider response did not contain final output text",
                                diagnostics)
        return content, {"provider": self.name, "model": model, "latency_ms": latency}


class OpenAIProvider:
    """Optional explicit cloud adapter. It is never selected as an automatic fallback."""
    name = "openai"
    def __init__(self, config: AgentProviderConfig, api_key: Optional[str] = None):
        self.model = config.openai_model
        self.api_key = api_key if api_key is not None else os.environ.get("OPENAI_API_KEY")
        self.timeout = config.timeout_seconds

    def complete(self, system_prompt: str, user_text: str) -> Tuple[Any, Dict[str, Any]]:
        if not self.api_key:
            raise ProviderError("missing_api_key", "OpenAI provider is not configured")
        if not self.model:
            raise ProviderError("model_not_configured", "FINVIS_OPENAI_MODEL is required")
        body = {"model": self.model, "instructions": system_prompt, "input": user_text, "store": False,
                "text": {"format": {"type": "json_schema", "name": "finvis_candidate_interpretation",
                                    "strict": True, "schema": interpretation_json_schema()}}}
        request = Request("https://api.openai.com/v1/responses", data=json.dumps(body).encode(), method="POST",
                          headers={"Content-Type": "application/json", "Accept": "application/json",
                                   "Authorization": "Bearer " + self.api_key})
        started = time.monotonic()
        data = _read_json(request, self.timeout)
        latency = round((time.monotonic() - started) * 1000)
        parts = []
        refused = False
        for item in data.get("output", []):
            if isinstance(item, dict):
                for content in item.get("content", []):
                    if isinstance(content, dict) and content.get("type") == "refusal":
                        refused = True
                    elif isinstance(content, dict) and content.get("type") == "output_text":
                        parts.append(content.get("text"))
        if refused:
            raise ProviderError("provider_refusal", "Provider refused the interpretation request")
        if not parts or any(not isinstance(part, str) for part in parts):
            raise ProviderError("malformed_response", "OpenAI response did not contain output text")
        return "".join(parts), {"provider": self.name, "model": self.model, "latency_ms": latency}


class DeterministicFallback:
    """Canonical routing remains in the existing UI; this object makes the boundary explicit."""
    name = "deterministic"
    def complete(self, _system_prompt: str, _user_text: str):
        raise ProviderError("deterministic_only", "Only canonical deterministic commands are available")


class LocalAIConnection:
    """Process-local local-model configuration and truthful discovery status."""

    def __init__(self, config: Optional[AgentProviderConfig] = None,
                 provider: Optional[AgentModelProvider] = None):
        self.config = config or AgentProviderConfig.from_env()
        self.provider = provider or configured_provider(self.config)
        self._lock = RLock()
        self._models: Tuple[LocalModelInfo, ...] = ()
        self._selected = (self.provider.model if isinstance(self.provider, LMStudioProvider)
                          else None)
        self._last_successful_check = None
        self._error_category = None
        self._connection = "disconnected"
        self._busy = 0

    @property
    def endpoint(self):
        if not isinstance(self.provider, LMStudioProvider):
            return None
        parsed = urlsplit(self.provider.base_url)
        return parsed.netloc

    def _snapshot(self, state):
        models = tuple(model.as_dict() for model in self._models)
        loaded = tuple(model.id for model in self._models if model.loaded)
        return {
            "provider": "LM Studio" if isinstance(self.provider, LMStudioProvider)
                        else getattr(self.provider, "name", "unavailable"),
            "connection": self._connection,
            "endpoint": self.endpoint,
            "selected_model": self._selected,
            "models": models,
            "loaded_model_ids": loaded,
            "state": "busy" if self._busy and state == "ready" else state,
            "ready": state == "ready" and not self._busy,
            "busy": bool(self._busy),
            "requests_enabled": state == "ready" and not self._busy,
            "last_successful_check": self._last_successful_check,
            "error_category": self._error_category,
        }

    def _state(self):
        if not isinstance(self.provider, LMStudioProvider):
            return "provider_error"
        loaded = tuple(model.id for model in self._models if model.loaded)
        discovered = {model.id for model in self._models}
        if self._selected:
            if self._selected in loaded:
                return "ready"
            return ("selected_model_unavailable" if self._selected in discovered
                    else "configured_model_not_found")
        if not loaded:
            return "no_model_loaded"
        if len(loaded) > 1:
            return "selection_required"
        return "ready"

    def refresh(self):
        with self._lock:
            if not isinstance(self.provider, LMStudioProvider):
                self._connection = "error"
                self._error_category = "unsupported_provider"
                return self._snapshot("provider_error")
            try:
                self._models = self.provider.discover_model_info()
                self._connection = "connected"
                self._error_category = None
                self._last_successful_check = datetime.now(timezone.utc).isoformat()
                loaded = tuple(model.id for model in self._models if model.loaded)
                if self._selected is None and len(loaded) == 1:
                    self._selected = loaded[0]
                self.provider.model = self._selected
                return self._snapshot(self._state())
            except ProviderError as error:
                self._models = ()
                self._error_category = error.category
                if error.category in {"connection_error", "provider_unavailable",
                                      "transport_interrupted", "timeout"}:
                    self._connection = "disconnected"
                    return self._snapshot("server_offline")
                self._connection = "error"
                return self._snapshot("provider_error")

    def select(self, model_id: str):
        if not isinstance(model_id, str) or not model_id.strip() or len(model_id) > 512:
            raise ProviderError("invalid_input", "A local model identifier is required")
        with self._lock:
            if self._busy:
                raise ProviderError("provider_busy", "Wait for the current local request to finish")
            self._selected = model_id.strip()
            if isinstance(self.provider, LMStudioProvider):
                self.provider.model = self._selected
            return self.refresh()

    def begin_request(self):
        with self._lock:
            self._busy += 1

    def end_request(self):
        with self._lock:
            self._busy = max(0, self._busy - 1)

    def status(self):
        with self._lock:
            return self._snapshot(self._state())


def configured_provider(config: Optional[AgentProviderConfig] = None) -> AgentModelProvider:
    config = config or AgentProviderConfig.from_env()
    if config.provider == "lmstudio":
        return LMStudioProvider(config)
    if config.provider == "openai":
        return OpenAIProvider(config)
    if config.provider == "deterministic":
        return DeterministicFallback()
    raise ProviderError("unknown_provider", "FINVIS_AGENT_PROVIDER is unsupported")

"""Opt-in production Responses adapter, independent of frozen benchmark sources."""
import os,json,time
from typing import Optional
from .agent_providers import AgentProviderConfig,ProviderError
from .agent_schema import interpretation_json_schema
from .openai_schema import strict_schema

class OpenAIProvider:
    """Explicit server-configured cloud interpreter; no automatic retries."""
    name = "openai"
    no_auto_retry = True
    cloud = True
    def __init__(self, config: AgentProviderConfig, api_key: Optional[str] = None, transport=None):
        from .openai_transport import Transport, MODEL
        self.model = config.openai_model or MODEL
        self.api_key = api_key if api_key is not None else os.environ.get("OPENAI_API_KEY", "")
        self.enabled = os.environ.get('FINVIS_OPENAI_ENABLED') == '1'
        self.timeout = max(1., min(90., config.timeout_seconds))
        self.transport = transport or Transport()
        from threading import Lock
        self._call_lock = Lock()

    def configuration_error(self):
        from .openai_transport import MODEL
        if not self.api_key:return 'missing_api_key'
        if not self.enabled:return 'cloud_disabled'
        if any(c.isspace() for c in self.api_key):return 'credentials_rejected'
        if self.model != MODEL:return 'model_unavailable'
        return None

    def complete(self, system_prompt, user_text):
        return self.complete_explanation(system_prompt,user_text,interpretation_json_schema())

    def complete_explanation(self, system_prompt, user_text, schema):
        from .openai_transport import INPUT_CAP, OUTPUT_CAP, fail, decode, secret_free
        error=self.configuration_error()
        if error:fail(error)
        if not self._call_lock.acquire(blocking=False):fail('provider_busy')
        try:
            body={'model':self.model,'instructions':system_prompt,'input':user_text,
                'text':{'format':{'type':'json_schema','name':'finvis_compound_meaning','strict':True,'schema':strict_schema(schema)}},
                'max_output_tokens':OUTPUT_CAP,'reasoning':{'effort':'low'},'store':False,'service_tier':'default'}
            secret_free(body,self.api_key)
            if len(json.dumps(body).encode())>262144:fail('input_token_limit')
            started=time.monotonic()
            count=self.transport('POST','/v1/responses/input_tokens',
                {k:body[k] for k in ('model','instructions','input','text')},self.api_key,self.timeout)
            n=count.get('input_tokens')
            if type(n) is not int or n<1:fail('invalid_token_count')
            if n>INPUT_CAP:fail('input_token_limit')
            remaining=self.timeout-(time.monotonic()-started)
            if remaining<=0:fail('timeout')
            value=self.transport('POST','/v1/responses',body,self.api_key,remaining)
            raw,metadata=decode(secret_free(value,self.api_key))
            metadata['latency_ms']=round((time.monotonic()-started)*1000)
            return raw,metadata
        finally:self._call_lock.release()



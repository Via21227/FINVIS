"""Session-only explicit provider choice. One stable reference for Demo/User."""
from threading import RLock
from secrets import token_urlsafe
from .agent_providers import (AgentProviderConfig, LMStudioProvider,
    LocalAIConnection, ProviderError)
from .openai_provider import OpenAIProvider

class ProviderSelection:
    def __init__(self, config=None, local=None, cloud=None):
        config=config or AgentProviderConfig.from_env()
        self.local=local or LocalAIConnection(provider=LMStudioProvider(config))
        self.cloud=cloud or OpenAIProvider(config)
        self.selected_provider='openai' if config.provider=='openai' else 'lmstudio'
        self.cloud_ack=False;self.consent_token=None
        self.credential_source='environment' if self.cloud.api_key else 'none'
        self._lock=RLock();self._busy=0
        self.provider=Router(self)

    def refresh(self):
        with self._lock:
            if self.selected_provider=='lmstudio':s=self.local.refresh()
            else:
                error=self.cloud.configuration_error()
                state=error or ('cloud_configured' if self.cloud_ack else 'cloud_consent_required')
                enabled=not error and self.cloud_ack
                s=dict(provider='OpenAI',connection='configured' if not error else 'unavailable',
                    endpoint='api.openai.com',selected_model=self.cloud.model,models=[],loaded_model_ids=[],
                    state=state,ready=enabled,requests_enabled=enabled,last_successful_check=None,error_category=error)
            s=dict(s,provider_id=self.selected_provider,provider_choices=True,cloud=self.selected_provider=='openai')
            s['credential_source']=self.credential_source
            s['credential_present']=bool(self.cloud.api_key)
            s['busy']=bool(self._busy)
            if self._busy:s.update(state='busy',ready=False,requests_enabled=False)
            return s

    status=refresh

    def select_provider(self, name, cloud_ack=False):
        with self._lock:
            if self._busy:raise ProviderError('provider_busy','Wait for current request')
            if name not in ('lmstudio','openai'):raise ProviderError('invalid_input','Unknown provider')
            if name=='openai' and cloud_ack is not True:raise ProviderError('cloud_consent_required','Cloud acknowledgement required')
            self.selected_provider=name;self.cloud_ack=name=='openai' and cloud_ack
            self.consent_token=token_urlsafe(32) if self.cloud_ack else None
            return dict(self.refresh(),cloud_consent_token=self.consent_token)

    def session_credential(self, action, api_key=None, cloud_ack=False):
        """Explicit local UI opt-in; no environment mutation, disk storage or probe."""
        with self._lock:
            if self._busy:raise ProviderError('provider_busy','Wait for current request')
            if action == 'connect':
                if cloud_ack is not True:
                    raise ProviderError('cloud_consent_required','Cloud acknowledgement required')
                # Bounded opaque header value. Format checks are not authentication.
                if (type(api_key) is not str or not 16 <= len(api_key) <= 512 or
                        any(not 33 <= ord(c) <= 126 for c in api_key)):
                    raise ProviderError('credentials_rejected','Invalid credential format')
                self.cloud.api_key=api_key
                self.cloud.enabled=True
                self.credential_source='session'
                self.selected_provider='openai'
                self.cloud_ack=True
                self.consent_token=token_urlsafe(32)
            elif action == 'disconnect':
                # Do not silently fall back to an inherited key or another provider.
                self.cloud.api_key=''
                self.cloud.enabled=False
                self.credential_source='none'
                self.cloud_ack=False
                self.consent_token=None
            else:
                raise ProviderError('invalid_input','Unknown credential action')
            return dict(self.refresh(),cloud_consent_token=self.consent_token)

    def select(self, model_id):
        with self._lock:
            if self._busy:raise ProviderError('provider_busy','Wait for current request')
            if self.selected_provider!='lmstudio':raise ProviderError('invalid_input','Local selection only')
            self.local.select(model_id)
            return self.refresh()

    def authorize_request(self, consent):
        with self._lock:
            if self._busy:raise ProviderError('provider_busy','Wait for current request')
            if self.selected_provider=='openai':
                if not self.cloud_ack or not consent or consent!=self.consent_token:
                    raise ProviderError('cloud_consent_required','Acknowledge cloud in this tab')
                error=self.cloud.configuration_error()
                if error:raise ProviderError(error,'Cloud unavailable')
            elif not self.local.refresh()['requests_enabled']:
                raise ProviderError(self.local.status()['state'],'Local model unavailable')
            self._busy=1

    def begin_local_request(self):
        """Atomically reserve local-only explanation; never route it to cloud."""
        with self._lock:
            if self.selected_provider!='lmstudio':
                raise ProviderError('local_only','Local explanation unavailable')
            self.authorize_request(None)

    def begin_request(self):
        with self._lock:
            if self._busy:raise ProviderError('provider_busy','Wait for current request')
            self._busy=1
    def end_request(self):
        with self._lock:self._busy=0

class Router:
    def __init__(self, selection):self.selection=selection
    @property
    def selected_provider(self):return self.selection.selected_provider
    @property
    def name(self):return self.selected_provider
    @property
    def no_auto_retry(self):return self.selected_provider=='openai'
    @property
    def cloud(self):return self.selected_provider=='openai'
    def _active(self):
        if self.cloud:
            if not self.selection.cloud_ack:raise ProviderError('cloud_consent_required','Cloud acknowledgement required')
            return self.selection.cloud
        return self.selection.local.provider
    def complete(self, prompt, text):
        return self._active().complete(prompt,text)
    def complete_explanation(self, prompt, text, schema):
        return self._active().complete_explanation(prompt,text,schema)


def production_connection():
    config=AgentProviderConfig.from_env()
    if config.provider not in ('lmstudio','openai'):
        return LocalAIConnection(config=config)
    return ProviderSelection(config=config)

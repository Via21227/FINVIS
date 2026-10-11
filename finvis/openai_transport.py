"""Bounded production OpenAI transport: fixed TLS host, no proxy/retry/logs."""
import http.client
import json
import queue
import re
import socket
import threading
from .agent_providers import ProviderError

INPUT_CAP = 13500
OUTPUT_CAP = 1536
MODEL = 'gpt-6-astra'

def fail(code, diagnostics=None):
    raise ProviderError(code, 'OpenAI request unavailable', diagnostics) from None

def pairs(items):
    result = {}
    for k,v in items:
        if k in result: fail('malformed_response')
        result[k] = v
    return result

def secret_free(value, key):
    text = json.dumps(value, ensure_ascii=False)
    if key and key in text: fail('sensitive_response_discarded')
    if re.search(r'(?i)\bsk-[a-z0-9_-]{8,}|bearer\s+\S+|-----BEGIN .*PRIVATE KEY', text):
        fail('sensitive_response_discarded')
    return value

def http_diagnostics(raw, status, path, key):
    """Finite vocabulary only: error messages, headers and payloads never escape."""
    result = {'http_status': status, 'stage': 'token_count' if path.endswith('input_tokens') else 'inference'}
    try:
        value = json.loads(raw, object_pairs_hook=pairs)
        error = value.get('error', {}) if isinstance(value, dict) else {}
        if not isinstance(error, dict):
            return result
        allowed = {
            'type': {'invalid_request_error', 'authentication_error', 'permission_error', 'rate_limit_error', 'server_error'},
            'code': {'invalid_json_schema', 'unsupported_parameter', 'unknown_parameter', 'invalid_value', 'invalid_parameter', 'model_not_found', 'insufficient_quota'},
            'param': {'model', 'input', 'instructions', 'text', 'text.format', 'text.format.schema', 'text.format.name', 'text.format.strict', 'reasoning', 'reasoning.effort', 'max_output_tokens', 'store', 'service_tier'},
        }
        for name, choices in allowed.items():
            item = error.get(name)
            if isinstance(item, str) and item in choices and (not key or key not in item):
                result['api_' + name] = item
    except (ValueError, UnicodeError, ProviderError):
        pass
    return result

class Transport:
    def __call__(self, method, path, body, key, timeout):
        if (method,path) not in {('POST','/v1/responses/input_tokens'),('POST','/v1/responses')}:
            fail('endpoint_not_allowed')
        conn = http.client.HTTPSConnection('api.openai.com', timeout=timeout)
        outcome = queue.Queue(maxsize=1)
        def work():
            try:
                conn.request(method,path,json.dumps(body).encode(),
                    {'Authorization':'Bearer '+key,'Content-Type':'application/json'})
                response=conn.getresponse()
                raw=response.read(2*1024*1024+1)
                if len(raw)>2*1024*1024: fail('response_too_large')
                if response.status != 200:
                    code={401:'credentials_rejected',403:'access_denied',404:'model_unavailable',
                        429:'rate_limited',400:'request_contract_rejected'}.get(response.status,
                        'provider_server_error' if response.status>=500 else 'provider_http_error')
                    if response.status==429:
                        try:
                            if json.loads(raw).get('error',{}).get('code')=='insufficient_quota':code='insufficient_credits'
                        except (ValueError,AttributeError):pass
                    fail(code, http_diagnostics(raw, response.status, path, key))
                value=json.loads(raw,object_pairs_hook=pairs,parse_constant=lambda _:fail('malformed_response'))
                if not isinstance(value,dict):fail('malformed_response')
                outcome.put((True,secret_free(value,key)))
            except ProviderError as e:outcome.put((False,e))
            except (TimeoutError,socket.timeout):outcome.put((False,'timeout'))
            except (ValueError,UnicodeError):outcome.put((False,'malformed_response'))
            except Exception:outcome.put((False,'transport_interrupted'))
            finally:conn.close()
        threading.Thread(target=work,daemon=True).start()
        try:ok,value=outcome.get(timeout=timeout)
        except queue.Empty:
            conn.close();fail('timeout')
        if not ok:
            if isinstance(value, ProviderError):raise value from None
            fail(value)
        return value

def decode(value):
    if not isinstance(value,dict):fail('malformed_response')
    if value.get('status')=='incomplete':fail('truncated_response')
    if value.get('status')!='completed' or value.get('error'):fail('response_not_completed')
    output=value.get('output')
    if not isinstance(output,list):fail('malformed_response')
    texts=[]
    for item in output:
        if not isinstance(item,dict):fail('malformed_response')
        if item.get('type')=='reasoning':continue
        if item.get('type')!='message' or item.get('role')!='assistant' or item.get('status')!='completed':fail('unexpected_output')
        if not isinstance(item.get('content'),list):fail('malformed_response')
        for part in item['content']:
            if not isinstance(part,dict):fail('malformed_response')
            if part.get('type')=='refusal':fail('provider_refusal')
            if part.get('type')!='output_text' or not isinstance(part.get('text'),str):fail('unexpected_output')
            texts.append(part['text'])
    if len(texts)!=1 or not texts[0].strip():fail('empty_final_content')
    usage=value.get('usage',{})
    if not isinstance(usage,dict):fail('invalid_usage')
    for k,limit in [('input_tokens',INPUT_CAP),('output_tokens',OUTPUT_CAP)]:
        if type(usage.get(k)) is not int or not 0<=usage[k]<=limit:fail('invalid_usage')
    if not isinstance(value.get('model'),str) or not value['model']:fail('missing_model_identity')
    return texts[0],{'provider':'openai','model':value['model'],
        'usage':{k:usage[k] for k in ('input_tokens','output_tokens')},'store':False}

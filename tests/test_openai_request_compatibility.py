"""Offline compatibility, equivalence and safe diagnostic regressions."""
import json,unittest
from copy import deepcopy
from unittest.mock import patch
from finvis.agent_providers import ProviderError
from finvis.agent_schema import interpretation_json_schema,FACT_TYPES,TEMPORAL_STATUSES
from finvis.compound_meaning import bundle_schema
from finvis.compound_payments import schema as payment_schema
from finvis.openai_schema import strict_schema
from finvis.openai_transport import Transport,http_diagnostics
from tests.test_agent_understanding import schema_accepts
from tests.test_production_openai import FakeTransport,cloud,response


def closed_objects(node):
    if not isinstance(node,dict):return
    if 'properties' in node:
        assert node.get('type')=='object'
        assert node.get('additionalProperties') is False
        assert set(node.get('required',[]))==set(node['properties'])
        for value in node['properties'].values():closed_objects(value)
    for branch in node.get('anyOf',[]):closed_objects(branch)
    if 'items' in node:closed_objects(node['items'])


def fact(kind='current_asset',status='current',amount=None):
    return dict(id='synthetic',type=kind,label='Synthetic reserve',account='Synthetic Bank',amount=amount,earmark=None,purpose=None,temporal_status=status,timing_note=None,source='user_report')

class SchemaCompatibilityTests(unittest.TestCase):
    def test_old_failure_and_new_complete_closed_unions(self):
        old=interpretation_json_schema()
        with self.assertRaises(AssertionError):closed_objects(old)
        new=strict_schema(old);closed_objects(new)
        branches=new['properties']['candidate_facts']['items']['anyOf']
        self.assertEqual(len(branches),9)
        self.assertTrue(all(len(b['properties'])==10 for b in branches))
    def test_270_type_time_amount_combinations_are_equivalent(self):
        old=interpretation_json_schema()['properties']['candidate_facts']['items']
        new=strict_schema(interpretation_json_schema())['properties']['candidate_facts']['items']
        amounts=[None]+[dict(currency='EUR',precision=p,value='73' if p in ['exact','approximate'] else None,range_min='60' if p=='range' else None,range_max='90' if p=='range' else None) for p in ['exact','approximate','range','unknown']]
        seen=0
        for kind in sorted(FACT_TYPES):
            for status in sorted(TEMPORAL_STATUSES):
                for amount in amounts:
                    with self.subTest(kind=kind,status=status,amount=amount):
                        v=fact(kind,status,amount);self.assertEqual(schema_accepts(v,old),schema_accepts(v,new));seen+=1
        self.assertEqual(seen,270)
    def test_missing_extra_and_wrong_type_still_rejected(self):
        old=interpretation_json_schema()['properties']['candidate_facts']['items'];new=strict_schema(interpretation_json_schema())['properties']['candidate_facts']['items']
        samples=[]
        for k in fact():
            v=fact();del v[k];samples.append(v)
        samples += [{**fact(),'unexpected':'value'},{**fact(),'label':42},{**fact(),'type':None},{**fact(),'source':'invented'}]
        for value in samples:self.assertFalse(schema_accepts(value,old));self.assertFalse(schema_accepts(value,new))
    def test_money_unknown_not_zero_range_precision_and_native_currency(self):
        old=interpretation_json_schema()['properties']['candidate_facts']['items'];new=strict_schema(interpretation_json_schema())['properties']['candidate_facts']['items']
        for p in ['exact','approximate','range','unknown']:
            for value in ['63',None,63]:
                for low,high in [(None,None),('50','75')]:
                    a=dict(currency='SGD',precision=p,value=value,range_min=low,range_max=high)
                    self.assertEqual(schema_accepts(fact(amount=a),old),schema_accepts(fact(amount=a),new))
    def test_projection_does_not_change_original_and_is_idempotent(self):
        old=interpretation_json_schema();copy=deepcopy(old);new=strict_schema(old)
        self.assertEqual(old,copy);self.assertEqual(new,strict_schema(new))
    def test_compound_and_payment_schemas_unchanged(self):
        for s in [bundle_schema(),payment_schema()]:self.assertEqual(strict_schema(s),s);closed_objects(s)
    def test_unsupported_conjunctions_fail_closed(self):
        base=interpretation_json_schema()['properties']['candidate_facts']['items']
        for mutate in [lambda x:x.update(additionalProperties=True),lambda x:x.update(required=[]),lambda x:x.update(anyOf=[]),lambda x:x.update(description='not supported'),lambda x:x['anyOf'][0]['properties']['type'].update(enum=['not-in-parent']),lambda x:x['anyOf'][0].update(type='object')]:
            s=deepcopy(base);mutate(s)
            with self.assertRaises(ProviderError):strict_schema(s)
    def test_count_and_inference_share_projection_and_prompt(self):
        t=FakeTransport(response({}));p=cloud(t);prompt='Unchanged synthetic protocol';p.complete(prompt,'Synthetic input')
        self.assertEqual(len(t.calls),2)
        for call in t.calls:
            self.assertEqual(call[2]['instructions'],prompt)
            self.assertEqual(call[2]['text']['format']['schema'],strict_schema(interpretation_json_schema()))
        self.assertEqual(t.calls[1][2]['reasoning'],{'effort':'low'})
        self.assertFalse(t.calls[1][2]['store'])

class ErrorDiagnosticTests(unittest.TestCase):
    def request(self,raw,status=400):
        with patch('finvis.openai_transport.http.client.HTTPSConnection') as c:
            res=c.return_value.getresponse.return_value;res.status=status;res.read.return_value=raw
            with self.assertRaises(ProviderError) as e:Transport()('POST','/v1/responses/input_tokens',{},'synthetic-secret',1)
            self.assertEqual(c.return_value.request.call_count,1)
            return e.exception
    def test_confirmed_schema_rejection_retains_only_safe_fields(self):
        e=self.request(json.dumps({'error':{'type':'invalid_request_error','code':'invalid_json_schema','param':'text.format.schema','message':'private account details synthetic-secret'}}).encode())
        self.assertEqual(e.category,'request_contract_rejected')
        self.assertEqual(e.diagnostics,dict(http_status=400,stage='token_count',api_type='invalid_request_error',api_code='invalid_json_schema',api_param='text.format.schema'))
        self.assertNotIn('private',str(e));self.assertNotIn('synthetic-secret',repr(e.diagnostics))
    def test_unstructured_malformed_duplicate_and_sensitive_fields_omitted(self):
        values=[b'private plaintext',b'[]',b'{"error":null}',b'{"error":{"code":"invalid_json_schema","code":"private"}}',json.dumps({'error':{'type':'private_person','code':'synthetic-secret','param':'Bank.balance','message':'sk-private-credential'}}).encode()]
        for raw in values:self.assertEqual(self.request(raw).diagnostics,dict(http_status=400,stage='token_count'))
    def test_unknown_parameter_and_invalid_value_are_safe(self):
        for code,param in [('unsupported_parameter','reasoning.effort'),('unknown_parameter','store'),('invalid_value','model')]:
            e=self.request(json.dumps({'error':dict(type='invalid_request_error',code=code,param=param)}).encode());self.assertEqual(e.diagnostics['api_code'],code);self.assertEqual(e.diagnostics['api_param'],param)
    def test_count_failure_never_infers_or_retries(self):
        with patch('finvis.openai_transport.http.client.HTTPSConnection') as c:
            res=c.return_value.getresponse.return_value;res.status=400;res.read.return_value=b'{"error":{"code":"invalid_json_schema","param":"text.format.schema"}}'
            p=cloud(Transport())
            with self.assertRaises(ProviderError):p.complete('Synthetic protocol','Synthetic input')
            self.assertEqual(c.return_value.request.call_count,1);self.assertEqual(c.return_value.request.call_args.args[1],'/v1/responses/input_tokens')
    def test_key_cannot_leak_even_if_it_matches_allowlist(self):
        d=http_diagnostics(b'{"error":{"code":"invalid_json_schema"}}',400,'/v1/responses','invalid_json_schema');self.assertNotIn('api_code',d)

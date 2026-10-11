"""I4 loopback API: exact apply, receipt lookup and authoritative Home read."""
import http.client
import json
from threading import Thread
import unittest

from finvis.application import DemoApplication
from finvis.web import STATIC
from tests.http_support import DemoServer
from tests.test_agent_understanding import SequenceProvider, realistic_output


TEXT = 'Workshop vault: 720 SGD. This is my current balance.'


def single_asset():
    value = realistic_output()
    fact = value['candidate_facts'][0]
    fact.update(id='vault', label='Workshop vault', account='Workshop vault',
                amount={'currency': 'SGD', 'value': '720', 'range_min': None,
                        'range_max': None, 'precision': 'exact'})
    value.update(language='en', default_currency='SGD', candidate_facts=[fact],
                 user_intents=['update_financial_state'], destructive_actions=[],
                 clarification_questions=[], user_question=None)
    return value


def review():
    source = {'start': 0, 'end': len(TEXT)}
    return {'facts': [{'id': 'vault', 'source': source, 'earmark_role': 'other',
             'emergency_use': False, 'decision_ids': [], 'funding_fact_id': None,
             'expected_ref': None, 'correction_reason': None}],
            'accounts': [{'key': 'vault-account', 'label': 'Workshop vault',
                          'source': source, 'existing_ref': None}],
            'positions': [{'key': 'vault-position', 'account_key': 'vault-account',
                           'balance_ids': ['vault'], 'allocation_ids': [],
                           'composition': 'total', 'expected_ref': None,
                           'correction_reason': None}],
            'coverage_note': 'This one reported balance only',
            'uncertainties': [], 'mode': 'merge', 'replacement': None,
            'occurrence': None}


class I4HTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = DemoServer(('127.0.0.1', 0),
            DemoApplication(agent_provider=SequenceProvider([single_asset()])))
        cls.thread = Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def call(self, path, payload=None, authorized=True):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=3)
        headers = {'Content-Type': 'application/json'}
        if payload is not None and authorized:
            headers.update(Origin=self.server.origin, **{'X-FINVIS-Token': self.server.csrf})
        connection.request('POST' if payload is not None else 'GET', path,
                           json.dumps(payload) if payload is not None else None, headers)
        response = connection.getresponse()
        code, data = response.status, json.loads(response.read())
        connection.close()
        return code, data

    def test_exact_apply_receipt_lookup_and_user_home_without_demo_fallback(self):
        demo = self.server.app.view()
        code, initial = self.call('/api/user/home')
        self.assertEqual(code, 200)
        self.assertFalse(initial['home']['initialized'])
        self.assertEqual(self.call('/api/user/apply', {}, False)[0], 403)
        self.assertEqual(self.call('/api/user/execute', {})[0], 404)
        self.assertIn('/i4_home.js', STATIC)
        _, interpreted = self.call('/api/user/interpret', {'text': TEXT})
        user = interpreted['user']
        _, started = self.call('/api/user/review/start', {'candidate_id': user['candidate']['id']})
        _, proposed = self.call('/api/user/review/submit',
                                {'session_id': started['user']['session_id'], 'review': review()})
        self.assertEqual(proposed['user']['stage'], 'proposal')
        proposal_id = proposed['user']['proposal']['id']
        _, previewed = self.call('/api/user/preview', {'proposal_id': proposal_id})
        preview = previewed['user']['preview']
        _, consented = self.call('/api/user/authorize',
            {'proposal_id': proposal_id, 'preview_id': preview['id'], 'digest': preview['digest']})
        grant_id = consented['user']['authorization']['id']
        self.assertEqual(self.call('/api/user/operation?authorization_id=' + grant_id)[1]['operation']['status'],
                         'authorized_not_started')
        command = {'proposal_id': proposal_id, 'preview_id': preview['id'],
                   'digest': preview['digest'], 'authorization_id': grant_id}
        before = self.server.user_workflow.store.snapshot
        before_home = self.call('/api/user/home')[1]['home']
        self.assertEqual(self.call('/api/user/apply', dict(command, arbitrary_command='write'))[0], 400)
        self.assertEqual(self.call('/api/user/apply', {'proposal_id': proposal_id})[0], 400)
        self.assertEqual(self.call('/api/user/apply', dict(command, digest='wrong'))[0], 409)
        self.assertIs(self.server.user_workflow.store.snapshot, before)
        self.assertEqual(self.call('/api/user/home')[1]['home'], before_home)
        self.assertEqual(before.receipts, ())
        self.assertEqual(before.authorizations, ())
        code, applied = self.call('/api/user/apply', command)
        self.assertEqual(code, 200)
        self.assertEqual(applied['operation']['status'], 'applied')
        self.assertEqual(applied['user']['stage'], 'applied')
        self.assertEqual(applied['operation']['receipt_revision'], 1)
        self.assertEqual(self.call('/api/user/apply', command)[1]['operation']['status'],
                         'receipt_recovered')
        self.assertEqual(self.call('/api/user/operation?authorization_id=' + grant_id)[1]['operation']['receipt_revision'], 1)
        code, home = self.call('/api/user/home')
        self.assertEqual(code, 200)
        self.assertEqual(home['home']['version']['revision'], 1)
        projection = home['home']['projection']
        self.assertEqual(projection['revision'], 1)
        self.assertEqual(projection['currencies'][0]['currency'], 'SGD')
        self.assertEqual(projection['currencies'][0]['current_assets']['value']['amount']['minor_units'], 72000)
        self.assertEqual(projection['runway']['status'], 'insufficient_input')
        self.assertEqual(len(projection['receipt_refs']), 1)
        self.assertEqual(self.server.app.view()['audit'], demo['audit'])
        self.assertEqual(len(self.server.user_workflow.store.snapshot.receipts), 1)
        expected = {key: demo[key] for key in ('generation', 'revision')}
        self.assertEqual(self.call('/api/command', {'command': 'reset', 'expected': expected})[0], 200)
        self.assertEqual(self.call('/api/user/home')[1]['home'], home['home'])
        self.assertEqual(self.server.app.view()['display']['liquidity'], 'SGD 12,000.00')


if __name__ == '__main__':
    unittest.main()

"""Deterministic full interaction paths; never invokes a model runtime."""
import http.client
import json
from threading import Thread
import unittest

from finvis.application import DemoApplication
from finvis.i3_workflow import I3Error
from finvis.i4_commit import I4Workflow
from tests.http_support import DemoServer
from finvis.world_store import current_assets
from tests.test_agent_understanding import SequenceProvider
from tests.test_generalization_gate import candidate, fact
from tests.test_i4_commit import candidate_workflow
from tests.test_world_mutation import store, prepared, future_add


def flow(facts, text, raw=None, target=None):
    raw = raw or candidate(facts)
    if target is None:
        provider = SequenceProvider([raw])
        workflow = I4Workflow.bootstrap(provider)
    else:
        workflow, _, provider = candidate_workflow(target, [raw])
    envelope = workflow.interpret(text)
    before = workflow.store.snapshot
    view = workflow.prepare(envelope['id'])
    assert workflow.store.snapshot is before
    return workflow, provider, view


def commit(workflow, view):
    proposal, preview = view['proposal']['id'], view['preview']
    authorized = workflow.authorize(proposal, preview['id'], preview['digest'])
    return workflow.apply(proposal, preview['id'], preview['digest'], authorized['authorization']['id'])


class InteractionSimplificationTests(unittest.TestCase):
    def test_auto_asset_preview_authorization_apply_receipt_chain(self):
        w, provider, view = flow([fact('cash', 'current_asset', 'Harbor', 720, account='Harbor')],
                                 'Harbor balance SGD 720.')
        self.assertEqual(view['stage'], 'preview')
        self.assertEqual(view['interaction']['questions'], [])
        self.assertIsNone(view['authorization'])
        self.assertEqual(w.store.snapshot.revision, 0)
        for _ in range(3):
            self.assertEqual(w.view()['preview']['id'], view['preview']['id'])
            self.assertEqual(w.prepare(view['candidate']['id'])['preview']['id'], view['preview']['id'])
        with self.assertRaises(I3Error):
            w.apply(view['proposal']['id'], view['preview']['id'], view['preview']['digest'], 'no-grant')
        result = commit(w, view)
        self.assertEqual((result['receipt_revision'], provider.calls), (1, 1))
        self.assertEqual(len(w.store.snapshot.receipts), 1)
        self.assertEqual(current_assets(w.store.snapshot).positions[0].balance.amount.minor_units, 72000)

    def test_generic_mixed_accounts_allocations_and_precision(self):
        facts = [fact('river', 'current_asset', 'River total', 1250, 'CNY', account='River'),
                 fact('course', 'earmarked_current_asset', 'Course reserve', 200, 'CNY', account='River', earmark='Course'),
                 fact('south', 'current_asset', 'South', 480, 'SGD', account='South'),
                 fact('invoice', 'expected_future_inflow', 'Illustration', 1350, precision='approximate'),
                 fact('roof', 'planned_expense', 'Roof', None, precision='range', lower=700, upper=1100),
                 fact('camera', 'contemplated_decision', 'Camera', None, precision='unknown')]
        text = ('River total balance CNY 1250. River reserved CNY 200 for Course reserve. '
                'South balance SGD 480. I expect Illustration about SGD 1350. '
                'I plan to buy Roof SGD 700–1100. I am considering Camera, amount SGD unknown.')
        w, _, view = flow(facts, text)
        self.assertEqual(view['stage'], 'preview', view['interaction'])
        precisions = {row['after']['precision'] for row in view['preview']['changes'] if row['after']}
        self.assertTrue({'exact_reported', 'approximate', 'range', 'unknown'} <= precisions)
        self.assertEqual(len(view['interaction']['questions']), 0)
        commit(w, view)
        self.assertEqual(len(current_assets(w.store.snapshot).positions), 2)

    def test_chinese_and_metamorphic_reordering_scaling_labels(self):
        for label, amount in [('春山账户', 3680), ('远山钱包', 7360)]:
            facts = [fact('a', 'current_asset', label, amount, 'CNY', account=label),
                     fact('b', 'expected_future_inflow', '插画尾款', 900, 'CNY', precision='approximate')]
            text = f'{label}余额人民币{amount}元。预计收到插画尾款约人民币900元。'
            for items in (facts, list(reversed(facts))):
                _, _, view = flow(items, text)
                self.assertEqual(view['stage'], 'preview')
                proof = view['interaction']['evidence']['preparation']
                self.assertEqual([f['id'] for f in proof['facts']], ['a', 'b'])
                self.assertEqual(proof['facts'][0]['excerpt'], text.split('。')[0] + '。')

    def test_one_context_ambiguity_does_not_repeat_other_evidence(self):
        facts = [fact('a', 'contemplated_decision', 'Bike', 900), fact('b', 'context', 'Commute')]
        w, provider, view = flow(facts, 'I am considering Bike SGD 900. Commute is long.')
        self.assertEqual([q['id'] for q in view['interaction']['questions']], ['decision:b'])
        resolved = w.clarify(view['session_id'], {'decision:b': ['a']})
        self.assertEqual(resolved['stage'], 'preview')
        self.assertEqual(provider.calls, 1)
        self.assertEqual(w.store.snapshot.revision, 0)

    def test_ambiguous_source_only_and_multiple_simultaneous_questions(self):
        facts = [fact('bank', 'current_asset', 'Bank', 10, account='Bank'),
                 fact('reserve', 'earmarked_current_asset', 'Emergency', 5, account='Bank', earmark='Emergency')]
        w, _, view = flow(facts, 'Bank balance SGD 10. Bank balance SGD 20. Bank reserved SGD 5 for Emergency.')
        questions = view['interaction']['questions']
        self.assertEqual({q['id'] for q in questions}, {'source:bank', 'role:reserve'})
        view = w.clarify(view['session_id'], {'source:bank': 0})
        self.assertEqual({q['id'] for q in view['interaction']['questions']}, {'role:reserve'})
        view = w.clarify(view['session_id'], {'role:reserve': 'emergency'})
        self.assertEqual(view['stage'], 'preview')

    def test_explicit_existing_account_and_position_correction(self):
        facts = [fact('balance', 'current_asset', 'Old wallet', 680, 'CNY', account='Old wallet')]
        w, _, view = flow(facts, 'Old wallet balance CNY 680.', target=store())
        self.assertEqual([q['id'] for q in view['interaction']['questions']], ['account:Old wallet'])
        view = w.clarify(view['session_id'], {'account:Old wallet': '0'})
        q = view['interaction']['questions'][0]
        self.assertEqual(q['kind'], 'correction')
        view = w.clarify(view['session_id'], {q['id']: {'target': q['targets'][0]['value'], 'reason': 'Correct reported balance'}})
        self.assertEqual(view['stage'], 'preview')
        row = next(c for c in view['preview']['changes'] if c['kind'] == 'asset_position')
        self.assertEqual(row['before']['amount']['minor_units'], 70000)
        self.assertEqual(row['after']['amount']['minor_units'], 68000)
        commit(w, view)

    def test_future_observation_correction_uses_exact_current_target(self):
        w, provider, view = flow([fact('invoice','expected_future_inflow','Mural invoice',100)],
                                 'I expect Mural invoice SGD 100.')
        commit(w,view)
        raw=candidate([fact('invoice','expected_future_inflow','Mural invoice',120)])
        raw['user_intents']=['correct_information'];provider.values.append(raw)
        envelope=w.interpret('I expect Mural invoice SGD 120.')
        view=w.prepare(envelope['id']);q=view['interaction']['questions'][0]
        self.assertEqual(q['id'],'correction:invoice')
        view=w.clarify(view['session_id'],{q['id']:{'target':q['targets'][0]['value'],'reason':'Correct invoice quote'}})
        self.assertEqual(view['stage'],'preview')
        row=next(c for c in view['preview']['changes'] if c['kind']=='future_inflow')
        self.assertEqual((row['before']['amount']['minor_units'],row['after']['amount']['minor_units']),(10000,12000))
        self.assertEqual(w.store.snapshot.revision,1)

    def test_scope_is_explicit_and_visible_before_authorization(self):
        facts = [fact('balance', 'current_asset', 'New jar', 80, 'CNY', account='New jar')]
        raw = candidate(facts, 'CNY');raw['destructive_actions'] = ['clear_financial_state']
        w, _, view = flow(facts, 'New jar balance CNY 80.', raw, store())
        self.assertEqual(view['stage'], 'review')
        scope = next(q for q in view['interaction']['questions'] if q['kind'] == 'scope')
        answers = {'scope': {'mode': 'replace', 'account_refs': [scope['accounts'][0]['value']], 'currencies': ['CNY']},
                   'account:New jar': 'new'}
        view = w.clarify(view['session_id'], answers)
        self.assertEqual(view['stage'], 'preview')
        self.assertTrue(view['preview']['destructive'])
        self.assertTrue(any(c['retired'] for c in view['preview']['changes']))
        self.assertEqual(w.store.snapshot.revision, 0)

    def test_disjoint_composition_needs_explicit_answer(self):
        facts = [fact('a', 'current_asset', 'Main funds', 300, account='Cove'),
                 fact('b', 'current_asset', 'Side funds', 40, account='Cove')]
        w, _, view = flow(facts, 'Cove Main funds balance SGD 300. Cove Side funds balance SGD 40.')
        self.assertEqual([q['id'] for q in view['interaction']['questions']], ['composition:0'])
        view = w.clarify(view['session_id'], {'composition:0': 'disjoint'})
        self.assertEqual(view['stage'], 'preview')
        amount = next(c['after']['amount']['minor_units'] for c in view['preview']['changes'] if c['kind'] == 'asset_position')
        self.assertEqual(amount, 34000)

    def test_replacement_cannot_omit_new_position_currency_from_scope(self):
        facts=[fact('new','current_asset','New jar',80,'SGD',account='New jar')]
        raw=candidate(facts);raw['destructive_actions']=['replace_financial_state']
        w,_,view=flow(facts,'New jar balance SGD 80.',raw,store())
        scope=next(q for q in view['interaction']['questions'] if q['kind']=='scope')
        answer={'mode':'replace','account_refs':[scope['accounts'][0]['value']],'currencies':['CNY']}
        with self.assertRaises(I3Error):
            w.clarify(view['session_id'],{'scope':answer,'account:New jar':'new'})
        self.assertTrue(w.view()['interaction']['questions'])
        answer['currencies']=['CNY','SGD']
        resolved=w.clarify(view['session_id'],{'scope':answer,'account:New jar':'new'})
        self.assertEqual(resolved['stage'],'preview')
        self.assertEqual(w.store.snapshot.revision,0)

    def test_actual_expense_and_inflow_remain_separate_settlement_review(self):
        for kind, verb in [('actual_expense', 'paid'), ('actual_inflow', 'received')]:
            facts = [fact('actual', kind, 'Workshop payment', 125, 'CNY', account='Old wallet')]
            text = f'I {verb} Workshop payment CNY 125 through Old wallet. Old wallet balance excludes this transaction.'
            w, _, view = flow(facts, text, target=store())
            self.assertEqual([q['kind'] for q in view['interaction']['questions']], ['settlement'])
            q = view['interaction']['questions'][0]
            source = lambda i: {k:q['excerpts'][i][k] for k in ('start','end')}
            answer = dict(account_source=source(0), position_ref=q['positions'][0]['value'],
                basis='apply_to_prior_balance', coverage_source=source(1),
                coverage_rationale='Explicitly excluded from the balance', freshness_current=True)
            invalid = dict(answer, coverage_source={'start': 0, 'end': len(text) + 1})
            with self.assertRaises(I3Error):
                w.clarify(view['session_id'], {'settlement': invalid})
            self.assertEqual(w.view()['interaction']['questions'][0]['id'], 'settlement')
            view = w.clarify(view['session_id'], {'settlement': answer})
            self.assertEqual(view['stage'], 'preview', view.get('proposal'))
            self.assertEqual(w.store.snapshot.revision, 0)
            commit(w, view)
            self.assertEqual(len(w.store.snapshot.receipts), 1)

    def test_conflicts_unsupported_and_unknown_source_fail_closed(self):
        for field in ('conflicts', 'assumptions', 'unsupported_requests'):
            raw = candidate([fact('a', 'planned_expense', 'Desk', 80)])
            raw[field] = ['Unresolved meaning']
            w, _, view = flow([], 'I plan to buy Desk SGD 80.', raw)
            self.assertEqual(view['stage'], 'review')
            self.assertTrue(view['interaction']['blockers'])
            self.assertIsNone(view['proposal'])
            self.assertEqual(w.store.snapshot.revision, 0)

    def test_retained_uncertainty_is_never_blindly_acknowledged(self):
        raw = candidate([fact('a', 'expected_future_inflow', 'Refund', 80, precision='approximate')],
                        uncertainties=['Refund arrival date unknown'])
        w, _, view = flow([], 'I expect Refund about SGD 80.', raw)
        self.assertEqual([q['id'] for q in view['interaction']['questions']], ['uncertainties'])
        view = w.clarify(view['session_id'], {'uncertainties': raw['uncertainties']})
        self.assertEqual(view['stage'], 'preview')
        self.assertIn('user_answered', [x['origin'] for x in view['interaction']['evidence']['origins']])

    def test_conditional_other_owner_and_negated_evidence_cannot_be_clicked_through(self):
        facts = [fact('a', 'current_asset', 'Bank', 80, account='Bank')]
        for text in ('If Bank balance is SGD 80.', "My mother's Bank balance is SGD 80.",
                     'Bank balance is not SGD 80.'):
            _, _, view = flow(facts, text)
            self.assertIn('revise_conflicting_interpretation', view['interaction']['blockers'])
            self.assertIsNone(view['preview'])

    def test_stale_and_tampered_answers_and_view_isolation(self):
        w, _, view = flow([fact('a', 'contemplated_decision', 'Bike', 90), fact('b', 'context', 'Commute')],
                          'I am considering Bike SGD 90. Commute is long.', target=store())
        before = w.store.snapshot
        with self.assertRaises(I3Error):
            w.clarify(view['session_id'], {'injected_authorization': True})
        with self.assertRaises(I3Error):
            w.clarify(view['session_id'], {'decision:b': ['wrong-id']})
        view['interaction']['questions'][0]['id'] = 'tampered'
        self.assertEqual(w.view()['interaction']['questions'][0]['id'], 'decision:b')
        self.assertIs(w.store.snapshot, before)
        _, _, command = prepared(w.store, future_add(w.store))
        w.store.execute(command)
        with self.assertRaises(I3Error):
            w.clarify(view['session_id'], {'decision:b': ['a']})


class InteractionHTTPTests(unittest.TestCase):
    def test_prepare_and_clarify_routes_keep_security_and_read_boundaries(self):
        provider = SequenceProvider([candidate([fact('a', 'current_asset', 'Harbor', 720, account='Harbor')])])
        server = DemoServer(('127.0.0.1', 0), DemoApplication(agent_provider=provider))
        thread = Thread(target=server.serve_forever, daemon=True);thread.start()
        def call(path, payload=None, token=True):
            conn = http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=3)
            headers = {'Content-Type':'application/json'}
            if token:headers.update({'Origin':server.origin,'X-FINVIS-Token':server.csrf})
            conn.request('GET' if payload is None else 'POST',path,None if payload is None else json.dumps(payload),headers)
            response=conn.getresponse();data=json.loads(response.read());status=response.status;conn.close();return status,data
        try:
            _, data = call('/api/user/interpret',{'text':'Harbor balance SGD 720.'})
            args={'candidate_id':data['user']['candidate']['id']}
            self.assertEqual(call('/api/user/review/prepare',args,False)[0],403)
            code,data=call('/api/user/review/prepare',args)
            self.assertEqual((code,data['user']['stage']),(200,'preview'))
            for _ in range(2):self.assertIsNone(call('/api/user/status')[1]['user']['authorization'])
            self.assertEqual(server.user_workflow.store.snapshot.revision,0)
            self.assertEqual(call('/api/user/review/clarify',{'session_id':'stale','answers':{}})[0],409)
            self.assertEqual(provider.calls,1)
        finally:
            server.shutdown();server.server_close();thread.join()

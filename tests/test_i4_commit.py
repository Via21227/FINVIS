"""I4 exact authorized commit, receipt recovery and I1 projection regressions."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from unittest.mock import patch
import unittest

from finvis.agent_understanding import AgentUnderstandingService
from finvis.world_commands import record_ref
from finvis.i3_workflow import I3Error
from finvis.i4_commit import I4Workflow
from finvis.world_application import ContextKind, UserWorldAuthority, WorldContext
from finvis.world_projection import ProjectionError
from finvis.world_settlement import BalanceEffect
from finvis.world_store import current_assets
from finvis.store import Rejected
from tests.test_agent_reconciliation import TEXT, synthetic
from tests.test_agent_understanding import SequenceProvider, realistic_output
from tests.test_actual_occurrence_agent import candidate_raw
from tests.test_i3_workflow import canonical_payload, wire_ref
from tests.test_world_mutation import future_add, prepared, store


def candidate_workflow(target=None, outputs=None):
    target = target or store()
    context = WorldContext(ContextKind.USER, 'user:i4-test',
                           target.snapshot.world.owner_id, target.snapshot.world.world_id)
    provider = SequenceProvider(outputs or [realistic_output(), realistic_output()])
    return I4Workflow(UserWorldAuthority(context, target),
                      AgentUnderstandingService(provider), target), target, provider


def authorized(workflow, target, text=TEXT, review=None):
    candidate = workflow.interpret(text)
    session = workflow.start(candidate['id'])
    proposal = workflow.submit(session['session_id'], review or canonical_payload(target))
    assert proposal['stage'] == 'proposal', proposal
    preview = workflow.preview(proposal['proposal']['id'])
    grant = workflow.authorize(proposal['proposal']['id'],
                               preview['preview']['id'], preview['preview']['digest'])
    return (proposal['proposal']['id'], preview['preview']['id'],
            preview['preview']['digest'], grant['authorization']['id'])


class I4CommitTests(unittest.TestCase):
    def test_exact_commit_receipt_projection_and_no_double_effect(self):
        workflow, target, provider = candidate_workflow()
        args = authorized(workflow, target)
        before = target.snapshot
        provider.values = []
        result = workflow.apply(*args)
        self.assertEqual(result['status'], 'applied')
        self.assertEqual(result['receipt_revision'], 1)
        self.assertEqual(target.snapshot.revision, 1)
        self.assertEqual(len(target.snapshot.receipts), 1)
        self.assertEqual(target.snapshot.records[:len(before.records)], before.records)
        self.assertEqual(target.snapshot.receipts[0].authorization_ref.id, args[3])
        self.assertEqual(target.snapshot.authorizations[0].ref.id, args[3])
        other_command = replace(workflow._operations[args[3]].command, command_id='different-command')
        committed = target.snapshot
        with self.assertRaises(Rejected):
            target.execute(other_command)
        self.assertIs(target.snapshot, committed)
        self.assertEqual(workflow.understanding.candidate.status, 'committed')
        home = workflow.home()
        self.assertEqual(home['version']['revision'], result['receipt_revision'])
        self.assertEqual(home['projection']['revision'], result['receipt_revision'])
        cny = home['projection']['currencies'][0]
        self.assertEqual(cny['currency'], 'CNY')
        self.assertEqual(cny['current_assets']['value']['amount']['minor_units'], 3400000)
        self.assertEqual(cny['earmarked']['value']['amount']['minor_units'], 2500000)
        self.assertEqual(cny['unallocated']['value']['amount']['minor_units'], 900000)
        self.assertEqual(home['projection']['expected_inflows_by_currency'][0]['amount']['value']['amount']['minor_units'], 2300000)
        self.assertEqual(home['projection']['open_expenses_by_currency'][0]['amount']['value']['amount']['minor_units'], 1800000)
        self.assertEqual(home['projection']['decisions'][0]['amount']['amount']['minor_units'], 600000)
        self.assertEqual(home['projection']['runway']['status'], 'insufficient_input')
        self.assertEqual(home['projection']['essential_monthly_outflow']['status'], 'unknown')
        self.assertEqual(workflow.apply(*args)['status'], 'receipt_recovered')
        self.assertEqual(target.snapshot.revision, 1)
        self.assertEqual(len(target.snapshot.receipts), 1)
        self.assertEqual(provider.calls, 1)

    def test_no_valid_grant_means_no_mutation(self):
        workflow, target, _ = candidate_workflow()
        candidate = workflow.interpret(TEXT)
        session = workflow.start(candidate['id'])
        proposal = workflow.submit(session['session_id'], canonical_payload(target))
        preview = workflow.preview(proposal['proposal']['id'])
        before = target.snapshot
        before_home = workflow.home()
        with self.assertRaises(I3Error):
            workflow.apply(proposal['proposal']['id'], preview['preview']['id'],
                           preview['preview']['digest'], 'absent-grant')
        self.assertIs(target.snapshot, before)
        self.assertEqual(workflow.home(), before_home)

    def test_wrong_proposal_preview_and_authorization_never_apply(self):
        workflow, target, _ = candidate_workflow()
        args = authorized(workflow, target)
        before = target.snapshot
        before_home = workflow.home()
        for index in range(4):
            wrong = list(args)
            wrong[index] = 'wrong'
            with self.subTest(index=index), self.assertRaises(I3Error):
                workflow.apply(*wrong)
        self.assertEqual(target.snapshot.revision, 0)
        self.assertEqual(target.snapshot.receipts, ())
        self.assertIs(target.snapshot, before)
        self.assertEqual(workflow.home(), before_home)

    def test_replaced_preview_or_candidate_cannot_use_old_grant(self):
        for changed in ('preview', 'candidate'):
            with self.subTest(changed=changed):
                workflow, target, _ = candidate_workflow()
                args = authorized(workflow, target)
                before = target.snapshot
                before_home = workflow.home()
                if changed == 'preview':
                    workflow.preview(args[0])
                else:
                    workflow.interpret(TEXT)
                with self.assertRaises(I3Error):
                    workflow.apply(*args)
                self.assertIs(target.snapshot, before)
                self.assertEqual(workflow.home(), before_home)

    def test_changed_world_invalidates_authorization(self):
        workflow, target, _ = candidate_workflow()
        args = authorized(workflow, target)
        _, _, external = prepared(target, future_add(target))
        target.execute(external)
        before = target.snapshot
        before_home = workflow.home()
        with self.assertRaises(I3Error) as error:
            workflow.apply(*args)
        self.assertEqual(error.exception.code, 'world_changed')
        self.assertEqual(target.snapshot.revision, 1)
        self.assertEqual(len(target.snapshot.receipts), 1)
        self.assertIs(target.snapshot, before)
        self.assertEqual(workflow.home(), before_home)

    def test_prepublication_failure_retains_consent_and_exact_retry(self):
        workflow, target, _ = candidate_workflow()
        args = authorized(workflow, target)
        before = target.snapshot
        before_home = workflow.home()
        def fail(_):
            raise RuntimeError('injected prepublication fault')
        target._before_publish = fail
        with self.assertRaises(I3Error) as error:
            workflow.apply(*args)
        self.assertEqual(error.exception.code, 'execution_failed_before_commit')
        self.assertIs(target.snapshot, before)
        self.assertEqual(workflow.home(), before_home)
        self.assertEqual(target.snapshot.authorizations, ())
        self.assertEqual(workflow.operation_status(args[3])['status'], 'authorized_not_committed')
        target._before_publish = None
        self.assertEqual(workflow.apply(*args)['status'], 'applied')
        self.assertEqual(target.snapshot.revision, 1)

    def test_response_lost_after_publication_recovers_one_receipt(self):
        workflow, target, _ = candidate_workflow()
        args = authorized(workflow, target)
        original = target.execute
        def lost(command):
            original(command)
            raise RuntimeError('response interrupted after publication')
        with patch.object(target, 'execute', side_effect=lost):
            result = workflow.apply(*args)
        self.assertEqual(result['status'], 'applied')
        self.assertEqual(workflow.operation_status(args[3])['receipt']['command_id'],
                         result['command_id'])
        self.assertEqual(workflow.apply(*args)['status'], 'receipt_recovered')
        self.assertEqual(target.snapshot.revision, 1)
        self.assertEqual(len(target.snapshot.receipts), 1)

    def test_concurrent_same_grant_and_new_candidate_preserve_old_lookup(self):
        workflow, target, _ = candidate_workflow()
        args = authorized(workflow, target)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: workflow.apply(*args), range(2)))
        self.assertEqual({row['status'] for row in results}, {'applied', 'receipt_recovered'})
        self.assertEqual(target.snapshot.revision, 1)
        self.assertEqual(len(target.snapshot.receipts), 1)
        workflow.interpret(TEXT)
        self.assertEqual(workflow.view()['stage'], 'understanding')
        self.assertEqual(workflow.operation_status(args[3])['status'], 'applied')

    def test_home_projection_failure_does_not_erase_receipt(self):
        workflow, target, _ = candidate_workflow()
        args = authorized(workflow, target)
        workflow.apply(*args)
        with patch.object(workflow.authority, 'read', side_effect=ProjectionError('injected')):
            with self.assertRaises(ProjectionError):
                workflow.home()
            self.assertEqual(workflow.operation_status(args[3])['status'], 'applied')
        self.assertEqual(workflow.home()['version']['revision'], 1)

    def test_empty_world_then_generic_sgd_and_cny_remain_separate(self):
        first_raw, first_text, first_review = synthetic('SGD', 'Workshop jar', 'Machine repair')
        second_raw, second_text, second_review = synthetic('CNY', 'Travel wallet', 'Train tickets')
        workflow = I4Workflow.bootstrap(SequenceProvider([first_raw, second_raw]))
        self.assertEqual(workflow.home()['projection']['currencies'], [])
        for text, request in ((first_text, first_review), (second_text, second_review)):
            request = replace(request, accounts=(replace(request.accounts[0],
                owner_id=workflow.context.owner_id),))
            args = authorized(workflow, workflow.store, text, request)
            workflow.apply(*args)
        home = workflow.home()['projection']
        self.assertEqual(home['revision'], 2)
        self.assertEqual({row['currency'] for row in home['currencies']}, {'CNY', 'SGD'})
        self.assertEqual(len(home['accounts']), 2)
        self.assertEqual(len(home['decisions']), 2)
        self.assertEqual(len(home['receipt_refs']), 2)
        self.assertEqual(home['runway']['status'], 'insufficient_input')

    def test_authorized_actual_occurrence_settles_once_with_reviewed_source(self):
        text = ('I paid 125 CNY from Old wallet yesterday. Coverage: this is '
                'separate from the last balance.')
        workflow, target, _ = candidate_workflow(outputs=[candidate_raw('actual_expense')])
        source = {'start': 0, 'end': text.index(' Coverage:')}
        coverage = {'start': text.index('Coverage: ') + 10, 'end': len(text)}
        review = {'facts': [], 'accounts': [], 'positions': [], 'coverage_note': '',
                  'uncertainties': [], 'mode': 'merge', 'replacement': None,
                  'occurrence': {'fact_id': 'occurrence', 'source': source,
                      'account_source': source,
                      'position_ref': wire_ref(record_ref(current_assets(target.snapshot).positions[0])),
                      'basis': 'apply_to_prior_balance', 'coverage_source': coverage,
                      'coverage_rationale': 'this is separate from the last balance.',
                      'freshness_current': True}}
        args = authorized(workflow, target, text, review)
        before = target.snapshot
        applied = workflow.apply(*args)
        self.assertEqual(applied['receipt_revision'], 1)
        self.assertEqual(len([row for row in target.snapshot.records if type(row) is BalanceEffect]), 1)
        self.assertEqual(workflow.home()['projection']['currencies'][0]
                         ['current_assets']['value']['amount']['minor_units'], 57500)
        self.assertEqual(workflow.apply(*args)['status'], 'receipt_recovered')
        self.assertEqual(len([row for row in target.snapshot.records if type(row) is BalanceEffect]), 1)
        self.assertEqual(len(target.snapshot.receipts), len(before.receipts) + 1)


if __name__ == '__main__':
    unittest.main()

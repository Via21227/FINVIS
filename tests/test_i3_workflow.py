"""I3 visible-boundary tests: exact review, preview and unconsumed grant."""
from dataclasses import replace
import unittest

from finvis.agent_understanding import AgentUnderstandingService
from finvis.i3_workflow import I3Error, I3Workflow
from finvis.world_application import ContextKind, UserWorldAuthority, WorldContext
from tests.test_actual_occurrence_agent import actual_review, candidate_raw
from tests.test_agent_reconciliation import (TEXT, canonical_review, synthetic)
from tests.test_agent_understanding import SequenceProvider, realistic_output
from tests.test_world_mutation import future_add, prepared, store
from finvis.world_planning import OccurrenceKind
from finvis.world_commands import record_ref
from finvis.world_store import current_assets


def workflow(raw=None, text=TEXT, target=None):
    target = target or store()
    context = WorldContext(ContextKind.USER, 'user:i3-test',
                           target.snapshot.world.owner_id, target.snapshot.world.world_id)
    provider = SequenceProvider([raw or realistic_output()])
    value = I3Workflow(UserWorldAuthority(context, target),
                       AgentUnderstandingService(provider), target)
    candidate = value.interpret(text)
    return value, target, provider, candidate


def wire_ref(value):
    return {'kind': value.record_kind, 'id': value.ref.id,
            'version': value.ref.version} if value is not None else None


def wire_source(value):
    return {'start': value[0].start, 'end': value[0].end}


def canonical_payload(target):
    reviewed = canonical_review(target)
    return {'facts': [
        {'id': row.fact_id, 'source': wire_source(row.sources),
         'earmark_role': row.earmark_role.value,
         'emergency_use': row.emergency_use_designated,
         'decision_ids': list(row.decision_ids),
         'funding_fact_id': row.funding_fact_id,
         'expected_ref': wire_ref(row.expected_ref),
         'correction_reason': row.correction_reason}
        for row in reviewed.facts],
        'accounts': [
            {'key': row.key, 'label': row.label, 'source': wire_source(row.sources),
             'existing_ref': wire_ref(row.existing_ref)}
            for row in reviewed.accounts],
        'positions': [
            {'key': row.key, 'account_key': row.account_key,
             'balance_ids': list(row.balance_ids),
             'allocation_ids': list(row.allocation_ids),
             'composition': row.composition,
             'expected_ref': wire_ref(row.expected_ref),
             'correction_reason': row.correction_reason}
            for row in reviewed.positions],
        'coverage_note': reviewed.coverage_note,
        'uncertainties': list(reviewed.acknowledged_uncertainties),
        'mode': 'replace',
        'replacement': {
            'account_refs': [wire_ref(row) for row in reviewed.replacement_scope.account_refs],
            'currencies': [row.value for row in reviewed.replacement_scope.currencies]},
        'occurrence': None}


class I3WorkflowTests(unittest.TestCase):
    def ready(self):
        value, target, provider, candidate = workflow()
        started = value.start(candidate['id'])
        result = value.submit(started['session_id'], canonical_payload(target))
        self.assertEqual(result['stage'], 'proposal')
        return value, target, provider, result

    def test_candidate_review_proposal_and_progressive_projection(self):
        value, target, _, result = self.ready()
        self.assertEqual(result['candidate']['status'], 'awaiting_confirmation')
        self.assertEqual(result['proposal']['status'], 'ready_for_proposal')
        items = result['proposal']['projection']['items']
        self.assertTrue({'add', 'remove_retire', 'preserve'} <=
                        {item['action'] for item in items})
        self.assertTrue({'CNY'} <= {item['amount']['currency'] for item in items
                                     if item['amount'] is not None and item['amount']['currency']})
        self.assertEqual(result['world_revision'], 0)
        self.assertEqual(target.snapshot.receipts, ())

    def test_preview_then_exact_grant_has_no_authoritative_effect(self):
        value, target, provider, proposal = self.ready()
        before = target.snapshot
        provider.outputs = []  # Deterministic stages must not call the model.
        preview = value.preview(proposal['proposal']['id'])
        self.assertEqual(preview['stage'], 'preview')
        self.assertTrue(preview['preview']['destructive'])
        self.assertIn('audit_history', preview['preview']['preserved_categories'])
        self.assertTrue(any(row['before'] and row['after'] for row in preview['preview']['changes']))
        with self.assertRaises(I3Error):
            value.authorize(proposal['proposal']['id'], preview['preview']['id'], 'wrong-digest')
        authorized = value.authorize(proposal['proposal']['id'],
                                     preview['preview']['id'], preview['preview']['digest'])
        self.assertEqual(authorized['stage'], 'authorized')
        self.assertEqual(authorized['authorization']['preview_digest'],
                         preview['preview']['digest'])
        self.assertIs(target.snapshot, before)
        self.assertEqual(target.snapshot.revision, 0)
        self.assertEqual(target.snapshot.receipts, ())
        self.assertFalse(hasattr(value, 'execute'))

    def test_proposal_alone_cannot_authorize_and_changed_review_cancels_preview(self):
        value, target, _, proposal = self.ready()
        with self.assertRaises(I3Error):
            value.authorize(proposal['proposal']['id'], 'unregistered', 'digest')
        preview = value.preview(proposal['proposal']['id'])
        changed = canonical_payload(target)
        changed['coverage_note'] = 'Reviewed a narrower explicit statement'
        second = value.submit(proposal['session_id'], changed)
        self.assertNotEqual(second['proposal']['id'], proposal['proposal']['id'])
        with self.assertRaises(I3Error):
            value.authorize(proposal['proposal']['id'], preview['preview']['id'],
                            preview['preview']['digest'])
        self.assertEqual(target.snapshot.revision, 0)

    def test_world_advance_invalidates_pending_preview(self):
        value, target, _, proposal = self.ready()
        preview = value.preview(proposal['proposal']['id'])
        _, _, command = prepared(target, future_add(target))
        target.execute(command)  # A separate already-authorized writer, never I3.
        with self.assertRaises(I3Error):
            value.authorize(proposal['proposal']['id'], preview['preview']['id'],
                            preview['preview']['digest'])
        self.assertEqual(value.view()['stage'], 'stale')

    def test_candidate_change_invalidates_review_and_preview(self):
        value, target, _, proposal = self.ready()
        preview = value.preview(proposal['proposal']['id'])
        value.understanding.candidate = replace(value.understanding.candidate,
                                                 id='changed-candidate')
        self.assertEqual(value.view()['stage'], 'stale')
        with self.assertRaises(I3Error):
            value.submit(proposal['session_id'], canonical_payload(target))
        with self.assertRaises(I3Error):
            value.authorize(proposal['proposal']['id'], preview['preview']['id'],
                            preview['preview']['digest'])
        self.assertEqual(target.snapshot.authorizations, ())

    def test_status_refresh_never_creates_a_second_authorization(self):
        value, target, _, proposal = self.ready()
        preview = value.preview(proposal['proposal']['id'])
        authorized = value.authorize(proposal['proposal']['id'],
                                     preview['preview']['id'], preview['preview']['digest'])
        first = authorized['authorization']['id']
        self.assertEqual(value.view()['authorization']['id'], first)
        self.assertEqual(value.authorize(proposal['proposal']['id'],
            preview['preview']['id'], preview['preview']['digest'])['authorization']['id'], first)
        self.assertEqual(len(target._authorizations), 1)
        self.assertEqual(target.snapshot.revision, 0)

    def test_bad_evidence_and_missing_scope_fail_closed(self):
        value, target, _, candidate = workflow()
        session = value.start(candidate['id'])
        request = canonical_payload(target)
        request['facts'][0]['source'] = {'start': 0, 'end': len(TEXT) + 5}
        with self.assertRaises(I3Error):
            value.submit(session['session_id'], request)
        request = canonical_payload(target)
        request['replacement'] = None
        with self.assertRaises(I3Error):
            value.submit(session['session_id'], request)
        self.assertEqual(target.snapshot.revision, 0)

    def test_noncanonical_sgd_proposal_and_cancel(self):
        raw, text, reviewed = synthetic('SGD', 'Payroll jar', 'Roof repair')
        value, target, _, candidate = workflow(raw, text)
        session = value.start(candidate['id'])
        result = value.submit(session['session_id'], reviewed)
        self.assertEqual(result['stage'], 'proposal')
        self.assertTrue(any(item['amount'] and item['amount']['currency'] == 'SGD'
                            for item in result['proposal']['projection']['items']))
        preview = value.preview(result['proposal']['id'])
        self.assertFalse(preview['preview']['destructive'])
        self.assertEqual(value.cancel()['stage'], 'idle')
        self.assertEqual(target.snapshot.revision, 0)

    def test_empty_user_world_is_distinct_from_demo_fixture(self):
        raw, text, _ = synthetic('CNY', 'Paper wallet')
        raw['candidate_facts'] = raw['candidate_facts'][:1]
        value = I3Workflow.bootstrap(SequenceProvider([raw]))
        self.assertEqual(value.store.snapshot.world.members, ())
        candidate = value.interpret(text)
        started = value.start(candidate['id'])
        self.assertEqual(started['stage'], 'review')
        self.assertEqual(started['current_accounts'], [])
        self.assertEqual(started['world_revision'], 0)

    def test_actual_occurrence_remains_a_separate_review(self):
        text = 'I paid 125 CNY from Old wallet yesterday. Coverage: this is separate from the last balance.'
        value, target, _, candidate = workflow(candidate_raw('actual_expense'), text)
        session = value.start(candidate['id'])
        review = actual_review(text, OccurrenceKind.EXPENSE)
        result = value.submit(session['session_id'], review)
        self.assertIn(result['stage'], ('review', 'proposal'))
        self.assertTrue(any(item['action'] == 'unresolved'
                            for item in result['proposal']['projection']['items']))
        self.assertTrue({'source_unknown', 'balance_coverage_unknown'} <=
                        {issue['code'] for issue in result['proposal']['issues']})
        self.assertEqual(target.snapshot.revision, 0)
        self.assertEqual(target.snapshot.receipts, ())

    def test_explicit_actual_coverage_can_reach_settlement_proposal_without_settling(self):
        text = 'I paid 125 CNY from Old wallet yesterday. Coverage: this is separate from the last balance.'
        value, target, _, candidate = workflow(candidate_raw('actual_expense'), text)
        session = value.start(candidate['id'])
        fact_end = text.index(' Coverage:')
        source = {'start': 0, 'end': fact_end}
        reviewed = {
            'facts': [], 'accounts': [], 'positions': [], 'coverage_note': '',
            'uncertainties': [], 'mode': 'merge', 'replacement': None,
            'occurrence': {'fact_id': 'occurrence', 'source': source,
                'account_source': source,
                'position_ref': wire_ref(record_ref(current_assets(target.snapshot).positions[0])),
                'basis': 'apply_to_prior_balance',
                'coverage_source': {'start': text.index('Coverage: ') + 10, 'end': len(text)},
                'coverage_rationale': 'this is separate from the last balance.',
                'freshness_current': True}}
        result = value.submit(session['session_id'], reviewed)
        self.assertEqual(result['stage'], 'proposal')
        self.assertTrue(any(item['effect'] == 'settlement_review_required'
                            for item in result['proposal']['projection']['items']))
        preview = value.preview(result['proposal']['id'])
        self.assertEqual(preview['stage'], 'preview')
        self.assertEqual(target.snapshot.revision, 0)
        self.assertEqual(target.snapshot.receipts, ())


if __name__ == '__main__':
    unittest.main()

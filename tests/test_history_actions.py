"""Generic History action safety, authority, replay and application tests."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timezone
import unittest
from unittest.mock import patch

from finvis.domain import Currency, Money, Ref
from finvis.history_actions import historical, proposal, key
from finvis.i3_workflow import I3Error
from finvis.world_commands import record_ref
from finvis.world_values import AmountEstimate
from finvis.world_serialization import payload_digest, dumps_world_mutation, loads_world_mutation
from finvis.world_replay import export_world_replay, replay_world, dumps_world_replay, loads_world_replay
from finvis.world_store import validate_world
from finvis.store import Rejected
from tests.test_i4_commit import candidate_workflow
from tests.test_world_application import synthetic
from tests.test_world_mutation import store, replacement, prepared, future_add
from tests.test_world_settlement import operation, settle, correction
from tests.test_world_planning import canonical_bundle


def workflow(target=None):
    return candidate_workflow(target or synthetic((Currency.CNY, Currency.SGD)), [])[0]


def request(w, action, kind=None, field=None, value=None, revision=None, index=0):
    revision = w.store.snapshot.revision if revision is None else revision
    detail = w.history(revision)['detail']
    target = None
    if kind:
        target = [r for r in detail['actions']['records'] if r['ref'] and r['ref']['record_kind'] == kind][index]['ref']
    return dict(binding=detail['actions']['binding'], action=action, target=target,
                field=field, value=value, reason='Correct the workshop report')


def amount(n, precision='exact_reported', lower='', upper=''):
    return dict(precision=precision, value=str(n), lower=str(lower), upper=str(upper), reason='Not yet known')


def apply(w, payload):
    before = w.store.snapshot
    p = w.history_actions.prepare(payload)
    assert w.store.snapshot is before
    a = w.history_actions.authorize(p['preview']['id'], p['preview']['digest'])
    assert w.store.snapshot is before
    args = (p['preview']['id'], p['preview']['digest'], a['authorization']['id'])
    result = w.history_actions.apply(*args)
    return result, args, p


class HistoryActionsTests(unittest.TestCase):
    def assertReplay(self, w):
        s = w.store.snapshot
        package = export_world_replay(s, w.store.session_generation)
        self.assertEqual(replay_world(loads_world_replay(dumps_world_replay(package))), s)
        self.assertEqual(loads_world_mutation(dumps_world_mutation(s)), s)

    def test_balance_correction_rebinds_earmarks_preserves_identity(self):
        w = workflow()
        before = w.store.snapshot
        result, _, p = apply(w, request(w, 'edit', 'asset_position', 'balance', amount(900)))
        self.assertEqual(result['receipt_revision'], 1)
        self.assertTrue(any(x['kind'] == 'earmark' for x in p['preview']['changes']))
        self.assertEqual(w.store.snapshot.records[:len(before.records)], before.records)
        validate_world(w.store.snapshot)
        self.assertReplay(w)

    def test_each_supported_correction_and_precision(self):
        for kind, field, value in [('earmark','allocation',amount(80)),
            ('future_inflow','expected_amount',amount(150,'approximate')),
            ('planned_expense','expected_amount',amount('', 'range', 30, 50)),
            ('world_decision','subject','Evening ceramics course'),
            ('decision_context','statement','Revised evening schedule'),
            ('account','label','North workshop')]:
            with self.subTest(kind=kind):
                w=workflow(); before=w.store.snapshot
                _,_,p=apply(w,request(w,'edit',kind,field,value))
                self.assertTrue(any(x['kind']==kind for x in p['preview']['changes']))
                self.assertEqual(w.store.snapshot.records[:len(before.records)],before.records)
                self.assertReplay(w)

    def test_unknown_does_not_become_zero(self):
        w=workflow(); _,_,p=apply(w,request(w,'edit','future_inflow','expected_amount',amount('', 'unknown')))
        row=next(x for x in p['preview']['changes'] if x['kind']=='future_inflow')
        self.assertEqual(row['after']['precision'],'unknown');self.assertIsNone(row['after']['amount'])

    def test_earmark_removal_and_preserved_sibling_references(self):
        w=workflow(); before=w.store.snapshot
        _,_,p=apply(w,request(w,'remove','earmark'))
        self.assertEqual(len(w.store.snapshot.world.members),len(before.world.members)-1)
        self.assertEqual(sum(x['retired'] for x in p['preview']['changes']),1)
        self.assertEqual(w.store.snapshot.records[:len(before.records)],before.records)
        self.assertReplay(w)

    def test_future_and_expense_removal_cancels_without_cash_effect(self):
        for kind in ('future_inflow','planned_expense'):
            w=workflow(); positions=[w.store.snapshot.get(r) for r in w.store.snapshot.world.members if r.record_kind=='asset_position']
            apply(w,request(w,'remove',kind))
            self.assertEqual(positions,[w.store.snapshot.get(r) for r in w.store.snapshot.world.members if r.record_kind=='asset_position'])
            self.assertTrue(any(getattr(w.store.snapshot.get(r),'status',None).value=='cancelled'
                for r in w.store.snapshot.world.members if r.record_kind==kind))
            self.assertReplay(w)

    def test_account_remove_exact_asset_closure(self):
        w=workflow();_,_,p=apply(w,request(w,'remove','account'))
        self.assertEqual({x['kind'] for x in p['preview']['changes'] if x['retired']},
                         {'account','asset_position','earmark'})
        self.assertReplay(w)

    def test_restore_forward_after_multiple_revisions_and_history_preserved(self):
        w=workflow();baseline=w.store.snapshot
        apply(w,request(w,'edit','future_inflow','expected_amount',amount(250)))
        apply(w,request(w,'edit','asset_position','balance',amount(930)))
        apply(w,request(w,'remove','planned_expense'))
        before=w.store.snapshot
        result,_,p=apply(w,request(w,'restore',revision=0))
        self.assertEqual(result['receipt_revision'],4)
        restored=next(row for row in p['preview']['changes'] if row['kind']=='planned_expense')
        self.assertEqual((restored['before_status'],restored['after_status']),('cancelled','planned'))
        self.assertEqual(w.store.snapshot.worlds[:len(before.worlds)],before.worlds)
        self.assertEqual(w.store.snapshot.records[:len(before.records)],before.records)
        self.assertEqual(w.history(4)['detail']['history_action']['source_revision'],0)
        self.assertEqual(w.history()['entries'][0]['history_action'],'restore')
        self.assertEqual(w.history(0)['detail']['root_ref']['id'],baseline.world.ref.id)
        self.assertEqual([x['revision'] for x in w.history()['entries']],[4,3,2,1,0])
        self.assertReplay(w)

    def test_restore_noop_and_retired_identity_fail_closed(self):
        w=workflow()
        with self.assertRaises(I3Error):w.history_actions.prepare(request(w,'restore',revision=0))
        apply(w,request(w,'remove','earmark'))
        before=w.store.snapshot
        self.assertEqual(w.history(0)['detail']['actions']['restore']['reason'],'retired_identity_requires_reactivation')
        with self.assertRaises(I3Error):w.history_actions.prepare(request(w,'restore',revision=0))
        self.assertIs(w.store.snapshot,before)

    def test_replacement_history_cannot_recreate_retired_ids(self):
        t=store();t.execute(prepared(t,replacement(t),'replace')[2]);w=workflow(t)
        self.assertFalse(w.history(0)['detail']['actions']['restore']['available'])
        self.assertReplay(w)

    def test_actual_settlement_and_correction_block_rewind(self):
        for corrected in (False,True):
            t=store(canonical_bundle());t.execute(settle(t,operation(t),'actual')[2])
            if corrected:t.execute(settle(t,correction(t),'correct')[2])
            w=workflow(t);before=t.snapshot
            a=w.history(0)['detail']['actions']
            self.assertEqual(a['restore']['reason'],'protected_reality')
            with self.assertRaises(I3Error):w.history_actions.prepare(request(w,'restore',revision=0))
            for kind in ('actual_occurrence','asset_position'):
                with self.assertRaises(I3Error):w.history_actions.prepare(request(w,'remove',kind))
            self.assertIs(t.snapshot,before);self.assertReplay(w)

    def test_unsupported_decision_retirement_explained(self):
        w=workflow();rows=w.history(0)['detail']['actions']['records']
        row=next(r for r in rows if r['ref']['record_kind']=='world_decision')
        self.assertFalse(row['remove']);self.assertEqual(row['reason'],'unsupported_retirement')
        with self.assertRaises(I3Error):w.history_actions.prepare(request(w,'remove','world_decision'))

    def test_stale_binding_preview_grant_and_tampered_historical_evidence(self):
        w=workflow();old=request(w,'edit','future_inflow','purpose','Earlier label')
        apply(w,request(w,'edit','planned_expense','purpose','Later label'))
        with self.assertRaises(I3Error):w.history_actions.prepare(old)
        restore=request(w,'restore',revision=0);p=w.history_actions.prepare(restore)
        a=w.history_actions.authorize(p['preview']['id'],p['preview']['digest'])
        # An independent ordinary writer advances authority.
        other=workflow(w.store)
        apply(other,request(other,'edit','future_inflow','purpose','Concurrent report'))
        for fn in (lambda:w.history_actions.authorize(p['preview']['id'],p['preview']['digest']),
                   lambda:w.history_actions.apply(p['preview']['id'],p['preview']['digest'],a['authorization']['id'])):
            with self.assertRaises(I3Error):fn()
        fresh=request(w,'restore',revision=0);fresh['binding']['source_digest']='0'*64
        with self.assertRaises(I3Error):w.history_actions.prepare(fresh)

    def test_duplicate_concurrent_apply_and_response_loss_recovery(self):
        w=workflow();p=w.history_actions.prepare(request(w,'edit','future_inflow','purpose','Changed'))
        a=w.history_actions.authorize(p['preview']['id'],p['preview']['digest'])
        args=(p['preview']['id'],p['preview']['digest'],a['authorization']['id'])
        execute=w.store.execute
        def lost(command):execute(command);raise OSError('response lost')
        with patch.object(w.store,'execute',side_effect=lost):result=w.history_actions.apply(*args)
        with ThreadPoolExecutor(max_workers=3) as pool:
            results=list(pool.map(lambda _:w.history_actions.apply(*args),range(3)))
        self.assertEqual(w.store.snapshot.revision,1)
        self.assertTrue(all(r['receipt']==result['receipt'] for r in results))
        self.assertEqual(w.operation_status(args[2])['receipt_revision'],1)
        with self.assertRaises(I3Error):w.history_actions.apply(args[0],'0'*64,args[2])

    def test_prepublication_failure_and_invalid_correction_are_atomic(self):
        w=workflow();before=w.store.snapshot
        with self.assertRaises(I3Error):w.history_actions.prepare(request(w,'edit','asset_position','balance',amount(1)))
        self.assertIs(w.store.snapshot,before)
        p=w.history_actions.prepare(request(w,'edit','future_inflow','purpose','Valid'))
        a=w.history_actions.authorize(p['preview']['id'],p['preview']['digest']);args=(p['preview']['id'],p['preview']['digest'],a['authorization']['id'])
        w.store._before_publish=lambda _: (_ for _ in ()).throw(RuntimeError('checkpoint'))
        with self.assertRaises(I3Error):w.history_actions.apply(*args)
        self.assertIs(w.store.snapshot,before)
        w.store._before_publish=None;w.history_actions.apply(*args);self.assertEqual(w.store.snapshot.revision,1)

    def test_reads_and_prepare_cannot_authorize_or_apply(self):
        w=workflow();before=w.store.snapshot
        p=w.history_actions.prepare(request(w,'edit','future_inflow','purpose','Changed'))
        for _ in range(2):w.history(0);w.history_actions.view()
        with self.assertRaises(I3Error):w.history_actions.apply(p['preview']['id'],p['preview']['digest'],'none')
        self.assertIs(w.store.snapshot,before)

    def test_restore_intent_is_bound_inside_replay_command(self):
        w=workflow();apply(w,request(w,'edit','future_inflow','purpose','Later'))
        apply(w,request(w,'restore',revision=0))
        package=export_world_replay(w.store.snapshot,w.store.session_generation)
        step=package.steps[-1];intent=step.command.change_set.operations[0]
        altered=replace(intent,source_digest='0'*64)
        changes=replace(step.command.change_set,operations=(altered,))
        with self.assertRaises(Rejected):
            replay_world(replace(package,steps=(*package.steps[:-1],replace(step,command=replace(step.command,change_set=changes)))))

    def test_removal_with_funding_dependency_fails_before_authorization(self):
        w=workflow(store(canonical_bundle()));before=w.store.snapshot
        choices=w.history(0)['detail']['actions']['records']
        # Canonical funding references are a regression fixture alongside the
        # materially different workshop worlds in the other tests.
        mark=next(r for r in choices if r['ref']['record_kind']=='earmark' and not r['remove'])
        self.assertIn(mark['reason'],('dependent_records','protected_reality'))
        payload=request(w,'remove','earmark');payload['target']=mark['ref']
        with self.assertRaises(I3Error):w.history_actions.prepare(payload)
        self.assertIs(w.store.snapshot,before)

    def test_restore_retires_later_observations_and_cancels_new_expectation(self):
        from finvis.world_commands import PutRecord
        from tests.test_world_mutation import proposal as ordinary_proposal
        target=store()
        account=next(target.snapshot.get(r) for r in target.snapshot.world.members if r.record_kind=='account')
        position=next(target.snapshot.get(r) for r in target.snapshot.world.members if r.record_kind=='asset_position')
        # Same textual ID across two typed kinds must not collide in expansion.
        account=replace(account,ref=Ref('studio-added',1),label='Studio SGD')
        position=replace(position,ref=Ref('studio-added',1),account_ref=record_ref(account),
                         currency=Currency.SGD,balance=AmountEstimate.exact_reported(Money(42000,Currency.SGD)))
        future=future_add(target,'later-invoice').operations[0]
        changes=ordinary_proposal(target,(PutRecord('account',account),PutRecord('position',position),future),'later-records')
        target.execute(prepared(target,changes,'later-records')[2])
        w=workflow(target);before=target.snapshot
        _,_,p=apply(w,request(w,'restore',revision=0))
        self.assertEqual(sum(r['retired'] for r in p['preview']['changes']),2)
        self.assertTrue(any(r['cancelled'] for r in p['preview']['changes']))
        self.assertEqual(target.snapshot.records[:len(before.records)],before.records)
        self.assertReplay(w)

    def test_restore_does_not_change_decision_intent(self):
        from finvis.domain import DecisionStatus
        from tests.test_s2_history import revise
        target=synthetic()
        revise(target,'world_decision','status',DecisionStatus.DEFERRED,'defer')
        w=workflow(target);before=target.snapshot
        self.assertEqual(w.history(0)['detail']['actions']['restore']['reason'],
                         'decision_intent_restore_unsupported')
        with self.assertRaises(I3Error):w.history_actions.prepare(request(w,'restore',revision=0))
        self.assertIs(target.snapshot,before)


if __name__=='__main__':unittest.main()

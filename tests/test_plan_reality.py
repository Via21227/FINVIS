"""Generic composer-to-D/E/I4 acceptance; all data is synthetic."""
from dataclasses import replace
import unittest
from unittest.mock import patch
from pathlib import Path
import tempfile

from finvis.domain import Currency, Ref
from finvis.i3_workflow import I3Error
from finvis.world_commands import PutRecord, record_ref
from finvis.world_values import AmountEstimate
from finvis.world_planning import PlannedExpenseStatus as S
from finvis.world_serialization import dumps_world_mutation
from finvis.world_history_replay import replay_history
from finvis.presentation_policy import recommend
from finvis.plan_reality import meaning
from finvis.user_runtime import UserRuntime, runtime_location
from tests.test_settlement_expansion import fixture, live, pay, refund, edit_plan
from tests.test_i4_commit import candidate_workflow
from tests.test_world_assets import exact,money
from tests.test_world_mutation import proposal,prepared
from tests.test_decision_baseline import amount
from tests.test_if_lines import setup_d2,make
from tests.test_agent_understanding import SequenceProvider
from tests.test_persistent_world import changes,prepare


def workflow(label='Local transport',gradual=True,currency=Currency.CNY):return candidate_workflow(fixture(label=label,gradual=gradual,currency=currency))[0]
def route(w,source):return w.interpret(source,route=True)['routing']
def ready(w,source,answers=None):
    r=route(w,source);answers=answers or {}
    while r['stage']=='question':
        q=r['question'];k=q['key']
        value=answers.get(k)
        if value is None:
            if k in ('source','destination','payment','target'):value=q['options'][0]['value']
            elif k=='coverage':value='apply_to_prior_balance'
            elif k=='earmark':value='none'
            elif k=='same_event':value='yes'
            else:raise AssertionError(r)
        r=w.plan_reality.answer(dict(proposal_id=r['id'],key=k,value=value))
    return w.plan_reality.prepare(dict(proposal_id=r['id']))
def execute(w,v):
    a=w.plan_reality.authorize(dict(preview_id=v['preview']['id'],digest=v['preview']['digest']))
    p=dict(preview_id=v['preview']['id'],digest=v['preview']['digest'],authorization_id=a['authorization']['id'])
    return w.plan_reality.apply(p),p


class PlanRealityTests(unittest.TestCase):
    def test_en_zh_one_time_purchase(self):
        for source in ('I bought a laptop for CNY 1844.','电脑买好了，1844。'):
            w=workflow('Laptop',False);before=w.store.snapshot
            v=ready(w,source);self.assertIs(w.store.snapshot,before)
            execute(w,v);p=live(w.store,'planned_expense');self.assertEqual(p.status,S.COMPLETED)
            self.assertEqual(p.expected_amount,before.get(record_ref(live(fixture(label='Laptop',gradual=False),'planned_expense'))).expected_amount)
            self.assertEqual(w.home()['lifecycle']['items'][0]['presentation']['recommended'],'BEFORE_ACTUAL')
    def test_gradual_budget_three_reports(self):
        w=workflow();original=live(w.store,'planned_expense').expected_amount
        for text in ('今天地铁花了86。','I spent CNY 120 on a taxi.','刚才地铁花了42。'):execute(w,ready(w,text))
        summary=w.home()['lifecycle']['items'][0]
        self.assertEqual(summary['summary']['net_actual']['value_minor'],24800)
        self.assertEqual(summary['summary']['remaining_planning']['value_minor'],45200)
        self.assertEqual(summary['summary']['estimated']['precision'],'approximate')
        self.assertEqual(live(w.store,'planned_expense').expected_amount,original)
        self.assertEqual(live(w.store,'planned_expense').status,S.PARTIALLY_REALIZED)
    def test_over_budget_no_completion(self):
        w=workflow('Food budget');execute(w,ready(w,'I spent CNY 900 on food.'))
        self.assertEqual(w.plan_reality.summary()['items'][0]['summary']['remaining_planning']['value_minor'],-20000)
        self.assertEqual(live(w.store,'planned_expense').status,S.PARTIALLY_REALIZED)
    def test_stage_course_sgd_and_explicit_complete(self):
        w=workflow('Course',currency=Currency.SGD)
        execute(w,ready(w,'I paid SGD 100 for the course.'))
        execute(w,ready(w,'I paid SGD 200 for the course.'))
        execute(w,ready(w,'The course is completed.'))
        self.assertEqual(live(w.store,'planned_expense').status,S.COMPLETED)
    def test_booked_no_cash_then_actual(self):
        w=workflow('Service',False);pos=live(w.store,'asset_position')
        execute(w,ready(w,'I booked a service for CNY 280.'))
        self.assertEqual(live(w.store,'asset_position'),pos)
        self.assertEqual(live(w.store,'planned_expense').fulfillment.committed_amount,exact(280))
        execute(w,ready(w,'I paid CNY 280 for the service.'))
        self.assertEqual(live(w.store,'planned_expense').fulfillment.committed_amount,exact(280))
    def test_partial_cancellation_no_invented_refund(self):
        w=workflow('Service');execute(w,ready(w,'I paid CNY 90 for service.'));balance=live(w.store,'asset_position').balance
        execute(w,ready(w,'Service cancelled.'))
        self.assertEqual(live(w.store,'planned_expense').status,S.CANCELLED);self.assertEqual(live(w.store,'asset_position').balance,balance)
    def test_refund_replacement_forward_history(self):
        w=workflow('Hotel');execute(w,ready(w,'酒店买好了，2800。'))
        execute(w,ready(w,'酒店后三晚退了，退回来1800。'))
        execute(w,ready(w,'我重新订了一个酒店，三晚2400。',{'consumed':amount('1000','exact')}))
        item=w.plan_reality.summary()['items'][0]
        self.assertEqual(item['summary']['net_actual']['value_minor'],340000)
        self.assertEqual([e['kind'] for e in item['events']],['actual','refund','replacement'])
        self.assertEqual(item['presentation']['recommended'],'NET_HISTORY')
        self.assertEqual(replay_history(w.store.export_replay()) if hasattr(w.store,'export_replay') else w.store.snapshot,w.store.snapshot)
    def test_legacy_refund_replacement_summary(self):
        w=workflow('Hotel',False);execute(w,ready(w,'I paid CNY 2800 for the hotel.'));execute(w,ready(w,'Hotel refunded CNY 1800.'))
        execute(w,ready(w,'I rebooked a hotel for CNY 2400.',{'consumed':amount('1000','exact')}))
        self.assertEqual(w.plan_reality.summary()['items'][0]['summary']['net_actual']['value_minor'],340000)
        self.assertEqual(live(w.store,'planned_expense').status,S.COMPLETED)
    def test_excess_refund_fails_without_change(self):
        w=workflow('Hotel');execute(w,ready(w,'I paid CNY 100 for the hotel.'));before=w.store.snapshot
        with self.assertRaises(ValueError):ready(w,'Hotel refunded CNY 101.')
        self.assertIs(w.store.snapshot,before)
    def test_unresolved_allocation_fails(self):
        w=workflow('Hotel');execute(w,ready(w,'I paid CNY 100 for the hotel.'))
        r=route(w,'I rebooked the hotel for CNY 90.');self.assertEqual(r['question']['key'],'consumed')
        with self.assertRaises(I3Error):w.plan_reality.answer(dict(proposal_id=r['id'],key='consumed',value=amount('100','approximate')))
    def test_source_and_coverage_never_inferred_from_only_account(self):
        w=workflow();r=route(w,'I spent CNY 10 on transport.')
        self.assertEqual(r['question']['key'],'source')
        r=w.plan_reality.answer(dict(proposal_id=r['id'],key='source',value='position/daily'))
        self.assertEqual(r['question']['key'],'coverage')
    def test_explicit_named_account_still_asks_coverage(self):
        w=workflow();r=route(w,'I spent CNY 10 on transport from Daily account.')
        self.assertEqual(r['question']['key'],'coverage')
    def test_included_balance_zero_posting(self):
        w=workflow();before=live(w.store,'asset_position').balance
        execute(w,ready(w,'I spent CNY 10 on transport.',{'coverage':'already_in_reported_balance'}))
        self.assertEqual(live(w.store,'asset_position').balance,before)
    def test_ambiguous_targets_require_choice(self):
        w=workflow('Local transport');old=live(w.store,'planned_expense');other=replace(old,ref=Ref('expense/other',1),purpose='Commuting')
        w.store.execute(prepared(w.store,proposal(w.store,[PutRecord('add',other)],'extra'),'extra')[2])
        r=route(w,'地铁花了42。');self.assertEqual(r['question']['key'],'target');self.assertEqual(len(r['question']['options']),2)
    def test_multiple_payments_require_original_choice(self):
        w=workflow('Service');execute(w,ready(w,'I paid CNY 100 for service.'));execute(w,ready(w,'I paid CNY 50 for service.'))
        self.assertEqual(route(w,'Service refunded CNY 30.')['question']['key'],'payment')
    def test_exact_and_finite_meaning(self):
        for source in ('I might have paid for transport.','我没支付酒店费用。','Hotel refunded CNY 10 and I paid CNY 90.'):
            self.assertEqual(meaning(source)[0],'uncertain')
        self.assertEqual(meaning('交通费别显示进度了，只显示剩多少钱。'),('display','numbers'))
    def test_currency_conflict_and_competing_amount_clarify(self):
        for source in ('I spent USD 10 on transport.','I spent CNY 10 and CNY 20 on transport.'):
            w=workflow();self.assertEqual(route(w,source)['question']['key'],'amount')
    def test_stop_and_supersession(self):
        w=workflow();r=route(w,'I spent CNY 10 on transport.');pid=r['id'];request=w.reality_routing.active_request
        w.stop_interpretation(request)
        with self.assertRaises(I3Error):w.plan_reality.answer(dict(proposal_id=pid,key='source',value='position/daily'))
        v=ready(w,'I spent CNY 20 on transport.');route(w,'I spent CNY 30 on transport.')
        with self.assertRaises(I3Error):w.plan_reality.authorize(dict(preview_id=v['preview']['id'],digest=v['preview']['digest']))
    def test_world_changed_and_wrong_digest_fail_closed(self):
        w=workflow();v=ready(w,'I spent CNY 10 on transport.')
        with self.assertRaises(I3Error):w.plan_reality.authorize(dict(preview_id=v['preview']['id'],digest='0'*64))
        pay(w.store,'external',1)
        with self.assertRaisesRegex(I3Error,'world_changed'):w.plan_reality.authorize(dict(preview_id=v['preview']['id'],digest=v['preview']['digest']))
    def test_retry_and_conflicting_identity(self):
        w=workflow();out,p=execute(w,ready(w,'I spent CNY 10 on transport.'));before=w.store.snapshot
        self.assertEqual(w.plan_reality.apply(p)['operation']['receipt'],out['operation']['receipt']);self.assertIs(w.store.snapshot,before)
        with self.assertRaises(I3Error):w.plan_reality.apply(dict(p,digest='0'*64))
    def test_no_authorization_no_apply(self):
        w=workflow();v=ready(w,'I spent CNY 10 on transport.');before=w.store.snapshot
        with self.assertRaises(I3Error):w.plan_reality.apply(dict(preview_id=v['preview']['id'],digest=v['preview']['digest'],authorization_id='fake'))
        self.assertIs(w.store.snapshot,before)
    def test_if_confirmation_materializes_and_retains_evidence(self):
        for source in ('I bought concert tickets for CNY 90.','演唱会票买了90。'):
            service,s,b,request=setup_d2();w,_,_=candidate_workflow(s);w.baselines=service.baselines;w.if_lines=service
            line=make(service,b,'If I buy concert tickets for CNY 1500?')
            r=route(w,source);self.assertEqual(r['question']['key'],'same_event')
            r=w.plan_reality.answer(dict(proposal_id=r['id'],key='same_event',value='no'));self.assertEqual(r['question']['key'],'different_event')
            out,_=execute(w,ready(w,source));v=service.view(service.lines[line['id']]);self.assertEqual(v['status'],'realized');self.assertIsNone(v['effects'])
            self.assertTrue(v['evaluation_history']);self.assertIsNotNone(v['materialized_ref'])
            self.assertEqual(w.plan_reality.summary()['items'][-1]['summary']['net_actual']['value_minor'],9000)
    def test_if_unrelated_branch_stays_valid(self):
        service,s,b,_=setup_d2();w,_,_=candidate_workflow(s);w.baselines=service.baselines;w.if_lines=service
        line=make(service,b,'If I buy concert tickets for CNY 100?');other=make(service,b,'If I buy a reference book for CNY 20?')
        execute(w,ready(w,'I bought concert tickets for CNY 90.'))
        self.assertEqual(service.view(service.lines[other['id']])['status'],'valid')
        self.assertEqual(service.view(service.lines[line['id']])['status'],'realized')
    def test_derived_component_saved_explicitly_before_actual(self):
        service,s,b,_=setup_d2();w,_,_=candidate_workflow(s);w.baselines=service.baselines;w.if_lines=service
        r=route(w,'I paid CNY 80 for the course.');self.assertEqual(r['stage'],'save_plan');before=live(s,'asset_position').balance
        execute(w,w.plan_reality.prepare(dict(proposal_id=r['id'])));self.assertEqual(live(s,'asset_position').balance,before)
        execute(w,ready(w,'I paid CNY 80 for the course.'))
        self.assertEqual(w.plan_reality.summary()['items'][-1]['summary']['net_actual']['value_minor'],8000)
    def test_legacy_budget_requires_explicit_gradual_review_first(self):
        w=workflow('Food budget',False);r=route(w,'I spent CNY 25 on food.')
        self.assertEqual(r['stage'],'enable_gradual');before=live(w.store,'asset_position').balance
        execute(w,w.plan_reality.prepare(dict(proposal_id=r['id'])))
        self.assertEqual(live(w.store,'asset_position').balance,before)
        execute(w,ready(w,'I spent CNY 25 on food.'))
        self.assertEqual(live(w.store,'planned_expense').status,S.PARTIALLY_REALIZED)
    def test_refund_preview_exposes_original_and_net(self):
        w=workflow('Hotel');execute(w,ready(w,'I paid CNY 100 for hotel.'))
        v=ready(w,'Hotel refunded CNY 30.')
        self.assertEqual(v['linked_payment']['amount']['minor_units'],10000)
        self.assertEqual(v['summary_after']['net_actual']['value_minor'],7000)
    def test_unknown_named_category_retains_summary(self):
        w=workflow('Ceramic kiln service');v=ready(w,'I booked Ceramic kiln service for CNY 60.')
        execute(w,v)
        self.assertEqual(live(w.store,'planned_expense').fulfillment.committed_amount,exact(60))
    def test_unrecognized_account_cannot_be_submitted(self):
        w=workflow();r=route(w,'地铁花了10。');before=w.store.snapshot
        with self.assertRaises(I3Error):w.plan_reality.answer(dict(proposal_id=r['id'],key='source',value='position/not-mine'))
        self.assertIs(w.store.snapshot,before)

    def test_goal_reserve_without_target_has_no_invented_progress(self):
        from finvis.world_domain import Earmark,EarmarkRole
        from tests.test_world_assets import observation
        w=workflow();pos=live(w.store,'asset_position')
        mark=Earmark(Ref('reserve/savings',1),pos.owner_id,pos.world_id,record_ref(pos),exact(300),'Workshop goal',EarmarkRole.GOAL,False,observation('goal'))
        w.store.execute(prepared(w.store,proposal(w.store,[PutRecord('goal',mark)],'goal'),'goal')[2])
        item=next(r for r in w.plan_reality.summary()['items'] if r['semantic']=='goal')
        self.assertEqual(item['summary']['accumulated']['amount']['minor_units'],30000)
        self.assertIsNone(item['summary']['target']);self.assertEqual(item['presentation']['recommended'],'SUMMARY')
        before=w.store.snapshot;r=route(w,'这个目标我想看进度。')
        self.assertEqual(r['question']['key'],'display_unavailable');self.assertIs(w.store.snapshot,before)
    def test_earmark_selection_is_explicit(self):
        from finvis.world_domain import Earmark,EarmarkRole
        from tests.test_world_assets import observation
        w=workflow();pos=live(w.store,'asset_position')
        mark=Earmark(Ref('reserve/transport',1),pos.owner_id,pos.world_id,record_ref(pos),exact(300),'Transport fund',EarmarkRole.PURCHASE,False,observation('fund'))
        w.store.execute(prepared(w.store,proposal(w.store,[PutRecord('fund',mark)],'fund'),'fund')[2])
        r=route(w,'I spent CNY 20 on transport.');r=w.plan_reality.answer(dict(proposal_id=r['id'],key='source',value='position/daily'))
        self.assertEqual(r['question']['key'],'earmark')
        execute(w,ready(w,'I spent CNY 20 on transport.',{'earmark':mark.ref.id}))
        self.assertEqual(live(w.store,'earmark').allocation,exact(280))

    def test_preference_route_is_non_financial(self):
        w=workflow();before=dumps_world_mutation(w.store.snapshot)
        r=route(w,'交通费别显示进度了，只显示剩多少钱。');self.assertEqual(r['stage'],'display');self.assertEqual(r['preference'],'numbers')
        self.assertEqual(dumps_world_mutation(w.store.snapshot),before)
        with self.assertRaises(I3Error):w.plan_reality.prepare(dict(proposal_id=r['id']))


class PresentationTests(unittest.TestCase):
    def test_registry_generic_defaults(self):
        for semantic,expected in [('purchase','BEFORE_ACTUAL'),('budget','BUDGET_PROGRESS'),('goal','GOAL_PROGRESS'),('net_history','NET_HISTORY'),('alien_new_category','SUMMARY')]:
            item=dict(semantic=semantic,summary=dict(estimated={'precision':'approximate','amount':{'minor_units':50000,'currency':'SGD'}}))
            self.assertEqual(recommend(item)['recommended'],expected)
    def test_unknown_and_range_budget_no_fake_progress(self):
        for precision in ('unknown','range'):
            r=recommend(dict(semantic='budget',summary=dict(estimated=dict(precision=precision))))
            self.assertEqual(r['recommended'],'SUMMARY');self.assertNotIn('progress',r['choices'])
    def test_only_meaningful_series_offers_trend(self):
        series=[dict(at='2026-09-0'+str(i),value_minor=i*100,currency='CNY') for i in (1,2,3)]
        self.assertEqual(recommend(dict(semantic='time_series',series=series))['recommended'],'TREND')
        for rows in ([dict(p,at=x) for p,x in zip(series,['a','b','c'])],series[:2],list(reversed(series)),[dict(v,currency='SGD') if i==1 else v for i,v in enumerate(series)]):
            self.assertNotIn('trend',recommend(dict(semantic='time_series',series=rows))['choices'])
        self.assertNotIn('trend',recommend(dict(semantic='purchase',series=series))['choices'])


class DurableLifecycleTests(unittest.TestCase):
    def test_restart_receipt_history_and_ephemeral_grant(self):
        root=Path(tempfile.mkdtemp(prefix='finvis-lifecycle-app-')).resolve();runtime=UserRuntime(SequenceProvider([]),runtime_location(root))
        w=runtime.ensure();s=w.store
        records=[replace(r,owner_id=s.snapshot.world.owner_id,world_id=s.snapshot.world.world_id) for r in fixture(label='Hotel').snapshot.records if record_ref(r).record_kind in ('account','asset_position','planned_expense')]
        s.execute(prepare(s,changes(s,[PutRecord('seed/'+r.ref.id,r) for r in records]),'seed'))
        execute(w,ready(w,'I paid CNY 2800 for hotel.'));out,retry=execute(w,ready(w,'Hotel refunded CNY 1800.'))
        pending=ready(w,'I rebooked hotel for CNY 2400.',{'consumed':amount('1000','exact')});grant=w.plan_reality.authorize(dict(preview_id=pending['preview']['id'],digest=pending['preview']['digest']))
        before=dumps_world_mutation(s.snapshot);runtime.close()
        runtime=UserRuntime(SequenceProvider([]),runtime_location(root));self.addCleanup(runtime.close);w=runtime.ensure()
        self.assertEqual(dumps_world_mutation(w.store.snapshot),before)
        self.assertEqual(w.plan_reality.apply(retry)['operation']['receipt'],out['operation']['receipt'])
        with self.assertRaises(I3Error):w.plan_reality.apply(dict(preview_id=pending['preview']['id'],digest=pending['preview']['digest'],authorization_id=grant['authorization']['id']))
        execute(w,ready(w,'I rebooked hotel for CNY 2400.',{'consumed':amount('1000','exact')}))
        self.assertEqual(w.plan_reality.summary()['items'][0]['summary']['net_actual']['value_minor'],340000)
        self.assertEqual(replay_history(w.store.export_replay()),w.store.snapshot)

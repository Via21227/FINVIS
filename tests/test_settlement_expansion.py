"""Generic reviewed Core lifecycle tests. All worlds are synthetic/in-memory."""
from dataclasses import replace
from datetime import timedelta
import json
import unittest

from finvis.domain import Currency, Money, PlanStatus, Ref
from finvis.world_values import AmountEstimate, AmountPrecision, ObservationRole, TypedRef
from finvis.world_domain import WorldAssetBundle, WorldState
from finvis.world_planning import (PlannedExpense, PlannedExpenseStatus as S, ActualOccurrence,
    OccurrenceKind as K, SettlementStatus as SS, WorldPlan, WorldPlanningBundle, PlanEvaluationStatus,
    dumps_world_planning, loads_world_planning)
from finvis.world_fulfillment import (ExpenseFulfillment, OccurrenceLineage, ReplacementLink,
    IfMaterializationEvidence, expense_summary, capture_if_evidence)
from finvis.world_settlement import SettleOccurrence, BalanceCoverage, BalanceBasis, propose_settlement
from finvis.world_commands import PutRecord, record_ref
from finvis.world_store import WorldStore, initial_world, validate_world
from finvis.world_serialization import dumps_world_mutation, loads_world_mutation, payload_digest
from finvis.world_replay import export_world_replay, replay_world, dumps_world_replay, loads_world_replay
from finvis.world_history import history_view
from finvis.history_actions import proposal as history_proposal
from tests.test_world_assets import account, position, observation, exact, money, NOW, OWNER, WORLD
from tests.test_world_mutation import proposal, prepared


def fixture(currency=Currency.CNY, estimate=None, label='Commuting', gradual=True):
    accounts = (account('daily', 'Daily account'), account('reserve', 'Second account'))
    positions = tuple(position(a.ref.id.split('/')[-1], a, exact(10000, currency), currency) for a in accounts)
    state = WorldState(Ref('state', 1), OWNER, WORLD, tuple(map(record_ref, accounts)),
        tuple(map(record_ref, positions)), (), observation('state'), 'Synthetic reviewed balances')
    meta = observation('budget', ObservationRole.PLANNED)
    expense = PlannedExpense(Ref('expense/component', 1), OWNER, WORLD,
        estimate or AmountEstimate.approximate(money(700, currency)), label, meta.as_of, (), None,
        S.PLANNED, meta, fulfillment=ExpenseFulfillment() if gradual else None)
    plan = WorldPlan(Ref('plan', 1), OWNER, WORLD, record_ref(state), None, (), (record_ref(expense),), (), (),
        PlanStatus.ADOPTED, PlanEvaluationStatus.PENDING, observation('plan', ObservationRole.PLANNED))
    bundle = WorldPlanningBundle(WorldAssetBundle(state, accounts, positions, ()), plan, (), (expense,), (), (), ())
    return WorldStore(initial_world(bundle, Ref('world', 1)), 'generic-lifecycle')


def live(store, kind, name=None):
    return next(store.snapshot.get(r) for r in store.snapshot.world.members if r.record_kind == kind
                and (name is None or r.ref.id == name))


def event(store, name, value, kind=K.EXPENSE, lineage=None, source='position/daily', related=True,
          correct=None, complete=False, basis=BalanceBasis.PRIOR):
    currency = live(store, 'planned_expense').expected_amount.currency
    amount = value if type(value) is AmountEstimate else exact(value, currency)
    at = max(NOW+timedelta(minutes=store.snapshot.revision+1), store.snapshot.receipts[-1].recorded_at+timedelta(minutes=1)) if store.snapshot.receipts else NOW+timedelta(minutes=1)
    meta = replace(observation(name, ObservationRole.ACTUAL), recorded_at=at)
    old = live(store, 'actual_occurrence', correct) if correct else None
    if old:
        meta = replace(meta, corrects_ref=old.ref, correction_reason='Original report was wrong')
        lineage = old.lineage if lineage is None else lineage
    pos = live(store, 'asset_position', source) if source else None
    expense = live(store, 'planned_expense') if related else None
    report = ActualOccurrence(Ref(old.ref.id, old.ref.version+1) if old else Ref('actual/'+name, 1),
        store.snapshot.world.owner_id, store.snapshot.world.world_id, kind, amount, meta.as_of,
        (record_ref(expense),) if expense else (), SS.UNALLOCATED, meta,
        record_ref(pos) if pos and kind is K.EXPENSE else None,
        record_ref(pos) if pos and kind is K.INFLOW else None, lineage=lineage)
    coverage = BalanceCoverage(record_ref(report), record_ref(pos), basis, meta,
        'Reviewed exact prior/current balance coverage') if pos else None
    return SettleOccurrence(name, report, record_ref(old) if old else None, coverage,
        record_ref(expense) if expense else None, None, old.balance_effect_ref if old else None,
        complete_plan=complete)


def command(store, operation, name=None):
    name = name or operation.operation_id
    change = propose_settlement(store.snapshot, store.session_generation, operation, Ref('change/'+name, 1))
    from tests.test_persistent_world import prepare
    return prepare(store, change, name)


def pay(store, name, value, **kwargs):
    c = command(store, event(store, name, value, **kwargs))
    return store.execute(c), c


def refund(store, name, value, parent='actual/payment', **kwargs):
    return pay(store, name, value, kind=K.INFLOW,
        lineage=OccurrenceLineage(refund_of_ref=record_ref(live(store, 'actual_occurrence', parent))), **kwargs)


def edit_plan(store, name, **values):
    old = live(store, 'planned_expense')
    at = max(NOW+timedelta(minutes=store.snapshot.revision+1), store.snapshot.receipts[-1].recorded_at+timedelta(minutes=1)) if store.snapshot.receipts else NOW+timedelta(minutes=1)
    meta = replace(observation(name, ObservationRole.PLANNED), recorded_at=at,
        corrects_ref=old.ref, correction_reason='Explicit reviewed plan lifecycle')
    new = replace(old, ref=Ref(old.ref.id, old.ref.version+1), observation=meta, **values)
    c = prepared(store, proposal(store, (PutRecord(name, new, record_ref(old)),), name, at=at), name)[2]
    return store.execute(c)


class ExpandedSettlementTests(unittest.TestCase):
    def rejected(self, store, fn, text=None):
        before = store.snapshot
        encoded = dumps_world_mutation(before)
        with self.assertRaises(ValueError) as e: fn()
        if text: self.assertIn(text, str(e.exception))
        self.assertIs(store.snapshot, before)
        self.assertEqual(encoded, dumps_world_mutation(store.snapshot))

    def roundtrip(self, store):
        snap = store.snapshot
        self.assertEqual(loads_world_mutation(dumps_world_mutation(snap)), snap)
        self.assertEqual(replay_world(loads_world_replay(dumps_world_replay(export_world_replay(snap, store.session_generation)))), snap)
        validate_world(snap)

    def test_gradual_transport_three_distinct_occurrences(self):
        s=fixture(); original=live(s,'planned_expense')
        for name, value in [('metro',86),('taxi',120),('bus',42)]: pay(s,name,value)
        p=live(s,'planned_expense'); view=expense_summary(s.snapshot,p)
        self.assertEqual(p.status,S.PARTIALLY_REALIZED);self.assertEqual(p.expected_amount,original.expected_amount)
        self.assertEqual(len(p.fulfillment.occurrence_refs),3)
        self.assertEqual(view['net_actual']['value_minor'],24800)
        self.assertEqual(view['remaining_planning']['value_minor'],45200)
        self.assertEqual(view['remaining_planning']['precision'],'approximate');self.roundtrip(s)

    def test_food_above_approximate_budget_stays_partial(self):
        s=fixture(estimate=AmountEstimate.approximate(money(100)),label='Food')
        pay(s,'groceries',75);pay(s,'meal',40)
        v=expense_summary(s.snapshot,live(s,'planned_expense'))
        self.assertEqual(v['remaining_planning']['value_minor'],-1500);self.assertEqual(v['status'],'partially_realized')

    def test_equal_exact_budget_is_not_completion_evidence(self):
        s=fixture(estimate=exact(200));pay(s,'service-deposit',200)
        self.assertEqual(live(s,'planned_expense').status,S.PARTIALLY_REALIZED)

    def test_course_stages_and_explicit_final_payment_sgd(self):
        s=fixture(Currency.SGD,exact(900,Currency.SGD),'Course')
        pay(s,'stage-one',300);pay(s,'stage-two',600,complete=True)
        self.assertEqual(live(s,'planned_expense').status,S.COMPLETED)
        self.assertEqual(s.snapshot.get(TypedRef('world_plan',s.snapshot.world.plan_ref)).planned_expense_refs,())
        self.roundtrip(s)

    def test_explicit_completion_after_payments(self):
        s=fixture();pay(s,'service',300);edit_plan(s,'finished',status=S.COMPLETED)
        self.assertEqual(live(s,'planned_expense').status,S.COMPLETED);self.roundtrip(s)

    def test_cancel_partial_keeps_payment_and_allows_refund(self):
        s=fixture();pay(s,'payment',300);edit_plan(s,'cancel',status=S.CANCELLED)
        refund(s,'refund',100)
        self.assertEqual(live(s,'planned_expense').status,S.CANCELLED)
        self.assertEqual(expense_summary(s.snapshot,live(s,'planned_expense'))['net_actual']['value_minor'],20000)
        self.rejected(s,lambda:pay(s,'later',20));self.roundtrip(s)

    def test_cancel_unpaid_no_balance_effect(self):
        s=fixture();before=live(s,'asset_position');edit_plan(s,'cancel',status=S.CANCELLED)
        self.assertEqual(live(s,'asset_position'),before)

    def test_no_reopen_completed_or_cancelled_gradual_plan(self):
        for status in (S.COMPLETED,S.CANCELLED):
            s=fixture();pay(s,'one',20);edit_plan(s,'close',status=status)
            self.rejected(s,lambda:edit_plan(s,'reopen',status=S.PARTIALLY_REALIZED))

    def test_legacy_one_time_payment_still_completes(self):
        s=fixture(gradual=False);pay(s,'payment',86)
        self.assertEqual(live(s,'planned_expense').status,S.COMPLETED)
        self.rejected(s,lambda:pay(s,'two',120),'already realized');self.roundtrip(s)

    def test_enroll_existing_unpaid_legacy_plan(self):
        s=fixture(gradual=False);edit_plan(s,'enroll',fulfillment=ExpenseFulfillment())
        pay(s,'one',86);pay(s,'two',120);self.assertEqual(live(s,'planned_expense').status,S.PARTIALLY_REALIZED)

    def test_completed_legacy_cannot_be_reinterpreted(self):
        s=fixture(gradual=False);pay(s,'payment',86)
        self.rejected(s,lambda:edit_plan(s,'enroll',status=S.PLANNED,occurrence_ref=None,fulfillment=ExpenseFulfillment()))

    def test_partial_retry_and_conflicting_command_identity(self):
        s=fixture();receipt,c=pay(s,'payment',86);snap=s.snapshot
        self.assertEqual(s.execute(c),receipt);self.assertIs(s.snapshot,snap)
        self.rejected(s,lambda:s.execute(replace(c,recorded_at=c.recorded_at+timedelta(seconds=1))))

    def test_refund_retry_no_duplicate_money(self):
        s=fixture();pay(s,'payment',2800);receipt,c=refund(s,'refund',1800)
        snap=s.snapshot;self.assertEqual(s.execute(c),receipt);self.assertIs(s.snapshot,snap)
        self.assertEqual(live(s,'asset_position','position/daily').balance,exact(9000));self.roundtrip(s)

    def test_multiple_partial_refunds_to_full_refund(self):
        s=fixture();pay(s,'payment',500);refund(s,'first',200);refund(s,'second',300)
        v=expense_summary(s.snapshot,live(s,'planned_expense'))
        self.assertEqual(v['gross_actual']['value_minor'],50000);self.assertEqual(v['refunded']['value_minor'],50000)
        self.assertEqual(v['net_actual']['value_minor'],0);self.assertEqual(len(v['occurrence_refs']),3);self.roundtrip(s)

    def test_refund_above_remaining_is_rejected_atomically(self):
        s=fixture();pay(s,'payment',500);refund(s,'first',400)
        self.rejected(s,lambda:refund(s,'second',101),'refundable')

    def test_refund_wrong_currency(self):
        s=fixture();pay(s,'payment',500)
        self.rejected(s,lambda:refund(s,'second',exact(100,Currency.SGD)))

    def test_unknown_refund_destination_and_payment_source(self):
        s=fixture();self.rejected(s,lambda:pay(s,'missing',30,source=None),'source_unknown')
        pay(s,'payment',500)
        self.rejected(s,lambda:refund(s,'missing',30,source=None),'destination_unknown')

    def test_refund_destination_can_be_another_reviewed_account(self):
        s=fixture(Currency.SGD);pay(s,'payment',500);refund(s,'returned',200,source='position/reserve')
        self.assertEqual(live(s,'asset_position','position/daily').balance,exact(9500,Currency.SGD))
        self.assertEqual(live(s,'asset_position','position/reserve').balance,exact(10200,Currency.SGD));self.roundtrip(s)

    def test_refund_of_inflow_or_unsettled_report_rejected(self):
        s=fixture();pay(s,'income',500,kind=K.INFLOW,related=False)
        self.rejected(s,lambda:refund(s,'bad',100,parent='actual/income'))

    def test_refund_cannot_detach_gradual_plan(self):
        s=fixture();pay(s,'payment',500)
        self.rejected(s,lambda:refund(s,'bad',100,related=False),'membership')

    def test_legacy_payment_can_have_forward_linked_refund(self):
        s=fixture(gradual=False);pay(s,'payment',500);original=live(s,'actual_occurrence')
        refund(s,'back',200,related=False)
        self.assertEqual(live(s,'actual_occurrence','actual/payment'),original)
        self.assertEqual(expense_summary(s.snapshot,live(s,'planned_expense'))['net_actual']['value_minor'],30000)
        self.assertEqual(len(expense_summary(s.snapshot,live(s,'planned_expense'))['occurrence_refs']),2);self.roundtrip(s)

    def test_included_refund_has_zero_posting_but_real_economic_refund(self):
        s=fixture();pay(s,'payment',500);before=live(s,'asset_position')
        refund(s,'included',200,basis=BalanceBasis.INCLUDED)
        self.assertEqual(live(s,'asset_position'),before)
        self.assertEqual(live(s,'actual_occurrence','actual/included').settlement_status,SS.ALREADY_IN_BALANCE)
        self.assertEqual(expense_summary(s.snapshot,live(s,'planned_expense'))['net_actual']['value_minor'],30000)

    def test_unbounded_refundable_amount_needs_clarification(self):
        s=fixture();pay(s,'payment',AmountEstimate.approximate(money(500)),basis=BalanceBasis.INCLUDED)
        self.rejected(s,lambda:refund(s,'back',100),'bounds')

    def test_bounded_range_refund_safety_and_precision(self):
        s=fixture();pay(s,'payment',500)
        value=AmountEstimate.range(money(100),money(120))
        refund(s,'back',value);v=expense_summary(s.snapshot,live(s,'planned_expense'))
        self.assertEqual(v['net_actual']['precision'],'range')
        self.assertEqual((v['net_actual']['lower_minor'],v['net_actual']['upper_minor']),(38000,40000));self.roundtrip(s)

    def test_range_budget_remaining_preserves_bounds(self):
        s=fixture(estimate=AmountEstimate.range(money(600),money(800)))
        pay(s,'one',206);v=expense_summary(s.snapshot,live(s,'planned_expense'))['remaining_planning']
        self.assertEqual((v['lower_minor'],v['upper_minor']),(39400,59400));self.assertEqual(v['precision'],'range')

    def test_unknown_estimate_does_not_block_supported_actual(self):
        s=fixture(estimate=AmountEstimate.unknown('No estimate',Currency.CNY));pay(s,'one',40)
        self.assertEqual(expense_summary(s.snapshot,live(s,'planned_expense'))['remaining_planning']['precision'],'unknown')

    def replacement(self,s,consumed=1000):
        return OccurrenceLineage(replacement=ReplacementLink(record_ref(live(s,'actual_occurrence','actual/payment')),
            (record_ref(live(s,'actual_occurrence','actual/back')),),exact(consumed)))

    def test_hotel_refund_replacement_forward_history(self):
        s=fixture(estimate=AmountEstimate.approximate(money(3000)),label='Accommodation')
        pay(s,'payment',2800);refund(s,'back',1800)
        pay(s,'replacement',2400,lineage=self.replacement(s))
        v=expense_summary(s.snapshot,live(s,'planned_expense'))
        self.assertEqual(v['net_actual']['value_minor'],340000)
        self.assertEqual(v['remaining_planning']['value_minor'],-40000)
        self.assertEqual([s.snapshot.get(r).ref.id for r in v['occurrence_refs']],['actual/payment','actual/back','actual/replacement'])
        from tests.test_world_application import authority
        h=history_view(authority(s).read(),3)
        self.assertEqual(len(h['entries']),4)
        row=next(r for r in h['detail']['rows'] if r['kind']=='actual_occurrence')
        self.assertEqual(row['event_semantics'],'replacement')
        self.assertEqual(row['lineage']['replacement']['consumed_amount']['amount']['minor_units'],100000)
        self.roundtrip(s)

    def test_replacement_allocation_must_be_explicit_and_exact(self):
        s=fixture();pay(s,'payment',2800);refund(s,'back',1800)
        self.rejected(s,lambda:pay(s,'replacement',2400,lineage=self.replacement(s,900)),'allocation')
        with self.assertRaises(ValueError):
            replace(self.replacement(s).replacement,consumed_amount=AmountEstimate.approximate(money(1000)))

    def test_replacement_must_enumerate_all_refunds(self):
        s=fixture();pay(s,'payment',2800);refund(s,'back',1800)
        bad=replace(self.replacement(s).replacement,refund_refs=())
        self.rejected(s,lambda:pay(s,'replacement',2400,lineage=OccurrenceLineage(replacement=bad)),'complete refund')

    def test_replacement_duplicate_successor_rejected(self):
        s=fixture();pay(s,'payment',2800);refund(s,'back',1800);link=self.replacement(s)
        pay(s,'replacement',2400,lineage=link)
        self.rejected(s,lambda:pay(s,'second',2400,lineage=link),'successor')

    def test_correction_changes_wrong_report_not_refund(self):
        s=fixture();pay(s,'payment',500);pay(s,'correct',450,correct='actual/payment')
        v=expense_summary(s.snapshot,live(s,'planned_expense'))
        self.assertEqual(v['gross_actual']['value_minor'],45000);self.assertEqual(v['refunded']['value_minor'],0)
        self.assertEqual(len(live(s,'planned_expense').fulfillment.occurrence_refs),1)
        self.assertEqual(s.snapshot.get(TypedRef('actual_occurrence',Ref('actual/payment',1))).actual_amount,exact(500));self.roundtrip(s)

    def test_refund_correction_applies_only_difference(self):
        s=fixture();pay(s,'payment',500);refund(s,'back',200)
        pay(s,'correct-back',150,kind=K.INFLOW,correct='actual/back')
        self.assertEqual(live(s,'asset_position','position/daily').balance,exact(9650));self.roundtrip(s)

    def test_correction_cannot_make_existing_refunds_excessive(self):
        s=fixture();pay(s,'payment',500);refund(s,'back',400)
        self.rejected(s,lambda:pay(s,'wrong',300,correct='actual/payment'),'refundable')

    def test_correction_cannot_relabel_income_as_refund(self):
        s=fixture();pay(s,'payment',500);pay(s,'income',100,kind=K.INFLOW,related=False)
        link=OccurrenceLineage(refund_of_ref=record_ref(live(s,'actual_occurrence','actual/payment')))
        self.rejected(s,lambda:pay(s,'relabel',100,kind=K.INFLOW,related=False,correct='actual/income',lineage=link),'identity')

    def test_stale_payment_ref_rejected_for_new_refund(self):
        s=fixture();pay(s,'payment',500);old=record_ref(live(s,'actual_occurrence'))
        pay(s,'correct',450,correct='actual/payment')
        self.rejected(s,lambda:pay(s,'refund',100,kind=K.INFLOW,lineage=OccurrenceLineage(refund_of_ref=old)),'stale')

    def test_later_refund_preserves_historical_replacement_allocation(self):
        s=fixture();pay(s,'payment',2800);refund(s,'back',1800);link=self.replacement(s)
        pay(s,'new',2400,lineage=link);refund(s,'late',100)
        self.assertEqual(live(s,'actual_occurrence','actual/new').lineage,link)
        self.assertEqual(expense_summary(s.snapshot,live(s,'planned_expense'))['net_actual']['value_minor'],330000)
        self.roundtrip(s)

    def test_no_forged_gradual_membership_through_put(self):
        s=fixture();pay(s,'one',50)
        self.rejected(s,lambda:edit_plan(s,'erase',status=S.PLANNED,fulfillment=ExpenseFulfillment()),'membership')

    def test_commitment_has_no_cash_effect_and_is_retained(self):
        s=fixture();before=live(s,'asset_position')
        f=ExpenseFulfillment(committed_amount=exact(2800),commitment_evidence=(Ref('booking-proof',1),))
        edit_plan(s,'booking',fulfillment=f)
        self.assertEqual(live(s,'asset_position'),before)
        pay(s,'deposit',500)
        self.assertEqual(expense_summary(s.snapshot,live(s,'planned_expense'))['committed'],exact(2800));self.roundtrip(s)

    def test_commitment_without_evidence_or_wrong_currency_rejected(self):
        with self.assertRaises(ValueError): ExpenseFulfillment(committed_amount=exact(100))
        s=fixture();self.rejected(s,lambda:edit_plan(s,'booking',fulfillment=ExpenseFulfillment(
            committed_amount=exact(100,Currency.SGD),commitment_evidence=(Ref('proof',1),))))

    def test_exact_authorization_required_and_stale_preview_rejected(self):
        s=fixture();c=command(s,event(s,'prepared',50));pay(s,'other',20)
        self.rejected(s,lambda:s.execute(c))

    def test_failure_before_publication_keeps_all_records_receipts_and_retry(self):
        s=fixture();pay(s,'payment',500);op=event(s,'back',100,kind=K.INFLOW,
            lineage=OccurrenceLineage(refund_of_ref=record_ref(live(s,'actual_occurrence'))))
        c=command(s,op);before=s.snapshot
        def fail(_):raise RuntimeError('injected failure')
        s._before_publish=fail
        with self.assertRaises(RuntimeError):s.execute(c)
        self.assertIs(s.snapshot,before);s._before_publish=None
        receipt=s.execute(c);self.assertEqual(s.execute(c),receipt)

    def test_expanded_codec_is_closed_and_legacy_codec_unchanged(self):
        s=fixture();new=live(s,'planned_expense');old=replace(new,fulfillment=None)
        wire=json.loads(dumps_world_planning(new));self.assertEqual(wire['schema_version'],'finvis.world-planning.v0.2')
        self.assertEqual(loads_world_planning(json.dumps(wire)),new)
        wire['schema_version']='finvis.world-planning.v0.1'
        with self.assertRaises(ValueError):loads_world_planning(json.dumps(wire))
        self.assertNotIn('fulfillment',dumps_world_planning(old))
        wire=json.loads(dumps_world_planning(new));wire['value']['fulfillment']['value']['hidden']=True
        with self.assertRaises(ValueError):loads_world_planning(json.dumps(wire))

    def test_complete_flag_roundtrip_and_no_implicit_boolean(self):
        s=fixture();op=event(s,'complete',40,complete=True)
        self.assertEqual(loads_world_mutation(dumps_world_mutation(op)),op)
        with self.assertRaises(ValueError):replace(op,complete_plan=1)

    def test_history_restore_cannot_erase_partial_realization(self):
        from finvis.world_commands import HistoryAction
        s=fixture();initial=s.snapshot;pay(s,'one',50)
        intent=HistoryAction('restore','restore',0,initial.world.ref,payload_digest(initial),None,None,None,'Explicit restore')
        self.rejected(s,lambda:history_proposal(s.snapshot,s.session_generation,intent,NOW+timedelta(minutes=4),'restore'))

    def test_if_evidence_foundation_no_if_authority(self):
        from tests.test_if_lines import setup_d2, make
        for source in ('If I buy a reference book for CNY 30?', '如果买一本参考书，大约人民币30元？'):
            service,s,b,_=setup_d2();line=make(service,b,source)
            before=s.snapshot;proof=capture_if_evidence(service,line['id'],(Ref('same-event-confirmed',1),))
            self.assertIs(s.snapshot,before)
            position=live(s,'asset_position')
            receipt,c=pay(s,'real-book',29,related=False,source=position.ref.id,
                lineage=OccurrenceLineage(if_evidence=proof))
            self.assertEqual(s.execute(c),receipt)
            actual=live(s,'actual_occurrence','actual/real-book')
            self.assertEqual(actual.lineage.if_evidence,proof)
            self.assertIn('evaluations',json.loads(proof.evidence_json));self.roundtrip(s)
            self.rejected(s,lambda:pay(s,'duplicate-book',29,related=False,source=position.ref.id,
                lineage=OccurrenceLineage(if_evidence=proof)),'already materialized')

    def test_bounded_approximate_remaining_keeps_bounds(self):
        estimate=AmountEstimate.approximate(money(700),money(650),money(750))
        s=fixture(estimate=estimate);pay(s,'one',206)
        v=expense_summary(s.snapshot,live(s,'planned_expense'))['remaining_planning']
        self.assertEqual((v['value_minor'],v['lower_minor'],v['upper_minor']),(49400,44400,54400))
        self.assertEqual(v['precision'],'approximate')

    def test_concurrent_prepared_refunds_fail_stale_then_excess(self):
        s=fixture();pay(s,'payment',500)
        link=OccurrenceLineage(refund_of_ref=record_ref(live(s,'actual_occurrence')))
        first=command(s,event(s,'first',300,kind=K.INFLOW,lineage=link))
        second=command(s,event(s,'second',300,kind=K.INFLOW,lineage=link))
        s.execute(first);self.rejected(s,lambda:s.execute(second))
        self.rejected(s,lambda:refund(s,'second',300),'refundable')

    def test_partial_reserve_consumption_refund_does_not_restore_reserve(self):
        from tests.test_world_assets import earmark
        from finvis.world_domain import EarmarkRole
        from finvis.world_planning import FundingLink
        s=fixture();pos=live(s,'asset_position','position/daily')
        mark=earmark('budget',pos,exact(700),EarmarkRole.PURCHASE,'Shared purpose')
        s.execute(prepared(s,proposal(s,(PutRecord('reserve',mark),),'reserve'),'reserve')[2])
        edit_plan(s,'funding',funding_links=(FundingLink(record_ref(mark),exact(700)),))
        op=replace(event(s,'payment',86),earmark_ref=record_ref(live(s,'earmark')))
        s.execute(command(s,op));refund(s,'back',30)
        self.assertEqual(live(s,'earmark').allocation,exact(614));self.roundtrip(s)

    def test_unknown_coverage_and_stale_evidence_fail_closed(self):
        from finvis.world_values import FreshnessAssessment, FreshnessStatus
        s=fixture();op=event(s,'one',50)
        self.rejected(s,lambda:command(s,replace(op,coverage=None)),'balance_coverage_unknown')
        report=replace(op.report,observation=replace(op.report.observation,
            freshness=FreshnessAssessment(FreshnessStatus.UNKNOWN,'Not established',NOW)))
        self.rejected(s,lambda:command(s,replace(op,report=report)),'Current reviewed')

    def test_forged_unsettled_refund_link_does_not_become_authority(self):
        s=fixture();pay(s,'payment',500)
        op=event(s,'unsettled',100,kind=K.INFLOW,lineage=OccurrenceLineage(
            refund_of_ref=record_ref(live(s,'actual_occurrence'))))
        bad=proposal(s,(PutRecord('bypass',op.report),),'bypass')
        self.rejected(s,lambda:s.preview(OWNER,bad,Ref('bypass',1)))

    def test_one_time_full_refund_history_keeps_original_actual(self):
        s=fixture(gradual=False);pay(s,'payment',300);original=live(s,'actual_occurrence')
        refund(s,'back',300,related=False)
        self.assertEqual(live(s,'actual_occurrence','actual/payment'),original)
        self.assertEqual(live(s,'planned_expense').status,S.COMPLETED)
        self.assertEqual(expense_summary(s.snapshot,live(s,'planned_expense'))['net_actual']['value_minor'],0)

    def test_d2_capsule_wrong_world_and_tampering_rejected(self):
        from tests.test_if_lines import setup_d2, make
        service,target,b,_=setup_d2();line=make(service,b)
        proof=capture_if_evidence(service,line['id'],(Ref('confirmed',1),))
        with self.assertRaises(ValueError):replace(proof,evidence_sha256='0'*64)
        with self.assertRaises(ValueError):replace(proof,confirmation_refs=())
        s=fixture()
        self.rejected(s,lambda:pay(s,'book',29,lineage=OccurrenceLineage(if_evidence=proof)),'origin')

    def test_if_evidence_alone_cannot_authorize_settlement(self):
        from tests.test_if_lines import setup_d2, make
        from finvis.world_commands import FinancialWorldCommand
        service,s,b,_=setup_d2();line=make(service,b)
        proof=capture_if_evidence(service,line['id'],(Ref('confirmed',1),))
        pos=live(s,'asset_position')
        op=event(s,'book',29,source=pos.ref.id,related=False,lineage=OccurrenceLineage(if_evidence=proof))
        change=propose_settlement(s.snapshot,s.session_generation,op,Ref('change/book',1))
        preview=s.preview(s.snapshot.world.owner_id,change,Ref('preview/book',1))
        ungranted=FinancialWorldCommand('ungranted',s.snapshot.world.owner_id,change,preview.ref,preview.digest,
            Ref('if-is-not-a-grant',1),change.created_at)
        self.rejected(s,lambda:s.execute(ungranted),'Registered')


if __name__=='__main__': unittest.main()

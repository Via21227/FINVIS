"""I1 deterministic integration tests; no local/cloud model or live UI calls."""
from dataclasses import FrozenInstanceError, replace
import json
import unittest
from unittest.mock import patch

from finvis.application import DemoApplication
from finvis.domain import Currency, DecisionStatus, Money, PlanStatus, Ref
from finvis.store import Rejected, StaleVersion
from finvis.world_application import (ApplicationReads, ContextKind,
    UserWorldAuthority, WorldContext, LegacyDemoRead)
from finvis.world_projection import (CoverageStatus, ProjectionError, project_world,
    projection_data, projection_json)
from finvis.world_commands import PutRecord, record_ref
from finvis.world_domain import (Account, AccountKind, AssetPosition, AssetPositionKind,
    AccessAssessment, AccessStatus, Earmark, EarmarkRole, WorldState, WorldAssetBundle)
from finvis.world_planning import (WorldPlan, WorldPlanningBundle, PlanEvaluationStatus,
    FutureInflow, FutureInflowStatus, PlannedExpense, PlannedExpenseStatus, WorldDecision,
    DecisionContext, ActualOccurrence, OccurrenceKind, SettlementStatus)
from finvis.world_values import AmountEstimate, AmountPrecision, ObservationRole, TemporalSpec, TypedRef
from finvis.world_store import WorldStore, initial_world
from finvis.world_serialization import dumps_world_mutation
from tests.test_agent_understanding import SequenceProvider, realistic_output
from tests.test_world_assets import observation
from tests.test_world_mutation import store, future_add, prepared, proposal
from tests.test_world_planning import canonical_bundle
from tests.test_world_settlement import operation, settle, correction
from tests.test_world_replay import cutover, generic_proposal


def synthetic(currencies=(Currency.SGD,), factor=1, balance=None, earmarks=True):
    """Independent household/workshop world, no canonical names or amounts."""
    owner, world = 'sample-workshop-owner', 'sample-workshop-world'
    accounts, positions, marks, futures, expenses, decisions, occurrences = [], [], [], [], [], [], []
    exact = lambda n, c: AmountEstimate.exact_reported(Money(n * 100 * factor, c))
    approx = lambda n, c: AmountEstimate.approximate(Money(n * 100 * factor, c))
    state_ref = Ref('workshop-state', 1)
    for currency in currencies:
        prefix = currency.value.lower()
        for index, amount in enumerate((800, 260)):
            identity = prefix + str(index)
            account = Account(Ref('account/' + identity, 1), owner, world, 'Container ' + identity,
                None, AccountKind.CASH_CONTAINER, observation(identity))
            accounts.append(account)
            position = AssetPosition(Ref('position/' + identity, 1), owner, world, record_ref(account),
                currency, AssetPositionKind.CASH, 'main', balance or exact(amount, currency),
                observation(identity), AccessAssessment(AccessStatus.UNKNOWN, 'Access not reviewed', observation(identity)))
            positions.append(position)
            if earmarks and index == 0:
                for j, reserve in enumerate((100, 50)):
                    marks.append(Earmark(Ref('reserve/' + identity + str(j), 1), owner, world,
                        record_ref(position), exact(reserve, currency), 'Workshop reserve ' + str(j),
                        EarmarkRole.OTHER, False, observation(identity)))
            timing = TemporalSpec.unknown('Household timing not reported')
            futures.append(FutureInflow(Ref('future/' + identity, 1), owner, world,
                approx(120, currency) if index == 0 else AmountEstimate.range(Money(3000*factor,currency), Money(7000*factor,currency)),
                'Freelance receipt ' + identity, timing, None, FutureInflowStatus.EXPECTED,
                observation(identity, ObservationRole.FUTURE)))
            expenses.append(PlannedExpense(Ref('expense/' + identity, 1), owner, world,
                approx(45, currency) if index == 0 else exact(25, currency), 'Workshop maintenance ' + identity,
                timing, (), None, PlannedExpenseStatus.PLANNED, observation(identity, ObservationRole.PLANNED)))
            decisions.append(WorldDecision(Ref('decision/' + identity, 1), owner, world,
                'Take this course?', 'Skills course ' + identity, TypedRef('world_state', state_ref),
                DecisionStatus.EXPLORING, None, exact(125, currency) if index == 0 else None,
                timing, (), observation(identity, ObservationRole.CONTEMPLATED)))
        occurrences.append(ActualOccurrence(Ref('actual/' + prefix, 1), owner, world, OccurrenceKind.EXPENSE,
            exact(18, currency), timing, (), SettlementStatus.UNALLOCATED,
            observation(prefix, ObservationRole.ACTUAL), record_ref(positions[-1])))
    state = WorldState(state_ref, owner, world, tuple(map(record_ref, accounts)), tuple(map(record_ref, positions)),
        tuple(map(record_ref, marks)), observation('household-state'), 'Reviewed household subset, not full asset coverage')
    plan = WorldPlan(Ref('workshop-plan',1),owner,world,record_ref(state),None,
        tuple(map(record_ref,decisions)),tuple(map(record_ref,expenses)),(),(),PlanStatus.DRAFT,
        PlanEvaluationStatus.PENDING,observation('workshop-plan',ObservationRole.PLANNED))
    context = DecisionContext(Ref('context/course',1),owner,world,(record_ref(decisions[0]),),
        'Evening learning fits the reported schedule',timing,observation('schedule',ObservationRole.CONTEMPLATED))
    bundle = WorldPlanningBundle(WorldAssetBundle(state,tuple(accounts),tuple(positions),tuple(marks)),plan,
        tuple(futures),tuple(expenses),tuple(occurrences),tuple(decisions),(context,))
    return WorldStore(initial_world(bundle,Ref(world,1)),'workshop-session')


def authority(target):
    root = target.snapshot.world
    return UserWorldAuthority(WorldContext(ContextKind.USER,'user:work',root.owner_id,root.world_id),target)


def amounts(summary):
    return tuple(getattr(summary, name).value.amount.minor_units for name in ('current_assets','earmarked','unallocated'))


class ProjectionTests(unittest.TestCase):
    def test_canonical_semantic_buckets_and_home_readiness(self):
        p = project_world(store(canonical_bundle()).snapshot)
        self.assertEqual(amounts(p.currencies[0]), (3400000,2500000,900000))
        self.assertEqual(p.expected_inflows_by_currency[0].amount.value.amount.minor_units,2300000)
        self.assertIs(p.expected_inflows_by_currency[0].amount.value.precision,AmountPrecision.APPROXIMATE)
        self.assertEqual(p.open_expenses_by_currency[0].amount.value.amount.minor_units,1800000)
        self.assertEqual(p.decisions[0].amount.amount.minor_units,600000)
        self.assertIs(p.essential_monthly_outflow.status,CoverageStatus.UNKNOWN)
        self.assertIs(p.emergency_liquidity.status,CoverageStatus.ENGINE_REQUIRED)
        self.assertIs(p.runway.status,CoverageStatus.INSUFFICIENT_INPUT)
        self.assertIs(p.goal_details.status,CoverageStatus.NOT_SUPPORTED)
        self.assertIs(p.if_lines.status,CoverageStatus.DEMO_ONLY)

    def test_independent_cny_and_sgd_worlds(self):
        for currency in Currency:
            with self.subTest(currency=currency):
                p=project_world(synthetic((currency,)).snapshot)
                self.assertEqual(len(p.currencies),1)
                self.assertIs(p.currencies[0].currency,currency)
                self.assertEqual(amounts(p.currencies[0]),(106000,15000,91000))
                self.assertEqual(len(p.accounts),2)
                self.assertEqual(len(p.earmarks),2)

    def test_multicurrency_has_no_scalar_total_or_fx(self):
        p=project_world(synthetic((Currency.SGD,Currency.CNY)).snapshot)
        self.assertEqual([x.currency for x in p.currencies],[Currency.CNY,Currency.SGD])
        self.assertEqual([amounts(x) for x in p.currencies],[(106000,15000,91000)]*2)
        self.assertEqual(len(p.expected_inflows_by_currency),2)
        self.assertEqual(len(p.open_expenses_by_currency),2)
        self.assertFalse(hasattr(p,'total_assets'))

    def test_future_plan_decisions_actuals_never_change_observed_assets(self):
        target=synthetic(); before=dumps_world_mutation(target.snapshot)
        p=project_world(target.snapshot)
        self.assertEqual(len(p.future_inflows),2)
        self.assertEqual(len(p.planned_expenses),2)
        self.assertEqual(len(p.decisions),2)
        self.assertEqual(len(p.occurrences),1)
        self.assertEqual(p.contexts[0].decision_refs,(p.decisions[0].ref,))
        self.assertEqual(amounts(p.currencies[0])[0],106000)
        self.assertEqual(p.occurrences[0].settlement_status,'unallocated')
        self.assertIn('unsettled_occurrence',{x.code for x in p.pending})
        self.assertEqual(dumps_world_mutation(target.snapshot),before)

    def test_no_earmarks_is_exact_zero_allocation_not_zero_assets(self):
        p=project_world(synthetic(earmarks=False).snapshot)
        self.assertEqual(p.earmarks,())
        self.assertEqual(amounts(p.currencies[0]),(106000,0,106000))

    def test_approximate_current_balances_preserve_precision(self):
        value=AmountEstimate.approximate(Money(123400,Currency.SGD))
        p=project_world(synthetic(balance=value,earmarks=False).snapshot)
        for position in p.positions:self.assertEqual(position.balance,value)
        self.assertIs(p.currencies[0].current_assets.value.precision,AmountPrecision.APPROXIMATE)
        self.assertEqual(p.currencies[0].current_assets.value.amount.minor_units,246800)

    def test_range_totals_and_temporal_provenance_are_preserved(self):
        value=AmountEstimate.range(Money(120000,Currency.SGD),Money(160000,Currency.SGD))
        p=project_world(synthetic(balance=value,earmarks=False).snapshot)
        derived=p.currencies[0].current_assets.value
        self.assertIs(derived.precision,AmountPrecision.RANGE)
        self.assertEqual((derived.lower_bound.minor_units,derived.upper_bound.minor_units),(240000,320000))
        self.assertIsNone(derived.amount)
        self.assertIs(p.future_inflows[0].observation.role,ObservationRole.FUTURE)
        self.assertEqual(p.positions[0].observation.evidence_refs,observation('sgd0').evidence_refs)
        self.assertEqual(p.positions[0].access_status,'unknown')

    def test_unknown_amount_does_not_become_zero(self):
        value=AmountEstimate.unknown('Cash observation missing',Currency.SGD)
        p=project_world(synthetic(balance=value,earmarks=False).snapshot)
        self.assertIs(p.assets_coverage.status,CoverageStatus.UNKNOWN)
        for metric in (p.currencies[0].current_assets,p.currencies[0].unallocated):
            self.assertIs(metric.value.precision,AmountPrecision.UNKNOWN)
            self.assertIsNone(metric.value.amount)
            self.assertIs(metric.coverage.status,CoverageStatus.UNKNOWN)
        self.assertEqual(p.positions[0].balance.missing_reason,'Cash observation missing')

    def test_exact_zero_report_is_distinct_from_empty_world(self):
        p=project_world(synthetic(balance=AmountEstimate.exact_reported(Money(0,Currency.SGD)),earmarks=False).snapshot)
        self.assertEqual(amounts(p.currencies[0]),(0,0,0))
        self.assertIs(p.assets_coverage.status,CoverageStatus.AVAILABLE)
        self.assertIn('reported_scope_only',p.assets_coverage.reasons)

    def test_ordering_and_repeated_serialization_are_deterministic(self):
        s=synthetic((Currency.SGD,Currency.CNY)).snapshot
        shuffled=replace(s,records=tuple(reversed(s.records)))
        self.assertEqual(projection_json(project_world(s)),projection_json(project_world(shuffled)))
        self.assertEqual(project_world(s),project_world(s))

    def test_label_renaming_changes_labels_only(self):
        s=synthetic().snapshot
        changed=replace(s,records=tuple(replace(r,label='Renamed container') if type(r) is Account else r for r in s.records))
        p,q=project_world(s),project_world(changed)
        self.assertEqual(p.currencies,q.currencies)
        self.assertEqual(replace(p,accounts=q.accounts),q)
        self.assertNotEqual(p.accounts,q.accounts)

    def test_scaling_preserves_structure_precision_and_currency(self):
        p,q=project_world(synthetic().snapshot),project_world(synthetic(factor=3).snapshot)
        self.assertEqual(amounts(q.currencies[0]),tuple(v*3 for v in amounts(p.currencies[0])))
        self.assertEqual([d.ref for d in p.decisions],[d.ref for d in q.decisions])
        self.assertEqual(q.open_expenses_by_currency[0].amount.value.amount.minor_units,
                         p.open_expenses_by_currency[0].amount.value.amount.minor_units*3)
        self.assertEqual(p.open_expenses_by_currency[0].amount.value.precision,q.open_expenses_by_currency[0].amount.value.precision)

    def test_added_currency_preserves_existing_currency_and_decisions(self):
        p=project_world(synthetic().snapshot);q=project_world(synthetic((Currency.CNY,Currency.SGD)).snapshot)
        self.assertEqual(p.currencies[0],q.currencies[1])
        self.assertEqual(p.decisions,tuple(d for d in q.decisions if 'sgd' in d.ref.ref.id))

    def test_independent_decision_does_not_change_balances(self):
        target=store(canonical_bundle());p=project_world(target.snapshot)
        old=next(r for r in target.snapshot.records if type(r) is WorldDecision)
        extra=replace(old,ref=Ref('decision/independent-course',1),subject='Evening course')
        _,_,cmd=prepared(target,proposal(target,(PutRecord('add-course',extra),)))
        target.execute(cmd);q=project_world(target.snapshot)
        self.assertEqual(p.currencies,q.currencies)
        self.assertEqual(len(q.decisions),2)
        self.assertEqual(q.revision,p.revision+1)

    def test_settled_and_corrected_occurrence_uses_current_balance_once(self):
        target=store(canonical_bundle())
        _,_,cmd=settle(target,operation(target));target.execute(cmd)
        p=project_world(target.snapshot)
        self.assertEqual(amounts(p.currencies[0]),(1640000,740000,900000))
        self.assertEqual(p.open_expenses_by_currency,())
        self.assertEqual(p.planned_expenses[0].status,'completed')
        self.assertEqual(p.occurrences[0].settlement_status,'applied_to_balance')
        self.assertIsNotNone(p.occurrences[0].balance_effect_ref)
        _,_,cmd=settle(target,correction(target),name='correct');target.execute(cmd)
        q=project_world(target.snapshot)
        self.assertEqual(q.currencies[0].current_assets.value.amount.minor_units,1670000)
        self.assertEqual(len(q.occurrences),1)
        self.assertEqual(q.occurrences[0].ref.ref.version,2)

    def test_retained_historical_funding_is_pending_not_projection_failure(self):
        target=store(canonical_bundle())
        old=next(r for r in target.snapshot.records if type(r) is Earmark and r.role is EarmarkRole.PURCHASE)
        new=replace(old,ref=Ref(old.ref.id,2),purpose='Revised purchase designation',
                    observation=replace(old.observation,corrects_ref=old.ref,correction_reason='Reviewed designation'))
        _,_,cmd=prepared(target,proposal(target,(PutRecord('designation',new,record_ref(old)),)))
        # Retained expenses still pin the original funding observation.
        target.execute(cmd);p=project_world(target.snapshot)
        self.assertIn('historical_dependency',{x.code for x in p.pending})
        self.assertEqual(len(p.planned_expenses),1)

    def test_cancelled_expectation_excluded_from_expected_totals(self):
        target=store(canonical_bundle());old=next(r for r in target.snapshot.records if type(r) is FutureInflow)
        new=replace(old,ref=Ref(old.ref.id,2),status=FutureInflowStatus.CANCELLED,
                    observation=replace(old.observation,corrects_ref=old.ref,correction_reason='Reviewed cancellation'))
        _,_,cmd=prepared(target,proposal(target,(PutRecord('cancel',new,record_ref(old)),)))
        target.execute(cmd);p=project_world(target.snapshot)
        self.assertEqual(len(p.future_inflows),2)
        self.assertEqual(sum(f.status=='expected' for f in p.future_inflows),1)
        self.assertEqual(p.currencies[0].current_assets.value.amount.minor_units,3400000)

    def test_legacy_migration_keeps_goal_plan_refs_without_creating_assets(self):
        target=cutover(generic_proposal())[1]
        p=project_world(target.snapshot)
        self.assertEqual(p.currencies,())
        self.assertIs(p.assets_coverage.status,CoverageStatus.UNKNOWN)
        self.assertEqual(len(p.plan.goal_allocations),1)
        self.assertEqual(p.plan.previous_plan_ref.record_kind,'legacy_world_plan')
        self.assertIs(p.essential_monthly_outflow.status,CoverageStatus.UNKNOWN)
        self.assertIs(p.runway.status,CoverageStatus.INSUFFICIENT_INPUT)

    def test_immutable_projection_and_detached_transport(self):
        target=synthetic();p=project_world(target.snapshot)
        with self.assertRaises(FrozenInstanceError):p.revision=99
        with self.assertRaises(FrozenInstanceError):p.positions[0].balance.amount=Money(1,Currency.SGD)
        data=projection_data(p);data['positions'][0]['balance']['amount']['minor_units']=1
        self.assertNotEqual(projection_data(p),data)
        self.assertEqual(json.loads(projection_json(p)),projection_data(p))

    def test_malformed_dangling_cross_currency_and_wrong_type_fail_closed(self):
        s=synthetic().snapshot
        future=next(r for r in s.records if type(r) is FutureInflow)
        position=next(r for r in s.records if type(r) is AssetPosition)
        bad_future=replace(future,expected_amount=AmountEstimate.exact_reported(Money(100,Currency.CNY)),destination_ref=record_ref(position))
        cases=[None,replace(s,revision=7),replace(s,records=tuple(r for r in s.records if r!=position)),
               replace(s,records=tuple(bad_future if r==future else r for r in s.records))]
        for value in cases:
            with self.subTest(value=type(value)):
                with self.assertRaises(ProjectionError):project_world(value)


class AuthorityTests(unittest.TestCase):
    def setUp(self):
        self.target=synthetic();self.user=authority(self.target)
        self.demo=DemoApplication(agent_provider=SequenceProvider([realistic_output()]))
        self.demo_context=WorldContext(ContextKind.DEMO,'demo:canonical','synthetic-user','legacy-demo')
        self.reads=ApplicationReads(self.demo,self.demo_context,self.user)

    def test_contexts_are_explicit_and_do_not_accept_ambiguous_ids(self):
        self.assertEqual(self.reads.contexts,(self.demo_context,self.user.context))
        self.assertIsInstance(self.reads.read(self.demo_context),LegacyDemoRead)
        self.assertIs(self.reads.read(self.user.context).snapshot,self.target.snapshot)
        with self.assertRaises(Rejected):self.reads.read(replace(self.user.context,owner_id='other'))
        with self.assertRaises(Rejected):self.reads.read(None)
        with self.assertRaises(ValueError):WorldContext(ContextKind.USER,'demo:canonical','person','world')
        with self.assertRaises(ValueError):ApplicationReads(self.demo,replace(self.demo_context,world_id=self.user.context.world_id),self.user)

    def test_no_user_world_is_not_empty_or_demo_world(self):
        read=UserWorldAuthority(self.user.context).read()
        self.assertFalse(read.initialized)
        self.assertIsNone(read.snapshot);self.assertIsNone(read.projection);self.assertIsNone(read.version)
        self.assertEqual(read.reasons,('user_world_not_initialized',))

    def test_empty_initialization_has_no_synthetic_money_or_goals(self):
        user=UserWorldAuthority.empty(self.user.context,'new-session',observation('explicit-initialization'))
        read=user.read();self.assertTrue(read.initialized)
        self.assertEqual(read.snapshot.world.members,())
        self.assertEqual(read.projection.currencies,())
        self.assertEqual(read.projection.decisions,())
        self.assertEqual(read.projection.plan.goal_allocations,())
        self.assertIs(read.projection.assets_coverage.status,CoverageStatus.UNKNOWN)
        self.assertEqual(read.version.revision,0)
        self.assertEqual(read.version.world_ref.version,1)

    def test_demo_reset_does_not_change_user_or_accept_old_demo_stamp(self):
        before=self.user.read();old=self.reads.read(self.demo_context)
        view=self.demo.view();self.demo.command('reset',{k:view[k] for k in ('generation','revision')})
        self.assertEqual(self.user.read(),before)
        with self.assertRaises(StaleVersion):self.reads.read(self.demo_context,old.version)
        self.assertEqual(self.demo.view()['display']['liquidity'],'SGD 12,000.00')

    def test_demo_full_story_and_user_read_are_independent(self):
        before=self.user.read()
        for action,version in [('prepare',1),('authorize',2),('employment',3),('reset',1)]:
            view=self.demo.view();after=self.demo.command(action,{k:view[k] for k in ('generation','revision')})
            self.assertEqual(after['plan']['ref']['version'],version)
            self.assertEqual(self.user.read(),before)

    def test_authority_reads_latest_store_without_second_financial_cache(self):
        target=store(canonical_bundle());user=authority(target);before=user.read();demo_before=self.demo.store.snapshot
        _,_,cmd=prepared(target,future_add(target));target.execute(cmd)
        after=user.read()
        self.assertIs(after.snapshot,target.snapshot)
        self.assertEqual(after.version.revision,before.version.revision+1)
        self.assertNotEqual(after.projection.future_inflows,before.projection.future_inflows)
        self.assertIs(self.demo.store.snapshot,demo_before)
        with self.assertRaises(StaleVersion):user.read(before.version)
        self.assertEqual(user.read(after.version),after)

    def test_context_generation_and_refs_are_part_of_read_precondition(self):
        read=self.user.read()
        for version in (replace(read.version,context=replace(self.user.context,context_id='user:other')),replace(read.version,session_generation='old'),
                        replace(read.version,world_ref=Ref('wrong',1)),replace(read.version,state_ref=Ref('wrong',1))):
            with self.subTest(version=version):
                with self.assertRaises(StaleVersion):self.user.read(version)
        with self.assertRaises(ValueError):self.user.read({'revision':0})
        for fields in ({'revision':False},{'revision':-1},{'session_generation':''},{'world_ref':None}):
            with self.subTest(invalid=fields):
                with self.assertRaises(ValueError):replace(read.version,**fields)
        with self.assertRaises(StaleVersion):self.user.read(self.reads.read(self.demo_context).version)

    def test_wrong_owner_world_and_legacy_source_are_rejected(self):
        for context in (replace(self.user.context,owner_id='other'),replace(self.user.context,world_id='other')):
            with self.assertRaises(ProjectionError):UserWorldAuthority(context,self.target).read()
        with self.assertRaises(ProjectionError):UserWorldAuthority(self.user.context,self.demo.store).read()
        with self.assertRaises(ValueError):UserWorldAuthority(self.demo_context,self.target)

    def test_single_snapshot_read_is_persistence_agnostic(self):
        snapshot=self.target.snapshot
        class Source:
            session_generation='durable-adapter-session'
            reads=0
            @property
            def snapshot(self):
                self.reads+=1
                return snapshot
        source=Source();read=UserWorldAuthority(self.user.context,source).read()
        self.assertEqual(source.reads,1)
        self.assertIs(read.snapshot,snapshot)
        self.assertEqual(read.version.session_generation,'durable-adapter-session')

    def test_source_failure_and_projection_failure_never_fall_back(self):
        class Source:
            session_generation='failed-source'
            @property
            def snapshot(self):raise OSError('private storage detail')
        with self.assertRaisesRegex(ProjectionError,'user_read_failed'):
            UserWorldAuthority(self.user.context,Source()).read()
        before=self.demo.store.snapshot
        with patch('finvis.world_application.project_world',side_effect=RuntimeError('render failure')):
            with self.assertRaisesRegex(ProjectionError,'user_read_failed'):self.reads.read(self.user.context)
        self.assertIs(self.demo.store.snapshot,before)

    def test_read_boundary_does_not_interpret_confirm_or_commit(self):
        before=self.demo.store.snapshot
        with patch.object(self.demo.agent,'interpret',side_effect=AssertionError('Unexpected model path')):
            self.reads.read(self.user.context);self.reads.read(self.demo_context)
        self.assertIs(self.demo.store.snapshot,before)
        for name in ('execute','authorize','preview','interpret','reset'):
            self.assertFalse(hasattr(self.reads,name));self.assertFalse(hasattr(self.user,name))

    def test_closed_candidate_stays_review_only_with_read_boundary_present(self):
        view=self.demo.view();expected={k:view[k] for k in ('generation','revision')}
        candidate=self.demo.interpret_agent_input('Synthetic review input',expected)
        before_user=self.user.read();before_demo=self.demo.store.snapshot
        result=self.demo.resolve_agent_candidate('confirm',candidate['id'],expected)
        self.assertEqual(result['status'],'confirmed_no_commit')
        self.assertIs(self.demo.store.snapshot,before_demo)
        self.assertEqual(self.user.read(),before_user)


if __name__=='__main__':
    unittest.main()

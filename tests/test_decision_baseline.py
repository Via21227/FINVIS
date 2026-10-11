"""D1 generic deterministic evidence and authority regressions. No research I/O."""
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import unittest

from finvis.decision_baseline import BaselineService, BaselineContext, UnavailableResearchProvider
from finvis.domain import Currency, Ref
from finvis.world_commands import MonthlyEssentialOutflow, PutRecord, record_ref
from finvis.world_values import AmountEstimate, AmountPrecision, ObservationRole
from finvis.world_serialization import dumps_world_mutation, loads_world_mutation
from finvis.engine_inputs import read_engine_inputs, InputKind, Capability, GateStatus
from finvis.persistent_world import PersistentWorldStore
from finvis.world_application import UserWorldAuthority
from tests.test_world_assets import observation, exact
from tests.test_world_mutation import store, prepared, future_add, proposal
from tests.test_world_planning import canonical_bundle
from tests.test_i4_commit import candidate_workflow
from tests.test_interaction_simplification import flow, commit
from tests.test_generalization_gate import fact
from tests.test_persistent_world import location, CONTEXT, changes, prepare

NOW = datetime.now(timezone.utc)


def amount(value='850', precision='approximate', currency='CNY', upper=None):
    return dict(currency=currency, value=value if precision not in ('range','unknown') else None,
                precision=precision, range_min=value if precision=='range' else None,
                range_max=upper if precision=='range' else None)


def context(category='electronics'):
    return dict(category=category, origin=None, destination=None, location=None, country_code=None,
                start_date=None, end_date=None, date_note=None, duration_days=None, nights=None,
                party_size=None, assumptions=[])


def component(value=None, identity='item', category='other'):
    return dict(id=identity, label='User estimate', category=category, basis='user_assumption',
                amount=value or amount(), assumptions=['User supplied planning assumption'], world_ref=None, external=None)


def setup():
    target = store(canonical_bundle())
    workflow, _, _ = candidate_workflow(target)
    decision = next(r for r in target.snapshot.world.members if r.record_kind=='world_decision')
    request = dict(decision_ref={'record_kind':decision.record_kind,'ref':{'id':decision.ref.id,'version':decision.ref.version}},
                   expected_version=0, context=context(), components=[component()], dependency_refs=[], scope_complete=True, buffer=None)
    return workflow.baselines, target, request


def external(request, value=None):
    c = component(category='other')
    c.update(basis='external', amount=None, assumptions=[])
    c['external'] = dict(evidence_ref='quote/1', category='other', source='User supplied vendor quote ref Q42', provider='manual_quote',
        observed_amount=value or amount('2843','exact'), observed_at=(NOW-timedelta(minutes=2)).isoformat(),
        retrieved_at=(NOW-timedelta(minutes=1)).isoformat(), valid_until=(NOW+timedelta(hours=1)).isoformat(),
        freshness='current', context=deepcopy(request['context']), assumptions=['Availability can change'])
    return c


class BaselineTests(unittest.TestCase):
    def test_generic_categories_no_authority_or_recommendation(self):
        from tests.test_world_application import synthetic, authority
        from finvis.world_planning import WorldDecision
        subjects={'electronics':'A refurbished lens', 'moving':'Furnish a new flat',
                  'training':'A weekend ceramics course', 'travel':'A coastal rail holiday'}
        for category in ('electronics','moving','training','travel'):
            with self.subTest(category=category):
                _, _, request=setup()
                target=synthetic((Currency.SGD,Currency.CNY))
                original=next(r for r in target.snapshot.world.members if r.record_kind=='world_decision')
                record=target.snapshot.get(original)
                revised=replace(record,ref=Ref(record.ref.id,2),subject=subjects[category],
                    observation=replace(record.observation,corrects_ref=record.ref,correction_reason='Clarify contemplated subject'))
                target.execute(prepare(target,changes(target,(PutRecord('decision',revised,original),),'generic'),'generic'))
                service=BaselineService(authority(target))
                request['decision_ref']={'record_kind':'world_decision','ref':{'id':revised.ref.id,'version':2}}
                request['context']=context(category)
                if category=='travel':request['context'].update(destination='Hanoi',date_note='After my course; exact dates unknown',duration_days=3,party_size=2)
                before=dumps_world_mutation(target.snapshot)
                out=service.establish(request,NOW)
                self.assertEqual(out['status'],'estimate_available')
                self.assertEqual(out['recommended_budget']['status'],'policy_required')
                self.assertIsNone(out['recommended_budget']['amount'])
                self.assertFalse(out['financial_authority'])
                self.assertEqual(dumps_world_mutation(target.snapshot),before)

    def test_approximate_range_unknown_and_mixed_currency(self):
        for components, precision in (([component(amount('41'))],'approximate'),
                ([component(amount('35','range',upper='70')),component(amount('12','range',upper='18'),'second')],'range'),
                ([component(amount('35','range',upper='70')),component(amount('12'),'second')],'unknown'),
                ([component(amount(precision='unknown'))],'unknown')):
            service,_,request=setup();request['components']=components
            out=service.establish(request,NOW)
            self.assertEqual(out['estimated_cost_by_currency'][0]['precision'],precision)
        service,_,request=setup();request['components'] += [component(amount('90',currency='SGD'),'other-currency')]
        self.assertEqual(len(service.establish(request,NOW)['estimated_cost_by_currency']),2)

    def test_exact_quote_is_preserved_but_future_cost_approximate(self):
        service,_,request=setup();request['components']=[external(request)]
        out=service.establish(request,NOW)
        self.assertEqual(out['components'][0]['observed_amount']['precision'],'exact_reported')
        self.assertEqual(out['components'][0]['observed_amount']['amount']['minor_units'],284300)
        self.assertEqual(out['estimated_cost_by_currency'][0]['precision'],'approximate')
        self.assertEqual(out['research']['status'],'research_unavailable')

    def test_no_components_no_zero_missing_travel_and_partial_scope(self):
        service,_,request=setup();request['components']=[]
        out=service.establish(request,NOW)
        self.assertEqual(out['status'],'insufficient_evidence');self.assertEqual(out['estimated_cost_by_currency'],[])
        service,_,request=setup();request['context']=context('travel');request['components'][0]['category']='flight'
        out=service.establish(request,NOW)
        self.assertEqual(set(out['missing_context']),{'origin','destination','duration_days','party_size','dates'})
        service,_,request=setup();request['scope_complete']=False
        self.assertEqual(service.establish(request,NOW)['status'],'partially_available')

    def test_freshness_expiry_unknown_invalid_dates(self):
        service,_,request=setup();request['components']=[external(request)]
        service.establish(request,NOW)
        baseline=next(iter(service.versions.values()))[-1]
        self.assertEqual(service.view(baseline,NOW+timedelta(hours=2))['status'],'stale')
        service,_,request=setup();request['components']=[external(request)]
        request['components'][0]['external']['valid_until']=None
        self.assertEqual(service.establish(request,NOW)['status'],'partially_available')
        service,_,request=setup();request['components']=[external(request)]
        request['components'][0]['external']['retrieved_at']=(NOW+timedelta(days=1)).isoformat()
        with self.assertRaises(ValueError):service.establish(request,NOW)

    def test_versions_component_and_context_invalidation(self):
        service,_,request=setup();first=service.establish(request,NOW);old=next(iter(service.versions.values()))[0]
        request['expected_version']=1;request['components'][0]['amount']=amount('180')
        second=service.establish(request,NOW)
        self.assertEqual(first['ref']['id'],second['ref']['id']);self.assertEqual(second['ref']['version'],2)
        self.assertIn('baseline_superseded',service.view(old,NOW)['stale_reasons'])
        with self.assertRaises(ValueError):service.establish(request,NOW)

    def test_selective_world_invalidation_and_world_evidence(self):
        service,target,request=setup()
        decision_ref=next(r for r in target.snapshot.world.members if r.record_kind=='world_decision')
        request['components'][0].update(basis='world',amount=None,assumptions=[],world_ref=request['decision_ref'])
        service.establish(request,NOW);old=next(iter(service.versions.values()))[0]
        target.execute(prepared(target,future_add(target),'unrelated')[2])
        self.assertEqual(service.view(old,NOW)['status'],'estimate_available')
        d=target.snapshot.get(decision_ref)
        revised=replace(d,ref=Ref(d.ref.id,d.ref.version+1),amount=exact(910),
                        observation=replace(d.observation,corrects_ref=d.ref,correction_reason='Reported price correction'))
        target.execute(prepared(target,proposal(target,(PutRecord('correct',revised,decision_ref),),'correct'),'correct')[2])
        self.assertIn('world_inputs_changed',service.view(old,NOW)['stale_reasons'])

    def test_buffer_separate_no_rounding_no_recommendation(self):
        service,_,request=setup();request['buffer']={'amount':amount('137','exact'),'reason':'My optional allowance'}
        out=service.establish(request,NOW)
        self.assertEqual(out['estimated_cost_by_currency'][0]['amount']['minor_units'],85000)
        self.assertEqual(out['budget_buffer']['amount']['amount']['minor_units'],13700)
        self.assertIsNone(out['recommended_budget']['amount'])

    def test_changed_evidence_context_duplicate_and_extra_fields_rejected(self):
        service,_,request=setup();request['components']=[external(request)];service.establish(request,NOW)
        request['expected_version']=1;request['components'][0]['external']['observed_amount']=amount('999')
        with self.assertRaises(ValueError):service.establish(request,NOW)
        for mutate in (lambda r:r['context'].update(category='madeup'),lambda r:r.update(fx=True),
                       lambda r:r['components'].append(deepcopy(r['components'][0])),
                       lambda r:r['components'][0].update(assumptions=[])):
            service,_,r=setup();mutate(r)
            with self.assertRaises(ValueError):service.establish(r,NOW)

    def test_unavailable_research_no_llm_fallback(self):
        result=UnavailableResearchProvider().research(BaselineContext.parse(context()),'phone')
        self.assertEqual(result.status,'research_unavailable');self.assertEqual(result.evidence,())

    def test_changed_context_membership_generation_and_evidence_binding(self):
        service,target,request=setup();service.establish(request,NOW)
        old=next(iter(service.versions.values()))[-1]
        from finvis.world_planning import DecisionContext
        decision=old.decision_ref
        new=DecisionContext(Ref('training-history',1),target.snapshot.world.owner_id,target.snapshot.world.world_id,
            (decision,), 'Additional reported context', target.snapshot.get(decision).timing,
            observation('new-context',ObservationRole.CONTEMPLATED))
        target.execute(prepared(target,proposal(target,(PutRecord('add',new),),'new-context'),'new-context')[2])
        self.assertIn('decision_context_changed',service.view(old,NOW)['stale_reasons'])
        target._generation='fresh-session'
        self.assertIn('authority_changed',service.view(old,NOW)['stale_reasons'])
        service,_,request=setup();request['components']=[external(request)]
        request['components'][0]['external']['context']['location']='Different quote location'
        with self.assertRaises(ValueError):service.establish(request,NOW)

    def test_world_dependency_ownership_and_exact_ref(self):
        service,target,request=setup()
        request['dependency_refs']=[{'record_kind':'asset_position','ref':{'id':'foreign-world/account','version':1}}]
        with self.assertRaises(ValueError):service.establish(request,NOW)
        request['dependency_refs']=[];request['decision_ref']['ref']['version']+=1
        with self.assertRaises(ValueError):service.establish(request,NOW)

    def test_world_stale_and_unknown_metadata_are_not_current(self):
        from finvis.world_values import FreshnessAssessment, FreshnessStatus
        for status,expected in ((FreshnessStatus.STALE,'stale'),(FreshnessStatus.UNKNOWN,'partially_available')):
            service,target,request=setup()
            ref=next(r for r in target.snapshot.world.members if r.record_kind=='world_decision')
            old=target.snapshot.get(ref)
            new=replace(old,ref=Ref(old.ref.id,2),observation=replace(old.observation,corrects_ref=old.ref,
                correction_reason='Report freshness',freshness=FreshnessAssessment(status,'User report freshness unknown',NOW)))
            target.execute(prepared(target,proposal(target,(PutRecord('correct',new,ref),),'freshness'),'freshness')[2])
            request['decision_ref']['ref']['version']=2
            self.assertEqual(service.establish(request,NOW)['status'],expected)

    def test_quote_revisions_keep_old_evidence_and_new_version(self):
        service,_,request=setup();request['components']=[external(request)];first=service.establish(request,NOW)
        request['expected_version']=1;request['components'][0]['external']['evidence_ref']='quote/2'
        request['components'][0]['external']['observed_amount']=amount('2710','exact')
        second=service.establish(request,NOW)
        self.assertEqual(first['components'][0]['external']['observed_amount']['amount']['minor_units'],284300)
        self.assertEqual(second['components'][0]['external']['observed_amount']['amount']['minor_units'],271000)


class MonthlyObservationTests(unittest.TestCase):
    def test_existing_report_requires_reviewed_correction_and_history_edit(self):
        from tests.test_history_actions import request as history_request, amount as history_amount, apply
        from tests.test_agent_understanding import SequenceProvider
        from tests.test_generalization_gate import candidate
        f=fact('monthly','current_asset','monthly essential outflow',1750)
        f.update(type='monthly_essential_outflow',account=None,earmark=None);f['amount']['precision']='approximate'
        w,_,view=flow([f],'My monthly essential outflow is about SGD 1750.');commit(w,view)
        before=w.store.snapshot
        apply(w,history_request(w,'edit','monthly_essential_outflow','amount',history_amount(1800,'approximate')))
        current=next(w.store.snapshot.get(r) for r in w.store.snapshot.world.members if r.record_kind=='monthly_essential_outflow')
        self.assertEqual(current.ref.version,2);self.assertEqual(current.amount.amount.minor_units,180000)
        self.assertIsNotNone(current.observation.corrects_ref)
        w.understanding.provider=SequenceProvider([candidate([f])])
        envelope=w.interpret('My monthly essential outflow is about SGD 1750.')
        view=w.prepare(envelope['id'])
        self.assertTrue(any(q['id']=='correction:monthly' for q in view['interaction']['questions']))
        question=next(q for q in view['interaction']['questions'] if q['id']=='correction:monthly')
        self.assertEqual(question['targets'][0]['currency'],'SGD')
        self.assertEqual(w.store.snapshot.revision,before.revision+1)

    def test_review_preview_apply_and_manifest_no_runway(self):
        for currency,label,text in (('SGD','monthly essential outflow','My monthly essential outflow is about SGD 1750.'),
                                    ('CNY','每月必要支出','我的每月必要支出大约是 CNY 4300 元。')):
            f=fact('monthly','current_asset',label,1750 if currency=='SGD' else 4300)
            f.update(type='monthly_essential_outflow',account=None,earmark=None)
            f['amount'].update(currency=currency,precision='approximate')
            w,_,view=flow([f],text)
            self.assertEqual(view['stage'],'preview',view)
            self.assertEqual(w.store.snapshot.revision,0)
            self.assertEqual(view['preview']['changes'][0]['kind'],'monthly_essential_outflow')
            commit(w,view)
            record=next(r for r in w.store.snapshot.records if type(r) is MonthlyEssentialOutflow)
            self.assertEqual(record.cadence,'monthly');self.assertTrue(record.observation.evidence_refs)
            self.assertEqual(record.amount.precision,AmountPrecision.APPROXIMATE)
            manifest=read_engine_inputs(w.authority)
            m=next(i for i in manifest.inputs if i.kind is InputKind.MONTHLY_ESSENTIALS)
            self.assertEqual(m.value,record.amount);self.assertEqual(m.observation,record.observation)
            self.assertTrue(all(g.status is GateStatus.BLOCKED for g in manifest.gates if g.capability is Capability.RUNWAY))
            self.assertEqual(w.home()['projection']['monthly_essential_observations'][0]['amount']['precision'],'approximate')

    def test_roundtrip_duplicate_protection_and_durable_recovery(self):
        loc=location();target=PersistentWorldStore.create(CONTEXT,observation('d1-initial'),loc)
        try:
            for currency in (Currency.CNY,Currency.SGD):
                m=MonthlyEssentialOutflow(Ref('monthly/'+currency.value,1),CONTEXT.owner_id,CONTEXT.world_id,
                    AmountEstimate.approximate(replace(exact(810).amount,currency=currency)),observation('monthly/'+currency.value))
                self.assertEqual(loads_world_mutation(dumps_world_mutation(m)),m)
                target.execute(prepare(target,changes(target,(PutRecord('put',m),),currency.value),currency.value))
            before=dumps_world_mutation(target.snapshot)
            duplicate=replace(m,ref=Ref('duplicate',1))
            from finvis.store import Rejected
            with self.assertRaises(Rejected):prepare(target,changes(target,(PutRecord('put',duplicate),),'duplicate'),'duplicate')
        finally:target.close()
        recovered=PersistentWorldStore.open(loc)
        try:self.assertEqual(dumps_world_mutation(recovered.snapshot),before)
        finally:recovered.close()


if __name__=='__main__':unittest.main()

"""FS3 generic authority/replay acceptance. Temporary databases, no models."""
import unittest
from dataclasses import replace
from finvis.future_adoption import FutureAdoption, decode_origin, canonical
from finvis.future_experience import FutureExperience, wire_ref
from finvis.future_memory import FutureMemoryService
from finvis.future_space import FutureError, FutureOption, FutureVariable, dumps
from finvis.persistent_world import PersistentWorldStore
from finvis.world_serialization import dumps_world_mutation, loads_world_mutation
from finvis.world_history_replay import replay_history
from finvis.world_commands import record_ref
from tests import test_future_experience as base
from tests.test_i4_commit import candidate_workflow
from tests.test_plan_reality import ready, execute
from tests.test_future_space import event


class FutureAdoptionTests(unittest.TestCase):
    keep=base.FutureExperienceTests.keep
    option=base.FutureExperienceTests.option
    options=base.FutureExperienceTests.options
    def setUp(self):
        base.FutureExperienceTests.setUp(self);self.w,_,_=candidate_workflow(self.store);self.a=FutureAdoption(self.w,self.x)
    def adopt(self,roots,options,**kw):return self.a.prepare(dict(roots=roots,options=options,**kw))
    def apply(self,p=None):
        p=p or self.a.view();v=p['preview'];a=self.a.authorize(dict(preview_id=v['id'],digest=v['digest']))
        body=dict(preview_id=v['id'],digest=v['digest'],authorization_id=a['authorization']['id'])
        return self.a.apply(body),body
    def restart(self):
        self.store.close();self.store=PersistentWorldStore.open(self.loc);self.addCleanup(self.store.close)
        self.m=FutureMemoryService(self.store);self.x=FutureExperience(self.m)
        self.w,_,_=candidate_workflow(self.store);self.a=FutureAdoption(self.w,self.x)
    def records(self,kind):return [self.store.snapshot.get(r) for r in self.store.snapshot.world.members if r.record_kind==kind]
    def test_adoption_is_not_payment_or_preview_mutation(self):
        n=self.keep('Pottery course',70,'SGD');before=self.store.snapshot
        self.adopt([n],self.options());self.assertIs(self.store.snapshot,before)
        self.apply();self.assertEqual(len(self.records('planned_expense')),1)
        self.assertEqual(self.records('actual_occurrence'),[]);self.assertEqual(self.records('asset_position'),[])
        self.assertEqual(self.records('planned_expense')[0].expected_amount.currency.value,'SGD')
    def test_change_forward_preserves_original_estimate(self):
        n=self.keep('Gloves',6000);new=self.options()[0];self.option(n,4000,label='Used gloves');used=self.options()[-1]
        self.apply(self.adopt([n],[used]));original=self.records('planned_expense')[0]
        self.apply(self.adopt([n],[new]));plans=self.records('planned_expense')
        self.assertEqual(sorted(p.status.value for p in plans),['cancelled','planned'])
        self.assertIn(original,self.store.snapshot.records)
        h=self.w.history();self.assertTrue(h['entries'][0]['intended_choice']);self.assertFalse(h['entries'][0]['has_correction'])
        self.assertEqual(replay_history(self.store.export_replay()),self.store.snapshot)
    def test_no_buy_has_no_zero_transaction(self):
        n=self.keep('Certification');p=self.x.prepare(dict(node=n,text='What if I do not buy',label='Do not buy'));self.x.confirm({'proposal_id':p['proposal_id']})
        self.apply(self.adopt([n],[self.options()[-1]]))
        self.assertEqual(self.records('planned_expense'),[]);self.assertEqual(self.records('actual_occurrence'),[])
        self.assertIsNone(self.records('world_decision')[0].amount)
        self.assertTrue(self.a.listing()[0]['absence'])
    def test_changed_to_absence_closes_unpaid_plan(self):
        n=self.keep();self.apply(self.adopt([n],self.options()))
        p=self.x.prepare(dict(node=n,text='What if I do not buy'));self.x.confirm({'proposal_id':p['proposal_id']})
        self.apply(self.adopt([n],[self.options()[-1]]));self.assertEqual(self.records('planned_expense')[0].status.value,'cancelled')
        self.assertEqual(self.records('actual_occurrence'),[])
    def test_native_currencies_atomic_separate_plans(self):
        roots=[self.keep('Music',500),self.keep('Work course',90,'SGD')]
        self.apply(self.adopt(roots,self.options()));self.assertEqual(self.store.snapshot.revision,1)
        self.assertEqual({p.expected_amount.currency.value for p in self.records('planned_expense')},{'SGD','CNY'})
    def scenario(self):
        a=event('Workshop',(200,300),scope=self.m.scope)
        b=list(event('Grant',(0,100),scope=self.m.scope));b[1]=replace(b[1],control='external_outcome')
        self.m.retain(self.m.prepare('seed',(*a,*b),'keep'))
        roots=[wire_ref(a[0].meta.ref),wire_ref(b[0].meta.ref)]
        options=[wire_ref(a[3].meta.ref),wire_ref(b[2].meta.ref)]
        p=self.x.prepare_scenario(dict(roots=roots,options=options,name='Course under uncertainty'));self.x.confirm({'proposal_id':p['proposal_id']})
        scenario=next(r['ref'] for r in self.x.listing()['records'] if r['ref']['kind']=='scenario')
        return scenario,options
    def test_external_context_preserved_not_adopted(self):
        scenario,options=self.scenario();p=self.a.prepare({'scenario':scenario})
        self.assertEqual([r['adopted'] for r in p['summary']],[True,False]);self.apply(p)
        self.assertEqual(len(self.records('planned_expense')),1);self.assertEqual(self.records('future_inflow'),[])
        self.assertEqual(self.a.listing()[0]['assumptions'][1]['control'],'external_outcome')
        self.restart();self.assertEqual(self.a.listing()[0]['assumptions'][1]['label'],'Option 0')
    def test_external_cannot_be_explicitly_adopted(self):
        scenario,options=self.scenario()
        with self.assertRaisesRegex(FutureError,'external_not_adoptable'):self.a.prepare({'scenario':scenario,'adopted':[options[1]]})
    def test_partial_scenario_keeps_unadopted_context(self):
        roots=[self.keep('Music',50),self.keep('Housing',900)];options=self.options()
        p=self.x.prepare_scenario(dict(roots=roots,options=options,name='Next quarter'));self.x.confirm({'proposal_id':p['proposal_id']})
        scenario=next(r['ref'] for r in self.x.listing()['records'] if r['ref']['kind']=='scenario')
        self.apply(self.a.prepare({'scenario':scenario,'adopted':options[:1]}))
        self.assertEqual(len(self.records('planned_expense')),1);self.assertEqual(len(self.a.listing()[0]['assumptions']),2)
    def test_restart_retry_exact_receipt(self):
        n=self.keep();result,body=self.apply(self.adopt([n],self.options()));snapshot=self.store.snapshot
        self.restart();self.assertEqual(snapshot,self.store.snapshot)
        retry=self.a.apply(body);self.assertEqual(retry['operation']['receipt'],result['operation']['receipt'])
        self.assertEqual(self.store.snapshot.revision,1)
    def test_restart_unconsumed_grant_expires(self):
        n=self.keep();v=self.adopt([n],self.options())['preview'];g=self.a.authorize(dict(preview_id=v['id'],digest=v['digest']))
        self.restart()
        with self.assertRaises(FutureError):self.a.apply(dict(preview_id=v['id'],digest=v['digest'],authorization_id=g['authorization']['id']))
    def test_tampered_command_rejected_and_old_codec_unchanged(self):
        n=self.keep();self.adopt([n],self.options());change=self.a.pending['preview'].change_set
        self.assertEqual(change,loads_world_mutation(dumps_world_mutation(change)))
        plan=change.operations[-1];bad=replace(change,operations=(*change.operations[:-1],replace(plan,record=replace(plan.record,purpose='Forged'))))
        with self.assertRaisesRegex(FutureError,'operations_mismatch'):self.store._stage(bad)
        old=replace(change,future_origin=None);encoded=dumps_world_mutation(old)
        self.assertNotIn('future_origin',encoded);self.assertEqual(loads_world_mutation(encoded),old)
    def test_wrong_world_capsule_rejected(self):
        n=self.keep();self.adopt([n],self.options());ch=self.a.pending['preview'].change_set
        p,c,_,_,_=decode_origin(ch.future_origin);p['catalog']=dumps((replace(c.scope,world_id='another'),tuple(replace(r,meta=replace(r.meta,scope=replace(c.scope,world_id='another'))) for r in c.records)))
        with self.assertRaises(FutureError):self.store._stage(replace(ch,future_origin=canonical(p)))
    def test_unknown_and_recurring_effect_not_silently_adopted(self):
        n=self.keep();self.option(n);c,_,_=self.m.read();o=c.get(__import__('finvis.future_experience',fromlist=['ref']).ref(self.options()[-1]))
        revised=replace(o,meta=replace(o.meta,ref=replace(o.meta.ref,version=2),previous=o.meta.ref),effects=(replace(o.effects[0],time_basis='monthly'),))
        self.m.retain(self.m.prepare('revise-effect',(revised,),'revise'))
        with self.assertRaisesRegex(FutureError,'effect_needs_review'):self.adopt([n],[wire_ref(revised.meta.ref)])
    def test_changed_future_head_invalidates_grant(self):
        n=self.keep();self.adopt([n],self.options());v=self.a.view()['preview']
        c,_,_=self.m.read();o=next(r for r in c.latest.values() if isinstance(r,FutureOption))
        o2=replace(o,meta=replace(o.meta,ref=replace(o.meta.ref,version=2),previous=o.meta.ref),label='Changed price')
        self.m.retain(self.m.prepare('revision',(o2,),'revise'))
        with self.assertRaisesRegex(FutureError,'inputs_stale'):self.a.authorize(dict(preview_id=v['id'],digest=v['digest']))
    def test_composer_en_zh_choices_and_ambiguous(self):
        n=self.keep('Camera');self.option(n,4000,label='二手 / Used');self.option(n,6000,label='全新 / New')
        for t in ['我决定买二手的了','I choose the used one']:
            self.assertEqual(self.a.interpret({'text':t,'node':n})['summary'][0]['label'],'二手 / Used')
        self.keep('Camera two');self.option(n,3500,label='Other used')
        self.assertEqual(self.a.interpret({'text':'Choose used'})['stage'],'clarify')
    def test_negated_and_hypothetical_not_choice(self):
        self.keep()
        for t in ['What if I choose it?','如果选这个呢','Maybe I will choose']:
            self.assertEqual(self.a.interpret({'text':t})['stage'],'unhandled')
    def test_selective_stale_original_scenario_intact(self):
        n=self.keep('Equipment');first=self.options()[0];p=self.x.prepare_scenario(dict(roots=[n],options=[first],name='Original'));self.x.confirm({'proposal_id':p['proposal_id']})
        other=self.keep('Course',50);p=self.x.prepare_scenario(dict(roots=[other],options=[self.options()[-1]],name='Unaffected'));self.x.confirm({'proposal_id':p['proposal_id']})
        self.option(n);self.apply(self.adopt([n],[self.options()[-1]]))
        saved={r['name']:r for r in self.a.space()['records'] if r['ref']['kind']=='scenario'}
        self.assertEqual(saved['Original']['status'],'stale');self.assertEqual(saved['Original']['selected'][0]['ref'],first)
        self.assertEqual(saved['Unaffected']['status'],'current')
    def seed_money(self):
        from tests.test_persistent_world import seed, changes, prepare
        from finvis.world_values import AmountEstimate
        from finvis.domain import Money
        command=seed(self.store)
        ops=tuple(replace(op,record=replace(op.record,balance=AmountEstimate.exact_reported(Money(3000000,op.record.currency)))) if op.record.__class__.__name__=='AssetPosition' else op for op in command.change_set.operations)
        self.store.execute(prepare(self.store,changes(self.store,ops,'funded'),'funded'))
    def actual(self,n,text,answers=None):
        r=self.a.interpret(dict(text=text,node=n))['routing'];answers=answers or {}
        while r['stage']=='question':
            k=r['question']['key'];v=answers.get(k)
            if v is None:
                v='yes' if k=='same_plan' else 'apply_to_prior_balance' if k=='coverage' else 'none' if k=='earmark' else r['question']['options'][0]['value']
            r=self.w.plan_reality.answer(dict(proposal_id=r['id'],key=k,value=v))
        result=self.w.plan_reality.prepare({'proposal_id':r['id']})
        self.assertEqual(result['stage'],'preview')
        return execute(self.w,result)
    def test_estimate_actual_source_coverage_restart_lineage(self):
        self.seed_money();n=self.keep('Equipment',4000);self.apply(self.adopt([n],self.options()))
        before=self.records('asset_position')[0].balance
        r=self.a.interpret(dict(node=n,text='最后4500买到了'))
        self.assertEqual(r['routing']['question']['key'],'same_plan')
        q=self.w.plan_reality.answer(dict(proposal_id=r['routing']['id'],key='same_plan',value='yes'))
        self.assertEqual(q['question']['key'],'source')
        self.actual(n,'最后4500买到了')
        row=self.a.listing()[0];self.assertEqual(row['plan']['summary']['estimated']['amount']['minor_units'],400000)
        self.assertEqual(row['plan']['summary']['net_actual']['value_minor'],450000)
        self.assertEqual(len(row['plan']['events']),1)
        self.restart();self.assertEqual(self.a.listing()[0],row)
        self.assertEqual(replay_history(self.store.export_replay()),self.store.snapshot)
    def test_realized_plan_cannot_be_re_adopted_or_replaced(self):
        self.seed_money();n=self.keep();first=self.options()[0];self.option(n,4000);second=self.options()[-1]
        self.apply(self.adopt([n],[second]));self.actual(n,'Finally bought it for CNY 4100')
        for o in [first,second]:
            with self.assertRaisesRegex(FutureError,'already_happened'):self.adopt([n],[o])
    def test_gradual_transport_three_payments(self):
        self.seed_money();n=self.keep('Local transport',700);self.apply(self.adopt([n],self.options(),gradual=self.options()))
        for text in ['今天地铁花了86。','I spent CNY 120.','刚才地铁花了42。']:self.actual(n,text)
        row=self.a.listing()[0]['plan'];self.assertEqual(row['summary']['net_actual']['value_minor'],24800)
        self.assertEqual(row['summary']['status'],'partially_realized');self.assertEqual(len(row['events']),3)
    def test_refund_replacement_linked_to_original_choice(self):
        self.seed_money();n=self.keep('Hotel',2800);self.apply(self.adopt([n],self.options(),gradual=self.options()))
        self.actual(n,'I paid CNY 2800.');self.actual(n,'Refunded CNY 1800.')
        from tests.test_decision_baseline import amount
        self.actual(n,'I rebooked for CNY 2400.',{'consumed':amount('1000','exact')})
        row=self.a.listing()[0]['plan'];self.assertEqual(row['summary']['net_actual']['value_minor'],340000)
        self.assertEqual([e['kind'] for e in row['events']],['actual','refund','replacement'])
        self.restart();self.assertEqual(self.a.listing()[0]['plan'],row)
    def test_unplanned_event_remains_normal_path(self):
        self.keep('Course',90)
        self.assertEqual(self.a.interpret({'text':'I paid CNY 900 to repair my laptop.'})['stage'],'unhandled')
    def test_committed_plan_cannot_silently_switch(self):
        self.seed_money();n=self.keep('Accommodation',800);first=self.options()[0];self.option(n,900);second=self.options()[-1]
        self.apply(self.adopt([n],[first]));self.actual(n,'I booked Accommodation for CNY 800')
        with self.assertRaisesRegex(FutureError,'already_happened'):self.adopt([n],[second])
    def test_source_decision_reused_and_change_forward(self):
        self.seed_money();d=self.records('world_decision')[0];count=len(self.records('world_decision'))
        review=self.x.prepare(dict(text='What if cheaper for CNY 400',source_ref=__import__('finvis.reviewed_proposals',fromlist=['_wire'])._wire(record_ref(d))))
        self.x.confirm({'proposal_id':review['proposal_id']});n=next(r['ref'] for r in self.x.listing()['records'] if r['ref']['kind']=='node')
        self.apply(self.adopt([n],[self.options()[-1]]));self.assertEqual(len(self.records('world_decision')),count)
        self.apply(self.adopt([n],[self.options()[0]]));self.assertEqual(self.records('world_decision')[0].ref.version,d.ref.version+2)
    def test_apply_response_loss_recovers_exact_receipt(self):
        from unittest.mock import patch
        from finvis.persistent_world import StorageUnavailable
        n=self.keep('Garden equipment');self.adopt([n],self.options());v=self.a.view()['preview']
        g=self.a.authorize(dict(preview_id=v['id'],digest=v['digest']));body=dict(preview_id=v['id'],digest=v['digest'],authorization_id=g['authorization']['id'])
        def fail(stage):
            if stage=='after_commit':raise StorageUnavailable('Synthetic response interruption')
        with patch.object(self.store,'_checkpoint',side_effect=fail):result=self.a.apply(body)
        self.restart();self.assertEqual(self.a.apply(body)['operation']['receipt'],result['operation']['receipt'])
        self.assertEqual(self.store.snapshot.revision,1)
    def test_before_commit_failure_leaves_no_adoption(self):
        from unittest.mock import patch
        from finvis.persistent_world import StorageUnavailable
        n=self.keep();self.adopt([n],self.options());v=self.a.view()['preview'];g=self.a.authorize(dict(preview_id=v['id'],digest=v['digest']))
        def fail(stage):
            if stage=='before_commit':raise StorageUnavailable('Synthetic interrupted transaction')
        with patch.object(self.store,'_checkpoint',side_effect=fail):
            with self.assertRaises(Exception):self.a.apply(dict(preview_id=v['id'],digest=v['digest'],authorization_id=g['authorization']['id']))
        self.restart();self.assertEqual(self.a.listing(),[]);self.assertEqual(self.store.snapshot.revision,0)
    def test_foreign_option_and_tampered_digest(self):
        n=self.keep();p=self.adopt([n],self.options())
        with self.assertRaises(FutureError):self.a.authorize(dict(preview_id=p['preview']['id'],digest='a'*64))
        with self.assertRaises(FutureError):self.adopt([n],self.options(),adopted=[dict(kind='option',id='foreign',version=1)])
        self.assertEqual(self.store.snapshot.revision,0)
    def test_real_event_different_from_plan_cannot_link(self):
        self.seed_money();n=self.keep();self.apply(self.adopt([n],self.options()));r=self.a.interpret(dict(node=n,text='Finally I paid CNY 50'))['routing']
        r=self.w.plan_reality.answer(dict(proposal_id=r['id'],key='same_plan',value='no'))
        self.assertEqual(r['question']['key'],'different_event')
        with self.assertRaises(Exception):self.w.plan_reality.prepare(dict(proposal_id=r['id']))
        self.assertEqual(self.a.listing()[0]['plan']['events'],[])
    def test_negation_never_selects_only_option(self):
        self.keep()
        for text in ['I do not choose it','我还没决定']:
            self.assertEqual(self.a.interpret({'text':text})['stage'],'unhandled')
    def test_imported_plan_does_not_duplicate_existing_expense(self):
        self.seed_money();p=self.records('planned_expense')[0]
        from finvis.reviewed_proposals import _wire
        d=self.x.prepare(dict(text='Explore existing plan',source_ref=_wire(record_ref(p))));self.x.confirm({'proposal_id':d['proposal_id']})
        n=next(r['ref'] for r in self.x.listing()['records'] if r['ref']['kind']=='node');before=self.store.snapshot
        with self.assertRaisesRegex(FutureError,'existing_plan_needs_review'):self.adopt([n],self.options())
        self.assertIs(self.store.snapshot,before)

    def test_no_proceed_phrase_after_forward_change(self):
        n=self.keep('Workshop',700,'SGD');self.option(n,990,'SGD','New course')
        p=self.x.prepare(dict(node=n,text='What if I do not buy',label='Do not proceed'));self.x.confirm({'proposal_id':p['proposal_id']})
        self.apply(self.adopt([n],[self.options()[0]]));self.apply(self.a.interpret(dict(text='I decide to switch to new',node=n)))
        p=self.a.interpret(dict(text='I decided not to proceed',node=n));self.assertEqual(p['stage'],'preview');self.apply(p)
        self.assertTrue(self.a.listing()[0]['absence'])
    def test_nested_trip_scenario_full_controllable_subset(self):
        trip=self.keep('Japan trip',None);self.keep('Standard hotel',3000,parent=trip);self.keep('Concert',300,parent=trip)
        gloves=self.keep('Used gloves',4000)
        grant=event('Conditional bonus',(8000,0),scope=self.m.scope,control='external_outcome')
        self.m.retain(self.m.prepare('bonus-context',grant,'keep'))
        c,_,_=self.m.read();controlled=[wire_ref(o.meta.ref) for o in c.latest.values() if isinstance(o,FutureOption) and c.get(o.variable).control=='user_choice']
        all_options=controlled+[wire_ref(grant[3].meta.ref)]
        p=self.x.prepare_scenario(dict(roots=[trip,gloves,wire_ref(grant[0].meta.ref)],options=all_options,name='Trip and equipment'))
        self.x.confirm({'proposal_id':p['proposal_id']});saved=next(r['ref'] for r in self.x.listing()['records'] if r['ref']['kind']=='scenario')
        before=self.x.listing();self.apply(self.a.prepare(dict(scenario=saved)))
        self.assertEqual(len(self.records('planned_expense')),3);self.assertEqual(self.store.snapshot.revision,1)
        self.assertEqual(self.records('future_inflow'),[]);self.assertEqual(self.x.listing()['future_revision'],before['future_revision'])
        self.restart();self.assertEqual(len(self.a.listing()),3)

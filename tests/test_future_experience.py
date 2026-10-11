"""FS2 generic application boundary: synthetic temporary stores only."""
import unittest
from dataclasses import replace
from finvis.future_space import FutureError, FutureNode, FutureOption, FutureCatalog
from finvis.future_memory import FutureMemoryService
from finvis.future_experience import FutureExperience, wire_ref
from finvis.persistent_world import PersistentWorldStore
from tests.test_persistent_world import location, CONTEXT
from tests.test_world_assets import observation
from tests.test_future_space import event, meta, AT, ref


def price(n,c='CNY'):
    return dict(currency=c,precision='approximate',value=str(n),range_min=None,range_max=None)


class FutureExperienceTests(unittest.TestCase):
    def setUp(self):
        self.loc=location();self.store=PersistentWorldStore.create(CONTEXT,observation('empty'),self.loc)
        self.addCleanup(self.store.close)
        self.m=FutureMemoryService(self.store);self.x=FutureExperience(self.m)

    def keep(self,title='Equipment',n=6000,c='CNY',parent=None):
        p=dict(text='I am considering '+title,title=title)
        if n is not None:p['amount']=price(n,c)
        if parent:p['parent']=parent
        review=self.x.prepare(p);self.x.confirm({'proposal_id':review['proposal_id']})
        return next(r for r in self.x.listing()['records'] if r.get('title')==title)['ref']

    def option(self,node,n=4000,c='CNY',label='Used'):
        p=self.x.prepare(dict(text='What if '+label,node=node,label=label,amount=price(n,c)))
        self.x.confirm({'proposal_id':p['proposal_id']});return p

    def options(self):return [r['ref'] for r in self.x.listing()['records'] if r['ref']['kind']=='option']

    def test_empty_read_does_not_publish(self):
        self.assertEqual(self.x.listing()['records'],[]);self.assertEqual(self.store.snapshot.revision,0)

    def test_prepare_is_transient_and_confirm_is_explicit(self):
        p=self.x.prepare(dict(text='Course for SGD 700',title='Ceramics',amount=price(700,'SGD')))
        self.assertEqual(self.x.listing()['future_revision'],0)
        self.x.confirm({'proposal_id':p['proposal_id']})
        self.assertEqual(self.x.listing()['future_revision'],1);self.assertEqual(self.store.snapshot.revision,0)

    def test_alternative_not_additive(self):
        n=self.keep('Gloves');p=self.option(n)
        b=p['summary']['result']['buckets'][0]
        self.assertEqual(b['known_selection_total'][0]['value_minor'],400000)
        self.assertEqual(b['delta'][0]['value_minor'],-200000)
        self.assertEqual(len([r for r in self.x.listing()['records'] if r['ref']['kind']=='node']),1)

    def test_explicit_absence(self):
        n=self.keep();p=self.x.prepare(dict(text='What if I do not buy',node=n))
        self.assertEqual(p['summary']['amount']['amount']['minor_units'],0)
        self.assertEqual(p['summary']['result']['buckets'][0]['delta'][0]['value_minor'],-600000)

    def test_unknown_price_is_not_zero(self):
        n=self.keep();p=self.x.prepare(dict(text='What if a premium option',node=n))
        self.assertEqual(p['summary']['amount']['precision'],'unknown')
        self.assertEqual(p['summary']['result']['status'],'needs_information')

    def test_same_category_distinct_events(self):
        a=self.keep('Hotel stay A');b=self.keep('Hotel stay B')
        r=self.x.preview(dict(roots=[a,b],options=self.options()))
        self.assertEqual(r['buckets'][0]['known_selection_total'][0]['value_minor'],1200000)

    def test_conflicting_options_rejected(self):
        n=self.keep();self.option(n)
        with self.assertRaisesRegex(FutureError,'exclusive_options'):self.x.preview(dict(roots=[n],options=self.options()))

    def test_cross_currency_stays_separate(self):
        a=self.keep('Course',700,'SGD');b=self.keep('Device',3000)
        r=self.x.preview(dict(roots=[a,b],options=self.options()))
        self.assertEqual({b['currency'] for b in r['buckets']},{'CNY','SGD'})

    def test_nested_three_components(self):
        root=self.keep('Travel',None)
        for name,n in [('Stay',900),('Music',300),('Camera',1500)]:self.keep(name,n,parent=root)
        p=self.x.preview(dict(roots=[root],options=self.options()))
        self.assertEqual(p['buckets'][0]['known_selection_total'][0]['value_minor'],270000)

    def test_bundle_child_overlap_preserved(self):
        root=self.keep('Whole trip');self.keep('Stay',900,parent=root)
        with self.assertRaisesRegex(FutureError,'bundle_child_overlap'):self.x.preview(dict(roots=[root],options=self.options()))

    def test_selection_and_scenario_prepare_do_not_save(self):
        root=self.keep();revision=self.x.listing()['future_revision']
        p=self.x.prepare_scenario(dict(roots=[root],options=self.options(),name='One coherent future'))
        self.assertEqual(self.x.listing()['future_revision'],revision)
        self.x.confirm({'proposal_id':p['proposal_id']})
        self.assertEqual(len([r for r in self.x.listing()['records'] if r['ref']['kind']=='scenario']),1)

    def test_saved_scenario_and_ack_survive_restart(self):
        n=self.keep();p=self.x.prepare_scenario(dict(roots=[n],options=self.options(),name='Travel later'))
        ack=self.x.confirm({'proposal_id':p['proposal_id']});before=self.x.listing()
        self.store.close();s=PersistentWorldStore.open(self.loc);self.addCleanup(s.close)
        x=FutureExperience(FutureMemoryService(s))
        self.assertEqual(before,x.listing());self.assertEqual(ack,x.confirm({'proposal_id':p['proposal_id']}))

    def test_pending_review_expires_restart(self):
        p=self.x.prepare(dict(text='A thought',title='Maybe'))
        self.store.close();s=PersistentWorldStore.open(self.loc);self.addCleanup(s.close)
        with self.assertRaisesRegex(FutureError,'future_review_expired'):FutureExperience(FutureMemoryService(s)).confirm({'proposal_id':p['proposal_id']})

    def test_stale_saved_selections_preserved(self):
        n=self.keep();p=self.x.prepare_scenario(dict(roots=[n],options=self.options(),name='Original'))
        self.x.confirm({'proposal_id':p['proposal_id']})
        c,_,_=self.m.read();old=next(r for r in c.latest.values() if isinstance(r,FutureOption))
        new=replace(old,meta=replace(old.meta,ref=replace(old.meta.ref,version=2),previous=old.meta.ref),label='Revised estimate')
        self.m.retain(self.m.prepare('revise',(new,),'revise'))
        row=next(r for r in self.x.listing()['records'] if r['ref']['kind']=='scenario')
        self.assertEqual(row['status'],'stale');self.assertEqual(row['selected'][0]['ref']['version'],1)
        self.assertEqual(row['result']['status'],'stale')

    def test_no_alternatives_or_unknown_selection_not_zero(self):
        root=self.keep('Maybe travel',None)
        # Structural node with no effect describes organization, not cash.
        self.assertEqual(self.x.preview(dict(roots=[root],options=[]))['buckets'],[])

    def test_generic_chinese_bare_price(self):
        n=self.keep('乐器');p=self.x.prepare(dict(text='如果乐器买二手只要4000呢',node=n))
        self.assertEqual(p['summary']['amount']['amount']['minor_units'],400000)

    def test_ambiguous_equal_labels_not_merged(self):
        self.keep('Travel');self.keep('Travel')
        self.assertEqual(self.x.prepare({'text':'What if Travel costs CNY 800'})['question'],'which_event')

    def test_invalid_payload_does_not_publish(self):
        for payload in ({'text':'x','adopt':True},{'text':'x','currency':'USD'},{'text':False}):
            with self.assertRaises((FutureError,ValueError)):self.x.prepare(payload)
        self.assertEqual(self.m.read()[1],0)

    def test_changed_head_rejects_old_review(self):
        p=self.x.prepare(dict(text='A thought',title='Old draft'));self.keep()
        with self.assertRaisesRegex(FutureError,'future_head_changed'):self.x.confirm({'proposal_id':p['proposal_id']})

    def test_different_source_on_existing_node_is_rejected(self):
        # Exact financial source handling is separately tested at HTTP boundary.
        with self.assertRaises(FutureError):self.x.prepare({'text':'x','node':{'kind':'node','id':'unknown','version':1}})

    def test_inactive_reference_component_not_counted(self):
        outer=event('outing',(0,200),scope=self.m.scope)
        child=list(event('rental',(50,),scope=self.m.scope,parent=outer[0].meta.ref))
        child[0]=replace(child[0],active_when=(outer[3].meta.ref,))
        self.m.retain(self.m.prepare('conditional',(*outer,*child),'keep'))
        result=self.x.preview(dict(roots=[wire_ref(outer[0].meta.ref)],options=[wire_ref(outer[2].meta.ref)]))
        self.assertEqual(result['status'],'available')
        self.assertEqual(result['buckets'][0]['known_selection_total'][0]['value_minor'],0)

    def test_nested_composer_adds_generic_component(self):
        root=self.keep('庆典',None);self.keep('住宿',500,parent=root)
        p=self.x.prepare(dict(text='如果再去看演唱会呢',node=root))
        self.assertEqual(p['stage'],'review');self.assertEqual(p['summary']['parent'],'庆典')
        self.assertEqual(p['summary']['amount']['precision'],'unknown')
        self.x.confirm({'proposal_id':p['proposal_id']})
        children=[r for r in self.x.listing()['records'] if r.get('parent')==root]
        self.assertEqual(len(children),2)

    def test_compound_cancel_not_silently_zeroed(self):
        n=self.keep('Workshop')
        with self.assertRaisesRegex(FutureError,'future_meaning_ambiguous'):
            self.x.prepare(dict(text='What if I do not buy and also buy another',node=n))
        p=self.x.prepare(dict(text='What if not cancelled',node=n))
        self.assertEqual(p['summary']['amount']['precision'],'unknown')

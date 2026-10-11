"""FS1 generic immutable domain/selection tests. No models or financial writes."""
from dataclasses import replace
from datetime import datetime, timezone, timedelta
import json
import unittest

from finvis.domain import Currency, Money
from finvis.world_values import AmountEstimate
from finvis.future_space import *

AT=datetime(2026,9,28,tzinfo=timezone.utc)
SCOPE=FutureScope('user:generic','generic-owner','generic-world','store-generic','activation-generic')


def ref(kind,identity,version=1):return FutureRef(kind,identity,version)


def meta(kind,identity,scope=SCOPE,version=1,dependencies=(),evidence=None):
    r=ref(kind,identity,version)
    return FutureMeta(scope,r,replace(r,version=version-1) if version>1 else None,AT,
        evidence or (FutureEvidence('user_report','synthetic/'+identity,1,None,AT,'Explicit synthetic assumption'),),dependencies)


def value(major,currency=Currency.CNY,precision='approximate'):
    if precision=='unknown':return AmountEstimate.unknown('No quote',currency)
    if precision=='range':return AmountEstimate.range(Money(major*100,currency),Money((major+100)*100,currency))
    maker=AmountEstimate.exact_reported if precision=='exact' else AmountEstimate.approximate
    return maker(Money(major*100,currency))


def effect(slot,major,currency=Currency.CNY,precision='approximate',typ='cost',basis='one_time',coverage=None,absence=False):
    observed=value(major,currency,'exact' if absence else precision)
    planning=AmountEstimate.approximate(observed.amount) if precision=='exact' and not absence else observed
    return FutureEffect(slot,typ,observed,planning,basis,coverage or (slot,),absence)


def event(identity,amounts=(6000,4000,0),scope=SCOPE,currency=Currency.CNY,control='user_choice',parent=None):
    n=FutureNode(meta('node',identity,scope),identity,'component' if parent else 'event',identity,parent)
    v=FutureVariable(meta('variable',identity+'/choice',scope),n.meta.ref,'Which state?',control,ref('option',identity+'/0'))
    opts=tuple(FutureOption(meta('option',identity+'/'+str(i),scope),v.meta.ref,'Option '+str(i),
        (effect(identity,a,currency,typ='expected_inflow' if control=='external_outcome' else 'cost',absence=a==0),),
        ('User supplied scoped estimate, no retained obligation implied',)) for i,a in enumerate(amounts))
    return (n,v,*opts)


def scenario(catalog,roots,options,name='scenario',complete=True):
    sel=catalog.select(roots,options,complete)
    return SavedScenario(meta('scenario',name,catalog.scope),name,sel)


class FutureDomainTests(unittest.TestCase):
    def setUp(self):
        self.rows=event('gloves');self.c=FutureCatalog(SCOPE,self.rows)

    def test_gloves_exclusive_not_additive(self):
        with self.assertRaisesRegex(FutureError,'exclusive_options'):
            self.c.select((self.rows[0].meta.ref,),(self.rows[2].meta.ref,self.rows[3].meta.ref))

    def test_gloves_used_comparison_4000_minus_6000(self):
        selection=self.c.select((self.rows[0].meta.ref,),(self.rows[2].meta.ref,))
        frame=ReferenceFrame(meta('reference','gloves/basis'),'New pair',selection)
        c=FutureCatalog(SCOPE,(*self.rows,frame))
        result=c.compile(c.select(selection.roots,(self.rows[3].meta.ref,)),frame.meta.ref)
        self.assertEqual(result['buckets'][0]['known_selection_total'][0]['value_minor'],400000)
        self.assertEqual(result['buckets'][0]['delta'][0]['value_minor'],-200000)
        self.assertFalse(result['financial_authority'])

    def test_do_not_buy_is_explicit_zero(self):
        result=self.c.compile(self.c.select((self.rows[0].meta.ref,),(self.rows[4].meta.ref,)))
        self.assertEqual(result['buckets'][0]['known_selection_total'][0]['value_minor'],0)
        self.assertEqual(result['buckets'][0]['known_selection_total'][0]['precision'],'exact_reported')

    def test_unselected_is_not_zero(self):
        result=self.c.compile(self.c.select((self.rows[0].meta.ref,),()))
        self.assertEqual(result['status'],'needs_information');self.assertEqual(result['buckets'],[])

    def test_nested_trip_independent_components_and_gloves(self):
        trip=FutureNode(meta('node','trip'),'Japan','event','trip')
        hotels=FutureNode(meta('node','stays'),'Stays','component','stays',trip.meta.ref)
        tokyo=event('tokyo',(3000,6000),parent=hotels.meta.ref)
        kyoto=event('kyoto',(2200,),parent=hotels.meta.ref)
        concert=event('concert',(1500,0),parent=trip.meta.ref)
        camera=event('camera',(6000,0),parent=trip.meta.ref)
        c=FutureCatalog(SCOPE,(*self.rows,trip,hotels,*tokyo,*kyoto,*concert,*camera))
        selected=(self.rows[3].meta.ref,tokyo[3].meta.ref,kyoto[2].meta.ref,concert[2].meta.ref,camera[3].meta.ref)
        result=c.compile(c.select((trip.meta.ref,self.rows[0].meta.ref),selected))
        self.assertEqual(result['buckets'][0]['known_selection_total'][0]['value_minor'],1370000)
        with self.assertRaisesRegex(FutureError,'exclusive_options'):
            c.select((trip.meta.ref,), (tokyo[2].meta.ref,tokyo[3].meta.ref))

    def test_bonus_external_outcome_coexists(self):
        bonus=event('bonus',(8000,0),control='external_outcome')
        c=FutureCatalog(SCOPE,(*self.rows,*bonus))
        result=c.compile(c.select((self.rows[0].meta.ref,bonus[0].meta.ref),(self.rows[3].meta.ref,bonus[3].meta.ref)))
        self.assertEqual({b['effect_type'] for b in result['buckets']},{'cost','expected_inflow'})
        self.assertEqual(bonus[1].control,'external_outcome')

    def test_explicit_bonus_prerequisite_blocks_cancellation(self):
        bonus=event('bonus',(8000,0),control='external_outcome')
        used=replace(self.rows[3],requires=(bonus[2].meta.ref,))
        c=FutureCatalog(SCOPE,(*self.rows[:3],used,self.rows[4],*bonus))
        with self.assertRaisesRegex(FutureError,'prerequisite_conflict'):
            c.select((self.rows[0].meta.ref,bonus[0].meta.ref),(used.meta.ref,bonus[3].meta.ref))

    def test_missing_prerequisite_is_incomplete(self):
        bonus=event('bonus',(8000,0),control='external_outcome')
        used=replace(self.rows[3],requires=(bonus[2].meta.ref,))
        c=FutureCatalog(SCOPE,(*self.rows[:3],used,self.rows[4],*bonus))
        sel=c.select((self.rows[0].meta.ref,bonus[0].meta.ref),(used.meta.ref,))
        self.assertIn('prerequisite_missing:bonus/0',c.compile(sel)['missing'])

    def test_explicit_exclusion(self):
        another=event('other',(100,))
        used=replace(self.rows[3],excludes=(another[2].meta.ref,))
        c=FutureCatalog(SCOPE,(*self.rows[:3],used,self.rows[4],*another))
        with self.assertRaisesRegex(FutureError,'excluded_options'):
            c.select((self.rows[0].meta.ref,another[0].meta.ref),(used.meta.ref,another[2].meta.ref))

    def test_exclusion_not_bypassed_by_new_version_of_same_option(self):
        b=event('other',(100,))
        a=replace(self.rows[3],excludes=(b[2].meta.ref,))
        new=replace(b[2],meta=meta('option',b[2].meta.ref.id,version=2),label='Updated estimate')
        c=FutureCatalog(SCOPE,(*self.rows[:3],a,self.rows[4],*b,new))
        with self.assertRaisesRegex(FutureError,'excluded_options'):
            c.select((self.rows[0].meta.ref,b[0].meta.ref),(a.meta.ref,new.meta.ref))

    def test_inactive_children_do_not_silently_drop_selected_option(self):
        trip=event('trip',(0,0))
        hotel=event('hotel',(3000,),parent=trip[0].meta.ref)
        hotel=(replace(hotel[0],active_when=(trip[2].meta.ref,)),*hotel[1:])
        c=FutureCatalog(SCOPE,(*trip,*hotel))
        with self.assertRaisesRegex(FutureError,'inactive_option_selected'):
            c.select((trip[0].meta.ref,),(trip[3].meta.ref,hotel[2].meta.ref))
        result=c.compile(c.select((trip[0].meta.ref,),(trip[3].meta.ref,)))
        self.assertEqual(result['status'],'available')

    def test_partial_activation(self):
        parent=event('journey',(0,0))
        child=event('child',(5,),parent=parent[0].meta.ref)
        child=(replace(child[0],active_when=(parent[2].meta.ref,)),*child[1:])
        c=FutureCatalog(SCOPE,(*parent,*child))
        self.assertEqual(c.compile(c.select((parent[0].meta.ref,),(child[2].meta.ref,)))['status'],'needs_information')

    def test_false_activation_dominates_other_unknown_condition(self):
        a=event('a',(0,0));b=event('b',(0,0))
        child=event('child',(3000,),parent=a[0].meta.ref)
        child=(replace(child[0],active_when=(a[2].meta.ref,b[2].meta.ref)),*child[1:])
        c=FutureCatalog(SCOPE,(*a,*b,*child))
        with self.assertRaisesRegex(FutureError,'inactive_option_selected'):
            c.select((a[0].meta.ref,b[0].meta.ref),(b[3].meta.ref,child[2].meta.ref))

    def test_parent_bundle_and_children_cannot_double_count(self):
        bundle=event('bundle',(4000,))
        child=event('child',(2000,),parent=bundle[0].meta.ref)
        c=FutureCatalog(SCOPE,(*bundle,*child))
        with self.assertRaisesRegex(FutureError,'bundle_child_overlap'):
            c.select((bundle[0].meta.ref,),(bundle[2].meta.ref,child[2].meta.ref))

    def test_shared_coverage_rejected(self):
        a=event('a',(20,));b=event('b',(30,))
        b=(*b[:2],replace(b[2],effects=(effect('different',30,coverage=('a',)),)))
        c=FutureCatalog(SCOPE,(*a,*b))
        with self.assertRaisesRegex(FutureError,'overlapping_effect_coverage'):
            c.select((a[0].meta.ref,b[0].meta.ref),(a[2].meta.ref,b[2].meta.ref))

    def test_same_effect_slot_conflict(self):
        extra=FutureVariable(meta('variable','second'),self.rows[0].meta.ref,'Other dimension','planning_assumption')
        opt=FutureOption(meta('option','second'),extra.meta.ref,'Reprice',(effect('gloves',10,coverage=('second',)),),('Explicit assumption',))
        c=FutureCatalog(SCOPE,(*self.rows,extra,opt))
        with self.assertRaisesRegex(FutureError,'competing_effect_assignment'):
            c.select((self.rows[0].meta.ref,),(self.rows[2].meta.ref,opt.meta.ref))

    def test_jobs_correlated_salary_and_start_date(self):
        jobs=event('employment',(5000,6500),currency=Currency.SGD)
        jobs=(*jobs[:2],replace(jobs[2],assumptions=('Salary SGD 5,000 monthly','Starts November')),
              replace(jobs[3],assumptions=('Salary SGD 6,500 monthly','Starts January')))
        c=FutureCatalog(SCOPE,jobs)
        with self.assertRaises(FutureError):c.select((jobs[0].meta.ref,),(jobs[2].meta.ref,jobs[3].meta.ref))
        self.assertEqual(c.get(jobs[2].meta.ref).assumptions[1],'Starts November')

    def test_housing_loan_terms_are_context_not_amortization_policy(self):
        housing=event('housing',(1000,100000),currency=Currency.SGD)
        mortgage=FutureOption(meta('option','loan'),housing[1].meta.ref,'Mortgage terms',(),('Interest rate unknown','LTV not evaluated'))
        c=FutureCatalog(SCOPE,(*housing,mortgage))
        result=c.compile(c.select((housing[0].meta.ref,),(mortgage.meta.ref,)))
        self.assertEqual(result['financial_metrics'],'unavailable_policy_required');self.assertEqual(result['buckets'],[])

    def test_course_installments_can_coexist(self):
        course=FutureNode(meta('node','course'),'Pottery workshop','event','term-A')
        first=event('deposit',(50,),parent=course.meta.ref,currency=Currency.SGD)
        second=event('tuition',(300,),parent=course.meta.ref,currency=Currency.SGD)
        c=FutureCatalog(SCOPE,(course,*first,*second))
        result=c.compile(c.select((course.meta.ref,),(first[2].meta.ref,second[2].meta.ref)))
        self.assertEqual(result['buckets'][0]['known_selection_total'][0]['value_minor'],35000)

    def test_cny_sgd_never_aggregated(self):
        course=event('course',(120,),currency=Currency.SGD)
        c=FutureCatalog(SCOPE,(*self.rows,*course))
        result=c.compile(c.select((self.rows[0].meta.ref,course[0].meta.ref),(self.rows[2].meta.ref,course[2].meta.ref)))
        self.assertEqual([b['currency'] for b in result['buckets']],['CNY','SGD'])

    def test_different_time_bases_never_aggregated(self):
        a=event('rent',(1200,));b=event('deposit',(2000,))
        a=(*a[:2],replace(a[2],effects=(effect('rent',1200,basis='per_month'),)))
        c=FutureCatalog(SCOPE,(*a,*b))
        result=c.compile(c.select((a[0].meta.ref,b[0].meta.ref),(a[2].meta.ref,b[2].meta.ref)))
        self.assertEqual(len(result['buckets']),2)

    def test_precision_exact_quote_remains_approximate_future(self):
        e=effect('quote',31,precision='exact')
        self.assertEqual(e.observed.precision,AmountPrecision.EXACT_REPORTED)
        self.assertEqual(e.planning.precision,AmountPrecision.APPROXIMATE)
        with self.assertRaises(FutureError):replace(e,planning=e.observed)

    def test_unknown_range_amounts_roundtrip(self):
        for precision in ('unknown','range','approximate','exact'):
            with self.subTest(precision=precision):
                e=effect('gloves',200,precision=precision)
                opt=replace(self.rows[3],effects=(e,))
                c=FutureCatalog(SCOPE,(*self.rows[:3],opt,self.rows[4]))
                self.assertEqual(loads(dumps(opt)),opt)
                result=c.compile(c.select((self.rows[0].meta.ref,),(opt.meta.ref,)))
                self.assertEqual(result['status'],'needs_information' if precision=='unknown' else 'available')

    def test_unchanged_range_cancels_in_delta(self):
        opt=replace(self.rows[3],effects=(effect('gloves',200,precision='range'),))
        c=FutureCatalog(SCOPE,(*self.rows[:3],opt,self.rows[4]))
        sel=c.select((self.rows[0].meta.ref,),(opt.meta.ref,));basis=ReferenceFrame(meta('reference','basis'),'Basis',sel)
        c=FutureCatalog(SCOPE,(*c.records,basis))
        self.assertEqual(c.compile(sel,basis.meta.ref)['buckets'][0]['delta'][0]['value_minor'],0)

    def test_saved_scenario_pins_versions_not_latest(self):
        s=scenario(self.c,(self.rows[0].meta.ref,),(self.rows[3].meta.ref,))
        old=dumps(s)
        revised=replace(self.rows[3],meta=meta('option',self.rows[3].meta.ref.id,version=2),effects=(effect('gloves',4200),))
        c=FutureCatalog(SCOPE,(*self.rows,s,revised))
        self.assertEqual(dumps(c.get(s.meta.ref)),old)
        self.assertEqual(c.compile(s.selection)['buckets'][0]['known_selection_total'][0]['value_minor'],400000)

    def test_scoped_selection_cannot_smuggle_unrelated_option(self):
        other=event('other',(10,));c=FutureCatalog(SCOPE,(*self.rows,*other))
        with self.assertRaisesRegex(FutureError,'option_outside_scope'):
            c.select((self.rows[0].meta.ref,),(other[2].meta.ref,))

    def test_scoped_records_reject_other_user(self):
        with self.assertRaisesRegex(FutureError,'foreign_future_record'):
            FutureCatalog(replace(SCOPE,owner_id='someone-else'),self.rows)

    def test_option_cannot_change_owning_question(self):
        other=event('other',(10,));changed=replace(self.rows[3],meta=meta('option',self.rows[3].meta.ref.id,version=2),variable=other[1].meta.ref)
        with self.assertRaisesRegex(FutureError,'option_identity_changed'):FutureCatalog(SCOPE,(*self.rows,*other,changed))

    def test_invalid_version_and_mutable_collections(self):
        for invalid in (0,True,1.0,-1):
            with self.assertRaises(ValueError):FutureRef('node','x',invalid)
        with self.assertRaises(FutureError):replace(self.rows[3],assumptions=['mutable'])
        with self.assertRaises(FutureError):replace(self.rows[3].meta,previous=ref('option','other'))

    def test_containment_cycle_rejected(self):
        a=FutureNode(meta('node','a'),'A','component','a',ref('node','b'))
        b=FutureNode(meta('node','b'),'B','component','b',ref('node','a'))
        with self.assertRaisesRegex(FutureError,'future_cycle'):FutureCatalog(SCOPE,(a,b))

    def test_requires_cycle_rejected(self):
        first=replace(self.rows[2],requires=(self.rows[3].meta.ref,));second=replace(self.rows[3],requires=(self.rows[2].meta.ref,))
        with self.assertRaisesRegex(FutureError,'future_cycle'):FutureCatalog(SCOPE,(*self.rows[:2],first,second,self.rows[4]))

    def test_activation_cycle_rejected(self):
        a=event('a',(1,));b=event('b',(2,))
        a=(a[0],replace(a[1],active_when=(b[2].meta.ref,)),a[2])
        b=(b[0],replace(b[1],active_when=(a[2].meta.ref,)),b[2])
        with self.assertRaisesRegex(FutureError,'future_cycle'):FutureCatalog(SCOPE,(*a,*b))

    def test_closed_codec_rejects_extra_schema_fields_float_duplicate(self):
        raw=dumps(self.rows[0]);obj=json.loads(raw)
        cases=[raw.replace('finvis.future-space.v1','finvis.future-space.v99'),raw.replace('"version":1','"version":1.0'),
               raw.replace('"schema_version":','"schema_version":"wrong","schema_version":')]
        obj['value']['fields']['surprise']=True;cases.append(canonical(obj))
        for case in cases:
            with self.subTest(case=case[:70]),self.assertRaises(ValueError):loads(case)

    def test_forged_evaluation_rejected(self):
        sel=self.c.select((self.rows[0].meta.ref,),(self.rows[3].meta.ref,))
        wrong=FutureEvaluation(meta('evaluation','wrong'),sel,None,COMPILER,'{}')
        with self.assertRaisesRegex(FutureError,'future_evaluation_mismatch'):FutureCatalog(SCOPE,(*self.rows,wrong))

    def test_partial_scenario_retains_missing_choices(self):
        s=scenario(self.c,(self.rows[0].meta.ref,),(),complete=False)
        self.assertEqual(loads(dumps(s)),s)
        self.assertEqual(self.c.compile(s.selection)['coverage'],'partial')

    def test_every_selection_does_not_create_scenario(self):
        before=self.c.records
        for opt in self.rows[2:]:self.c.select((self.rows[0].meta.ref,),(opt.meta.ref,))
        self.assertEqual(self.c.records,before)


if __name__=='__main__':unittest.main()

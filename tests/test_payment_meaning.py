"""General synthetic source/continuation acceptance; no live model quality claim."""
from copy import deepcopy
import unittest
from pathlib import Path
import tempfile
from finvis.compound_payments import interpret_bundle, payment_basis_for, schema
from finvis.compound_meaning import CompoundMeaningError
from finvis.compound_future import plan
from finvis.simple_surface import SimpleSurface
from finvis.user_runtime import UserRuntime,runtime_location
from finvis.i3_workflow import I3Error
from tests.test_compound_workflow import BundleProvider
from tests.test_compound_future import claim,bundle


def price_case(zh=False,paid='240',quote='960',label=None):
    c='CNY' if zh else 'EUR'
    a=claim('past','定金' if zh else 'deposit',paid,currency=c,kind='actual_expense',source=('定金人民币'+paid+'已经付了' if zh else 'I paid EUR '+paid+' for a deposit'))
    b=claim('next',label or ('场地' if zh else 'studio'),quote,currency=c,source=('换到场地需'+quote+'元' if zh else 'switching to a studio would cost EUR '+quote))
    text=a['source']+('，' if zh else ', ')+b['source']+'.'
    q=dict(id='basis',claim_ids=['past','next'],field='price_basis',source=text,
      question='是包含定金的总价，还是额外追加金额？' if zh else 'Is this the full price including the deposit or an additional payment?',choices=['总价','追加金额'] if zh else ['Full price','Additional payment'])
    return text,bundle(a,b,ambiguities=[q])


def resolve(zh=False,answer='Full price',**kwargs):
    text,b=price_case(zh,**kwargs);p=BundleProvider([b]);first,md=interpret_bundle(p,text)
    out,md=interpret_bundle(p,text,prior=first,answer=dict(ambiguity_id='basis',text=answer),prior_ledger=md['evidence_ledger'])
    return text+'\n'+answer,out,md,p


class PaymentMeaningTests(unittest.TestCase):
    def test_en_zh_total_and_extra_same_quote_different_effect(self):
        from finvis.payment_amounts import payment_planning
        from finvis.compound_payments import _money
        for zh,answer,expected in [(False,'Full price',72000),(False,'Additional payment',96000),(True,'总价',72000),(True,'追加金额',96000)]:
            with self.subTest(zh=zh,answer=answer):
                text,b,md,p=resolve(zh,answer)
                basis=payment_basis_for(b,'next',text)
                self.assertEqual(payment_planning(_money(b['claims'][1]['money']),basis).amount.minor_units,expected)
                self.assertEqual(b['claims'],price_case(zh)[1]['claims']);self.assertEqual(p.calls,1)
                self.assertTrue(md['evidence_ledger']['complete'],md['evidence_ledger'])
                self.assertEqual(len(b['payment_resolutions']),1)
    def test_different_amounts_entities_are_not_case_specialized(self):
        from finvis.payment_amounts import payment_planning
        from finvis.compound_payments import _money
        text,b,_,_=resolve(False,'Total cost',paid='65',quote='430')
        self.assertEqual(payment_planning(_money(b['claims'][1]['money']),payment_basis_for(b,'next',text)).amount.minor_units,36500)
    def test_unrelated_negated_uncertain_answers_fail_closed(self):
        for answer in ['Thanks','maybe total','not the total','总价还是追加','不是总价','the additional payment is 780','是另一个','full price and extra payment']:
            text,b=price_case();p=BundleProvider([])
            with self.subTest(answer=answer),self.assertRaises(CompoundMeaningError):interpret_bundle(p,text,prior=b,answer=dict(ambiguity_id='basis',text=answer))
            self.assertEqual(p.calls,0)
    def test_multiple_paid_scope_cannot_choose_by_recency(self):
        text,b=price_case();other=claim('other','booking','20',currency='EUR',kind='actual_expense',source='I paid EUR 20 for booking')
        b['claims'].append(other);text+=' '+other['source'];b['ambiguities'][0]['source']=text
        with self.assertRaisesRegex(CompoundMeaningError,'payment_scope_unresolved'):
            interpret_bundle(BundleProvider([]),text,prior=b,answer=dict(ambiguity_id='basis',text='Full price'))
    def test_question_cannot_credit_unnamed_paid_item(self):
        text,b=price_case();b['ambiguities'][0]['question']='Full price including an unrelated payment or additional payment?'
        b['ambiguities'][0]['choices']=[]
        with self.assertRaises(CompoundMeaningError):interpret_bundle(BundleProvider([]),text,prior=b,answer=dict(ambiguity_id='basis',text='Full price'))
    def test_direct_total_source_and_allocation(self):
        text,b=price_case();b['ambiguities']=[]
        source='The studio total includes the entire deposit.';text+=' '+source
        row=dict(claim_id='next',basis='total',source=source,allocations=[dict(paid_claim_id='past',role='credited',money=b['claims'][0]['money'],amount_source=b['claims'][0]['source'],source=source)])
        out,md=interpret_bundle(BundleProvider([dict(version=2,meaning=b,payments=[row])]),text)
        self.assertEqual(payment_basis_for(out,'next',text).kind,'total')
    def test_invented_credit_negation_amount_and_currency_reject(self):
        text,b=price_case();b['ambiguities']=[];source='The studio total includes the entire deposit.';text+=' '+source
        row=dict(claim_id='next',basis='total',source=source,allocations=[dict(paid_claim_id='past',role='credited',money=deepcopy(b['claims'][0]['money']),amount_source=b['claims'][0]['source'],source=source)])
        for defect in ['source','money','currency','endpoint','role']:
            r=deepcopy(row)
            if defect=='source':r['allocations'][0]['source']='invented relationship'
            if defect=='money':r['allocations'][0]['money']['value']='200'
            if defect=='currency':r['allocations'][0]['money']['currency']='SGD'
            if defect=='endpoint':r['allocations'][0]['paid_claim_id']='next'
            if defect=='role':r['basis']='refund'
            with self.subTest(defect=defect),self.assertRaises((CompoundMeaningError,ValueError)):
                interpret_bundle(BundleProvider([dict(version=2,meaning=b,payments=[r])]),text)
    def test_frozen_v1_schema_unchanged_and_envelope_closed(self):
        from finvis.compound_meaning import bundle_schema
        self.assertNotIn('payments',bundle_schema()['properties']);self.assertEqual(schema()['properties']['version']['enum'],[2])
        text,b=price_case();raw=dict(version=2,meaning=b,payments=[],authorize=True)
        with self.assertRaises(CompoundMeaningError):interpret_bundle(BundleProvider([raw]),text)
    def test_same_pending_revision_simulation_and_stale_retry(self):
        for zh,answer,expected in [(False,'Full price',72000),(True,'追加金额',96000)]:
            text,b=price_case(zh);provider=BundleProvider([b]);location=runtime_location(Path(tempfile.mkdtemp(prefix='finvis-payment-workflow-')).resolve())
            rt=UserRuntime(provider,location);self.addCleanup(rt.close);w=rt.ensure()
            v=w.interpret(text,route=True)['routing'];before=w.store.snapshot
            args=dict(bundle_id=v['bundle_id'],revision=v['revision'],ambiguity_id=v['question']['id'],answer=answer)
            after=w.compound.answer(args)['compound'];self.assertEqual(after['revision'],2);self.assertEqual(after['bundle_id'],v['bundle_id'])
            p=w.compound.future(dict(bundle_id=after['bundle_id'],revision=2))['trial']
            result=SimpleSurface(rt.if_interaction).simulate(dict(proposal_id=p['proposal_id'],roots=p['selection']['roots'],options=p['selection']['options']))
            self.assertEqual(result['buckets'][0]['known_cost']['lower_minor'],expected)
            self.assertIs(w.store.snapshot,before);self.assertEqual(w.store.snapshot.revision,0)
            with self.assertRaisesRegex(I3Error,'compound_stale'):w.compound.answer(args)
            rt.close();rt2=UserRuntime(BundleProvider([]),location);self.addCleanup(rt2.close);w2=rt2.ensure()
            with self.assertRaisesRegex(I3Error,'compound_expired'):w2.compound.answer(args)
            self.assertEqual(w2.store.snapshot.revision,0)
    def direct(self,relation='The studio total includes the entire deposit.'):
        text,b=price_case();b['ambiguities']=[];text+=' '+relation
        row=dict(claim_id='next',basis='total',source=relation,allocations=[dict(paid_claim_id='past',role='credited',money=deepcopy(b['claims'][0]['money']),amount_source=b['claims'][0]['source'],source=relation)])
        return text,b,row
    def test_omitted_credit_cannot_become_full_future_quote(self):
        text,b,row=self.direct();row['allocations']=[]
        with self.assertRaisesRegex(CompoundMeaningError,'payment_credit_omitted'):
            interpret_bundle(BundleProvider([dict(version=2,meaning=b,payments=[row])]),text)
    def test_omitted_entire_payment_extension_triggers_coverage(self):
        text,b,_=self.direct();out,md=interpret_bundle(BundleProvider([b]),text)
        self.assertIn('unrepresented_payment_relationship',[i['code'] for i in md['evidence_ledger']['issues']])
    def test_partial_credit_cannot_borrow_full_paid_amount(self):
        for relation in ['The studio total includes only part of the deposit.','The studio total includes only EUR 50 of the deposit.']:
            text,b,row=self.direct(relation)
            with self.subTest(relation=relation),self.assertRaises(CompoundMeaningError):
                interpret_bundle(BundleProvider([dict(version=2,meaning=b,payments=[row])]),text)
    def test_negated_total_inclusion_not_authorized_by_full_answer(self):
        text,b=price_case();b['ambiguities'][0]['question']='Is this the full price not including the deposit or an additional payment?'
        with self.assertRaisesRegex(CompoundMeaningError,'payment_scope_unresolved'):
            interpret_bundle(BundleProvider([]),text,prior=b,answer=dict(ambiguity_id='basis',text='Full price'))
    def test_inclusion_on_wrong_question_branch_not_credit(self):
        text,b=price_case();b['ambiguities'][0]['question']='Is this the full price or an additional payment including the deposit?'
        with self.assertRaisesRegex(CompoundMeaningError,'payment_scope_unresolved'):
            interpret_bundle(BundleProvider([]),text,prior=b,answer=dict(ambiguity_id='basis',text='Full price'))
    def test_independent_identical_reports_have_distinct_payment_identity(self):
        text,a,_,_=resolve();_,b,_,_=resolve()
        self.assertNotEqual(payment_basis_for(a,'next',text).parts[0].identity,payment_basis_for(b,'next',text).parts[0].identity)
        self.assertEqual(payment_basis_for(a,'next',text),payment_basis_for(deepcopy(a),'next',text))
    def test_negated_refund_not_positive_inflow(self):
        paid=claim('past','booking','300',currency='EUR',kind='actual_expense',source='I paid EUR 300 for booking')
        refund=claim('next','booking refund','150',currency='EUR',kind='expected_inflow',source='booking refund will not be EUR 150')
        b=bundle(paid,refund);text=paid['source']+'. '+refund['source']
        b['payment_relationships']=[dict(claim_id='next',basis='refund',source=refund['source'],allocations=[dict(paid_claim_id='past',role='refundable',money=refund['money'],amount_source=refund['source'],source=refund['source'])])]
        with self.assertRaisesRegex(CompoundMeaningError,'payment_refund_negated'):payment_basis_for(b,'next',text)
    def test_withheld_fee_not_separate_future_cost(self):
        fee=claim('fee','cancellation fee','25',currency='EUR',source='cancellation fee EUR 25 will be deducted from the refund')
        b=bundle(fee);b['payment_relationships']=[dict(claim_id='fee',basis='cancellation_fee',source=fee['source'],allocations=[])]
        with self.assertRaisesRegex(CompoundMeaningError,'payment_withholding_requires_net_refund'):payment_basis_for(b,'fee',fee['source'])
    def test_inclusion_cannot_borrow_unrelated_clause_or_predicate(self):
        for relation in ['The studio total includes tuition. The deposit is refundable.','The studio total includes tuition and the deposit is refundable.']:
            text,b,row=self.direct(relation)
            with self.subTest(relation=relation),self.assertRaises(CompoundMeaningError):
                interpret_bundle(BundleProvider([dict(version=2,meaning=b,payments=[row])]),text)
    def test_pending_price_witness_survives_revalidation_without_model(self):
        text,b,md,p=resolve(True,'总价')
        original=deepcopy(b);effect=payment_basis_for(b,'next',text)
        self.assertEqual(payment_basis_for(original,'next',text),effect)
        self.assertEqual(p.calls,1)
        self.assertEqual(md['continuation'],'deterministic_payment_basis')
    def test_planned_total_cannot_stage_gross_as_new_obligation(self):
        text,b,row=self.direct();b['claims'][1]['kind']='planned_outflow'
        old=b['claims'][1]['source'];b['claims'][1]['source']='I plan to pay EUR 960 for studio';text=text.replace(old,b['claims'][1]['source'])
        provider=BundleProvider([dict(version=2,meaning=b,payments=[row])])
        rt=UserRuntime(provider,runtime_location(Path(tempfile.mkdtemp(prefix='finvis-payment-plan-')).resolve()));self.addCleanup(rt.close);w=rt.ensure()
        view=w.compound.interpret(text)['routing'];before=w.store.snapshot
        with self.assertRaisesRegex(I3Error,'compound_item_unavailable'):
            w.compound.stage(dict(bundle_id=view['bundle_id'],revision=view['revision'],claim_id='next'))
        self.assertIs(w.store.snapshot,before)

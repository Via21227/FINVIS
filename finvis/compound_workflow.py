"""Transient Compound Meaning coordination over existing I3/I4 and FS1.

This module owns no financial storage, command, consent or arithmetic. A pending
bundle can expire; a committed I4 operation never depends on its survival.
"""
from copy import deepcopy
from dataclasses import replace
from uuid import uuid4
import re

from .agent_providers import ProviderError
from .agent_schema import CandidateInterpretation, SCHEMA_VERSION, reconcile_candidate
from .agent_understanding import CandidateEnvelope
from .i3_workflow import I3Error, I3Workflow
from .reviewed_proposals import _wire
from .compound_payments import interpret_bundle
from .compound_meaning import CompoundMeaningError
from .compound_binding_support import timing_support

FINANCIAL = {'current_asset', 'actual_expense', 'actual_inflow', 'planned_outflow', 'refund', 'correction'}
FUTURE = {'future_outflow', 'expected_inflow', 'planned_outflow'}


class CompoundWorkflow:
    def __init__(self, workflow):
        self.w = workflow
        self.pending = {}
        self.active = None

    def _context(self, source):
        """Bounded labels/identities only. No full World or account balances sent."""
        read = self.w.authority.read()
        rows = []
        for ref in read.snapshot.world.members:
            record = read.snapshot.get(ref)
            label = next((getattr(record,k) for k in ('label','purpose','subject') if getattr(record,k,None)), None)
            if label and ref.record_kind in ('account','planned_expense','actual_occurrence','world_decision','future_inflow'):
                rows.append(dict(id=ref.ref.id,label=label,kind=ref.record_kind,ref=_wire(ref)))
        if hasattr(self.w, 'future_adoption'):
            rows += [dict(id=row['ref']['id'],label=row.get('title') or row.get('label'),kind='future_'+row['ref']['kind'],ref=row['ref'])
                     for row in self.w.future_adoption.x.listing()['records']
                     if row['ref']['kind'] in ('node','option')]
        terms = lambda s: set(re.findall(r'[a-z]{3,}|[\u4e00-\u9fff]{2}',s.lower()))
        rank = lambda row: (row['label'].lower() in source.lower(), len(terms(source)&terms(row['label'])))
        # Cloud receives only source-relevant labels, never unrelated holdings.
        if getattr(self.w.understanding._provider(), 'cloud', False):
            rows=self._provider_context(source,rows)
        return sorted(rows,key=rank,reverse=True)[:24]

    def _provider_context(self, source, context):
        """Recheck each send under the reserved provider, including continuation."""
        if not getattr(self.w.understanding._provider(), 'cloud', False):return context
        def mentioned(label):
            label=label.strip()
            if not label:return False
            # Latin labels need word edges: account A is not mentioned by 'paid'.
            left=r'(?<![A-Za-z0-9_])' if re.match(r'[A-Za-z0-9_]',label) else ''
            right=r'(?![A-Za-z0-9_])' if re.search(r'[A-Za-z0-9_]$',label) else ''
            return re.search(left+re.escape(label)+right,source,re.IGNORECASE) is not None
        return [row for row in context if mentioned(row['label'])][:24]

    def interpret(self, text, request_id=None, provider_call=None):
        if type(text) is not str or not text.strip() or len(text)>20000:
            raise ProviderError('invalid_input','Input must contain 1 to 20,000 characters')
        w=self.w; identity=request_id or uuid4().hex
        with w._lock:
            if type(identity) is not str or not identity or len(identity)>128 or identity in w._interpret_requests:
                raise I3Error('interpretation_request_invalid',400)
            if len(self.pending)>=64 or len(w._interpret_requests)>=4096:raise I3Error('session_capacity',503)
            if w._active_interpret_request:raise I3Error('interpretation_busy')
            I3Workflow.cancel(w)
            w._latest_authorization_id=None
            if self.active:self.invalidate(preserve_future=True)
            captured=w.authority.read().version
            context=[]
            w._interpret_requests[identity]=dict(status='active',candidate_id=None)
            w._active_interpret_request=identity
        try:
            with w._provider_gate:
                def run():
                    nonlocal context
                    context=self._context(text)
                    return interpret_bundle(w.understanding._provider(),text,context)
                bundle,metadata=provider_call(run) if provider_call else run()
            with w._lock:
                if w._active_interpret_request!=identity:raise ProviderError('interpretation_cancelled','Interpretation stopped')
                if w.authority.read().version!=captured:raise I3Error('candidate_stale')
                b=dict(id=identity,revision=1,text=text,original=text,bundle=bundle,metadata=metadata,
                       context=context,version=captured,cancelled=False,operations={},answers=[],future=None,lifecycle={},future_ids=[])
                self.pending[identity]=b;self.active=identity
                w._interpret_requests[identity]['status']='ready';w._active_interpret_request=None
                return {'routing':self.view(dict(bundle_id=identity))}
        except Exception as error:
            with w._lock:
                if w._interpret_requests[identity]['status']=='active':w._interpret_requests[identity]['status']='failed'
                if w._active_interpret_request==identity:w._active_interpret_request=None
            if isinstance(error,CompoundMeaningError):raise ProviderError('invalid_interpretation','Interpretation could not be verified') from None
            raise

    def _get(self,payload,revision=False):
        if type(payload) is not dict or type(payload.get('bundle_id')) is not str:raise I3Error('compound_request_invalid',400)
        b=self.pending.get(payload['bundle_id'])
        if (not b or b['cancelled'] or self.active!=b['id'] or
                self.w._interpret_requests.get(b['id'],{}).get('status')=='cancelled'):raise I3Error('compound_expired')
        if revision and (type(payload.get('revision')) is not int or payload['revision']!=b['revision']):raise I3Error('compound_stale')
        return b

    def observe_lifecycle(self, pending, operation):
        """Remember ownership only; this grants nothing and changes no command."""
        if not self.active:return
        b=self.pending[self.active]
        for cid,op in b['operations'].items():
            if op.get('lifecycle_id')==pending['id']:
                b['lifecycle'][operation.authorization_id]=dict(claim_id=cid,
                    auxiliary=pending['action'] in ('balance_review','save_plan','enable_gradual'))

    def _sync(self,b):
        owned={}
        for operation in self.w._operations.values():
            matched=next((cid for cid,op in b['operations'].items()
                if op.get('candidate_id') and operation.candidate_id==op['candidate_id']),None)
            marker=b['lifecycle'].get(operation.authorization_id)
            cid=matched or (marker['claim_id'] if marker else None)
            if cid and self.w._receipt(operation):
                result=self.w._status(operation)
                owned[result['receipt_revision']]=result
                if not marker or not marker['auxiliary']:
                    b['operations'][cid].update(complete=True,authorization_id=operation.authorization_id,receipt=result)
        b['owned_receipts']=owned
        for key,op in list(b['operations'].items()):
            if op.get('complete'):continue
            c=self.w.understanding.candidate
            if op.get('candidate_id') and (c is None or c.id!=op['candidate_id'] or c.status=='cancelled'):
                b['operations'].pop(key)
        return b

    def _fresh(self,b):
        self._sync(b)
        now=self.w.authority.read().version
        if now==b['version']:return
        interval=range(b['version'].revision+1,now.revision+1)
        if (now.session_generation==b['version'].session_generation and now.revision>b['version'].revision
                and all(revision in b['owned_receipts'] for revision in interval)):
            b['version']=now
            return
        raise I3Error('compound_world_changed')

    def invalidate(self, *, preserve_future=False):
        """Expire Meaning/Reality work; topic switches keep explored If drafts.

        Explicit Stop/cancel still retracts this bundle's hypothetical layer.
        Preserving a draft neither retains it nor grants financial authority.
        """
        if not self.active:return
        b=self.pending[self.active]
        for op in b['operations'].values():
            rid=op.get('request_id')
            if rid and rid in self.w._interpret_requests:self.w._interpret_requests[rid]['status']='cancelled'
            pending=self.w.plan_reality.pending
            if pending and pending['id']==op.get('lifecycle_id'):self.w.plan_reality.cancel({})
        if not preserve_future and hasattr(self.w,'compound_if'):
            for identity in b['future_ids']:
                # These are proposal registry entries, not durable records.
                self.w.compound_if.x.pending.pop(identity,None)
                self.w.compound_if.selections.pop(identity,None)
        b['cancelled']=True;self.active=None

    def _invalidate_future_answer(self,b):
        if not hasattr(self.w,'compound_if'):return
        old=b.get('future')
        if old and old.get('proposal_id') and self.w.compound_if.x.memory.lookup(old['proposal_id']):
            ack=self.w.compound_if.x.memory.lookup(old['proposal_id'])
            kept={(r['kind'],r['id'],r['version']) for r in ack['record_refs']}
            b.setdefault('retained_future_claims',set()).update(cid for cid,r in old.get('claim_options',{}).items()
                if (r['kind'],r['id'],r['version']) in kept)
        for identity in b['future_ids']:
            self.w.compound_if.x.pending.pop(identity,None)
            self.w.compound_if.selections.pop(identity,None)
        b['future']=None

    def _coverage_issues(self,b):
        return b['metadata'].get('evidence_ledger',{}).get('issues',[])

    def _question(self,b):
        issues=self._coverage_issues(b)
        if issues:
            issue=next((i for i in issues if i['code']=='unsupported_condition_polarity'),issues[0])
            prompts={
                'unsupported_condition_polarity':('If that does not happen, what would you still pay or receive?', '如果不发生，你还会支付或收到什么？'),
                'unresolved_payment_reference':('Which earlier payment does this price relate to?', '这个价格与之前哪笔付款有关？'),
                'unresolved_structure':('Which items belong to this event?', '哪些项目属于这件事？'),
                'external_outcome_requires_review':('What is the approval outcome, and what income is still expected?', '审批结果是什么？还有哪笔收入仍在预期中？'),
                'unresolved_condition':('What depends on what in this part?', '这部分，什么发生了才会影响另一件事？'),
                'unresolved_existing_reference':('Which earlier item does this refer to?', '这指的是之前哪一项？'),
                'unresolved_alternatives':('Are these separate possibilities or alternatives?', '这几项可以一起考虑，还是只能选一种？'),
                'unrepresented_request':('What would you like to find out about this?', '这部分，你想了解什么？'),
            }
            en,zh=prompts.get(issue['code'],('What does this part mean for your money?', '这部分具体是什么收支或打算？'))
            income_condition=issue['code']=='unresolved_condition' and len(issue['claim_ids'])==1 and any(c['id']==issue['claim_ids'][0] and c['kind']=='expected_inflow' for c in b['bundle']['claims'])
            if income_condition:en,zh='What must happen for this income to arrive?', '这笔收入到账需要满足什么条件？'
            return dict(id='coverage:'+str(issue['start'])+':'+issue['code'],field='understanding',
                claim_ids=(issue['claim_ids'] if income_condition or issue['code'] in ('unresolved_structure','external_outcome_requires_review') else [c['id'] for c in b['bundle']['claims']]),
                source=(issue['source'] if income_condition or issue['code'] in ('unresolved_structure','external_outcome_requires_review') else b['text'][max(0,issue['start']-56):min(len(b['text']),issue['end']+56)]),
                question={'en':en,'zh-CN':zh},choices=[])
        questions=b['bundle']['ambiguities']
        if questions:return questions[0]
        from .compound_premises import structural_parents
        parents=structural_parents(b['bundle'])
        for c in b['bundle']['claims']:
            if c['id'] not in parents and c['money']['currency'] is None and c['kind'] in FINANCIAL | FUTURE:
                zh=bool(re.search('[\u4e00-\u9fff]',b['original']))
                return dict(id='currency:'+c['id'],claim_ids=[c['id']],field='currency',source=c['source'],
                    question=(c['label']+'用什么货币？' if zh else 'Which currency is '+c['label']+' in?'),
                    choices=['CNY','SGD','EUR'])
        # Payment source remains a material local question. It is not inferred
        # merely because only one account currently exists.
        for c in b['bundle']['claims']:
            if c['kind'] in ('actual_expense','actual_inflow') and c.get('account') is None and not b['operations'].get(c['id'],{}).get('complete'):
                zh=bool(re.search('[\u4e00-\u9fff]',b['original']))
                return dict(id='account:'+c['id'],claim_ids=[c['id']],field='account',source=c['source'],
                    question=(c['label']+'用的是哪个账户？' if zh else 'Which account was used for '+c['label']+'?'),
                    choices=[r['label'] for r in self._context(b['text']) if r['kind']=='account'])
        return None

    def _dependent_reality(self,b):
        # Existing single-occurrence review cannot authorize a relationship
        # between financial mutations. Preserve it, never discard it to Apply.
        financial={c['id'] for c in b['bundle']['claims'] if c['kind'] in FINANCIAL}
        blocked=set()
        for r in b['bundle']['relationships']:
            if r['kind'] in ('alternative','conditional_on','part_of','replacement'):
                if r['from'] in financial and r['to'] in financial:
                    blocked.update((r['from'],r['to']))
                elif r['kind']=='conditional_on' and r['from'] in financial:
                    blocked.add(r['from'])
        return blocked

    def view(self,payload):
        with self.w._lock:
            b=self._sync(self._get(payload));q=self._question(b)
            blocked={c for a in b['bundle']['ambiguities'] for c in a['claim_ids']} | self._dependent_reality(b)
            if q:blocked.update(q['claim_ids'])
            claims=[]
            for c in b['bundle']['claims']:
                op=b['operations'].get(c['id'],{})
                target=next((r for r in b['context'] if r['id']==c.get('target')),None)
                future=c['kind'] in FUTURE or (c['kind']=='correction' and target and target['kind'].startswith('future_'))
                flow='future' if future else 'review' if c['kind'] in FINANCIAL-{'correction'} else None
                state='complete' if op.get('complete') else 'staged' if op else 'unsupported' if c['id'] in self._dependent_reality(b) else 'blocked' if c['id'] in blocked else 'unsupported' if flow is None else 'pending'
                claims.append(dict(deepcopy(c),state=state,flow=flow))
            active=next((key for key,op in b['operations'].items() if not op.get('complete')),None)
            return dict(kind='compound',stage='understood',limited_understanding=bool(self._coverage_issues(b)),bundle_id=b['id'],revision=b['revision'],source=b['original'],
                claims=claims,relationships=deepcopy(b['bundle']['relationships']),requests=deepcopy(b['bundle']['requests']),
                question=deepcopy(q),future_available=not self._coverage_issues(b) and any(c['flow']=='future' and c['id'] not in blocked for c in claims),
                active_review=active,limitations=['transfer_not_supported'] if any(c['kind']=='transfer' for c in claims) else [])

    def answer(self,payload,provider_call=None):
        if set(payload)!={'bundle_id','revision','ambiguity_id','answer'} or type(payload['answer']) is not str or not 0<len(payload['answer'].strip())<=1000:
            raise I3Error('compound_request_invalid',400)
        with self.w._lock:
            b=self._get(payload,True);self._fresh(b);q=self._question(b)
            if not q or q['id']!=payload['ambiguity_id']:raise I3Error('compound_stale')
            if any(not op.get('complete') for op in b['operations'].values()):raise I3Error('compound_review_active')
            answer=payload['answer'].strip();revision=b['revision'];version=b['version']
            if q['id'].startswith('currency:'):
                codes={'cny':'CNY','rmb':'CNY','人民币':'CNY','sgd':'SGD','新币':'SGD','eur':'EUR','欧元':'EUR'}
                currency=codes.get(answer.casefold())
                if currency is None:raise I3Error('compound_answer_unresolved',400)
                c=next(c for c in b['bundle']['claims'] if c['id']==q['claim_ids'][0])
                self._invalidate_future_answer(b)
                c['money']['currency']=currency
                b['answers'].append(dict(question=q['question'],answer=answer,claim_ids=q['claim_ids']))
                b['metadata'].setdefault('evidence_ledger',{}).setdefault('answer_support',[]).append(dict(start=len(b['text'])+1,end=len(b['text'])+1+len(answer),claim_ids=q['claim_ids']))
                b['text']+='\n'+answer;b['revision']+=1
                return {'compound':self.view(dict(bundle_id=b['id']))}
            if q['id'].startswith('account:'):
                c=next(c for c in b['bundle']['claims'] if c['id']==q['claim_ids'][0])
                self._invalidate_future_answer(b)
                c['account']=answer
                b['answers'].append(dict(question=q['question'],answer=answer,claim_ids=q['claim_ids']))
                b['metadata'].setdefault('evidence_ledger',{}).setdefault('answer_support',[]).append(dict(start=len(b['text'])+1,end=len(b['text'])+1+len(answer),claim_ids=q['claim_ids']))
                b['text']+='\n'+answer;b['revision']+=1
                return {'compound':self.view(dict(bundle_id=b['id']))}
            prior=deepcopy(b['bundle']);context=deepcopy(b['context']);source=b['text']
        try:
            with self.w._provider_gate:
                def run():
                    nonlocal context
                    context=self._provider_context(source+'\n'+answer,context)
                    return interpret_bundle(self.w.understanding._provider(),source,context,prior=prior,answer=dict(ambiguity_id=q['id'],text=answer),coverage_question=q if q['id'].startswith('coverage:') else None,prior_ledger=b['metadata'].get('evidence_ledger'))
                resolved,metadata=provider_call(run) if provider_call else run()
            with self.w._lock:
                self._get(dict(bundle_id=b['id'],revision=revision),True)
                if self.w.authority.read().version!=version:raise I3Error('compound_world_changed')
                if any(a['id']==q['id'] for a in resolved['ambiguities']):raise I3Error('compound_answer_unresolved',400)
                self._invalidate_future_answer(b)
                b['bundle']=resolved;b['metadata']=metadata;b['text']+='\n'+answer;b['revision']+=1;b['future']=None
                b['answers'].append(dict(question=q['question'],answer=answer,claim_ids=q['claim_ids']))
                return {'compound':self.view(dict(bundle_id=b['id']))}
        except CompoundMeaningError:raise I3Error('compound_answer_unresolved',400) from None

    def stage(self,payload):
        if set(payload)!={'bundle_id','revision','claim_id'}:raise I3Error('compound_request_invalid',400)
        with self.w._lock:
            b=self._get(payload,True);self._fresh(b)
            claim=next((c for c in self.view(dict(bundle_id=b['id']))['claims'] if c['id']==payload['claim_id']),None)
            if claim is None or claim['state'] in ('blocked','unsupported') or claim['kind'] not in FINANCIAL:raise I3Error('compound_item_unavailable',400)
            if claim['kind']=='planned_outflow':
                from .compound_payments import payment_basis_for
                if payment_basis_for(b['bundle'],claim['id'],b['text']) is not None:
                    # Use the basis-preserving Future adoption preview, never
                    # stage the original gross quote as a fresh obligation.
                    raise I3Error('compound_item_unavailable',400)
            previous=b['operations'].get(claim['id'])
            if previous:
                if previous.get('complete'):return {'compound':self.view(dict(bundle_id=b['id'])),'operation':previous['receipt']}
                if self.w.understanding.candidate and self.w.understanding.candidate.id==previous.get('candidate_id'):
                    return {'review':self.w.view(),'compound':self.view(dict(bundle_id=b['id']))}
                if previous.get('routing'):
                    return {'routing':self.w.plan_reality.inspect(self.w.plan_reality.proposals[previous['lifecycle_id']]),'compound':self.view(dict(bundle_id=b['id']))}
            if any(not op.get('complete') for op in b['operations'].values()):raise I3Error('compound_review_active')
            if claim['kind']=='correction':raise I3Error('compound_item_unavailable',400)
            I3Workflow.cancel(self.w);self.w._latest_authorization_id=None
            target=next((c for c in b['context'] if c['id']==claim.get('target') and c['kind']=='planned_expense'),None)
            if claim.get('target') and claim['kind'] in ('actual_expense','actual_inflow','refund') and (target is None or claim['kind']=='actual_inflow'):
                raise I3Error('compound_item_unavailable',400)
            if target is None and claim['kind'] in ('actual_expense','refund'):
                matches=[c for c in b['context'] if c['kind']=='planned_expense' and c['label'].casefold()==claim['label'].casefold()]
                target=matches[0] if len(matches)==1 else None
            if claim['kind'] in ('refund','actual_expense','actual_inflow'):
                rid='compound-stage/'+uuid4().hex
                self.w._interpret_requests[rid]=dict(status='ready',candidate_id=None)
                self.w.reality_routing.active_request=rid
                support=timing_support(b['text'],claim)
                excerpt=b['text'][support['scope'][0]:support['scope'][1]] if support else claim['source']
                source=excerpt+'\n'+'\n'.join(a['answer'] for a in b['answers'] if claim['id'] in a['claim_ids'])
                routed=self.w.plan_reality.from_claim(source.strip(),self.w.authority.read(),rid,
                    {'refund':'refund','actual_inflow':'inflow','actual_expense':'actual'}[claim['kind']],
                    claim['money'],target['id'] if target else None,claim['account'],claim['label'],claim['timing'])
                if routed:
                    b['operations'][claim['id']]=dict(lifecycle_id=routed['id'],request_id=rid,routing=routed,complete=False)
                    return {'routing':routed,'compound':self.view(dict(bundle_id=b['id']))}
                raise I3Error('compound_item_unavailable',400)
            fact_type={'planned_outflow':'planned_expense'}.get(claim['kind'],claim['kind'])
            money=claim['money']
            raw=dict(schema_version=SCHEMA_VERSION,language='zh-CN' if re.search('[\u4e00-\u9fff]',b['original']) else 'en',
                user_intents=['update_financial_state'],default_currency=None,candidate_facts=[dict(
                    id=claim['id'],type=fact_type,label=claim['label'],account=claim['account'],
                    amount=dict(currency=money['currency'],precision=money['precision'],value=money['value'],range_min=money['lower'],range_max=money['upper']),
                    earmark=None,purpose=None,temporal_status={'current_asset':'current','planned_expense':'planned'}.get(fact_type,'actual'),
                    timing_note=claim['timing']['text'],source='user_report')],destructive_actions=[],assumptions=[],uncertainties=[],conflicts=[],clarification_questions=[],unsupported_requests=[],user_question=None,interpretation_notes=[])
            interpretation=CandidateInterpretation.parse(raw)
            read=self.w.authority.read();candidate_id='compound/'+b['id']+'/'+claim['id']+'/'+str(b['revision'])
            env=CandidateEnvelope(candidate_id,interpretation,reconcile_candidate(interpretation),
                b['metadata'].get('provider','compound'),b['metadata'].get('model'),b['metadata'].get('latency_ms'),read.version.session_generation,read.version.revision)
            self.w.understanding.candidate=env;self.w._text=b['text']
            result=self.w.prepare(env.id)
            b['operations'][claim['id']]=dict(candidate_id=candidate_id,complete=False)
            return {'review':result,'compound':self.view(dict(bundle_id=b['id']))}

    def future(self,payload):
        if (type(payload) is not dict or not {'bundle_id','revision'} <= set(payload) or
                set(payload)-{'bundle_id','revision','proposal_id','roots','options'}):
            raise I3Error('compound_request_invalid',400)
        from .compound_future import plan
        with self.w._lock:
            b=self._get(payload,True);self._fresh(b)
            if self._coverage_issues(b):raise I3Error('compound_understanding_incomplete',400)
            if not hasattr(self.w,'compound_if'):raise I3Error('compound_item_unavailable')
            refs={c['id']:c['ref'] for c in b['context'] if c['kind'].startswith('future_')}
            old=b.get('future')
            retained=old and old.get('proposal_id') and self.w.compound_if.x.memory.lookup(old['proposal_id'])
            identity=old['proposal_id'] if retained else 'compound-future/'+b['id']+'/'+str(b['revision'])+'/'+str(b['version'].revision)
            requested=[c['id'] for c in b['bundle']['claims'] if c['id'] not in b.get('retained_future_claims',set())]
            # Resolved hypothetical scope changes the trial, not the preserved
            # claims or another request. The source-bound witness is validated
            # by the production envelope before it reaches this coordinator.
            for witness in b['metadata'].get('evidence_ledger',{}).get('answer_support',[]):
                if witness.get('rule')=='scoped_request_selection':
                    excluded=set(witness['original_request']['claim_ids'])-set(witness['claim_ids'])
                    requested=[cid for cid in requested if cid not in excluded]
            # Bind the first explicit workspace selection to this preparation.
            # A transport retry must not re-add the bundle over its own result.
            basis=b.get('future_basis')
            if basis is None:
                basis={key:deepcopy(payload[key]) for key in ('proposal_id','roots','options') if key in payload}
            result=plan(b['bundle'],b['text'],self.w.compound_if,claim_ids=requested,operation_id=identity,context_refs=refs,**basis)
            b['future_basis']=basis
            if result.get('proposal_id') not in b['future_ids']:b['future_ids'].append(result.get('proposal_id'))
            b['future']=result
            return {'trial':result,'compound':self.view(dict(bundle_id=b['id']))}

    def cancel(self,payload):
        if set(payload)!={'bundle_id'}:raise I3Error('compound_request_invalid',400)
        with self.w._lock:
            b=self._get(payload);self._sync(b)
            if any(not op.get('complete') for op in b['operations'].values()):I3Workflow.cancel(self.w)
            self.invalidate()
            return dict(stage='cancelled')

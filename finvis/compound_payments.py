"""Production payment relationships over the frozen v1 Meaning contract.

One bounded provider interpretation (none for an exact price-choice answer).
Source-grounded descriptors are not settled cash or authorization. The frozen
benchmark binder/schema/prompt remain independently replayable.
"""
from copy import deepcopy
from hashlib import sha256
import json
import re
from uuid import uuid4

from . import compound_binding as legacy
from .compound_meaning import (_object, _array, _string, _enum, _shape,
    _json_pairs, _error, _source, _contains, bundle_schema as v1_schema)
from .compound_binding_support import evidence_ledger
from .clarification_obligations import price_questions, reconcile_ledger, resolve_request

PRICE = re.compile(r'\b(?:total|full|whole)\b|总价|全价|总共|合计', re.I)
EXTRA = re.compile(r'\b(?:additional|extra|top.up|incremental|upgrade fee)\b|补差|追加|另外|额外|加付', re.I)
INCLUDE = re.compile(r'\b(?:includ\w*|credit\w*|count\w* towards?)\b|包含|包括|计入|抵扣|抵价', re.I)
REFUND = re.compile(r'\b(?:refund\w*|return\w*)\b|退回|退还|可退|退款', re.I)
NONREFUND = re.compile(r'\b(?:non.refundable|no refund|not refundable|cannot be refunded)\b|不退|不可退|不能退|不退款', re.I)
FEE = re.compile(r'\bcancellation fee\b|取消费|退订费|退票费|违约金', re.I)
NEGATE = re.compile(r'\b(?:not|never|maybe|perhaps|unsure|uncertain|dont|don.t|cannot|can.t)\b|不是|不含|不包括|未包含|不确定|不知道|可能|也许|还是|或者|[？?]', re.I)


def schema():
    money = deepcopy(v1_schema()['properties']['claims']['items']['properties']['money'])
    allocation = _object(dict(paid_claim_id=_string(64), role=_enum(('paid','credited','refundable','nonrefundable')),
        money=money, amount_source=_string(2048), source=_string(4096)))
    payment = _object(dict(claim_id=_string(64), basis=_enum(('total','additional','refund','cancellation_fee')),
        source=_string(4096), allocations=_array(allocation,16)))
    return _object(dict(version=dict(type='integer',enum=[2]),meaning=v1_schema(),payments=_array(payment,16)))


PROMPT = '''\nPreserve explicit life-event/component membership using part_of. A non-priced structural parent has unknown money and no invented currency; never count it as another expense. When membership is material but unclear, ask one scoped relationship question and preserve all known claims. Do not invent membership from proximity. For an expected inflow with a nonfinancial approval condition, keep the complete literal condition and any denial/unknown status in that inflow's source; do not invent a financial approval claim or claim approval occurred. Selection only assumes receipt. Unsupported conditions require scoped clarification.
Production payment envelope v2: return {version:2, meaning:<unchanged v1 Meaning schema>, payments:[...]}. Preserve each original money quote in meaning; never replace a quoted total by calculated remainder. Payment relationships are separate, never authoritative.
For a source-explicit total quote use basis total; only explicitly included already-paid portions get credited allocations. Additional/upgrade amounts use additional (do not deduct past payment again). A possible explicit refund is an expected_inflow and basis refund, not actual refund. A separately payable cancellation fee uses cancellation_fee; a fee never determines a refund. Non-refundable portions are annotations, not extra future costs. Unpaid deposit and later balance are separate future claims, not historical payments. Keep conditions on each effect in meaning.relationships.
Each payment row: claim_id, basis, source (literal supporting excerpt), allocations. Each allocation: paid_claim_id (an actual_expense claim), role paid/credited/refundable/nonrefundable, money (same schema as claim money), amount_source (literal amount excerpt), source (literal relationship excerpt naming BOTH endpoints). You may use the original paid amount source for a full allocation only when the relationship explicitly supports the entire payment. Never invent missing refund, credit, fee or amount. Unknown stays unknown. Do not choose total/additional if unclear; ask one scoped ambiguity with both choices. No payments row is better than an unsupported relationship. No client authorization or historical-balance coverage exists in this envelope.'''


def _read(raw):
    if isinstance(raw,str):
        if len(raw)>131072:_error('output_size')
        try:return json.loads(raw,object_pairs_hook=_json_pairs,parse_constant=lambda _: _error('json_nonfinite'))
        except (ValueError,RecursionError):_error('malformed_json')
    return deepcopy(raw)


def _base(bundle):
    result=deepcopy(bundle)
    result.pop('payment_relationships',None);result.pop('payment_resolutions',None);result.pop('payment_scope',None)
    return result


def payment_claim_identity(claim, scope=""):
    data={k:claim[k] for k in ('id','source','money')}
    data['scope']=scope
    return 'meaning-payment/'+sha256(json.dumps(data,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def _money(m):
    from .decision_baseline import amount
    return amount(dict(currency=m['currency'],precision=m['precision'],value=m['value'],range_min=m['lower'],range_max=m['upper']))


def _unique(bundle,cid):
    claims=[c for c in bundle['claims'] if c['id']==cid]
    if len(claims)!=1:_error('payment_claim_identity')
    return claims[0]


def _scope(question,bundle):
    """A question is a proposal, never proof: only one named paid/price pair."""
    wording=question['question']+' '+' '.join(question['choices'])
    if not PRICE.search(wording) or not EXTRA.search(wording):return None
    future=[c for c in bundle['claims'] if c['id'] in question['claim_ids'] and c['kind'] in ('future_outflow','planned_outflow') and c['source'] in question['source']]
    paid=[c for c in bundle['claims'] if c['kind']=='actual_expense' and c['source'] in question['source']]
    if len(future)!=1 or len(paid)!=1:return None
    branches=re.split(r'\bor\b|\bvs\.?\b|还是|或者',question['question'],flags=re.I)
    total_branches=[part for part in branches if PRICE.search(part) and not EXTRA.search(part)]
    if len(branches)!=2 or len(total_branches)!=1:return None
    total_branch=total_branches[0].strip(' ?？')
    if NEGATE.search(total_branch) or not _contains(total_branch,paid[0]['label']) or not INCLUDE.search(total_branch):return None
    return future[0],paid[0]


def _answer_basis(answer, paid):
    from .amount_basis_answer import amount_basis_answer
    return amount_basis_answer(answer, paid)


def _resolution_basis(bundle,row,text):
    from .payment_amounts import PaymentBasis,PaymentPart
    if set(row)!= {'claim_id','paid_claim_id','basis','question','answer'}:_error('payment_resolution_shape')
    q=row['question'];_shape(q,v1_schema()['properties']['ambiguities']['items'])
    _source(q['source'],text,'payment.question.source');_source(row['answer'],text,'payment.answer')
    scope=_scope(q,bundle)
    if not scope or scope[0]['id']!=row['claim_id'] or scope[1]['id']!=row['paid_claim_id'] or _answer_basis(row['answer'], scope[1])!=row['basis']:_error('payment_resolution_binding')
    future,paid=scope
    identity=payment_claim_identity(paid,bundle.get('payment_scope',''));money=_money(paid['money'])
    parts=[PaymentPart(identity,'paid',money,paid['source'])]
    if row['basis']=='total':parts.append(PaymentPart(identity,'credited',money,paid['source']))
    return PaymentBasis(row['basis'],tuple(parts),json.dumps(dict(question=q,answer=row['answer']),ensure_ascii=False,sort_keys=True))


def _require_allocations(bundle,row,text):
    claim=_unique(bundle,row['claim_id'])
    from .compound_binding_support import windows
    for _,_,fragment in windows(text):
        if not _contains(fragment,claim['label']):continue
        for paid in bundle['claims']:
            if paid['kind']!='actual_expense' or not _contains(fragment,paid['label']):continue
            roles={a['role'] for a in row['allocations'] if a['paid_claim_id']==paid['id']}
            if row['basis']=='total' and INCLUDE.search(fragment) and not NEGATE.search(fragment) and 'credited' not in roles:_error('payment_credit_omitted')
            if row['basis']=='refund' and REFUND.search(fragment) and not NONREFUND.search(fragment) and 'refundable' not in roles:_error('payment_refund_omitted')
            if NONREFUND.search(fragment) and 'nonrefundable' not in roles:_error('payment_nonrefund_omitted')
            if row['basis']=='additional' and EXTRA.search(fragment) and not roles:_error('payment_historical_link_omitted')


def _direct_basis(bundle,row,text):
    from .payment_amounts import PaymentBasis,PaymentPart
    _shape(row,schema()['properties']['payments']['items'])
    claim=_unique(bundle,row['claim_id']);src=row['source'];kind=row['basis']
    _source(src,text,'payment.source')
    if not _contains(src,claim['label']):_error('payment_endpoint_binding')
    if claim['kind'] not in (('expected_inflow',) if kind=='refund' else ('future_outflow','planned_outflow')):_error('payment_role_binding')
    cue={'total':PRICE,'additional':EXTRA,'refund':REFUND,'cancellation_fee':FEE}[kind]
    if not cue.search(src):_error('payment_basis_binding')
    if kind!='refund' and NEGATE.search(src):_error('payment_basis_negated')
    denial=re.compile(r"\b(?:not|never|no|cannot|can't|won't)\b|不退|不可|不能|不会|并非|不是|没有",re.I)
    if kind=='refund' and denial.search(src):_error('payment_refund_negated')
    withheld=re.compile(r'\b(?:withheld|deducted|net of|after.*fee)\b|扣除|扣掉|扣费|从退款.{0,8}扣',re.I)
    net=re.compile(r'\bnet refund\b|实际退回|净退款|扣费后退',re.I)
    from .compound_binding_support import windows
    for _,_,fragment in windows(text):
        if _contains(fragment,claim['label']) and withheld.search(fragment):
            if kind!='refund' or not net.search(src):_error('payment_withholding_requires_net_refund')
    if PRICE.search(src) and EXTRA.search(src):_error('payment_basis_ambiguous')
    # Completeness: emitting a total row is not enough when it omits the
    # explicitly included historical component. No amount-based matching.
    _require_allocations(bundle,row,text)
    parts=[];seen=set()
    for a in row['allocations']:
        paid=_unique(bundle,a['paid_claim_id']);relation=a['source']
        if paid['kind']!='actual_expense':_error('payment_not_historical')
        _source(relation,text,'payment.allocation.source');_source(a['amount_source'],text,'payment.allocation.amount_source')
        if not _contains(relation,paid['label']) or not _contains(relation,claim['label']):_error('payment_endpoint_binding')
        role=a['role']
        if role=='paid':
            if a['money']!=paid['money'] or a['amount_source']!=paid['source']:_error('payment_paid_descriptor')
        pat={'paid':re.compile(r'paid|deposit|已付|押金|定金|订金',re.I),'credited':INCLUDE,'refundable':REFUND,'nonrefundable':NONREFUND}[role]
        if not pat.search(relation):_error('payment_allocation_binding')
        from .compound_binding_support import windows
        linked=[fragment for _,_,fragment in windows(relation) if _contains(fragment,paid['label']) and _contains(fragment,claim['label']) and pat.search(fragment)]
        if len(linked)!=1:_error('payment_allocation_clause')
        relation_clause=linked[0]
        if role=='credited':
            # Inclusion must govern the payment, not an unrelated noun in the
            # same sentence. Bound the intervening phrase to money/modifiers.
            marks=list(INCLUDE.finditer(relation_clause));names=list(re.finditer(re.escape(paid['label']),relation_clause,re.I))
            governed=False
            for mark in marks:
                for name in names:
                    if name.start()>=mark.end():gap=relation_clause[mark.end():name.start()]
                    elif name.end()<=mark.start():gap=relation_clause[name.end():mark.start()]
                    else:continue
                    gap=re.sub(r'\b(?:the|a|an|entire|full|whole|all|already|paid|of|original|previous|only|part|is|was|be|been|has|will|amount|CNY|EUR|SGD)\b|人民币|欧元|新币|的|全额|全部|已付|已经|支付|部分|这笔|计入|被|将|已|是|\d+(?:[.,]\d+)?|[\s,，:：-]','',gap,flags=re.I)
                    if not gap:governed=True
            if not governed:_error('payment_credit_scope')
        if role=='credited' and NEGATE.search(relation):_error('payment_allocation_negated')
        if role=='refundable' and denial.search(relation):_error('payment_allocation_negated')
        probe=deepcopy(paid);probe['money']=a['money'];probe['source']=a['amount_source']
        legacy._validate_money(probe,text,{},'payment.allocation')
        # An amount-bearing excerpt must belong to the explicit relationship,
        # or to the exact full original payment described by that relationship.
        if a['amount_source'] not in relation:
            if a['money']!=paid['money'] or a['amount_source']!=paid['source'] or not re.search(r'\b(?:full|whole|entire|all|deposit)\b|全部|全额|整笔|押金|定金|订金',relation,re.I):_error('payment_allocation_amount_scope')
            if re.search(r'\b(?:part|portion|half|only|fraction|percent)\b|部分|一半|仅|只|%',relation,re.I):_error('payment_partial_allocation_unbound')
            from .compound_meaning import _number_evidence
            supported={v for excerpt in (claim['source'],paid['source']) for v,_,_ in _number_evidence(excerpt)}
            if any(v not in supported for v,_,_ in _number_evidence(relation)):_error('payment_allocation_amount_scope')
        identity=payment_claim_identity(paid,bundle.get('payment_scope',''))
        if identity not in seen:
            parts.append(PaymentPart(identity,'paid',_money(paid['money']),paid['source']));seen.add(identity)
        if role!='paid':parts.append(PaymentPart(identity,role,_money(a['money']),relation+'\n'+a['amount_source']))
    return PaymentBasis(kind,tuple(parts),src)


def payment_basis_for(bundle,claim_id,text):
    direct=[r for r in bundle.get('payment_relationships',[]) if r['claim_id']==claim_id]
    resolved=[r for r in bundle.get('payment_resolutions',[]) if r['claim_id']==claim_id]
    if len(direct)+len(resolved)>1:_error('payment_duplicate_basis')
    if direct:return _direct_basis(bundle,direct[0],text)
    if resolved:return _resolution_basis(bundle,resolved[0],text)
    return None


def _validate(bundle,text):
    from .payment_amounts import payment_planning
    for key in ('payment_relationships','payment_resolutions'):
        rows=bundle.get(key,[])
        if type(rows) is not list or len(rows)>16:_error('payment_size')
        for row in rows:
            claim=_unique(bundle,row['claim_id'])
            try:
                basis=payment_basis_for(bundle,claim['id'],text)
                payment_planning(_money(claim['money']),basis)
            except legacy.CompoundMeaningError:raise
            except ValueError:_error('payment_arithmetic_invalid')


def _ledger(bundle,text,context,bindings=()):
    base=_base(bundle);ledger=evidence_ledger(base,text,context,bindings)
    # Only the exact previously scoped prospective-price ambiguity has now
    # gained a source/answer-bound role. No general coverage exception.
    scopes={r['claim_id'] for r in bundle.get('payment_resolutions',[])}
    ledger['issues']=[i for i in ledger['issues'] if not (i['code']=='unresolved_authority_scope' and i['claim_ids'] and set(i['claim_ids'])<=scopes)]
    # A legacy envelope cannot silently discard an explicit paid/quote relation.
    paid=[c for c in bundle['claims'] if c['kind']=='actual_expense']
    questions={cid for q in bundle['ambiguities'] for cid in q['claim_ids']}
    represented={r['claim_id'] for r in bundle.get('payment_relationships',[])}|scopes
    for c in bundle['claims']:
        if not paid or c['kind'] not in ('future_outflow','planned_outflow','expected_inflow') or c['id'] in questions|represented:continue
        # Only explicit relational wording naming this object and a paid object
        # is a witness; unrelated total words elsewhere do not create a link.
        for fragment in re.split(r'[;；。\n]',text):
            if not _contains(fragment,c['label']) or not any(_contains(fragment,p['label']) for p in paid):continue
            relational=(INCLUDE.search(fragment) and PRICE.search(fragment)) or EXTRA.search(fragment) or (c['kind']=='expected_inflow' and REFUND.search(fragment))
            if relational:
                a=text.find(fragment);ledger['issues'].append(dict(code='unrepresented_payment_relationship',start=a,end=a+len(fragment),source=fragment,claim_ids=[c['id']]))
                break
    missing,issues=price_questions(bundle,text,_scope)
    for row in issues:
        a=text.find(row['source']);ledger['issues'].append(dict(row,start=a,end=a+len(row['source'])))
    for q in missing:
        a=text.find(q['source']);ledger['issues'].append(dict(code='unresolved_payment_reference',start=a,end=a+len(q['source']),source=q['source'],claim_ids=q['claim_ids']))
    ledger['payment_relationship_version']=1
    return reconcile_ledger(bundle,text,ledger,bindings)


def interpret_bundle(provider,text,context=(),prior=None,answer=None,coverage_question=None,prior_ledger=None):
    prior=deepcopy(prior)
    if prior is not None and isinstance(answer,dict) and not coverage_question:
        q=next((a for a in prior['ambiguities'] if a['id']==answer.get('ambiguity_id')),None)
        resolved=resolve_request(prior,q,answer.get('text',''),text,prior_ledger) if q else None
        if resolved:
            result,bindings=resolved;evidence=text+'\n'+answer['text'];_validate(result,evidence)
            return result,dict(provider=getattr(provider,'name','local'),model=None,latency_ms=0,continuation='deterministic_request_reference',evidence_ledger=_ledger(result,evidence,context,bindings))
        scope=_scope(q,prior) if q else None
        if q and PRICE.search(q['question']) and EXTRA.search(q['question']) and not scope:_error('payment_scope_unresolved')
        if scope:
            basis=_answer_basis(answer.get('text'), scope[1])
            if not basis:_error('clarification_answer_unrelated')
            result=deepcopy(prior);result.setdefault('payment_scope',uuid4().hex);result['ambiguities']=[a for a in result['ambiguities'] if a['id']!=q['id']]
            if any(r['claim_id']==scope[0]['id'] for r in result.get('payment_relationships',[])+result.get('payment_resolutions',[])):_error('payment_duplicate_basis')
            result.setdefault('payment_resolutions',[]).append(dict(claim_id=scope[0]['id'],paid_claim_id=scope[1]['id'],basis=basis,question=q,answer=answer['text']))
            evidence=text+'\n'+answer['text'];_validate(result,evidence)
            bindings=deepcopy((prior_ledger or {}).get('answer_support',[]))+[dict(start=len(text)+1,end=len(evidence),claim_ids=[scope[0]['id']])]
            return result,dict(provider=getattr(provider,'name','local'),model=None,latency_ms=0,continuation='deterministic_payment_basis',evidence_ledger=_ledger(result,evidence,context,bindings))
    captured={}
    class Adapter:
        def complete_explanation(self,prompt,payload,unused_schema):
            raw,metadata=provider.complete_explanation(prompt+PROMPT,payload,schema())
            value=_read(raw)
            if type(value) is dict and value.get('version')==2:
                _shape(value,schema());captured['payments']=value['payments'];value=value['meaning']
            # Explicit legacy v1 compatibility; no inferred payment extension.
            return value,metadata
    base,metadata=legacy.interpret_bundle(Adapter(),text,context,prior=_base(prior) if prior else None,answer=answer,coverage_question=coverage_question,prior_ledger=prior_ledger)
    evidence=text+('\n'+answer['text'] if isinstance(answer,dict) else '')
    if captured.get('payments'):
        base['payment_relationships']=captured['payments'];base['payment_scope']=prior.get('payment_scope',uuid4().hex) if prior else uuid4().hex
    if prior:
        old=prior.get('payment_relationships',[])
        if captured.get('payments') is not None:
            if coverage_question:
                if any(r not in captured['payments'] for r in old):_error('clarification_changed_payment_relationship')
            elif captured['payments']!=old:_error('clarification_changed_payment_relationship')
        if old and not captured.get('payments'):base['payment_relationships']=deepcopy(old)
        if prior.get('payment_resolutions'):base['payment_resolutions']=deepcopy(prior['payment_resolutions'])
        if prior.get('payment_scope'):base['payment_scope']=prior['payment_scope']
    _validate(base,evidence)
    questions,_=price_questions(base,evidence,_scope)
    # Preserve a model's useful price question, but make its missing paid
    # referent explicit before the existing answer path can credit anything.
    originals=[]
    for generated in questions:
        future,paid=_scope(generated,base)
        for old in list(base['ambiguities']):
            ids=set(old['claim_ids'])
            if (ids in ({future['id']},{future['id'],paid['id']})
                    and old['source'].strip(' .。') in generated['source']
                    and PRICE.search(old['question']) and EXTRA.search(old['question'])
                    and not INCLUDE.search(old['question'])):
                originals.append(deepcopy(old));base['ambiguities'].remove(old)
    if originals:metadata['clarification_questions_refined']=originals
    base['ambiguities'].extend(questions)
    metadata['evidence_ledger']=_ledger(base,evidence,context,metadata['evidence_ledger'].get('answer_support',[]))
    return base,metadata

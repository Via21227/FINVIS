"""Source-bound clarification obligations; no new financial facts or arithmetic.

A scoped question suspends meaning; it cannot authorize an effect. The frozen
model contract and evaluator remain unchanged. All witnesses use original spans.
"""
from copy import deepcopy
from hashlib import sha256
import re
from .compound_meaning import _contains, _error
from .compound_binding_support import windows

CHANGE = re.compile(r'\b(?:switch\w*|upgrad\w*|replac\w*)\b|换到|换成|更换|升级|改成', re.I)
DEPOSIT = re.compile(r'\bdeposit\b|定金|订金|押金', re.I)
BASIS = re.compile(r'\b(?:total|full price|additional|extra|upgrade fee|remaining|balance due)\b|总价|全价|额外|追加|补差|剩余|尾款', re.I)
FEE_REFUND = re.compile(r'\b(?:refund|cancellation fee|cancel\w* fee)\b|退款|退还|取消费|退订费|违约金', re.I)
CONDITION = re.compile(r'\b(?:if|unless|provided that|only when)\b|如果|假如|要是|只要|除非', re.I)
NEGATIVE = re.compile(r"\b(?:not|never|without|unless|wouldn't|won't|don't|doesn't|didn't|cannot|can't)\b|不|没|未|除非", re.I)


def request_question(q, bundle):
    """Only an exact choice among local financial referents, not any question."""
    if q.get('field') not in ('target','request.claim_ids','request_target'):return None
    claims=[c for c in bundle['claims'] if c['id'] in q['claim_ids']]
    if len(claims)<2 or len(claims)!=len(q['claim_ids']):return None
    labels=[c['label'] for c in claims]
    if len(set(labels))!=len(labels) or any(c['kind'] not in ('future_outflow','planned_outflow') for c in claims):return None
    if sorted(q['choices'])!=sorted(labels):return None
    if not all(_contains(q['question'],label) for label in labels):return None
    requests=[r for r in bundle['requests'] if r['kind'] in ('simulate','compare') and r['source']==q['source'] and set(r['claim_ids'])==set(q['claim_ids'])]
    if len(requests)!=1:return None
    # A specific new noun/amount is not resolved merely by supplied choices.
    if re.search(r'\d',q['source']) or not re.search(r'\b(?:it|this|that|which)\b|哪个|那个|这个|它',q['source'],re.I):return None
    if not re.search(r'\?|？|呢',q['source']):return None
    return requests[0]


def resolve_request(bundle, question, answer, text, prior_ledger):
    req=request_question(question,bundle)
    if req is None:return None
    choices=[c for c in bundle['claims'] if c['id'] in req['claim_ids']]
    raw=answer.strip().strip('.。');clean=re.sub(r'^(?:I mean (?:the )?|The |我说的是|是|选)', '',raw,flags=re.I).strip()
    selected=[c for c in choices if clean.casefold()==c['label'].casefold()]
    if len(selected)!=1:_error('clarification_answer_unrelated')
    result=deepcopy(bundle);target=next(r for r in result['requests'] if r==req)
    target['claim_ids']=[selected[0]['id']]
    result['ambiguities']=[q for q in result['ambiguities'] if q['id']!=question['id']]
    bindings=deepcopy((prior_ledger or {}).get('answer_support',[]))
    bindings.append(dict(start=len(text)+1,end=len(text)+1+len(answer),claim_ids=target['claim_ids'],
        rule='scoped_request_selection',original_request=deepcopy(req),question=deepcopy(question),answer=answer))
    return result,bindings


def price_questions(bundle,text,can_resolve):
    """Ask about an evidenced paid/quote link, never infer recovery or credit."""
    represented={r['claim_id'] for k in ('payment_relationships','payment_resolutions') for r in bundle.get(k,[])}
    paid=[c for c in bundle['claims'] if c['kind']=='actual_expense']
    additions=[];issues=[]
    for c in bundle['claims']:
        if c['kind'] not in ('future_outflow','planned_outflow') or c['id'] in represented:continue
        src=c['source']
        # A model's excerpt can begin after the replacement verb. Recover only
        # its containing source sentence, never a recent conversation object.
        if not CHANGE.search(src):
            containing=[fragment for _,_,fragment in windows(text) if src in fragment and CHANGE.search(fragment)]
            if len(containing)==1:src=containing[0]
        if FEE_REFUND.search(src) or not CHANGE.search(src):continue
        # Explicit unambiguous basis isn't a missing price-basis question.
        if BASIS.search(src) and not NEGATIVE.search(src) and not re.search(r'\b(?:or|unsure|unclear)\b|还是|不确定|不知道',src,re.I):continue
        if NEGATIVE.search(src):continue  # unsupported negative effects stay held below
        links=[]
        for p in paid:
            a=text.find(p['source']);b=text.find(src)
            if a<0 or b<0:continue
            direct=any(r['kind']=='replacement' and r['from']==c['id'] and r['to']==p['id'] for r in bundle['relationships'])
            named=_contains(src,p['label'])
            # A unique immediately preceding deposit plus an explicit upgrade
            # frame is a candidate relationship, NOT a credit allocation.
            gap=text[a+len(p['source']):b] if a<b else None
            adjacent=gap is not None and bool(re.fullmatch(r'[\s,;，；.。]*(?:and\s+|then\s+|然后|现在)?',gap,re.I))
            same_sentence=p['source'] in src and DEPOSIT.search(p['source'])
            if direct or named or same_sentence or (adjacent and DEPOSIT.search(p['source'])):
                links.append(p)
        if not links:continue
        # A source-explicit relationship omitted by the model is a coverage
        # defect, not a new ambiguity; do not let a generated question mask it.
        if any(_contains(fragment,c['label']) and any(_contains(fragment,p['label']) for p in links)
               and BASIS.search(fragment) and not re.search(r'\b(?:or|unsure|unclear)\b|还是|不确定|不知道',fragment,re.I)
               for _,_,fragment in windows(text)):continue
        if any(c['id'] in q['claim_ids'] and can_resolve(q,bundle) for q in bundle['ambiguities']):continue
        if len(links)!=1:
            issues.append(dict(code='unresolved_payment_reference',source=src,claim_ids=[c['id']]))
            continue
        p=links[0];a=min(text.find(p['source']),text.find(src));end=max(text.find(p['source'])+len(p['source']),text.find(src)+len(src));quote=text[a:end]
        # A source span with another paid fact cannot license selecting one.
        if sum(q['source'] in quote for q in paid)!=1:
            issues.append(dict(code='unresolved_payment_reference',source=src,claim_ids=[c['id']]))
            continue
        zh=bool(re.search('[\u4e00-\u9fff]',text))
        question=(f"这是包含{p['label']}的总价，还是额外付款？" if zh else f"Is this the total including {p['label']}, or an additional payment?")
        ident='obligation-price-'+sha256((c['id']+'\0'+p['id']+'\0'+quote).encode()).hexdigest()[:16]
        q=dict(id=ident,field='price_basis',claim_ids=[c['id']],source=quote,question=question,choices=['总价','追加金额'] if zh else ['Full price','Additional payment'])
        if can_resolve(q,bundle):additions.append(q)
        else:issues.append(dict(code='unresolved_payment_reference',source=src,claim_ids=[c['id']]))
    return additions,issues


def reconcile_ledger(bundle,text,ledger,bindings=()):
    """Replace only a proved reference gap with its still-blocking question."""
    covered=[]
    for q in bundle['ambiguities']:
        if request_question(q,bundle):covered.append(q['source'])
    for witness in bindings:
        if witness.get('rule')!='scoped_request_selection':continue
        q=witness['question'];original=witness['original_request'];prior=deepcopy(bundle)
        current=[r for r in bundle['requests'] if r['kind']==original['kind'] and r['source']==original['source']]
        if len(current)!=1:_error('request_resolution_binding')
        prior['requests']=[original if r==current[0] else r for r in prior['requests']]
        result=resolve_request(prior,q,witness['answer'],text[:witness['start']-1],{})
        if result is None or result[0]['requests']!=bundle['requests'] or text[witness['start']:witness['end']]!=witness['answer']:_error('request_resolution_binding')
        covered.append(q['source'])
    punct=' .!?。！？'
    ledger['issues']=[i for i in ledger['issues'] if not (i['code'] in ('unrepresented_financial_statement','unresolved_request_reference') and any(i['source'].strip(punct)==s.strip(punct) for s in covered))]
    for start,end,window in windows(text):
        if CONDITION.search(window) and NEGATIVE.search(window) and any(_contains(window,c['label']) for c in bundle['claims']):
            if not any(i['code']=='unsupported_condition_polarity' and i['start']==start for i in ledger['issues']):
                ledger['issues'].append(dict(code='unsupported_condition_polarity',start=start,end=end,source=window,claim_ids=[c['id'] for c in bundle['claims'] if _contains(window,c['label'])]))
    ledger['complete']=not ledger['issues'];return ledger

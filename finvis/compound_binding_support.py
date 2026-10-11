"""Source-derived support for the current binder, never authority or model truth.

v1.1 support remains byte-frozen for the original provider comparison. Every
exception here needs a bounded grammatical witness; no corpus IDs or money values.
"""
import re
from copy import deepcopy
from . import compound_meaning as old
from .compound_support import (COORD, CONDITION, SENTENCE, occurrences,
    evidence_ledger as legacy_ledger, answer_supports_resolution as legacy_answer)

_TIME = re.compile(r'\b(?:yesterday|today|just|last week|next month|tomorrow)\b|\d{4}-\d{2}(?:-\d{2})?|刚刚|刚才|刚|昨天|今天|明天|下个月', re.I)
_SWITCH = re.compile(r'\b(?:but|however|instead|not|never|if|unless|havent|hasnt|didnt)\b|但是|不过|没|未|不是|并非|如果|要是|假如|改成',re.I)


def windows(text):
    # A period after an amount is punctuation when followed by whitespace and
    # a non-digit sentence, including Chinese. Decimal points remain inside.
    boundary=re.compile(SENTENCE.pattern+r'|(?<=\d)\.(?=\s+[^\d\s])')
    start=0
    for match in boundary.finditer(text):
        if text[start:match.start()].strip():yield start,match.start(),text[start:match.start()]
        start=match.end()
    if text[start:].strip():yield start,len(text),text[start:]


def coordination_support(text, claim):
    """License only immediate coordination, never whole-paragraph inheritance."""
    cm=old
    spans=occurrences(text,claim['source']);choices=[]
    for a,b in spans:
        containing=next(((s,e,t) for s,e,t in windows(text) if s<=a and b<=e+1),None)
        if not containing:continue
        start,end,window=containing;prefix=text[start:a]
        times=list(_TIME.finditer(window))
        if times:
            lead=window[:times[0].start()].strip().casefold()
            if lead not in ('','i','we','我','我们') or len(times)>1:continue
            if claim['kind'] in cm.ACTUAL_KINDS and re.search(r'tomorrow|next month|明天|下个月',times[0].group(),re.I):continue
        # A full source has no inherited evidence. A separate sentence or a
        # topic/authority switch cannot donate a role or a denomination.
        if not prefix.strip() or len(window)>512 or not COORD.search(prefix):continue
        if re.search(r'\b(?:but|however|instead)\b|但是|不过|改成',prefix,re.I):continue
        if CONDITION.search(window) or cm._NEGATED_ACTUAL.search(window):continue
        currencies=cm._currencies(window)
        role_matches={k for k,v in cm._ROLE_EVIDENCE.items() if v.search(prefix)}
        # Refund/inflow lexical overlap is not a license to inherit either.
        role=claim['kind'] if role_matches=={claim['kind']} else None
        if cm._FUTURE.search(prefix) and claim['kind'] in cm.ACTUAL_KINDS|{'current_asset'}:role=None
        prefix_currencies=cm._currencies(prefix)
        currency=next(iter(currencies)) if len(currencies)==1 and prefix_currencies==currencies else None
        choices.append(dict(direct=[a,b],support=[start,a],currency=currency,role=role))
    if not choices:return None
    if len({(c['currency'],c['role']) for c in choices})!=1:return None
    return choices[0]


def timing_support(text, claim):
    """Only a leading time modifier over one immediate coordinated predicate.

    A local predicate/time, contrast, negation, topic switch or duplicate quote
    prevents inheritance. ISO/date validation still runs in the binder.
    """
    literal=claim['timing']['text']
    direct=occurrences(text,claim['source'])
    if not literal or literal in claim['source'] or len(direct)!=1:return None
    support=coordination_support(text,claim)
    if not support:return None
    start,a=support['support'];b=direct[0][1];prefix=text[start:a]
    window=text[start:b]
    tokens=list(_TIME.finditer(prefix));local=list(_TIME.finditer(claim['source']))
    if len(tokens)!=1 or local or tokens[0].group().casefold()!=literal.casefold() or _SWITCH.search(window):return None
    before=prefix[:tokens[0].start()].strip()
    if before.casefold() not in ('','i','we','我','我们'):return None
    if old._ACTUAL.search(claim['source']) or old._FUTURE.search(claim['source']):return None
    if len(list(old._ACTUAL.finditer(prefix)))>1:return None
    same_role=support['role']==claim['kind']
    future=(claim['kind'] in ('future_outflow','expected_inflow') and old._FUTURE.search(prefix) and not old._ACTUAL.search(prefix))
    if not same_role and not future:return None
    if claim['kind'] in old.ACTUAL_KINDS and re.search(r'tomorrow|next month|明天|下个月',literal,re.I):return None
    return dict(direct=list(direct[0]),support=[start+tokens[0].start(),start+tokens[0].end()],
                scope=[start,b],kind='shared_time',rule='leading_time_immediate_coordination')


def refund_report(source):
    """Positive reported return TO the speaker, not a prospective/reversed one."""
    match=re.search(r'退(?:还)?给我(?=\s*(?:人民币|[A-Z]{3}\b|[¥￥€\d一二两三四五六七八九十百千万]|了|[，,。.!]|$))',source)
    if re.search(r'不是|并非|没有|还没|尚未|是否|吗|[？?]',source):return False
    if not match:return False
    prefix=source[:match.start()]
    if re.search(r'没|未|不|将|会|要|准备|预计|明天|可能|假如|如果|要是',prefix):return False
    return True


def plan_support(source):
    """Allow one duplicated letter in an explicit first-person commitment frame.

    No edit-distance search. Negated completion may follow a positive commitment;
    a negated commitment, hypothetical or similarity alone never licenses Plan.
    """
    if old._HYPOTHETICAL.search(source):return None
    match=re.search(r'\b(?:I|we)\s+([a-z]+)\s+to\s+(buy|order|book|purchase|pay)\b',source,re.I)
    if not match:return None
    token=match[1].lower();verbs={'decided','planned','intended'}
    candidates={token[:i]+token[i+1:] for i in range(1,len(token)) if token[i]==token[i-1]} & verbs
    normalized=token if token in verbs else next(iter(candidates)) if len(candidates)==1 else None
    if normalized is None:return None
    # Negation of the commitment must never be hidden before the local frame.
    if re.search(r'\b(?:not|never|didn.t|didnt)\b',source[:match.end()],re.I):return None
    if old._NEGATED_ACTUAL.search(source) or re.search(r'\b(?:havent|hasnt|didnt)\b',source,re.I):
        tail=source[match.end():]
        if not re.search(r"[,;]?\s*but\s+(?:I\s+)?(?:haven'?t|have not)\s+paid\s+yet[.!]?\s*$",tail,re.I):return None
    return dict(span=[match.start(1),match.end(1)],literal=match[1],normalized=normalized,
                rule='explicit_commitment_frame_single_duplicate_letter' if normalized!=token else 'explicit_commitment_frame')


def request_cue(kind,source):
    if old._REQUEST_EVIDENCE[kind].search(source):return True
    # A local hypothetical QUERY, not any occurrence of the word 要是.
    return kind=='simulate' and bool(re.search(r'(?:要是|假如).+(?:呢[？?]?|[？?])\s*$',source)) and not re.search(r'[,，;；]|就|才|否则',source)


def request_referent(request,bundle,text):
    """Resolve only a closed elliptical question to a unique adjacent proposition.

    Explicit new nouns, amounts, two eligible objects or an intervening sentence
    do not get hidden by an emitted request's claim_ids.
    """
    source=request['source'].strip()
    if request['kind']!='simulate':return None
    if not re.fullmatch(r'(?:What if (?:I|we) (?:buy|choose|take) (?:it|that|this)[?]?|(?:如果|假如|要是)(?:我)?(?:买|选)(?:它|那个|这个)(?:呢)?[？?]?)',source,re.I):return None
    spans=occurrences(text,request['source'])
    if len(spans)!=1:return None
    a,b=spans[0];prior=[(s,e,w) for s,e,w in windows(text) if e<=a]
    if not prior:return None
    start,end,_=prior[-1]
    candidates=[c for c in bundle['claims'] if c['kind'] in ('future_outflow','planned_outflow') and
                any(start<=x and y<=end+1 for x,y in occurrences(text,c['source']))]
    if len(candidates)!=1 or request['claim_ids']!=[candidates[0]['id']]:return None
    return dict(claim_ids=request['claim_ids'],direct=[a,b],support=[start,end],rule='unique_adjacent_elliptical_request')


def prospective_price(claim):
    source=claim['source']
    return (claim['kind']=='future_outflow' and
        not old._ACTUAL.search(source) and not _SWITCH.search(source) and
        bool(re.search(r'\b(?:switch|upgrade|replace|change)\w*\b.+\b(?:costs?|would|will)\b|(?:换到|换成|升级).+(?:要|需|得|花)',source,re.I)))


def price_question(question, bundle):
    """A scoped total-vs-extra question is not a chosen price interpretation."""
    claims={c['id']:c for c in bundle['claims']}
    affected=[claims[c] for c in question['claim_ids'] if c in claims]
    source=question['source'];wording=question['question']+' '+' '.join(question['choices'])
    dual=bool(re.search(r'\b(?:total|full|whole)\b|总价|全价',wording,re.I) and re.search(r'\b(?:additional|extra|top.up|incremental)\b|之外|补差|追加|另外',wording,re.I))
    return dual and any(prospective_price(c) and c['source'] in source for c in affected) and any(
        c['kind']=='actual_expense' and c['source'] in source and re.search(r'\bdeposit\b|订金|定金|押金',c['source'],re.I) for c in bundle['claims'])


def evidence_ledger(bundle,text,context=(),answer_support=()):
    ledger=legacy_ledger(bundle,text,context,answer_support)
    ledger['version']='source-support-reconciliation-1'
    for entry,claim in zip(ledger['claims'],bundle['claims']):
        entry['licensed']=coordination_support(text,claim)
        entry['timing_support']=timing_support(text,claim)
        entry['plan_support']=plan_support(claim['source']) if claim['kind']=='planned_outflow' else None
    references=[r for req in bundle['requests'] for r in [request_referent(req,bundle,text)] if r]
    ledger['request_support']=references
    from .calendar_literals import calendar_spans
    dates=calendar_spans(text)
    from .compound_discourse import nonpayment_witnesses, membership_witness, membership_gaps
    statuses=nonpayment_witnesses(bundle,text)
    ledger['nonpayment_support']=statuses
    from .compound_discourse import planned_membership_support
    ledger['planned_membership_support']=[w for c in bundle['claims'] for w in [planned_membership_support(c,bundle,text)] if w]
    ledger['membership_support']=[w for r in bundle['relationships'] for w in [membership_witness(r,{c['id']:c for c in bundle['claims']},text)] if w]
    clean=[]
    for issue in ledger['issues']:
        if issue['code']=='unrepresented_financial_statement' and any(issue['start']==w['direct'][0] and issue['end']==w['direct'][1] for w in statuses):continue
        if issue['code']=='uncovered_amount' and any(a<=issue['start'] and issue['end']<=b for a,b,_,_ in dates):continue
        if issue['code']=='unrepresented_financial_statement' and any(
            issue['source'].strip(' .!?。！？') == text[slice(*r['direct'])].strip(' .!?。！？') for r in references):continue
        if issue['code']=='unresolved_authority_scope':
            affected=[c for c in bundle['claims'] if c['id'] in issue['claim_ids']]
            if affected and all(prospective_price(c) and any(c['id'] in q['claim_ids'] and price_question(q,bundle) for q in bundle['ambiguities']) for c in affected):continue
        clean.append(issue)
    def add(code,source,ids):
        spans=occurrences(text,source)
        if spans:
            a,b=spans[0];row=dict(code=code,start=a,end=b,source=source,claim_ids=list(ids))
            if not any(i['code']==code and i['start']==a for i in clean):clean.append(row)
    for request in bundle['requests']:
        # An elliptical request must not cover a third/new claim or pick recency
        # from a multi-object sentence. Existing literal scoped requests remain.
        if request['kind']=='simulate' and re.search(r'\b(?:it|this|that)\b|它|那个|这个',request['source'],re.I):
            if not any(c['source'] in request['source'] or old._contains(request['source'],c['label']) for c in bundle['claims'] if c['id'] in request['claim_ids']) and not request_referent(request,bundle,text):
                add('unresolved_request_reference',request['source'],request['claim_ids'])
    for rel in bundle['relationships']:
        if rel['kind']=='alternative' and re.search(r'\b(?:not|never)\s+(?:an?\s+)?alternative(?:s)?\s+to\b|不是.{1,60}的替代(?:选项|方案)',rel['source'],re.I):
            add('contradicted_relationship',rel['source'],[rel['from'],rel['to']])
    # Trailing conditions are not unconditional income/cost just because the
    # whole quote contains a supported amount. Preserve the quote and hold the
    # bundle unless an existing financial antecedent is explicitly represented.
    claims={c['id']:c for c in bundle['claims']}
    condition_rows={**old._context(context),**claims}
    from .compound_premises import external_premise, structure_gaps, outcome_conflict, CONDITION, existing_selection_refund
    premises=[p for c in bundle['claims'] for p in [external_premise(c,text)] if p]
    ledger['external_premises']=premises
    for c in bundle['claims']:
        if c['kind'] not in ('future_outflow','expected_inflow','planned_outflow'):continue
        if any(p['claim_id']==c['id'] for p in premises):continue
        if c['kind']=='expected_inflow' and outcome_conflict(c,text):
            add('external_outcome_requires_review',c['source'],[c['id']]);continue
        if c['kind']=='expected_inflow' and CONDITION.search(c['source']) and not existing_selection_refund(bundle,c,text) and not any(rel['kind']=='conditional_on' and rel['from']==c['id'] for rel in bundle['relationships']):
            add('unresolved_condition',c['source'],[c['id']]);continue
        condition=re.search(r'\b(?:if|provided that|only when)\b|如果|只要|前提是',c['source'],re.I)
        if not condition or condition.start()==0:continue
        prefix=c['source'][:condition.start()]
        if re.search(r'\bwhat\s*$',prefix,re.I):continue
        if not old._contains(prefix,c['label']):continue
        tail=c['source'][condition.end():]
        supported=any(rel['kind']=='conditional_on' and rel['from']==c['id'] and
            rel['to'] in condition_rows and old._contains(tail,str(condition_rows[rel['to']].get('label') or old._target_label(condition_rows[rel['to']])))
            for rel in bundle['relationships'])
        if not supported:add('unresolved_condition',c['source'],[c['id']])
    # Remove only a condition gap proved by the same exact claim witness.
    clean=[i for i in clean if not (i['code']=='unresolved_condition' and i['claim_ids'] and
        all(any(p['claim_id']==cid and i['source'].strip() in p['claim_source'] for p in premises) for cid in i['claim_ids']))]
    clean.extend(structure_gaps(bundle,text))
    clean.extend(membership_gaps(bundle,text))
    rows=old._context(context)
    for c in bundle['claims']:
        if c['target'] is None and len(rows)>1 and re.fullmatch(r'(?:那个|这个|它|it|that|this)',c['label'],re.I):
            if not any(c['id'] in q['claim_ids'] and re.search(r'target|reference|object',q['field'],re.I) for q in bundle['ambiguities']):
                add('unresolved_existing_reference',c['source'],[c['id']])
    ledger['issues']=clean;ledger['complete']=not clean
    return ledger


def answer_supports_resolution(selected,answer,old_bundle,new_bundle):
    from .compound_premises import relationship_answer_supported
    if selected.get('field') in ('relationship','relationships','structure','parent','part_of','relationships.part_of') and ({c['id']:c for c in old_bundle['claims']}!={c['id']:c for c in new_bundle['claims']} or any(r not in new_bundle['relationships'] for r in old_bundle['relationships'])):return False
    if relationship_answer_supported(selected,answer,old_bundle,new_bundle):return True
    if price_question(selected,old_bundle):
        probe=deepcopy(selected);probe['field']='price_basis'
        return legacy_answer(probe,answer,old_bundle,new_bundle)
    return legacy_answer(selected,answer,old_bundle,new_bundle)

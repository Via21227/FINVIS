"""Finite source-grounded follow-ups to existing Future objects.

This adapter only prepares the existing timing/alternative actions. Unsupported
leftovers remain with Compound Meaning; no provider output or new facts guessed.
"""
import re
from copy import deepcopy
from decimal import Decimal
from .calendar_literals import MONTHS
from .future_timing import parse_timing, DATE_TEXT

_DATE = r'(?:'+'|'.join(MONTHS)+r')(?:\s+\d{4})?|'+DATE_TEXT
_PAYMENT = r'(?:(?:I|we)\s+)?(?:(?:would|will|plan to)\s+)?pay\s+for\s+'
_UNSAFE = r'\b(?:paid|bought|received|refund|cancel|unless|if|not|never|cannot|can.t)\b|已经|已付|退款|取消|如果|除非|不(?:会|付|是)|没'


def _base(text,operation):
    return dict(status='clarify',layer='future',operation=operation,target=None,candidates=[],
        payload=None,question=None,evidence=[],clauses=[],source_text=text,
        source_spans=[dict(start=0,end=len(text),text=text)],bounded_followup=True,
        read_only=True,financial_authority=False)


def _options(node,space):
    variables=[r for r in space['records'] if r['ref']['kind']=='variable' and r.get('node')==node['ref']]
    options=[r for r in space['records'] if r['ref']['kind']=='option' and r.get('variable') in [v['ref'] for v in variables]
        and len(r.get('effects',[]))==1 and not r['effects'][0].get('absence')]
    return variables,options


def _defer(text):
    # The complete request must go through the established Compound path. Do
    # not let an incomplete follow-up fall back to a generic unknown-price If.
    return dict(_base(text,None),status='unhandled',bounded_followup=False,compound_required=True)


def _closed_name(name,row,payload):
    """Require coverage of the entire noun phrase, not a title substring."""
    from .interaction_meaning import _normal,NOUNS
    value=_normal(name).strip()
    value=re.sub(r'^(?:the|a|an)\s+','',value)
    names=[row.get('title') or row.get('label') or '']
    names.extend(a.get('name','') for a in payload.get('aliases',[]) if isinstance(a,dict) and a.get('ref')==row['ref'])
    if any(value==_normal(n).strip() for n in names):return True
    # Category-only shorthand is safe only after existing unique-target
    # resolution. Extra clauses, adjectives, dates and actors do not disappear.
    return any(re.fullmatch(pattern,value,re.I) and re.search(pattern,names[0],re.I) for pattern in NOUNS.values())


def _target(name,objects,payload):
    from .interaction_meaning import retrieve_targets
    found=retrieve_targets(name,objects,aliases=payload.get('aliases'))
    rows=found['candidates']
    # An explicit answer may only narrow the original offered candidates.
    reviewed=payload.get('reviewed_target')
    if reviewed and reviewed in [r['ref'] for r in rows]:
        rows=[r for r in rows if r['ref']==reviewed];found=dict(found,ambiguous=False)
    if payload.get('reviewed_name'):
        named=retrieve_targets(payload['reviewed_name'],rows,aliases=payload.get('aliases'))
        if len(named['candidates'])==1 and not named['ambiguous']:return named['candidates'],False
    return rows,found['ambiguous']


def _question(base,rows,key='target'):
    return dict(base,candidates=[r['ref'] for r in rows],question=dict(key=key,choices=[dict(ref=r['ref'],label=r.get('title') or r.get('label')) for r in rows]))


def _timing_clauses(text):
    raw=text.strip().rstrip('.。')
    if re.search(_UNSAFE,raw,re.I):return None
    # Shared predicate is licensed only by an explicit payment clause. Commas
    # between amount digits never split a supported date-only statement.
    en=re.match(_PAYMENT,raw,re.I)
    parts=re.split(r'\s*[,，;；]\s*(?:and\s+)?|\s+and\s+',raw,flags=re.I)
    result=[]
    for index,part in enumerate(parts):
        match=None
        if en:
            body=re.sub(r'^'+_PAYMENT,'',part,flags=re.I)
            match=re.fullmatch(r'(?P<name>.+?)\s+(?:in|on)\s+(?P<date>'+_DATE+r')',body,re.I)
        else:
            match=re.fullmatch(r'(?:我(?:会|将|打算)?(?:在)?)?(?P<date>'+_DATE+r')(?:付|支付|付款)(?P<name>.+)',part,re.I)
            if not match:
                match=re.fullmatch(r'(?P<name>.+?)(?:在)?(?P<date>'+_DATE+r')(?:付|支付|付款)',part,re.I)
        if not match:return None
        name=match['name'].strip();when=match['date'].strip()
        if re.search(r'\d|[€$¥]|\b(?:cost|price|and|or)\b|或者|和',name,re.I):return None
        result.append((name,when))
    return result if 1<=len(result)<=8 else None


def _timings(payload,space,objects,context,clauses):
    base=_base(payload['text'],'timing');updates=[];seen=set()
    for name,when in clauses:
        rows,ambiguous=_target(name,objects,payload)
        if len(rows)!=1 or ambiguous:return _question(base,rows)
        n=rows[0]
        if not _closed_name(name,n,payload):return _defer(payload['text'])
        variables,options=_options(n,space)
        reviewed=payload.get('reviewed_target')
        named=[o for o in options if o['ref']==reviewed] if reviewed else []
        selected=[o for o in options if o['ref'] in (context or {}).get('selected',[])]
        if named:options=named
        elif len(selected)==1:options=selected
        else:
            replaced=[o.get('timing_replaces') for o in options if o.get('timing_replaces')]
            options=[o for o in options if o['ref'] not in replaced]
        if len(options)!=1:return _question(base,options,'money_option')
        if options[0]['ref']['id'] in seen:return _question(base,[], 'separate_clauses')
        seen.add(options[0]['ref']['id'])
        try:
            timing=parse_timing(when)
        except ValueError:return _question(base,[],'money_date')
        if timing.precision=='unknown':return _question(base,[],'money_date')
        updates.append(dict(node=n['ref'],option=options[0]['ref'],timing=when))
    return dict(base,status='supported',question=None,target=updates[0]['node'],
        evidence=[dict(kind='explicit_payment_clauses',refs=[r['node'] for r in updates])],
        payload=dict(kind='timing_batch',text=payload['text'],timings=updates))


def _alternative_parts(text):
    raw=text.strip().rstrip('.。')
    # Exclusion wording is explicit source evidence, not negated purchase.
    if re.search(r'\b(?:but|and|or|when|after|before|provided|subject|because|while)\b|但是|而且|或者|之后|之前',raw,re.I):return None
    raw=re.sub(r',?\s*not an additional [a-z ]+$','',raw,flags=re.I)
    raw=re.sub(r'[，,]?\s*(?:而)?不是额外的?[^，。]+$','',raw)
    if re.search(_UNSAFE,raw,re.I):return None
    m=re.fullmatch(r'(?P<new>.+?)(?:\.\s*It|\s+which)?\s+is\s+an?\s+alternative\s+to\s+(?P<old>.+)',raw,re.I)
    if not m:m=re.fullmatch(r'(?P<new>.+?)是(?P<old>.+?)的替代(?:选项|方案)?',raw)
    if not m:return None
    return m['new'].strip(),m['old'].strip()


def _closed_offer_label(label,target):
    """Small literal option-descriptor grammar; all richer meaning defers.

    This is not a language classifier. Only a named object category plus
    ordinary physical/service option descriptors can bypass Compound. Financial
    basis, recurrence, conditions and arbitrary clauses have no production here.
    """
    from .interaction_meaning import NOUNS
    modifiers={'premium','economy','business','first','standard','basic','deluxe',
        'new','used','refurbished','folding','electric','manual','direct','express',
        'shared','private','single','double','twin','compact','large','small'}
    for pattern in NOUNS.values():
        if not re.search(pattern,target.get('title',''),re.I):continue
        for start in range(len(label)):
            if not re.fullmatch(pattern,label[start:],re.I):continue
            prefix=label[:start].strip().casefold()
            if not prefix:return True
            if prefix.isascii() and 1<=len(prefix.split())<=3 and set(prefix.split())<=modifiers:return True
            if re.fullmatch(r'(?:商务舱|头等舱|经济舱|二手|全新|折叠|电动|手动|豪华|标准|基础|单人|双人|高级|普通){1,3}',prefix):return True
    return False


def _alternative(payload,space,objects,context,parts):
    from .interaction_meaning import parse_amount,_NUMBER,ACTUAL,PLAN,ADVICE
    base=_base(payload['text'],'alternative');new,old=parts
    if re.search(ACTUAL+'|'+PLAN+'|'+ADVICE,new,re.I):return _defer(payload['text'])
    if re.search(r'\b(?:at most|at least|over|under|less than|more than|up to|minimum|maximum|have|has|had|own|owns|already|deposit|fee|remaining|balance|refundable|might|will|would|not)\b|最多|最少|至少|不超过|超过|低于|定金|订金|尾款|手续费|已经|已付',new,re.I):return _defer(payload['text'])
    # Currency symbols have the same meaning as explicit codes, never FX.
    clean=lambda s:s.replace('€','EUR ').replace('￥','CNY ').replace('¥','CNY ')
    oldclean=clean(old)
    target_name=re.sub(r'\b(?:EUR|CNY|SGD|RMB|euros?)\b|欧元|人民币|新币|'+_NUMBER,'',oldclean,flags=re.I).strip()
    target_name=re.sub(r'^the\s+','',target_name,flags=re.I)
    rows,ambiguous=_target(target_name,objects,payload)
    if len(rows)!=1 or ambiguous:return _question(base,rows)
    target=rows[0]
    if not _closed_name(target_name,target,payload):return _defer(payload['text'])
    variables,options=_options(target,space)
    if len(variables)!=1:return _question(base,variables,'money_option')
    currency=target.get('currency');reference=parse_amount(oldclean,currency)
    if reference['question']:return _question(base,[],reference['question'])
    # Explicit old price is a disambiguating witness, never a new expenditure.
    if reference['amount']:
        a=reference['amount']
        if a['precision'] not in ('exact','approximate'):return _question(base,options,'money_option')
        minor=int(Decimal(a['value'])*100)
        def same(o):
            e=o['effects'][0];value=e.get('planning') or e.get('observed') or {};money=value.get('amount') or {}
            return money.get('minor_units')==minor and money.get('currency')==a['currency']
        options=[o for o in options if same(o)]
    selected=[o for o in options if o['ref'] in (context or {}).get('selected',[])]
    reviewed=[o for o in options if o['ref']==payload.get('reviewed_target')]
    if len(reviewed)==1:options=reviewed
    elif len(selected)==1:options=selected
    else:
        replaced=[o.get('timing_replaces') for o in options if o.get('timing_replaces')]
        options=[o for o in options if o['ref'] not in replaced]
    if len(options)!=1:return _question(base,options,'money_option')
    parsed=parse_amount(clean(new),currency)
    if parsed['question'] or not parsed['amount']:return _question(base,[],parsed['question'] or 'amount')
    label=re.sub(r'^(?:there\s+is\s+(?:also\s+)?|I\s+(?:am|was)\s+considering\s+|also\s+)(?:an?\s+)?','',new,flags=re.I)
    label=re.sub(r'^(?:另外还有|还有|另有|我在考虑)','',label)
    label=re.sub(r'(?:(?:about|around|approximately|大约|约)\s*)?(?:€|￥|¥|EUR|CNY|SGD|RMB)?\s*'+_NUMBER+r'\s*(?:欧元|人民币|新币|euros?|EUR|CNY|SGD)?','',label,flags=re.I).strip()
    label=re.sub(r'^(?:an?\s+|的)','',label,flags=re.I)
    label=re.sub(r'\s+(?:costing|costs?|priced at)\s*$','',label,flags=re.I)
    label=re.sub(r'\s+',' ',label).strip(' ,，')
    if not label or len(label)>80 or re.search(r'[.;；。!?]|\b(?:and|or|paid|would|will|I|we|you|he|she|they|my|our|your|his|her|their)\b|而且|或者|我|你|他|她',label,re.I):return _defer(payload['text'])
    if not _closed_offer_label(label,target):return _defer(payload['text'])
    return dict(base,status='supported',question=None,target=target['ref'],amount=parsed['amount'],
        evidence=[dict(kind='explicit_existing_alternative',refs=[target['ref'],options[0]['ref']])],
        payload=dict(text=payload['text'],node=target['ref'],question=variables[0]['ref'],label=label,
            amount=parsed['amount'],currency=parsed['currency'],timing_source=options[0]['ref']))


def resolve_followup(payload,space,objects,context):
    text=payload.get('text')
    if not isinstance(text,str) or not 0<len(text)<=1000:return None
    reviewed=payload.get('reviewed_target')
    if reviewed is not None and reviewed not in [r.get('ref') for r in space.get('records',[])]:
        if _timing_clauses(text) or _alternative_parts(text):
            return dict(_question(_base(text,None),[]),reason='answer_stale')
    clauses=_timing_clauses(text)
    if clauses:return _timings(payload,space,objects,context,clauses)
    parts=_alternative_parts(text)
    if parts:return _alternative(payload,space,objects,context,parts)
    from .future_timing import movement_phrase
    if movement_phrase(text):return None
    if re.search(r'\bpay\s+for\b|\balternative\s+to\b|替代(?:选项|方案)|(?:支付|付款)',text,re.I):return _defer(text)
    return None

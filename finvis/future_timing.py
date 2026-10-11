"""Bounded explicit one-time money timing and read-only native trajectories."""
from calendar import monthrange
from dataclasses import asdict, replace
from datetime import date
import re
from .future_space import MoneyTiming, FutureError, require


def parse_timing(source, anchor=None):
    require(type(source) is str and 0 < len(source.strip()) <= 200, 'money_timing_text')
    anchor = anchor or date.today()
    text=source.strip();value=None;precision='unknown';relative=False
    exact=re.fullmatch(r'(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})日?',text)
    month=re.fullmatch(r'(\d{4})[-/年](\d{1,2})(?:月(?:底|初|中旬|左右)?)?',text)
    short=re.fullmatch(r'(\d{1,2})月(?:底|初|中旬|左右)?',text)
    from .calendar_literals import explicit_calendar, MONTHS
    named=explicit_calendar(text)
    if named: precision,value=named
    elif exact: value=date(*map(int,exact.groups())).isoformat();precision='day'
    elif month:
        year,m=map(int,month.groups());value=date(year,m,1).isoformat()[:7];precision='month'
    elif text.casefold() in MONTHS:
        value=date(anchor.year,MONTHS.index(text.casefold())+1,1).isoformat()[:7];precision='month';relative=True
    elif short: value=date(anchor.year,int(short.group(1)),1).isoformat()[:7];precision='month';relative=True
    elif text.casefold() in ('next month','下个月','下月','this month','这个月','本月'):
        add=1 if text.casefold() in ('next month','下个月','下月') else 0
        y=anchor.year+(anchor.month+add-1)//12;m=(anchor.month+add-1)%12+1
        value=date(y,m,1).isoformat()[:7];precision='month';relative=True
    elif text.casefold() in ('tomorrow','明天','today','今天'):
        from datetime import timedelta
        value=(anchor+timedelta(days=text.casefold() in ('tomorrow','明天'))).isoformat();precision='day';relative=True
    elif re.search(r'\d',text):
        raise FutureError('money_timing_unclear')
    return MoneyTiming(precision,value,text,anchor.isoformat() if relative else None)


DATE_TEXT=r'(?:\d{4}[-/年]\d{1,2}(?:[-/月]\d{1,2}日?|月(?:底|初|中旬|左右)?)?|\d{1,2}月(?:底|初|中旬|左右)?|next month|this month|tomorrow|today|下个月|下月|这个月|本月|明天|今天|时间未定|不知道|回国前)'
MOVEMENT=re.compile(r'(?:will pay|pay on|pay in|payment (?:on|in)|payment (?:moves?|changes?) to|expected (?:on|in)|receive on|receive in|预计(?:付款|支付|到账|收到)|(?:付款|支付|到账)(?:时间)?(?:改到|改为|在|：|:)?)[\s：:]*(?P<date>'+DATE_TEXT+r')',re.I)


def movement_phrase(text):
    matches=list(MOVEMENT.finditer(text))
    return matches[0] if len(matches)==1 else None


def overdue(timing, today):
    if timing is None or timing.precision=='unknown':return False
    end=date.fromisoformat(timing.value) if timing.precision=='day' else date.fromisoformat(timing.value+'-'+str(monthrange(int(timing.value[:4]),int(timing.value[5:]))[1]))
    return end<today


def timing_wire(timing): return asdict(timing) if timing else None


def trajectory(items, starting, today=None, stale=False, starting_approximate=()):
    """Groups before arithmetic. No intra-day/month order, FX or unknown zero."""
    today=today or date.today();result=[]
    for currency in sorted({i['currency'] for i in items}):
        same=[i for i in items if i['currency']==currency and i['time_basis']=='one_time']
        dated=[];undated=[];review=[]
        for item in same:
            t=item.get('money_timing');timing=MoneyTiming(**t) if t else None
            if overdue(timing,today):
                if timing.pending_checked_on!=today.isoformat():review.append(item)
                undated.append(item);continue
            if timing is None or timing.precision=='unknown':undated.append(item)
            else:dated.append(item)
        months={i['money_timing']['value'][:7] for i in dated if i['money_timing']['precision']=='month'}
        grouped={}
        for i in dated:
            value=i['money_timing']['value'];key=value[:7] if value[:7] in months else value
            grouped.setdefault(key,[]).append(i)
        initial=starting.get(currency);lo=initial[0] if initial else None;hi=initial[1] if initial else None
        unknown=0;groups=[];approximate=currency in starting_approximate or any(i.get('approximate',False) for i in same)
        for key,rows in sorted(grouped.items()):
            costs=[i['amount'] for i in rows if i['effect_type']=='cost' and i['amount']]
            income=[i['amount'] for i in rows if i['effect_type']=='expected_inflow' and i['amount']]
            delta=(sum(a['lower_minor'] for a in income)-sum(a['upper_minor'] for a in costs),sum(a['upper_minor'] for a in income)-sum(a['lower_minor'] for a in costs))
            unknown+=sum(i['amount'] is None for i in rows)
            if lo is not None:lo+=delta[0];hi+=delta[1]
            balance=None if lo is None or stale or review else dict(lower_minor=lo,upper_minor=hi)
            groups.append(dict(when=key,precision='month' if len(key)==7 else 'day',items=rows,
                known_change=dict(lower_minor=delta[0],upper_minor=delta[1]),balance=balance,
                partial=bool(unknown),unknown_count=unknown,approximate=approximate,
                negative=bool(balance and hi<0),crosses_zero=bool(balance and lo<0<=hi)))
        result.append(dict(currency=currency,groups=groups,undated=undated,disposition_required=review,
            unknown_count=unknown,conditional_inflow=any(i['effect_type']=='expected_inflow' for i in dated),
            starting_known=initial is not None))
    return dict(as_of=today.isoformat(),currencies=result)


def resolve_timing(payload, space, objects, context):
    """Explicit future payment wording only; existing semantic target retrieval."""
    from .interaction_meaning import retrieve_targets
    phrase=movement_phrase(payload.get('text',''))
    if not phrase:return None
    text=payload['text']
    remainder=(text[:phrase.start()]+' '+text[phrase.end():]).strip(' ,，;；。')
    # Reuse the real Meaning price/new-event grammar after removing only the
    # explicit movement clause; do not let its calendar numbers become money.
    if re.search(r'\d',remainder):
        from .interaction_meaning import resolve
        amended=dict(payload,text=remainder)
        result=resolve(amended,space)
        if result.get('status')=='supported' and result.get('layer')=='future' and result.get('payload'):
            result['payload']=dict(result['payload'],text=text,money_timing_text=phrase.group('date'))
            return result
        return dict(status='clarify',layer='future',operation='timing',target=None,candidates=[],evidence=[],payload=None,
                    question=dict(key='separate_clauses',choices=[]))
    found=retrieve_targets(text,objects,context=context,aliases=payload.get('aliases'))
    candidates=found['candidates'];reviewed=payload.get('reviewed_target')
    if payload.get('reviewed_name'):
        named=retrieve_targets(payload['reviewed_name'],candidates or objects,aliases=payload.get('aliases'))
        if len(named['candidates'])==1 and not named['ambiguous']:
            candidates=named['candidates'];reviewed=candidates[0]['ref']

    base=dict(status='clarify',layer='future',operation='timing',target=None,candidates=[o['ref'] for o in candidates],
        evidence=[],source_spans=[dict(start=phrase.start(),end=phrase.end(),text=phrase.group())],payload=None)
    if reviewed and reviewed.get('kind')=='node':candidates=[o for o in candidates if o['ref']==reviewed]
    if len(candidates)!=1 or (found['ambiguous'] and not reviewed):
        return dict(base,question=dict(key='target',choices=[dict(ref=o['ref'],label=o['title']) for o in candidates]))
    n=candidates[0];base['target']=n['ref'];base['title']=n['title']
    variables=[r['ref'] for r in space['records'] if r.get('node')==n['ref'] and r['ref']['kind']=='variable']
    options=[r for r in space['records'] if r.get('variable') in variables and len(r.get('effects',[]))==1 and not r['effects'][0].get('absence')]
    if reviewed and reviewed.get('kind')=='option':options=[o for o in options if o['ref']==reviewed]
    else:
        selected=[o for o in options if o['ref'] in (context or {}).get('selected',[])]
        if len(selected)==1:options=selected
    if len(options)!=1:
        return dict(base,question=dict(key='money_option',choices=[dict(ref=o['ref'],label=o['label']) for o in options]))
    timing_text=payload.get('reviewed_timing',phrase.group('date'))
    try:parse_timing(timing_text)
    except (ValueError,FutureError):return dict(base,question=dict(key='money_date',choices=[]))
    # Reject additional monetary/actual clauses; a date correction is not a price edit.
    remainder=(text[:phrase.start()]+' '+text[phrase.end():]).strip()
    if re.search(r'\d|已(?:经)?(?:付|买|收到)|\b(?:paid|bought|received)\b',remainder,re.I):
        return dict(base,question=dict(key='separate_clauses',choices=[]))
    return dict(base,status='supported',question=None,payload=dict(kind='timing',text=text,node=n['ref'],option=options[0]['ref'],timing=timing_text))


def timing_predecessor(catalog,option):
    """Read-only exact lineage for folding date corrections, never choices.

    prepare_timing preserves every business field and appends inherited source
    evidence. Labels/amount similarity alone cannot establish this relation.
    """
    from .future_space import FutureOption
    if not isinstance(option,FutureOption) or len(option.effects)!=1:return None
    candidates=[]
    for old in catalog.latest.values():
        if not isinstance(old,FutureOption) or old.meta.ref==option.meta.ref or len(old.effects)!=1:continue
        if len(option.meta.evidence)!=len(old.meta.evidence)+1 or option.meta.evidence[:-1]!=old.meta.evidence:continue
        if option.meta.dependencies!=old.meta.dependencies or option.meta.contexts!=old.meta.contexts:continue
        if option.effects[0].money_timing==old.effects[0].money_timing:continue
        reconstructed=replace(option,meta=old.meta,effects=(replace(option.effects[0],money_timing=old.effects[0].money_timing),))
        if reconstructed==old:candidates.append(old.meta.ref)
    return candidates[0] if len(candidates)==1 else None

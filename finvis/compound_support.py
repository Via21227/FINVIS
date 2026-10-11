"""Bounded source support and material-coverage checks; never financial truth.

The ledger is derived from the original text, independently of model-supplied
coverage claims. Lexical anchors are conservative tripwires, not a language parser
or a proof of completeness. All offsets refer to literal input; inherited support
is explicitly separate from a claim's direct quotation.
"""
import re

SENTENCE = re.compile(r'[。！？!?;；\n]|(?<!\d)\.(?!\d)|\.(?=\s+[A-Z])')
COORD = re.compile(r'\b(?:and|also|plus)\b|[，,、]|以及|还有|和|及', re.I)
PRONOUN = re.compile(r'\b(?:it|that|this|previous one|earlier one|the former)\b|那个|这个|它|之前那个|前一个|前面那个', re.I)
CONDITION = re.compile(r'\b(?:if|unless|provided that|only when)\b|如果|假如|要是|只要|除非', re.I)
FINANCIAL = re.compile(r'\b(?:pay|paid|spend|spent|buy|bought|purchase\w*|receiv\w*|refund\w*|cancel\w*|replac\w*|transfer\w*|costs?|fees?|charge\w*|balance|deposit|salary|wages?|gift|rent|go to|travel|postpone)\b|买|购买|花|付|收到|给我|退款|退票|取消|更换|换|转账|费用|收费|余额|工资|房租|押金|旅行|去|推迟', re.I)
REFERENCE = re.compile(r'\b(?:instead|same|mean|meant|correct\w*|previous|earlier|that|this|it)\b|更正|说错|不是|之前|那个|这个|它|原来', re.I)
NEGATION = re.compile(r'\b(?:not|never|without|unless)\b|不|没|除非', re.I)


def windows(text):
    start=0
    for match in SENTENCE.finditer(text):
        if text[start:match.start()].strip():yield start,match.start(),text[start:match.start()]
        start=match.end()
    if text[start:].strip():yield start,len(text),text[start:]


def occurrences(text, quote):
    return [(m.start(),m.end()) for m in re.finditer(re.escape(quote),text)]


def coordination_support(text, claim):
    """License only immediate coordination, never whole-paragraph inheritance."""
    from . import compound_meaning as cm
    spans=occurrences(text,claim['source']);choices=[]
    for a,b in spans:
        containing=next(((s,e,t) for s,e,t in windows(text) if s<=a and b<=e+1),None)
        if not containing:continue
        start,end,window=containing;prefix=text[start:a]
        # A full source has no inherited evidence. A separate sentence or a
        # topic/authority switch cannot donate a role or a denomination.
        if not prefix.strip() or len(window)>512 or not COORD.search(prefix):continue
        if re.search(r'\b(?:but|however|instead|tomorrow|yesterday)\b|但是|不过|改成|明天|昨天',prefix,re.I):continue
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


def reference_ids(source, rows):
    """One explicit non-negated entity, or exactly one supplied referent."""
    from . import compound_meaning as cm
    explicit=[]
    for ident,row in rows.items():
        label=cm._target_label(row)
        if label and cm._contains(source,str(label)):
            if not re.search(r'(?:\bnot\s+(?:the\s+)?|不是|并非)'+re.escape(str(label)),source,re.I):explicit.append(ident)
    if explicit:return set(explicit)
    # A demonstrative followed by a new noun is explicit topic evidence, not
    # an elliptical pronoun licensed merely by a one-item context.
    demonstratives=re.findall(r'\b(?:this|that)\s+([a-z]+)|(?:这个|那个)([\u4e00-\u9fff]{1,4})',source,re.I)
    predicates={'is','was','costs','cost','would','will','might','could','at','for','instead','one','should','has','had'}
    for en,zh in demonstratives:
        if en and en.casefold() not in predicates:return set()
        if zh and not re.match(r'改|说错|应|是|要|会|可能|大概|约|不|没|贵|便宜|花|才|得',zh):return set()
    if PRONOUN.search(source) and len(rows)==1:return set(rows)
    return set()


def endpoint_supported(ident, relation, claims, rows):
    """Bind a corrected claim's label through its uniquely grounded target."""
    from . import compound_meaning as cm
    row=claims.get(ident) or rows.get(ident,{})
    label=row.get('label') or cm._target_label(row)
    if label and cm._contains(relation['source'],label):return True
    refs=reference_ids(relation['source'],rows)
    if ident in rows:return ident in refs
    target=row.get('target')
    return bool(target in refs and (row['source'] in relation['source'] or relation['source'] in row['source']))


def evidence_ledger(bundle,text,context=(),answer_support=()):
    """Detect bounded material gaps without accepting any ungrounded model field.

    Returns immutable-by-copy diagnostics; no ledger field grants authority.
    Unresolved findings must block coordinator compilation, not just a UI button.
    """
    from . import compound_meaning as cm
    rows=cm._context(context);claims=bundle['claims'];relations=bundle['relationships'];entries=[];issues=[]
    def issue(code,start,end,ids=()):
        key=(code,start,end)
        if any((i['code'],i['start'],i['end'])==key for i in issues):return
        issues.append(dict(code=code,start=start,end=end,source=text[start:end],claim_ids=list(ids)))
    for c in claims:
        direct=occurrences(text,c['source'])
        entries.append(dict(claim_id=c['id'],direct=direct,licensed=coordination_support(text,c)))
        if c['kind']=='expected_inflow' and re.search(r'\b(?:costs?|pay|spend|buy|purchase)\b|花费|购买|支付',c['source'],re.I) and not re.search(r'\b(?:receive|give|gift|bonus|salary|refund|income)\b|收到|给|工资|奖金|退款|收入',c['source'],re.I):
            if direct:issue('unresolved_direction',direct[0][0],direct[0][1],[c['id']])
        for a,b in direct:
            scoped=next(((s,e,t) for s,e,t in windows(text) if s<=a and b<=e+1),None)
            if not scoped:continue
            prefix=text[scoped[0]:a]
            # A clipped quote must not hide an earlier completed predicate or
            # conditional commitment. Keep this uncertain, never infer a write.
            if c['kind'] in ('future_outflow','expected_inflow') and cm._ACTUAL.search(prefix) and not (cm._FUTURE.search(prefix+c['source']) or cm._HYPOTHETICAL.search(prefix+c['source']) or cm._NEGATED_ACTUAL.search(prefix)):
                issue('unresolved_authority_scope',scoped[0],scoped[1],[c['id']])
            if c['kind']=='planned_outflow' and CONDITION.search(prefix):
                issue('unresolved_authority_scope',scoped[0],scoped[1],[c['id']])
    for start,end,window in windows(text):
        present=[c for c in claims if cm._contains(window,c['label']) or
                 c['target'] in reference_ids(window,rows) or
                 any(a<end and b>start for a,b in occurrences(text,c['source']))]
        licensed={cid for a in answer_support if a['start'] < end and a['end'] > start for cid in a['claim_ids']}
        present += [c for c in claims if c['id'] in licensed and c not in present]
        ids=[c['id'] for c in present]
        numbers=cm._number_evidence(window)
        financial=bool(FINANCIAL.search(window) or cm._currencies(window))
        if financial:
            for value,a,b in numbers:
                a+=start;b+=start
                covering=[c for c in claims if any(x<=a and b<=y for x,y in occurrences(text,c['source']))]
                # Existing money validation already binds the amount/precision
                # within each quote. A currency/default statement has no amount.
                if not covering and not any(c['id'] in licensed and str(value) in [str(v) for v in c['money'].values()] for c in claims):issue('uncovered_amount',a,b,ids)
        # A conditional has at least two propositions or an explicit consequence;
        # "What if X costs 30?" alone is exploration, not a dependency edge.
        cond=CONDITION.search(window)
        if cond and re.search(r'\bunless\b|除非|\bif\s+(?:not|no)\b|如果不|要是不',window,re.I):
            issue('unsupported_condition_polarity',start,end,ids)
        if cond and not re.search(r'\bwhat\s+$',window[:cond.start()],re.I):
            split=re.search(r'[,，]|\bthen\b|就|才',window[cond.end():],re.I)
            if split and financial:
                divider=cond.end()+split.start();ante=window[cond.end():divider];after=window[divider+len(split.group()):]
                left={c['id'] for c in claims if cm._contains(ante,c['label'])}
                right={c['id'] for c in claims if cm._contains(after,c['label'])}
                linked=any(r['kind']=='conditional_on' and r['from'] in right and r['to'] in left and
                           cond.group().lower() in r['source'].lower() for r in relations)
                if not left or not right or not linked:issue('unresolved_condition',start,end,ids)
        # Metadata about balance coverage is handled by the existing exact
        # lifecycle review; a bare financial noun is not a new money event.
        event=bool(cm._ACTUAL.search(window) or cm._FUTURE.search(window) or CONDITION.search(window) or re.search(r'\b(?:cancel|replace|buy|pay|spend|receive|transfer)\b|取消|更换|买|付|收|转账',window,re.I))
        if financial and event and not present:issue('unrepresented_financial_statement',start,end)
        # Two priced alternatives cannot be accepted as unrelated additive facts.
        if len(numbers)>=2 and re.search(r'\b(?:either|or)\b|或者|二选一',window,re.I):
            selected={c['id'] for c in claims if cm._contains(window,c['label'])}
            if not any(r['kind']=='alternative' and {r['from'],r['to']}<=selected for r in relations):issue('unresolved_alternatives',start,end,ids)
        for kind in ('compare','simulate','explain','decision_support'):
            trigger=cm._REQUEST_EVIDENCE[kind]
            # Do not elevate incidental "choice" or an ordinary if-clause into
            # an explicit request. Questions/imperatives must concern finances.
            strong={'compare':r'\bcompare\b|比较|对比','simulate':r'\b(?:simulate|what if|what would|how would)\b|模拟|会怎样|会怎么样',
                    'explain':r'\b(?:why|explain)\b|为什么|解释','decision_support':r'\b(?:should I|how should|recommend|advice)\b|你觉得|该怎么|该不该'}[kind]
            if re.search(strong,window,re.I) and (financial or bool(claims)) and not any(r['kind']==kind for r in bundle['requests']):
                # compare/decision-support can express the same ordinary request.
                if kind in ('compare','decision_support') and any(r['kind'] in ('compare','decision_support') for r in bundle['requests']):continue
                issue('unrepresented_request',start,end,ids)
    for c in claims:
        # An explicit existing target in a correction/hypothetical reference must
        # not silently become an additive duplicate just because target=null.
        matching=reference_ids(text,rows)
        if c['target'] is None and len(matching)==1 and REFERENCE.search(text):
            ident=next(iter(matching));label=str(cm._target_label(rows[ident]))
            if cm._contains(c['source'],label) or PRONOUN.search(c['source']):
                a,b=occurrences(text,c['source'])[0];issue('unresolved_existing_reference',a,b,[c['id']])
    for r in relations:
        # A negated compatibility/alternative declaration cannot license an edge.
        if r['kind'] in ('compatible','alternative') and re.search(r'\b(?:not compatible|not alternatives|not both|cannot both)\b|不能同时|不是二选一|不能一起',r['source'],re.I):
            a,b=occurrences(text,r['source'])[0];issue('contradicted_relationship',a,b,[r['from'],r['to']])
    return dict(version='source-support-1.1',claims=entries,issues=issues,complete=not issues,answer_support=list(answer_support),
                scope='bounded monetary, conditional, reference and request coverage; not general semantic proof')


def answer_supports_resolution(selected,answer,old,new):
    """No empty acknowledgement may erase a material question."""
    from . import compound_meaning as cm
    choices=selected['choices']
    if any(answer.strip().casefold()==c.strip().casefold() for c in choices):return True
    field=selected['field'].lower();affected=set(selected['claim_ids'])
    previous={c['id']:c for c in old['claims']}
    changed=any(c!=previous[c['id']] for c in new['claims'] if c['id'] in affected)
    if field in ('currency','account','amount','target','timing') and changed:return True
    patterns={'price_basis':r'\b(?:total|full|additional|extra|top.up|difference|incremental)\b|总价|全价|追加|补差|差价|另外',
              'target':r'\b(?:this|that|previous)\b|那个|这个|之前'}
    if 'price' in field or 'basis' in field:
        # Mentioning both interpretations or uncertainty is not choosing one.
        total=bool(re.search(r'\b(?:total|full|whole)\b|总价|全价',answer,re.I))
        extra=bool(re.search(r'\b(?:additional|extra|top.up|difference|incremental)\b|追加|补差|差价|另外',answer,re.I))
        uncertain=bool(re.search(r'\b(?:maybe|perhaps|probably|unsure|uncertain|not|don.t know|or)\b|不知道|不确定|也许|可能|还是|或者|不是',answer,re.I))
        return total != extra and not uncertain
    return changed and bool(cm._number_evidence(answer) or cm._currencies(answer) or cm._RELATION_EVIDENCE['correction'].search(answer))

"""Bounded compound-report source preparation; never a financial writer.

Only exact, uniquely supported clauses or explicit two-part allocations qualify.
Offsets always refer to the original text. Candidate meaning is not authority.
"""
from dataclasses import replace
from decimal import Decimal
import hashlib
import re
from .review_preparation import (PreparedFact, PreparationStatus, SourceSelection,
    _spans, _contains, _APPROX, _CURRENCIES, _NEGATION, _CONDITIONAL, _OTHER_OWNER)

NUMBER = re.compile(r'(?<![a-zA-Z0-9.])(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?:万|千|[kK])?(?!\d|\.\d)')
ROLES = {
 'current_asset': r'余额|目前|现在|\bbalance\b|\bcurrently\b|\bI have\b',
 'earmarked_current_asset': r'预留|留着|留作|备用|用来|reserved|set aside|earmark',
 'expected_future_inflow': r'会(?:退|给)|将收到|预计|待到账|\bexpect\b|\bwill (?:receive|give|refund)\b|pending refund',
 'planned_expense': r'用来买|要花|计划|准备买|打算|plan to|planning|budget for|will buy',
 'contemplated_decision': r'考虑|权衡|considering|thinking about|deciding whether',
}

def clauses(text):
    for start,end,sentence in _spans(text):
        cursor=start
        for m in re.finditer(r'，|,(?!\d{3}(?:\D|$))',sentence):
            stop=start+m.start();yield cursor,stop,text[cursor:stop];cursor=start+m.end()
        if text[cursor:end].strip():yield cursor,end,text[cursor:end]


def numbers(text):
    out=[]
    # Do not interpret product specifications (16oz), dates or duration as money.
    for m in NUMBER.finditer(text):
        if re.match(r'\s*(?:oz|days?\b|years?\b|nights?\b|年|天|晚)',text[m.end():],re.I):continue
        value=m.group().replace(',','');scale=1
        if value[-1] in '万千kK':scale={'万':10000,'千':1000,'k':1000,'K':1000}[value[-1]];value=value[:-1]
        out.append(Decimal(value)*scale)
    return out


def anchor(fact,excerpt):
    label=fact.account if fact.type=='current_asset' else fact.earmark if fact.type=='earmarked_current_asset' else fact.label
    if not label:return False
    if _contains(excerpt,label):return True
    # Permit formatting-only label differences and a closed event-role suffix,
    # never arbitrary two-character overlap with an unrelated noun.
    compact=lambda value:re.sub(r'\s+','',value).lower()
    token=compact(label)
    left=r'(?<![a-z0-9])' if token[0].isascii() and token[0].isalnum() else ''
    right=r'(?![a-z0-9])' if token[-1].isascii() and token[-1].isalnum() else ''
    if re.search(left+re.escape(token)+right,compact(excerpt)):return True
    if fact.type=='expected_future_inflow':
        roles=[(r'(?:退款|退还款)$',r'退|refund'),(r'(?:赠予|赠款|赠与)$',r'给|赠|give|gift')]
        for suffix,verb in roles:
            stem=re.sub(suffix,'',label)
            if stem!=label and len(stem)>=2 and _contains(excerpt,stem) and re.search(verb,excerpt,re.I):return True
    return False



def explicit_default(text,currency):
    prefix = r'(?:默认(?:货币)?(?:单位)?(?:为|是)?|default\s+(?:currency|units?)\s*(?:is|:)?)[：:]?\s*'
    found={code for code,marker in _CURRENCIES.items() if re.search(prefix+'(?:'+marker.pattern+')',text,re.I)}
    return found == {currency}


def supported(fact,interpretation,text,span):
    start,end,excerpt=span; currency=(fact.amount.currency or interpretation.default_currency) if fact.amount else None
    if fact.type not in ROLES or not fact.amount or currency not in _CURRENCIES:return False
    if not anchor(fact,excerpt) or not re.search(ROLES[fact.type],excerpt,re.I):return False
    if any(p.search(excerpt) for p in (_NEGATION,_CONDITIONAL,_OTHER_OWNER)):return False
    found={code for code,p in _CURRENCIES.items() if p.search(excerpt)}
    if found!={currency} and not (not found and explicit_default(text,currency)):return False
    if re.search(r'\bUSD\b|美元|\bor\b|或者|还是',excerpt,re.I):return False
    a=fact.amount;ns=numbers(excerpt)
    if a.precision not in ('exact','approximate'):return False
    if ns!=[Decimal(a.value)]:return False
    if a.precision=='exact' and _APPROX.search(excerpt):return False
    if a.precision=='approximate' and not _APPROX.search(excerpt):
        # A shared explicit approximation in this same coordinated sentence is
        # conservative only for another future observation, never a cash balance.
        sentence=next((s for lo,hi,s in _spans(text) if lo<=start and end<=hi),'')
        if fact.type!='expected_future_inflow' or not _APPROX.search(sentence):return False
    return True


def compound_binding(fact,interpretation,text,original):
    if original.excerpt and re.search(r'\bUSD\b|美元',original.excerpt,re.I):
        return replace(original,status=PreparationStatus.UNSUPPORTED,reason='unsupported_currency',extraction_verified=False,review=None)
    if original.extraction_verified or original.reason in ('unsupported_currency','precision_conflict','conditional_or_other_owner','negated_or_unresolved_timing','conflicting_temporal_evidence'):
        return original
    match=[]
    # Preserve contradictions: do not cherry-pick one matching amount among two
    # observations of the same role/subject.
    parts=list(clauses(text))
    expanded=[(a[0],b[1],text[a[0]:b[1]]) for a,b in zip(parts,parts[1:])
              if a[1]+1==b[0] and not numbers(a[2]) and numbers(b[2]) and anchor(fact,a[2]) and not anchor(fact,b[2])]
    relevant=[c for c in parts+expanded if anchor(fact,c[2]) and re.search(ROLES.get(fact.type,r'(?!)'),c[2],re.I)
              and not any(a<=c[0] and c[1]<b for a,b,_ in expanded)]
    if len(relevant)==1 and supported(fact,interpretation,text,relevant[0]):match=relevant
    if fact.type=='context' and fact.amount is None:
        compact=lambda s:re.sub(r'[\s，。,.！？!?的了我在]','',s).lower()
        needle=compact(fact.label)
        matches=[c for c in clauses(text) if len(needle)>=4 and (compact(c[2])==needle or (len(needle)/len(compact(c[2]))>=.85 and re.fullmatch('.*'.join(map(re.escape,needle)),compact(c[2]))))]
        if len(matches)==1 and not any(p.search(matches[0][2]) for p in (_NEGATION,_CONDITIONAL,_OTHER_OWNER)):match=matches
    if fact.type=='earmarked_current_asset' and fact.account and fact.earmark and fact.amount:
        # Explicit A+B followed by former/latter assigns two disjoint partitions.
        for lo,hi,s in _spans(text):
            pattern=re.escape(fact.account)+r'[^。；\n]*?(\d+(?:\.\d+)?)\s*\+\s*(\d+(?:\.\d+)?)[，,]\s*前者([^，。]+)[，,]\s*后者([^，。]+)'
            m=re.search(pattern,s)
            if not m or any(p.search(s) for p in (_NEGATION,_CONDITIONAL,_OTHER_OWNER)):continue
            group=[f for f in interpretation.candidate_facts if f.account==fact.account and f.type=='earmarked_current_asset']
            if len(group)!=2 or len({f.earmark for f in group})!=2:continue
            idx=0 if fact.earmark in m[3] else 1 if fact.earmark in m[4] else None
            cur=fact.amount.currency or interpretation.default_currency
            if idx is not None and fact.amount.precision=='exact' and Decimal(fact.amount.value)==Decimal(m[idx+1]) and cur in _CURRENCIES and (explicit_default(text,cur) or _CURRENCIES[cur].search(s)):
                match=[(lo,hi,s)]
    if len(match)!=1:return original
    lo,hi,excerpt=match[0]
    # Keep declared currency evidence in the contiguous original excerpt.
    cur=(fact.amount.currency or interpretation.default_currency) if fact.amount else None
    if cur in _CURRENCIES and not _CURRENCIES[cur].search(excerpt) and explicit_default(text,cur):lo=0;excerpt=text[:hi]
    return PreparedFact(fact.id,PreparationStatus.AUTO_VERIFIED,'unique_compound_source_binding',
                        (SourceSelection('fact',lo,hi),),excerpt,hashlib.sha256(excerpt.encode()).hexdigest(),True)


def explicit_partition(group,text):
    if len(group)!=2 or not all(f.type=='earmarked_current_asset' for f in group):return False
    return all(compound_binding(f,type('Meaning',(),{'candidate_facts':group,'default_currency':f.amount.currency})(),text,
        PreparedFact(f.id,PreparationStatus.CLARIFICATION_REQUIRED,'unproven')).extraction_verified for f in group)

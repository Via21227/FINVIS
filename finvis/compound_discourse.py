"""Bounded adjacent-sentence witnesses, never inferred financial facts.

Only explicit membership predicates and complete nonpayment statements qualify.
No recency ranking, model calls, amount arithmetic or authority is introduced.
"""
import re
from .compound_meaning import _contains, _ACTUAL

_MEMBER = re.compile(r"^\s*(?:it|this|that)\s+(?:includes?|contains?)\b|^\s*(?:它|这项|这次|这个)(?:活动|计划|项目)?(?:包括|包含)", re.I)
_INTRO = re.compile(r"^\s*(?:I|we)(?:\s+am|\s+are|['’]m|['’]re)?\s+(?:consider(?:ing)?|plan(?:ning)?|intend(?:ing)?)\b|^\s*(?:我|我们)?(?:在)?(?:考虑|计划|打算)", re.I)
_CONFLICT = re.compile(r"\b(?:not|never|if|unless|but|instead|except|or|and)\b|不|没|未|如果|或者|并且|和|与|除外|而非", re.I)
_STATUS = re.compile(
    r"(?:(?:I|we)\s+(?:have not|haven['’]?t)\s+(?:yet\s+)?paid\s+(?:for\s+)?(?:any of\s+)?(?:them|these|those)(?:\s+yet)?"
    r"|none of (?:them|these|those)\s+(?:has|have)\s+been paid(?: for)?(?: yet)?"
    r"|(?:我|我们)?(?:这些|它们)(?:都)?(?:还没有|还没|尚未|没有|未)(?:付款|支付|付钱)"
    r"|(?:我|我们)(?:还没有|还没|尚未|没有)(?:为|给)(?:这些|它们)(?:付款|支付|付钱))", re.I)


def _windows(text):
    from .compound_binding_support import windows
    return list(windows(text))


def _enumerated_items(fragment, mark, claims):
    """Every item is an exact validated claim quote, separated only by list glue.

    A contrast or an unrepresented noun cannot hide between listed items. This
    is deliberately narrower than a general English/Chinese clause parser.
    """
    spans=[]
    for ident,claim in claims.items():
        literal=claim.get('source','').strip(' .。')
        if not literal or claim.get('kind') not in ('future_outflow','planned_outflow','expected_inflow'):continue
        matches=[m for m in re.finditer(re.escape(literal),fragment) if m.start()>=mark.end()]
        if len(matches)>1:return None
        if matches:spans.append((matches[0].start(),matches[0].end(),ident))
    spans.sort()
    if not spans:return None
    end=mark.end()
    for index,(a,b,_) in enumerate(spans):
        if a<end:return None
        glue=fragment[end:a]
        allowed=r"\s*(?:(?:a|an|the)\s+)?" if index==0 else r"\s*(?:(?:[,，、]\s*(?:(?:and|和|及|与)\s*)?)|(?:(?:and|和|及|与)\s*))(?:(?:a|an|the)\s+)?"
        if not re.fullmatch(allowed,glue,re.I):return None
        end=b
    if fragment[end:].strip():return None
    return {ident:(a,b) for a,b,ident in spans}


def membership_witness(relation, claims, text):
    """A unique immediate parent introduction + 'It includes <child>'.

    The model must still supply the edge. The source must quote the predicate
    and child. The preceding parent's own exact source must support its label.
    Ambiguous/negated/conjoined introductions and intervening topics fail closed.
    """
    if relation['kind'] != 'part_of' or not text or text.count(relation['source']) != 1:
        return None
    parent=claims.get(relation['to'],{});child=claims.get(relation['from'],{})
    if (parent.get('kind') not in ('future_outflow','planned_outflow') or
            parent.get('money',{}).get('precision')!='unknown' or parent.get('target') or
            child.get('kind') not in ('future_outflow','planned_outflow','expected_inflow')):
        return None
    quote=relation['source'];qa=text.find(quote);qb=qa+len(quote)
    windows=_windows(text);found=[]
    for index,(a,b,fragment) in enumerate(windows):
        mark=_MEMBER.search(fragment)
        if not index or not mark or not _contains(fragment[mark.end():],child['label']):continue
        items=_enumerated_items(fragment,mark,claims)
        if not items or relation['from'] not in items:continue
        ca,cb=items[relation['from']]
        # Cover the actual exact child quote, not a similar word elsewhere.
        if not (qa<=a+len(fragment)-len(fragment.lstrip()) and qa<=a+ca and a+cb<=qb):continue
        if re.search(r"\b(?:not|never|except|instead|if|unless|but|whereas|while|rather|however|unlike|belongs?)\b|\bpart of\b|不|没|未|除外|而非|如果|但是|不过|而|属于|用于",fragment,re.I):continue
        pa,pb,prior=windows[index-1]
        if not _INTRO.search(prior) or _CONFLICT.search(prior) or len(prior)>512:continue
        if not _contains(prior,parent['label']):continue
        if parent.get('source','').strip(' .。') not in prior.strip(' .。'):continue
        named=[cid for cid,c in claims.items() if c.get('label') and _contains(prior,c['label'])]
        if named!=[relation['to']]:continue
        found.append(dict(rule='unique_adjacent_membership',claim_ids=[relation['from'],relation['to']],
                          direct=[a,b],support=[pa,pb]))
    return found[0] if len(found)==1 else None


def nonpayment_witnesses(bundle,text):
    """Only status of the immediately named plural Future outflow list.

    No new amount, refund, unpaid remainder, account or actual event is supplied.
    All other coverage/authority findings are retained by the caller.
    """
    windows=_windows(text);result=[]
    for index,(a,b,fragment) in enumerate(windows):
        if not index or not _STATUS.fullmatch(fragment.strip()):continue
        pa,pb,prior=windows[index-1]
        if _ACTUAL.search(prior):continue
        if not re.search(r'\band\b|[,，]|和|及|、',prior,re.I):continue
        affected=[]
        for c in bundle['claims']:
            if _contains(prior,c['label']):
                # Only directly grounded claims wholly in the preceding clause.
                if c['source'].strip(' .。') not in prior.strip(' .。'):
                    affected=[];break
                affected.append(c)
        if len(affected)<2 or any(c['kind'] not in ('future_outflow','planned_outflow') for c in affected):continue
        result.append(dict(rule='adjacent_future_list_not_paid',claim_ids=[c['id'] for c in affected],
                           direct=[a,b],support=[pa,pb]))
    return result


def membership_gaps(bundle, text):
    """Detect missing explicit anaphoric edges, but never manufacture them."""
    claims={c['id']:c for c in bundle['claims']};gaps=[]
    for a,b,fragment in _windows(text):
        if not _MEMBER.search(fragment):continue
        ids=set()
        for parent in claims:
            for child in claims:
                if child==parent:continue
                proposed=dict(kind='part_of',**{'from':child,'to':parent},source=fragment.strip())
                if not membership_witness(proposed,claims,text):continue
                if any(r['kind']=='part_of' and r['from']==child and r['to']==parent for r in bundle['relationships']):continue
                ids.update((parent,child))
        if ids:gaps.append(dict(code='unresolved_structure',start=a,end=b,source=fragment,
                               claim_ids=sorted(ids)))
    return gaps


_NONCOMMITTAL = re.compile(r'\b(?:no|neither|hypothetic\w*|tentative|potential|unconfirmed|uncertain|optional|maybe|perhaps|possible|possibly)\b|假想|假设|暂定|待定|未确定|不确定|可选|备选|可能|也许|没有|不是|不打算', re.I)

def planned_membership_support(claim, bundle, text):
    """Source-bound planned intent for an explicitly included bare component.

    Only supports a model-proposed planned_outflow. Does not promote Future,
    copy payment dates, or authorize Plan/Reality. Optional or conditional
    components cannot inherit the parent's intent.
    """
    from .compound_meaning import _ROLE_EVIDENCE, _HYPOTHETICAL, _NEGATED_ACTUAL
    if claim['kind']!='planned_outflow':return None
    source=claim['source']
    if (_ACTUAL.search(source) or _HYPOTHETICAL.search(source) or _NONCOMMITTAL.search(source) or
        re.search(r"\b(?:optional|optionally|maybe|perhaps|possible|possibly|conditional|potential|not|never|undecided|consider\w*|alternativ\w*|either|or)\b|可选|备选|可能|考虑|不|没|未|或者|也许",source,re.I)):
        return None
    claims={c['id']:c for c in bundle['claims']}
    edges=[r for r in bundle['relationships'] if r['kind']=='part_of' and r['from']==claim['id']]
    if len(edges)!=1:return None
    edge=edges[0];parent=claims.get(edge['to'])
    if not parent or parent['kind']!='planned_outflow':return None
    intro=parent['source']
    if (not _ROLE_EVIDENCE['planned_outflow'].search(intro) or _HYPOTHETICAL.search(intro)
        or _NEGATED_ACTUAL.search(intro) or _ACTUAL.search(intro) or _NONCOMMITTAL.search(intro)
        or re.search(r'\b(?:cancel\w*|abandon\w*|optional|possible|tentative|uncertain|no longer|maybe|perhaps)\b|取消|放弃|可选|可能|暂定|不确定',intro,re.I)):
        return None
    witness=membership_witness(edge,claims,text)
    if not witness:return None
    return dict(witness,rule='explicit_parent_planned_component',claim_id=claim['id'],parent_id=parent['id'])

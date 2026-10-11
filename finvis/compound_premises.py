"""Bounded source witnesses for non-authoritative Future premises.

No new model schema, financial claim, outcome state machine or calculator.
A selected external income remains a hypothetical, never an approval fact.
"""
import re
from .compound_meaning import _contains

CONDITION = re.compile(r"\b(?:if|provided that|only when|subject to|contingent on|pending|requires)\b|如果|只要|前提是|须经|需经|取决于|除非", re.I)
APPROVAL = re.compile(r"\b(?:(?:it|this|the (?:request|claim|application|reimbursement|grant))\s+(?:is|gets)\s+approved|(?:the )?(?:panel|committee|employer|company|insurer)\s+approves?\s+(?:it|this|the (?:request|claim|application))|approval(?: is granted)?|approved)\b|(?:审核|审批|申请|报销)(?:获得)?通过|(?:公司|单位|委员会)(?:批准|审批同意)|批准", re.I)
DENIED = re.compile(r"\b(?:denied|rejected|refused|revoked)\b|拒绝|拒批|驳回|未通过|没有通过|不予批准", re.I)
UNSUPPORTED = re.compile(r"\b(?:unless|otherwise|either|or|and|not|never|without|already)\b|否则|除非|或者|并且|而且|不通过|不批准|已批准|已经通过", re.I)


def outcome_conflict(claim, text):
    """Never read a clipped quote as proof that no contrary outcome was reported."""
    source=claim['source'];start=text.find(source)
    tail=text[start+len(source):] if start>=0 else ''
    adjacent=re.split(r'[。.!?！？\n]',tail.lstrip(' .。!?！？'),maxsplit=1)[0]
    # Only the immediate approval/status assertion, not a distant other event.
    if re.search(r'\b(?:approval|application|request|it)\b|审批|审核|申请|批准',adjacent,re.I):source+=' '+adjacent
    clean=re.sub(r"\b(?:not|never)\s+(?:been\s+)?(?:denied|rejected|refused)\b|(?:没有|尚未|未曾)(?:被)?(?:拒绝|拒批|驳回)", '', source, flags=re.I)
    return bool(DENIED.search(clean) or re.search(r"\b(?:was not|wasn't)\s+approved\b|\b(?:has (?:already )?been|was|is already)\s+approved\b|已经批准|审批已通过",clean,re.I))


def external_premise(claim, text):
    """One explicit positive approval contingency on one expected inflow.

    Outcome is deliberately never inferred, even from a hypothetical selection.
    Negative/compound conditions and asserted outcomes require review.
    """
    if claim['kind'] != 'expected_inflow' or claim['source'] not in text:
        return None
    source=claim['source'];marks=list(CONDITION.finditer(source))
    if len(marks)!=1 or outcome_conflict(claim,text) or text.count(source)!=1:return None
    mark=marks[0]
    tail=source[mark.end():]
    # Semicolon starts a separate assertion, e.g. "it has not arrived".
    pieces=re.split(r'[;；。.!?！？]',tail,maxsplit=1)
    predicate=pieces[0].strip(' ,，:：')
    suffix=pieces[1].strip(' .。') if len(pieces)>1 else ''
    benign=r"(?:(?:it|this|the money|the reimbursement) (?:has not|hasn't) (?:yet )?(?:arrived|been received)|(?:approval |the outcome )?(?:is |has )?(?:still )?(?:unknown|pending|not yet granted)|it has not been denied|(?:尚未|还没|还未|没有)(?:到账|收到)|(?:审批|结果)(?:尚未确定|未知|待定|还不知道))"
    if suffix and not re.fullmatch(benign,suffix,re.I):return None
    if mark.start()==0:
        chunks=re.split(r'[,，]',predicate,maxsplit=1)
        if len(chunks)!=2:return None
        predicate=chunks[0].strip();effect=chunks[1]
    else:effect=source[:mark.start()]
    if not _contains(effect,claim['label']) or UNSUPPORTED.search(predicate):return None
    match=APPROVAL.fullmatch(predicate)
    if not match:return None
    # A second assertion of denied/confirmed approval is not silently discarded.
    if re.search(r'\b(?:approval|application|request)\s+(?:has been|was|is)\s+(?:approved|granted)\b|审批已通过|已经批准',source,re.I):return None
    start=text.find(source)+mark.start();end=text.find(source)+mark.end()+tail.find(predicate)+len(predicate)
    return dict(version=1,claim_id=claim['id'],kind='external_approval',
        outcome='unresolved',source=text[start:end],claim_source=source,
        interpretation='hypothetical_only',start=start,end=end)


def structural_parents(bundle):
    claims={c['id']:c for c in bundle['claims']}
    return {r['to'] for r in bundle['relationships'] if r['kind']=='part_of'
        and r['from'] in claims and r['to'] in claims
        and claims[r['to']]['kind'] in ('future_outflow','planned_outflow')
        and claims[r['to']]['money']['precision']=='unknown'
        and not claims[r['to']].get('target')}


def structure_gaps(bundle, text):
    """Ask about a source-stated unrepresented grouping, never infer its links.

    A nonmonetary Future introduction scoping several claims is evidence for a
    question only. A parent's label in that introduction plus explicit part_of
    links is required before it counts as represented.
    """
    from .calendar_literals import money_numbers
    gaps=[];claims={c['id']:c for c in bundle['claims']}
    parents=structural_parents(bundle)
    for req in bundle['requests']:
        source=req['source'];ids=[i for i in req['claim_ids'] if i in claims and claims[i]['kind'] in ('future_outflow','planned_outflow','expected_inflow')]
        if req['kind']!='record' or len(ids)<2 or money_numbers(source):continue
        if not re.search(r'\b(?:considering|planning)\s+(?:a|an|the|my|our)\s+|(?:考虑|计划)(?:一次|一个|这次|这项|我的|我们的)',source,re.I):continue
        if any(_contains(source,claims[i]['label']) for i in ids):continue
        if any(_contains(source,claims[p]['label']) for p in parents):continue
        start=text.find(source)
        if start>=0:gaps.append(dict(code='unresolved_structure',start=start,end=start+len(source),source=source,claim_ids=ids))
    # Explicit membership naming existing objects must not disappear with an
    # omitted relation. No amount/role is manufactured from the membership text.
    for fragment in re.split(r'[.。!?！？\n]',text):
        if not re.search(r'\b(?:part of|includes?|including)\b|包括|包含|组成|属于',fragment,re.I):continue
        ids=[i for i,c in claims.items() if _contains(fragment,c['label'])]
        if len(ids)<2 or not any(claims[i]['kind'] in ('future_outflow','planned_outflow') and claims[i]['money']['precision']=='unknown' for i in ids):continue
        if any(q['field'] in ('relationship','structure','parent') and set(ids)&set(q['claim_ids']) for q in bundle['ambiguities']):continue
        linked={i for rel in bundle['relationships'] if rel['kind']=='part_of' and rel['source'] in text and rel['from'] in ids and rel['to'] in ids for i in (rel['from'],rel['to'])}
        if set(ids)<=linked:continue
        start=text.find(fragment)
        gaps.append(dict(code='unresolved_structure',start=start,end=start+len(fragment),source=fragment,claim_ids=ids))
    return gaps


def membership_supported(relation, claims, text=None):
    """Direction-bearing literal membership for non-priced structural parents."""
    child=claims[relation['from']]['label'];parent=claims[relation['to']]['label']
    source=relation['source']
    for fragment in re.split(r'[;；。.!?！？\n]',source):
        if re.search(r"\b(?:not|never|doesn't)\b|不属于|不包括|不包含|并非|不是",fragment,re.I):continue
        for pattern,first,second in [(r'\b(?:part of|belongs? to)\b|属于',child,parent),(r'\b(?:includes?|including|contains?)\b|包括|包含',parent,child)]:
            for mark in re.finditer(pattern,fragment,re.I):
                if _contains(fragment[:mark.start()],first) and _contains(fragment[mark.end():],second):return True
    from .compound_discourse import membership_witness
    return bool(membership_witness(relation, claims, text or source))


def relationship_answer_supported(question, answer, old, new):
    if question.get('field') not in ('relationship','relationships','structure','parent','part_of','relationships.part_of'):return False
    before={c['id']:c for c in old['claims']};after={c['id']:c for c in new['claims']}
    if before!=after:return False
    if any(r not in new['relationships'] for r in old['relationships']):return False
    additions=[r for r in new['relationships'] if r not in old['relationships']]
    return bool(additions) and all(r['kind']=='part_of' and r['from'] in question['claim_ids'] and r['to'] in question['claim_ids'] and r['source'] in answer and membership_supported(r,after) for r in additions)


def existing_selection_refund(bundle, claim, text):
    """Keep the existing validated refund/selection contract, not approval.

    This recognizes no new financial effect: the payment binder must establish
    a refund first, and the sole antecedent must literally name an existing
    Future choice. Arbitrary or compound external conditions do not qualify.
    """
    from .compound_payments import payment_basis_for
    from .future_space import FutureError
    try:
        basis = payment_basis_for(bundle, claim['id'], text)
    except (FutureError, ValueError, TypeError, KeyError):
        return False
    if basis is None or basis.kind != 'refund':return False
    source = claim['source']
    if len(list(CONDITION.finditer(source))) != 1:return False
    for other in bundle['claims']:
        if other['kind'] not in ('future_outflow','planned_outflow'):continue
        label = re.escape(other['label'])
        if re.match(r'(?:If '+label+r' is chosen[,，]|如果选择'+label+r'[,，])', source, re.I):
            tail = re.split(r'[,，]', source, maxsplit=1)[1]
            return not re.search(r'\b(?:unless|only|provided|approval|approved|and|or)\b|除非|批准|审批|并且|或者',tail,re.I)
    return False

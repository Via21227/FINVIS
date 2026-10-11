"""Read-only meaning summary before unresolved review choices; never a D preview."""
from .agent_reconciliation import _amount
from .world_domain import sum_amount_estimates
from .reviewed_proposals import _wire


def understood_changes(candidate, payload, questions):
    meaning=candidate.interpretation
    facts={f.id:f for f in meaning.candidate_facts}
    rows=[]; grouped=set()
    question_ids={q['id'] for q in questions}
    for i,position in enumerate(payload['positions']):
        if 'composition:'+str(i) in question_ids:continue
        values=[facts[fid] for fid in position['balance_ids']]
        if not values:continue
        try:
            amounts=[_amount(f,meaning.default_currency) for f in values]
            value=amounts[0] if position['composition']=='total' and len(amounts)==1 else sum_amount_estimates(amounts,amounts[0].currency)
        except (ValueError,TypeError):continue
        rows.append(dict(kind='current_asset',label=values[0].account,amount=_wire(value),fact_ids=[f.id for f in values]))
        grouped.update(f.id for f in values if f.type=='current_asset')
    for f in facts.values():
        if f.id in grouped:continue
        try:value=_wire(_amount(f,meaning.default_currency)) if f.amount else None
        except (ValueError,TypeError):value=None
        rows.append(dict(kind=f.type,label=f.label,amount=value,fact_ids=[f.id]))
    return rows

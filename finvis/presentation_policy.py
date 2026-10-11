"""Deterministic display recommendations; no mutation or financial policy."""
from datetime import datetime


def ordered_series(points):
    try:
        dates=[datetime.fromisoformat(p["at"]) for p in points]
        return len(dates)>=3 and all(a<b for a,b in zip(dates,dates[1:]))
    except (ValueError,KeyError,TypeError):
        return False


REGISTRY=('SUMMARY','BEFORE_ACTUAL','BUDGET_PROGRESS','GOAL_PROGRESS','NET_HISTORY','TREND')

def recommend(item):
    semantic=item.get('semantic');summary=item.get('summary',{})
    estimate=summary.get('estimated') or summary.get('target')
    scalar=estimate and estimate.get('precision') in ('exact_reported','approximate') and estimate.get('amount',{}).get('minor_units',0)>0
    result='SUMMARY';choices=['auto','numbers','details']
    if semantic=='net_history':result='NET_HISTORY'
    elif semantic=='purchase':result='BEFORE_ACTUAL'
    elif semantic in ('budget','goal') and scalar:
        result='BUDGET_PROGRESS' if semantic=='budget' else 'GOAL_PROGRESS';choices.append('progress')
    points=item.get('series',[])
    # Time-series presentation requires a caller-supplied semantic series of
    # consistent exact observations, not arbitrary event order or mixed units.
    if semantic=='time_series' and len(points)>=3 and all(type(p.get('at')) is str and type(p.get('value_minor')) is int for p in points) and len({p.get('currency') for p in points})==1 and points[0].get('currency') in ('CNY','SGD') and ordered_series(points):
        result='TREND';choices.append('trend')
    return dict(recommended=result,choices=choices)

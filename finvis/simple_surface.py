"""Read-only selected one-time consequences. No forecast, policy, FX or writer."""
from .future_experience import fields, ref, wire_ref
from .future_space import FutureOption, FutureError, FutureCatalog, ReferenceFrame, require
from .if_judgment import IfJudgment
from .world_projection import project_world
from .world_values import AmountPrecision
from .future_timing import timing_wire, trajectory
from .compound_future import complete_conditionals


def interval(value):
    if value.precision is AmountPrecision.UNKNOWN:
        return None
    if value.precision is AmountPrecision.RANGE:
        return (value.lower_bound.minor_units, value.upper_bound.minor_units)
    return (value.amount.minor_units, value.amount.minor_units)


def wire_interval(bounds):
    return None if bounds is None else dict(lower_minor=bounds[0], upper_minor=bounds[1])


class SimpleSurface:
    def __init__(self, interaction, adoption=None):
        self.j = IfJudgment(interaction, adoption)

    def _result(self, x, c, snapshot, roots, options):
        selection, _, result = x._comparison(c, snapshot, roots, options)
        continuity = self.j._continuity(c, options)
        stale = result['status'] == 'stale' or bool(continuity)
        items = []
        for chosen in options:
            option = c.get(chosen)
            node = c.get(c.get(option.variable).node)
            for effect in option.effects:
                items.append(dict(option=wire_ref(chosen), node=wire_ref(node.meta.ref),
                    title=node.title, label=option.label, currency=effect.planning.currency.value,
                    effect_type=effect.effect_type, time_basis=effect.time_basis,
                    money_timing=timing_wire(effect.money_timing),
                    amount=wire_interval(interval(effect.planning)),
                    approximate=effect.planning.precision is AmountPrecision.APPROXIMATE))
                if effect.payment_basis:
                    items[-1]['payment_basis']=dict(kind=effect.payment_basis.kind,
                        quoted=wire_interval(interval(effect.observed)),
                        quoted_approximate=effect.observed.precision is AmountPrecision.APPROXIMATE,
                        balance_review_required=any(p.role=='paid' for p in effect.payment_basis.parts))
        projection = project_world(snapshot)
        assets = {b.currency.value:interval(b.current_assets.value) for b in projection.currencies}
        timing = trajectory(items,assets,stale=stale,starting_approximate={b.currency.value for b in projection.currencies if b.current_assets.value.precision is AmountPrecision.APPROXIMATE})
        # A pending reported payment is not reviewed balance coverage. Its
        # arithmetic remains useful, but no current-after claim is justified.
        payment_review={i['currency'] for i in items if i.get('payment_basis',{}).get('balance_review_required')}
        for currency in timing['currencies']:
            if currency['currency'] in payment_review:
                currency['balance_review_required']=True
                for group in currency['groups']:
                    group.update(balance=None,negative=False,crosses_zero=False)
        pending = {c['currency'] for c in timing['currencies'] if c['disposition_required']}
        buckets = []
        for currency in sorted({i['currency'] for i in items}):
            same = [i for i in items if i['currency'] == currency]
            known = [i for i in same if i['amount'] and i['time_basis'] == 'one_time'
                     and i['effect_type'] in ('cost', 'expected_inflow')]
            unknown = sum(i['amount'] is None for i in same)
            unsupported = sum(i['time_basis'] != 'one_time' or i['effect_type'] not in ('cost','expected_inflow') for i in same)
            cost = tuple(sum(i['amount'][k] for i in known if i['effect_type']=='cost') for k in ('lower_minor','upper_minor'))
            inflow = tuple(sum(i['amount'][k] for i in known if i['effect_type']=='expected_inflow') for k in ('lower_minor','upper_minor'))
            before = assets.get(currency)
            # Invalid/stale structure never yields a plausible numeric consequence.
            after = None if stale or before is None or unsupported or currency in pending or currency in payment_review else (before[0]+inflow[0]-cost[1],before[1]+inflow[1]-cost[0])
            buckets.append(dict(currency=currency,starting_approximate=any(b.currency.value==currency and b.current_assets.value.precision is AmountPrecision.APPROXIMATE for b in projection.currencies),starting=wire_interval(before),known_cost=wire_interval(cost),
                known_inflow=wire_interval(inflow),after=wire_interval(after),unknown_count=unknown,
                unsupported_count=unsupported,partial=bool(unknown or result['status']=='needs_information' or currency in payment_review),
                unavailable_reason='stale' if stale else 'payment_balance_review' if currency in payment_review else 'money_timing_disposition' if currency in pending else 'unsupported_effect' if unsupported else 'starting_unknown' if before is None else None))
        return dict(status='stale' if stale else result['status'],buckets=buckets,items=items,
            timing=timing,
            omissions=dict(monthly_cash_flow=True,unselected_items=True,scope_incomplete=not selection.scope_complete),
            world_revision=snapshot.revision,financial_authority=False)

    def simulate(self, payload):
        fields(payload,'roots options proposal_id language',('roots','options'))
        require(payload.get('language','en') in ('en','zh-CN'),'future_request_invalid')
        with self.j.x.memory.store._lock:
            x,c,snapshot,_,roots,options=self.j._view(payload)
            selection=complete_conditionals(c,roots,options)
            roots=[ref(r) for r in selection['roots']]
            options=[ref(r) for r in selection['options']]
            result=self._result(x,c,snapshot,roots,options)
            result['selection']=selection
            comparisons=[]
            from .future_timing import timing_predecessor
            timing_parents={o.meta.ref:timing_predecessor(c,o) for o in c.latest.values() if isinstance(o,FutureOption)}
            replaced_dates={r for r in timing_parents.values() if r is not None}
            def timing_root(r):
                while timing_parents.get(r):r=timing_parents[r]
                return r
            if result['status'] != 'stale':
                for selected in options:
                    original=c.get(selected);variable=c.get(original.variable)
                    # Bounded simple monetary alternatives only, not structural branches.
                    if variable.control!='user_choice' or len(original.effects)!=1:continue
                    effect=original.effects[0]
                    if effect.time_basis!='one_time' or effect.effect_type!='cost':continue
                    for alternative in c.latest.values():
                        if len(comparisons)>=6:break
                        if type(alternative) is not FutureOption or alternative.meta.archived or alternative.variable!=original.variable or alternative.meta.ref==selected or len(alternative.effects)!=1:continue
                        if alternative.meta.ref in replaced_dates or timing_root(alternative.meta.ref)==timing_root(selected):continue
                        other=alternative.effects[0]
                        if (other.slot,other.effect_type,other.time_basis,other.coverage,other.planning.currency)!=(effect.slot,effect.effect_type,effect.time_basis,effect.coverage,effect.planning.currency):continue
                        changed=[alternative.meta.ref if r==selected else r for r in options]
                        try:
                            completed=complete_conditionals(c,roots,changed)
                            changed=[ref(r) for r in completed['options']]
                            trial=self._result(x,c,snapshot,roots,changed)
                            trial['selection']=completed
                        except FutureError:continue
                        if trial['status']=='stale':continue
                        # Existing FS1 comparison cancels shared immutable terms
                        # before interval arithmetic, including unchanged ranges.
                        # This adapter changes selections, never financial math.
                        frame=ReferenceFrame(x.meta('reference','Current reviewed selection'),
                            'Current selection',c.select(tuple(roots),tuple(options)))
                        comparison_catalog=FutureCatalog(c.scope,(*c.records,frame))
                        comparison=comparison_catalog.compile(
                            comparison_catalog.select(tuple(roots),tuple(changed)),frame.meta.ref)
                        bucket=next((b for b in comparison['buckets'] if
                            b['effect_type']=='cost' and b['time_basis']=='one_time' and
                            b['currency']==effect.planning.currency.value),None)
                        delta=bucket['delta'][0] if bucket and bucket['delta'] else None
                        difference=None
                        if delta and delta['precision']!='unknown':
                            difference=((delta['lower_minor'],delta['upper_minor']) if
                                delta['precision']=='range' else (delta['value_minor'],delta['value_minor']))
                        comparisons.append(dict(current=wire_ref(selected),alternative=wire_ref(alternative.meta.ref),
                            current_label=original.label,alternative_label=alternative.label,
                            currency=effect.planning.currency.value,cost_difference=wire_interval(difference),
                            result=trial))
            result['alternatives']=comparisons
            return result

"""Read-only explicit budget comparison over existing Explanation Facts.

Targets are explicit versioned FS1 context, not financial policy or authority.
No eligibility, funds, default thresholds, advice, provider, FX or new writer.
"""
from dataclasses import replace
import hashlib
import json
from types import SimpleNamespace
from .domain import Currency
from .future_space import FutureNode, FutureOption, SavedScenario, require, FutureError
from .future_experience import FutureExperience, fields, ref, wire_ref, sentence
from .future_explanation import FutureExplanation
from .future_memory import validity
from .if_interaction import _OverlayMemory
from .world_values import money_from_major_units

PREFIX='finvis.user-selected-budget.v0.1:'

def canonical(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'))

def digest(value):return hashlib.sha256(canonical(value).encode()).hexdigest()

def target_of(c,node):
    found=[]
    for r in c.latest.values():
        if type(r) is not FutureNode or r.meta.archived:continue
        tagged=[s[len(PREFIX):] for s in r.context if s.startswith(PREFIX)]
        if len(tagged)!=1:continue
        try:
            v=json.loads(tagged[0])
            if (set(v)!= {'kind','currency','maximum_minor','scope','event_id','occurrence'} or
                v['kind']!='maximum_selected_cost' or v['scope']!='selected_one_time_choices' or
                v['event_id']!=node.meta.ref.id or v['occurrence']!=node.occurrence or
                v['currency'] not in {cc.value for cc in Currency} or
                (v['maximum_minor'] is not None and (type(v['maximum_minor']) is not int or v['maximum_minor']<0))):continue
            found.append((r,v))
        except (ValueError,TypeError):continue
    require(len(found)<=1,'budget_target_ambiguous')
    return found[0] if found else (None,None)

class IfJudgment:
    def __init__(self,interaction,adoption=None):self.i=interaction;self.x=interaction.x;self.a=adoption

    def prepare_target(self,p):
        fields(p,'node currency maximum text remove',('node','text'))
        source=sentence(p['text']);c,_,_=self.x.memory.read()
        old=self.x._current(c,p['node'],'node')
        require(old.parent is None,'budget_root_required')
        require(type(p.get('remove',False)) is bool,'future_request_invalid')
        previous,prior=target_of(c,old)
        if not p.get('remove'):
            require(set(p)>={'currency','maximum'},'budget_amount_required')
            require(type(p['currency']) is str and p['currency'] in {cc.value for cc in Currency},'currency_required')
            value=money_from_major_units(p['maximum'],Currency(p['currency']))
            require(value.minor_units>=0,'budget_amount_required')
            target=dict(kind='maximum_selected_cost',currency=value.currency.value,
                maximum_minor=value.minor_units,scope='selected_one_time_choices',
                event_id=old.meta.ref.id,occurrence=old.occurrence)
        else:
            require(prior is not None,'budget_target_missing')
            target=dict(prior,maximum_minor=None)
        require(target!=prior,'future_unchanged')
        meta=self.x.meta('node',source,previous.meta.ref.id if previous else None,
            previous.meta.ref if previous else None)
        new=FutureNode(meta,'Budget · '+old.title,'event','budget/'+old.occurrence,
            context=(PREFIX+canonical(target),))
        return self.x._draft((new,),'revise' if previous else 'keep',dict(title=old.title,target=target,
            meaning='explicit_user_budget',financial_authority=False))

    def _view(self,p):
        if p.get('proposal_id'):
            _,_,_,op=self.i._catalog(sentence(p['proposal_id']))
            x=FutureExperience(_OverlayMemory(self.x.memory,op))
        else:x=self.x
        c,revision,snapshot=x.memory.read()
        e=FutureExplanation(SimpleNamespace(x=x))
        request={k:v for k,v in p.items() if k in ('roots','options','scenario')}
        request['language']=p.get('language','en')
        if p.get('scenario'):
            scenario=c.get(ref(p['scenario']));require(type(scenario) is SavedScenario,'future_request_invalid')
            # Saved Scenario explanation uses normal receipt-derived stale state.
            from .future_adoption import FutureAdoption
            # The transient service needs only a.space for this path.
            e.a=self.a or SimpleNamespace(x=x,space=lambda: self.x.listing())
            roots=list(scenario.selection.roots);options=list(scenario.selection.options)
        else:
            require(type(p.get('roots')) is list and type(p.get('options')) is list,'future_request_invalid')
            roots=[ref(r) for r in p['roots']];options=[ref(r) for r in p['options']]
        require(len(roots)<=64 and len(options)<=128,'future_request_invalid')
        out=e.facts(request)
        require(out['stage']=='explained','budget_selection_required')
        return x,c,snapshot,out['facts'],roots,options

    def _continuity(self,c,options):
        # Reuse FS3's receipt-derived path checks for a live, unsaved selection.
        # This is stale evidence, never an instruction to replan or pay again.
        if self.a is None:return []
        affected=[]
        for r in options:
            o=c.get(r)
            a=next((a for a in self.a.listing() if a['variable']['id']==o.variable.id),None)
            if a and (not a['current'] or a['option']!=wire_ref(r) or a['plan'] and a['plan']['events']):
                affected.append(dict(option=wire_ref(r),node=wire_ref(c.get(o.variable).node),reason='future_path_changed'))
        return affected

    def _dimension(self,c,root,facts,continuity=(),snapshot=None):
        n=c.get(root);require(type(n) is FutureNode and n.parent is None,'budget_root_required')
        target_node,target=target_of(c,c.latest[('node',n.meta.ref.id)])
        if target and target['maximum_minor'] is None:target=None
        factors=[f for f in facts['factors'] if any(c.is_descendant(c.get(c.get(ref(r)).variable).node,root) for r in f['option_refs'])]
        cost=[f for f in factors if f['effect_type']=='cost' and f['selected_amount']]
        out=dict(node=wire_ref(root),title=n.title,target=target,target_ref=wire_ref(target_node.meta.ref) if target_node else None,state='unassessed',reason='target_missing',
                 estimate=None,comparison=None,drivers=[],scope='selected_one_time_choices',evidence_unknowns=[])
        if facts['status']=='stale' or any(c.is_descendant(ref(a['node']),root) for a in continuity):out['reason']='inputs_changed';return out
        if not target:return out
        if snapshot is not None:
            selected={ref(r) for f in factors for r in f['option_refs']}
            relevant=[c.get(r) for r in selected]+([target_node] if target_node else [])
            states=[validity(c,r,snapshot) for r in relevant]
            if any(s['status']=='stale' for s in states):out['reason']='inputs_changed';return out
            missing=sorted({m for s in states for m in s['missing']})
            if missing:out['reason']='supporting_information_unknown';out['evidence_unknowns']=missing;return out
        if not cost:out['reason']='no_selected_cost';return out
        if any(f['currency']!=target['currency'] for f in cost):out['reason']='mixed_currency';return out
        if any(f['time_basis']!='one_time' for f in cost):out['reason']='unsupported_time_basis';return out
        cost_refs={ref(r) for f in cost for r in f['option_refs']}
        # Existing targets do not say whether their cap covers a gross quote
        # or the remaining payment. Do not silently choose either meaning.
        if any(e.effect_type=='cost' and e.payment_basis and e.payment_basis.kind=='total'
               and any(p.role=='credited' for p in e.payment_basis.parts)
               for r in cost_refs for e in c.get(r).effects):
            out['reason']='payment_target_basis';return out
        amounts=[f['selected_amount'] for f in cost]
        if any(a['precision']=='unknown' for a in amounts):out['reason']='amount_unknown';return out
        # Use existing precision arithmetic rather than a second financial total.
        from .if_lines import bucket
        effects=[e.planning for r in cost_refs for e in c.get(r).effects if e.effect_type=='cost']
        total=bucket(((a,1) for a in effects),{Currency(target['currency'])})[0]
        out['estimate']=total
        lo=total['value_minor'] if total['value_minor'] is not None else total['lower_minor']
        hi=total['value_minor'] if total['value_minor'] is not None else total['upper_minor']
        if lo is None or hi is None:out['reason']='amount_unknown';return out
        cap=target['maximum_minor'];out['comparison']=dict(lower_difference_minor=lo-cap,upper_difference_minor=hi-cap)
        out['state']='conflict' if lo>cap else 'within_target' if hi<=cap else 'unassessed'
        out['reason']='above_user_cap' if lo>cap else 'within_user_cap' if hi<=cap else 'range_crosses_target'
        known=[f for f in cost if f['selected_amount']['value_minor'] is not None]
        out['drivers']=sorted(known,key=lambda f:(-f['selected_amount']['value_minor'],f['event']))[:3]
        return out

    def assess(self,p):
        fields(p,'roots options scenario proposal_id language previous_binding')
        require(p.get('language','en') in ('en','zh-CN'),'future_request_invalid')
        if 'previous_binding' in p:require(type(p['previous_binding']) is dict,'future_request_invalid')
        with self.x.memory.store._lock:
            x,c,snapshot,facts,roots,options=self._view(p)
            continuity=self._continuity(c,options)
            dims=[self._dimension(c,r,facts,continuity,snapshot) for r in roots]
            relevance=dict(roots=[wire_ref(r) for r in roots],selected=facts['selected'],
                targets=[dict(target=d['target'],ref=d['target_ref']) for d in dims],factors=facts['factors'],status=facts['status'],
                unknowns=facts['known_unknowns'],stale=facts['stale'],continuity=continuity,
                assessment_inputs=[dict(reason=d['reason'],unknowns=d['evidence_unknowns']) for d in dims])
            signature=digest(relevance)
            alternatives=[]
            if facts['status']!='stale' and not continuity and any(d['target'] for d in dims):
                # Compatible exact existing options and explicit exclusion trials.
                seen=set();candidates=[];omissions=[]
                for selected in options:
                    o=c.get(selected);v=c.get(o.variable);n=c.get(v.node)
                    for alt in c.latest.values():
                        if type(alt) is FutureOption and alt.variable==o.variable and alt.meta.ref!=o.meta.ref and not alt.meta.archived:
                            candidates.append((alt.label,[r for r in options if r!=selected]+[alt.meta.ref]))
                    if n.parent:
                        keep=[r for r in options if not c.is_descendant(c.get(c.get(r).variable).node,n.meta.ref)]
                        if keep:omissions.append((('不选 ' if p.get('language')=='zh-CN' else 'Without ')+n.title,keep))
                for label,chosen in (candidates+omissions)[:64]:
                    key=tuple(sorted(canonical(wire_ref(r)) for r in chosen))
                    if key in seen:continue
                    seen.add(key)
                    try:
                        request=dict(roots=[wire_ref(r) for r in roots],options=[wire_ref(r) for r in chosen],language=p.get('language','en'))
                        trial=FutureExplanation(SimpleNamespace(x=x)).facts(request)['facts']
                        ds=[self._dimension(c,r,trial,snapshot=snapshot) for r in roots]
                        if trial['status']=='stale' or not any(d['estimate'] for d in ds):continue
                        alternatives.append(dict(label=label,selection=dict(roots=request['roots'],options=request['options']),dimensions=ds))
                    except (FutureError,ValueError):continue
                    if len(alternatives)>=3:break
            out=dict(schema='finvis.judgment-facts.v0.1',stage='assessed',financial_authority=False,
                dimensions=dims,explanation_facts=facts,continuity=continuity,alternatives=alternatives,
                binding=dict(scenario_ref=facts['scenario_ref'],world_version=facts['world_version'],
                    future_revision=facts['future_revision'],relevance_signature=signature),
                previous_valid=(p.get('previous_binding',{}).get('relevance_signature')==signature) if p.get('previous_binding') else None)
            out['digest']=digest(out)
            return out

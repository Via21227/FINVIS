"""Simulation entry recovery over existing FS1 review/confirm, never a World writer."""
from datetime import datetime, timezone
from dataclasses import fields as dcfields, is_dataclass, replace
from .future_experience import fields, ref, wire_ref
from .future_space import FutureRef, FutureNode, FutureVariable, FutureOption, FutureCatalog, SavedScenario, WorldDependency, ContextBinding, require, FutureError
from .future_memory import validity
from .decision_baseline import digest, linked
from .reviewed_proposals import _wire
from .simple_surface import SimpleSurface


def remap(value, mapping):
    if type(value) is FutureRef:
        seen=set()
        while value in mapping and value not in seen:seen.add(value);value=mapping[value]
        return value
    if type(value) is tuple:return tuple(remap(v,mapping) for v in value)
    if is_dataclass(value):return replace(value,**{f.name:remap(getattr(value,f.name),mapping) for f in dcfields(value) if f.name!='meta'})
    return value


class SimulationEntry:
    def __init__(self,interaction,adoption=None):
        self.i=interaction;self.x=interaction.x;self.sim=SimpleSurface(interaction,adoption)

    def prepare(self,p):
        fields(p,'roots options proposal_id language confirmed_estimates',('roots','options'))
        confirmations=p.get('confirmed_estimates',[])
        require(type(confirmations) is list and len(confirmations)<=64,'future_request_invalid')
        confirmed={ref(r) for r in confirmations}
        p={k:v for k,v in p.items() if k!='confirmed_estimates'}
        require(p.get('language','en') in ('en','zh-CN'),'future_request_invalid')
        with self.x.memory.store._lock:
            x,c,snapshot,_,roots,options=self.sim.j._view(p)
            require(bool(roots),'future_selection_required')
            require(confirmed.issubset(set(roots)),'future_request_invalid')
            # Normal entry is a fresh read, not retention or permission.
            out=self.sim.simulate(p)
            overdue=[i for currency in out['timing']['currencies'] for i in currency['disposition_required']]
            if overdue and out['status']!='stale':
                return dict(stage='needs_input',reason='money_timing_disposition',financial_authority=False,
                    items=[dict(ref=i['node'],option=i['option'],title=i['title'],needed_fact='money_disposition',
                        timing=i['money_timing'],direction=i['effect_type']) for i in overdue])
            if out['status']!='stale':
                # Capture the existing target assessment under the same store lock.
                # The browser must not re-assess a frozen result against newer facts.
                out['assessment']=self.sim.j.assess(p)
                return dict(stage='ready',simulation=out,financial_authority=False)
            selection,_,old=x._comparison(c,snapshot,roots,options)
            consumed=set(c.consumed_structure(selection))|set(options)
            for r in list(consumed):
                row=c.get(r)
                if type(row) is FutureVariable and row.reference_option:consumed.add(row.reference_option)
            allowed={'world_input_changed','decision_context_changed','future_version_changed'}
            states=[validity(c,c.get(r),snapshot) for r in consumed]
            reasons={s for v in states for s in v['reasons']}
            continuity=self.sim.j._continuity(c,options)
            renew=set()
            for root in confirmed:
                latest_root=c.latest[(root.kind,root.id)]
                local=[r for r in consumed if r==root or
                    (type(c.get(r)) is FutureNode and c.is_descendant(r,root)) or
                    (type(c.get(r)) is FutureVariable and c.is_descendant(c.get(r).node,root)) or
                    (type(c.get(r)) is FutureOption and c.is_descendant(c.get(c.get(r).variable).node,root))]
                for r in local:
                    row=c.latest[(r.kind,r.id)]
                    if any(e.freshness=='stale' or e.expires_at is not None and e.expires_at<=datetime.now(timezone.utc) for e in row.meta.evidence):renew.add(row.meta.ref)
            unconfirmed_stale=any(e.freshness=='stale' or e.expires_at is not None and e.expires_at<=datetime.now(timezone.utc) for r in consumed for row in [c.latest[(r.kind,r.id)]] if row.meta.ref not in renew for e in row.meta.evidence)
            if reasons-allowed-({'evidence_stale'} if renew and not unconfirmed_stale else set()) or continuity:
                return self.blocked(c,roots,'changed_path' if continuity else 'support_unavailable',options,snapshot)
            current={ (r.record_kind,r.ref.id):r for r in snapshot.world.members }
            seeds={r:c.get(r) for r in renew};changes=[]
            for r in sorted(consumed,key=lambda r:(r.kind,r.id)):
                row=c.latest[(r.kind,r.id)]
                if row.meta.archived:return self.blocked(c,roots,'support_unavailable',options,snapshot)
                deps=[];contexts=[];different=False
                for dep in row.meta.dependencies:
                    new=current.get((dep.ref.record_kind,dep.ref.ref.id))
                    if new is None:return self.blocked(c,roots,'support_unavailable',options,snapshot)
                    val=snapshot.get(new);obs=getattr(val,'observation',None)
                    if obs and obs.freshness.status.value=='stale':return self.blocked(c,roots,'support_unavailable',options,snapshot)
                    if dep.ref.record_kind in ('future_inflow','planned_expense') and val.status.value not in ('expected','planned','partially_realized'):
                        return self.blocked(c,roots,'changed_path',options,snapshot)
                    deps.append(WorldDependency(new,digest(val)))
                    if new!=dep.ref or deps[-1]!=dep:
                        different=True
                        a=next((getattr(val,k,None) for k in ('proposed_amount','expected_amount','estimated_amount','amount','balance') if getattr(val,k,None) is not None),None)
                        changes.append(dict(label=next((getattr(val,k,None) for k in ('subject','purpose','label') if getattr(val,k,None)),getattr(row,'title',getattr(row,'label',''))),amount=_wire(a)))
                for ctx in row.meta.contexts:
                    new=current.get(('world_decision',ctx.decision_ref.ref.id))
                    if new is None:return self.blocked(c,roots,'support_unavailable',options,snapshot)
                    value=ContextBinding(new,linked(snapshot,new));contexts.append(value);different|=value!=ctx
                    if value!=ctx:
                        for r in value.context_refs:changes.append(dict(label=getattr(snapshot.get(r),'statement','Updated context'),amount=None))
                if different:seeds[row.meta.ref]=replace(row,meta=replace(row.meta,dependencies=tuple(deps),contexts=tuple(contexts)))
            # Revisions propagate immutable references through structural records;
            # old Scenarios/evaluations remain pinned history, never rewritten.
            base,_,_=self.x.memory.read()
            structural=[r for r in base.latest.values() if type(r) in (FutureNode,FutureVariable,FutureOption) and not r.meta.archived]
            mapping={r:c.latest[(r.kind,r.id)].meta.ref for r in consumed if c.latest[(r.kind,r.id)].meta.ref!=r}
            mapping.update({r:replace(r,version=r.version+1) for r in seeds})
            while True:
                extra=[r for r in structural if r.meta.ref not in mapping and remap(r,mapping)!=r]
                if not extra:break
                mapping.update({r.meta.ref:replace(r.meta.ref,version=r.meta.ref.version+1) for r in extra})
            records=[]
            source='Reviewed updated supporting information; keep the selected hypothetical estimates unchanged.'
            for row in structural:
                if row.meta.ref not in mapping:continue
                oldref=row.meta.ref;row=seeds.get(oldref,row);meta=x.meta(oldref.kind,source,oldref.id,oldref, row.meta.dependencies)
                require(len(row.meta.evidence)<32,'future_review_capacity')
                # A new explicit hypothetical attestation supersedes expired Future evidence
                # only for that version. Old evidence remains in immutable history.
                evidence=meta.evidence if oldref in renew else (*row.meta.evidence,*meta.evidence)
                meta=replace(meta,contexts=row.meta.contexts,evidence=evidence)
                records.append(replace(remap(row,mapping),meta=meta))
            base,_,_=self.x.memory.read()
            # Include only needed transient records, never unrelated draft ideas.
            if p.get('proposal_id'):
                op=self.i._catalog(p['proposal_id'])[3]
                require(not any(r.meta.ref in mapping for r in op.records),'future_review_expired')
                needed=set(consumed)
                for row in op.records:
                    if row.meta.ref in needed:records.append(remap(row,mapping))
            augmented=FutureCatalog(c.scope,(*base.records,*records))
            def latest(r):return remap(r,mapping) if r in mapping else augmented.latest[(r.kind,r.id)].meta.ref
            newroots=[latest(r) for r in roots];newoptions=[latest(r) for r in options]
            try:selected,frame,result=self.x._comparison(augmented,snapshot,newroots,newoptions)
            except FutureError:return self.blocked(c,roots,'support_unavailable',options,snapshot)
            if result['status']=='stale':return self.blocked(c,roots,'support_unavailable',options,snapshot)
            title=' / '.join(c.get(r).title for r in roots)[:160]
            prefix='已核对的选择 · ' if p.get('language')=='zh-CN' else 'Reviewed choices · '
            scenario=SavedScenario(x.meta('scenario',source),prefix+title,selected,frame.meta.ref)
            review=self.x._draft((*records,frame,scenario),'save_scenario',dict(title=title,meaning='simulation_resume',changes=[v for k,v in {str(v):v for v in changes}.items()],
                choices=[dict(label=c.get(r).label,amount=_wire(c.get(r).effects[0].planning) if len(c.get(r).effects)==1 else None) for r in options],
                selection=dict(roots=[wire_ref(r) for r in newroots],options=[wire_ref(r) for r in newoptions])))
            return review

    def blocked(self,c,roots,reason,options,snapshot):
        affected=[]
        for root in roots:
            local=[o for o in options if c.is_descendant(c.get(c.get(o).variable).node,root)]
            if validity(c,c.get(root),snapshot)['reasons'] or any(validity(c,c.get(o),snapshot)['reasons'] for o in local) or self.sim.j._continuity(c,local):affected.append(root)
        roots=affected or roots
        items=[]
        for r in roots:
            local=[c.get(o) for o in options if c.is_descendant(c.get(c.get(o).variable).node,r)]
            states=[validity(c,c.latest[(r.kind,r.id)],snapshot),*(validity(c,o,snapshot) for o in local)]
            reasons=sorted({reason for state in states for reason in state['reasons']})
            effects=[e for o in local for e in o.effects]
            estimate=effects[0].planning if len(effects)==1 else None
            renew=bool(reasons) and set(reasons)<= {'evidence_stale','future_version_changed'} and estimate is not None and estimate.precision.value!='unknown'
            fact='estimate_confirmation' if renew else 'original_payment' if reason=='changed_path' else 'current_financial_record' if any(s.startswith('world_') or s=='decision_context_changed' for s in reasons) else 'current_estimate'
            items.append(dict(ref=wire_ref(r),title=c.get(r).title,needed_fact=fact,reasons=reasons,
                amount=_wire(estimate),can_confirm_estimate=renew))
        return dict(stage='needs_input',reason=reason,items=items,financial_authority=False)

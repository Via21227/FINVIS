"""Explicit live D1/D2 retention adapter. Never migrates vanished RAM history.

Only verified exact current artifacts enter. Unsafe mapping stops with a code;
original baselines, If IDs/generations, evidence and parameter intent are retained.
"""
from datetime import datetime, timezone
from dataclasses import replace
from hashlib import sha256

from .decision_baseline import digest, BaselineService
from .domain import Ref, Money
from .if_lines import IfLineService
from .persistence_evidence import canonical
from .reviewed_proposals import _wire
from .world_values import AmountEstimate
from .future_space import (FutureError, FutureRef, FutureMeta, FutureEvidence, WorldDependency,
    ContextBinding, FutureNode, FutureVariable, FutureOption, FutureEffect, FutureCatalog,
    ReferenceFrame, SavedScenario, require)


def prepare_legacy(memory, lines, baseline_ref, line_ids, operation_id, combination_id=None, at=None):
    """Return an explicit, nonpublishing import operation for review/retention.

    A new Future container is a version-pinned import of one exact D1 baseline,
    not an inferred merge of similarly named Decisions or separate generations.
    """
    at=at or datetime.now(timezone.utc)
    require(type(lines) is IfLineService and type(lines.baselines) is BaselineService, 'live_legacy_services_required')
    require(type(baseline_ref) is Ref and type(line_ids) is tuple and len(set(line_ids))==len(line_ids), 'exact_legacy_selection_required')
    with memory.store._lock:
        catalog,_,snapshot=memory.read()
        try: read=lines.baselines.authority.read()
        except Exception: raise FutureError('legacy_authority_unavailable') from None
        require(read.snapshot==snapshot and read.version.session_generation==memory.store.session_generation
                and read.context==memory.store.context, 'legacy_authority_mismatch')
        baseline=lines.baseline(_wire(baseline_ref))
        require(baseline.components, 'empty_legacy_baseline')
        selected=[]
        for identity in line_ids:
            require(identity in lines.lines, 'legacy_if_missing')
            line=lines.lines[identity]
            require(line.baseline==baseline and lines.validity(line)[0], 'legacy_if_not_current_or_different_basis')
            selected.append(line)
        # No import of old generations or silently superseding an earlier import.
        for old in catalog.records:
            if any(e.origin=='legacy_d1' and e.identity==baseline.ref.id and e.generation==baseline.generation
                   for e in old.meta.evidence):
                raise FutureError('legacy_basis_already_retained_use_exact_operation_retry')
        prefix='legacy/'+sha256(canonical((memory.scope.store_id,baseline.generation,baseline.ref.id,baseline.ref.version)).encode()).hexdigest()[:32]
        ref=lambda kind,key:FutureRef(kind,prefix+'/'+key,1)
        ev=FutureEvidence('legacy_d1',baseline.ref.id,baseline.ref.version,baseline.generation,baseline.created_at,
            'Explicitly retained reviewed baseline',canonical(_wire(baseline)))
        full_baseline_evidence=ev
        ev=replace(ev,payload=canonical(dict(baseline_ref=_wire(baseline.ref),source_digest=digest(baseline))))
        deps=tuple(WorldDependency(r,h) for r,h in baseline.dependencies)
        context_binding=ContextBinding(baseline.decision_ref,baseline.linked_contexts)
        def meta(r,evidence=(ev,),dependencies=(),contexts=()):
            return FutureMeta(memory.scope,r,None,at,evidence,dependencies,contexts)
        event=ref('node','event')
        decision=snapshot.get(baseline.decision_ref)
        rows=[FutureNode(meta(event,(full_baseline_evidence,),dependencies=deps,contexts=(context_binding,)),decision.subject,'event',baseline.decision_ref.ref.id,
                         context=(canonical(_wire(baseline.context)),))]
        components={}; default_options=[]
        for component in baseline.components:
            node=ref('node','component/'+component.id); variable=ref('variable','choice/'+component.id); reference=ref('option','reference/'+component.id)
            components['component:'+component.id]=(node,variable,component)
            evidence=(ev,)
            if component.external:
                quote=component.external
                evidence=(FutureEvidence('manual_quote',quote.evidence_ref,1,None,quote.retrieved_at,
                    quote.source,canonical(_wire(quote)),quote.freshness,quote.valid_until),)
            rows.extend((FutureNode(meta(node),component.label,'component',component.id,event),
                FutureVariable(meta(variable),node,'Reported alternative','user_choice',reference),
                FutureOption(meta(reference,evidence),variable,component.label,
                    (FutureEffect(prefix+'/'+component.id,'cost',component.observed_amount,component.planning_amount,
                        'legacy_reported_scope',('legacy-component:'+baseline.ref.id+'/'+component.id,)),),
                    component.assumptions or ('Existing reviewed estimate',))))
            default_options.append(reference)
        inflow_targets=set(k for line in selected for k in (*line.depends_on, *((line.target,) if line.target and line.target.startswith('inflow:') else ())))
        inflows={};roots=[event]
        for key in sorted(inflow_targets):
            source=next((r for r in snapshot.world.members if r.record_kind=='future_inflow' and 'inflow:'+r.ref.id==key),None)
            require(source is not None, 'legacy_inflow_missing')
            value=snapshot.get(source)
            node=ref('node','external/'+source.ref.id);var=ref('variable','outcome/'+source.ref.id);received=ref('option','received/'+source.ref.id)
            roots.append(node);infl=WorldDependency(source,digest(value))
            evidence=FutureEvidence('user_report',source.ref.id,source.ref.version,None,at,'Existing expected inflow; not received cash')
            observed=value.expected_amount
            from .world_values import AmountPrecision
            planning=AmountEstimate.approximate(observed.amount) if observed.precision is AmountPrecision.EXACT_REPORTED else observed
            rows.extend((FutureNode(meta(node,(evidence,),(infl,)),value.purpose,'event',source.ref.id),
                FutureVariable(meta(var,(evidence,)),node,'External outcome','external_outcome',received),
                FutureOption(meta(received,(evidence,)),var,'Expected arrival',
                    (FutureEffect(prefix+'/inflow/'+source.ref.id,'expected_inflow',observed,planning,'legacy_reported_scope',('world:'+source.ref.id,)),),
                    ('Hypothetical expected inflow; not a controllable choice',))))
            inflows[key]=(var,received,value);default_options.append(received)
        option_by_line={}
        for line in selected:
            evidence=FutureEvidence('legacy_d2',line.id,len(lines.evaluations.get(line.id,())) or 1,baseline.generation,
                line.created_at,line.source_text,canonical(_wire(line)))
            option=ref('option','if/'+line.id); requires=tuple(inflows[k][1] for k in line.depends_on)
            option_by_line[line.id]=option
            extra=tuple(WorldDependency(r,h) for r,h in line.extra_dependencies)
            assumptions=tuple(line.assumptions)
            if line.parameter:
                assumptions=(*assumptions,canonical(line.parameter))
            if line.kind=='add':
                # D2 ADD has explicit independent intent but no source overlap proof.
                # Require that no other imported ADD repeats source/label, as D2 does.
                require(not any(other.id!=line.id and other.kind=='add' and
                    (other.source_text.casefold()==line.source_text.casefold() or other.label.casefold()==line.label.casefold())
                    for other in selected), 'ambiguous_legacy_add_overlap')
                node=ref('node','addition/'+line.id);var=ref('variable','addition/'+line.id)
                rows.extend((FutureNode(meta(node),line.label,'component',line.id,event),
                             FutureVariable(meta(var),node,'Optional reported addition','user_choice')))
                effect=FutureEffect(prefix+'/'+line.id,'cost',line.observed_amount,line.planning_amount,'legacy_reported_scope',('legacy-add:'+line.id,))
                # No unreported zero option is invented for an absent addition.
            elif line.target in components:
                node,var,component=components[line.target]
                observed,planning=line.observed_amount,line.planning_amount
                if line.kind=='remove':
                    observed=planning=AmountEstimate.exact_reported(Money(0,component.planning_amount.currency))
                effect=FutureEffect(prefix+'/'+component.id,'cost',observed,planning,'legacy_reported_scope',
                    ('legacy-component:'+baseline.ref.id+'/'+component.id,),line.kind=='remove')
            elif line.kind=='remove' and line.target in inflows:
                var,_,value=inflows[line.target]
                zero=AmountEstimate.exact_reported(Money(0,value.expected_amount.currency))
                effect=FutureEffect(prefix+'/inflow/'+value.ref.id,'expected_inflow',zero,zero,'legacy_reported_scope',('world:'+value.ref.id,),True)
            else:
                raise FutureError('legacy_mapping_unsupported')
            rows.append(FutureOption(meta(option,(evidence,),extra),var,line.label,(effect,),assumptions,requires))
        staged=FutureCatalog(memory.scope,(*catalog.records,*rows))
        # Frame contains only explicitly existing baseline states. Optional ADD
        # questions without a reported reference remain honest missing selections.
        selection=staged.select(tuple(roots),tuple(default_options),baseline.scope_complete)
        frame=ReferenceFrame(meta(ref('reference','baseline')), 'Retained reference estimate',selection)
        rows.append(frame)
        if combination_id is not None:
            require(combination_id in lines.combinations, 'legacy_combination_missing')
            combo=lines.combinations[combination_id]
            require(set(combo['line_ids'])==set(line_ids) and not lines.conflicts(selected), 'legacy_combination_incompatible')
            chosen={staged.get(r).variable.id:r for r in default_options}
            for r in option_by_line.values():chosen[staged.get(r).variable.id]=r
            scenario_selection=staged.select(tuple(roots),tuple(chosen.values()),baseline.scope_complete)
            ce=FutureEvidence('legacy_combination',combination_id,len(lines.evaluations.get(combination_id,())) or 1,
                baseline.generation,at,'Explicit retained existing combination',canonical(_wire(combo)))
            rows.append(SavedScenario(meta(ref('scenario','combination/'+combination_id),(ce,)),
                'Retained legacy combination',scenario_selection,frame.meta.ref))
        return memory.prepare(operation_id,tuple(rows),'import_legacy',at)

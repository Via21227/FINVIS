"""FS3: receipt-derived choices; native D/I4 plans and E settlement remain truth.

A closed source capsule travels inside the exact financial command. Its pure
expansion is verified both during execution and replay, without a Future DB.
The capsule is not an independently writable plan or an authorization.
"""
from dataclasses import replace
from datetime import datetime, timezone
from uuid import uuid4
import json
import re

from .future_space import FutureCatalog, FutureOption, SavedScenario, dumps, loads, require
from .future_experience import fields, sentence, ref, wire_ref
from .future_memory import validity
from .domain import Ref, DecisionStatus
from .world_values import TypedRef, ObservationRole, TemporalSpec, AmountPrecision
from .world_planning import WorldDecision, PlannedExpense, PlannedExpenseStatus as S
from .world_fulfillment import ExpenseFulfillment
from .world_commands import CandidateChangeSet, PutRecord, ChangeMode, FinancialWorldCommand, record_ref
from .world_serialization import payload_digest
from .plan_reality import metadata
from .reviewed_proposals import _wire
from .decision_baseline import digest as world_digest
from .i3_workflow import I3Error, _preview_view

SCHEMA='finvis.future-adoption.v0.1'

def payment_label(label,effect):
    if effect.payment_basis and effect.payment_basis.kind=='total' and any(p.role=='credited' for p in effect.payment_basis.parts):
        return label+(' · 剩余付款' if re.search('[\u4e00-\u9fff]',label) else ' · Remaining payment')
    return label

def canonical(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'))

def decode_origin(source):
    require(type(source) is str and len(source.encode())<=2*1024*1024,'future_origin_invalid')
    p=json.loads(source)
    require(type(p) is dict and set(p)=={'schema','catalog','selection','scenario','adopted','identity','gradual'},'future_origin_invalid')
    require(p['schema']==SCHEMA and canonical(p)==source,'future_origin_invalid')
    scope,records=loads(p['catalog']);c=FutureCatalog(scope,records);selection=loads(p['selection'])
    missing,_=c.check_selection(selection)
    require(c.select(selection.roots,selection.options,selection.scope_complete,selection.unresolved)==selection,'future_origin_invalid')
    require(not any(x.startswith(('activation_unknown:','prerequisite_missing:')) for x in missing),'future_effect_needs_review')
    require(type(p['identity']) is str and bool(re.fullmatch('[a-f0-9]{32}',p['identity'])),'future_origin_invalid')
    require(type(p['adopted']) is list and type(p['gradual']) is list,'future_origin_invalid')
    adopted=tuple(ref(r) for r in p['adopted']);gradual=tuple(ref(r) for r in p['gradual'])
    require(bool(adopted) and len(set(adopted))==len(adopted) and set(adopted)<=set(selection.options) and set(gradual)<=set(adopted),'future_origin_invalid')
    if p['scenario'] is not None:
        scenario=c.get(ref(p['scenario']))
        require(type(scenario) is SavedScenario and scenario.selection==selection,'future_origin_invalid')
    for r in adopted:
        option=c.get(r);v=c.get(option.variable)
        require(v.control=='user_choice','future_external_not_adoptable')
        require(len(option.effects)==1,'future_effect_needs_review')
        e=option.effects[0]
        require(e.effect_type=='cost' and e.time_basis=='one_time' and e.planning.precision is not AmountPrecision.UNKNOWN,'future_effect_needs_review')
        require(not e.absence or r not in gradual,'future_origin_invalid')
    return p,c,selection,adopted,gradual


def choices(snapshot):
    """Only completed receipt-bound commands contribute chosen-path history."""
    result={}
    for command,receipt in zip(snapshot.commands,snapshot.receipts):
        if command.change_set.future_origin is None:continue
        p,c,selection,adopted,_=decode_origin(command.change_set.future_origin)
        for r in adopted:
            o=c.get(r);v=c.get(o.variable)
            decision=next(op.record for op in command.change_set.operations if type(op.record) is WorldDecision and op.record.selected_option_ref==Ref(r.id,r.version))
            plan=next((op.record for op in command.change_set.operations if type(op.record) is PlannedExpense and op.record.status is S.PLANNED and op.record.decision_ref==record_ref(decision)),None)
            result[v.meta.ref.id]=dict(option=o,variable=v,node=c.get(v.node),decision=decision,plan=plan,
                receipt=receipt,origin=p,reference=c.get(v.reference_option) if v.reference_option else None)
    return result


def expansion(snapshot,change):
    p,c,selection,adopted,gradual=decode_origin(change.future_origin)
    require(c.scope.owner_id==change.owner_id and c.scope.world_id==change.world_id,'foreign_future_record')
    require(change.mode is ChangeMode.MERGE and change.replacement_scope is None,'future_origin_invalid')
    previous=choices(snapshot);ops=[]
    live={(r.record_kind,r.ref.id):snapshot.get(r) for r in snapshot.world.members}
    for index,r in enumerate(adopted):
        o=c.get(r);v=c.get(o.variable);n=c.get(v.node);e=o.effects[0];prior=previous.get(v.meta.ref.id)
        old=None
        require(not any(d.ref.record_kind=='planned_expense' for d in n.meta.dependencies),'future_existing_plan_needs_review')
        if prior:
            old=live.get(('world_decision',prior['decision'].ref.id))
            require(old==prior['decision'],'future_choice_changed')
            require(prior['option'].meta.ref!=r,'future_already_chosen')
        else:
            deps=[d.ref for d in n.meta.dependencies if d.ref.record_kind=='world_decision']
            require(len(deps)<=1,'future_target_ambiguous')
            if deps:
                old=live.get(('world_decision',deps[0].ref.id))
                require(old is not None and record_ref(old)==deps[0],'world_input_changed')
                require(not any(type(x) is PlannedExpense and x.decision_ref==record_ref(old) and x.status in (S.PLANNED,S.PARTIALLY_REALIZED) for x in live.values()),'future_existing_plan_needs_review')
        oldplan=live.get(('planned_expense',prior['plan'].ref.id)) if prior and prior['plan'] else None
        if oldplan:
            require(oldplan.status is S.PLANNED and oldplan.occurrence_ref is None and
                not (oldplan.fulfillment and (oldplan.fulfillment.occurrence_refs or oldplan.fulfillment.committed_amount)), 'future_plan_already_happened')
        identity=p['identity']+'/'+str(index)
        purpose=payment_label(n.title+' · '+o.label,e)
        source='Chosen intended path: '+payment_label(n.title+' — '+o.label,e)
        meta=metadata(source,ObservationRole.CONTEMPLATED,change.created_at,identity,old)
        dr=Ref(old.ref.id,old.ref.version+1) if old else Ref('future/decision/'+identity,1)
        decision=WorldDecision(dr,change.owner_id,change.world_id,v.question,n.title,
            TypedRef('world_state',snapshot.world.state_ref),DecisionStatus.SELECTED,Ref(r.id,r.version),
            None if e.absence else e.planning,old.timing if old else TemporalSpec.unknown('Future timing not confirmed'),
            old.related_refs if old else (),meta)
        ops.append(PutRecord('future/decision/'+str(index),decision,record_ref(old) if old else None))
        if oldplan:
            closed=replace(oldplan,ref=Ref(oldplan.ref.id,oldplan.ref.version+1),status=S.CANCELLED,
                decision_ref=record_ref(decision),observation=metadata('Changed intended path: '+o.label,ObservationRole.PLANNED,change.created_at,identity,oldplan))
            ops.append(PutRecord('future/close/'+str(index),closed,record_ref(oldplan)))
        if not e.absence:
            plan=PlannedExpense(Ref('future/plan/'+identity,1),change.owner_id,change.world_id,e.planning,
                purpose,decision.timing,(),record_ref(decision),S.PLANNED,
                metadata(source,ObservationRole.PLANNED,change.created_at,identity),
                fulfillment=ExpenseFulfillment() if r in gradual else None)
            ops.append(PutRecord('future/plan/'+str(index),plan))
    return tuple(ops)


def validate_origin(snapshot,change):
    require(change.operations==expansion(snapshot,change),'future_origin_operations_mismatch')


class FutureAdoption:
    def __init__(self,workflow,experience):
        self.w=workflow;self.x=experience;self.pending=None

    def selection(self,payload):
        c,_,snapshot=self.x.memory.read()
        if payload.get('scenario'):
            row=self.x._current(c,payload['scenario'],'scenario');selection=row.selection
        else:
            roots=tuple(ref(r) for r in payload.get('roots',[]));options=tuple(ref(r) for r in payload.get('options',[]))
            selection=c.select(roots,options)
        return c,snapshot,selection

    def check_current(self,c,snapshot,selection,adopted):
        prior=choices(snapshot)
        # A newly itemized Future is still the same occurrence. Do not create
        # child plans alongside an already chosen whole-event plan (or reverse
        # that direction). Existing cancellation/Reality review resolves this;
        # old command expansion and historical replay remain unchanged.
        live_plans={r.ref.id:snapshot.get(r) for r in snapshot.world.members if r.record_kind=='planned_expense'}
        for r in adopted:
            option=c.get(r);node=c.get(option.variable).node
            ancestor=c.get(node)
            while ancestor:
                for dependency in ancestor.meta.dependencies:
                    require(dependency.ref.record_kind!='planned_expense','future_existing_plan_needs_review')
                    if ancestor.meta.ref.id!=node.id and dependency.ref.record_kind=='world_decision':
                        require(not any(p.decision_ref and p.decision_ref.ref.id==dependency.ref.ref.id
                            and p.status is not S.CANCELLED for p in live_plans.values()),'future_structure_plan_needs_review')
                ancestor=c.get(ancestor.parent) if ancestor.parent else None
            for previous in prior.values():
                plan=previous['plan'];other=previous['node'].meta.ref
                if not plan or other.id==node.id:continue
                live=live_plans.get(plan.ref.id)
                if live is None:continue
                related=c.is_descendant(node,other) or c.is_descendant(other,node)
                if not related:continue
                happened=live.occurrence_ref is not None or live.fulfillment and (live.fulfillment.occurrence_refs or live.fulfillment.committed_amount)
                require(live.status is S.CANCELLED and not happened,'future_structure_plan_needs_review')
        adjusted=[]
        for row in c.records:
            deps=[]
            for d in row.meta.dependencies:
                own=next((x for x in prior.values() if x['decision'].ref.id==d.ref.ref.id and d.ref.record_kind=='world_decision'),None)
                if own and record_ref(own['decision']) in snapshot.world.members:
                    deps.append(replace(d,ref=record_ref(own['decision']),digest=world_digest(own['decision'])))
                else:deps.append(d)
            adjusted.append(replace(row,meta=replace(row.meta,dependencies=tuple(deps))))
        checked=FutureCatalog(c.scope,tuple(adjusted))
        missing,_=c.check_selection(selection)
        require(not any(x.startswith(('activation_unknown:','prerequisite_missing:')) for x in missing),'future_effect_needs_review')
        for r in (*c.consumed_structure(selection),*selection.options):
            state=validity(checked,checked.get(r),snapshot)
            require(state['status']!='stale','future_inputs_stale')
        # An adopted plan with reality cannot be re-chosen as a new zero-history
        # plan. New/different real events still use the ordinary Reality route.
        for r in adopted:
            o=c.get(r);prior_row=prior.get(o.variable.id)
            if prior_row and prior_row['plan']:
                plan=next((snapshot.get(x) for x in snapshot.world.members if x.record_kind=='planned_expense' and x.ref.id==prior_row['plan'].ref.id),None)
                require(plan is not None and plan.status is S.PLANNED,'future_plan_already_happened')

    def prepare(self,payload):
        fields(payload,'roots options scenario adopted gradual')
        c,snapshot,selection=self.selection(payload)
        adoptable=tuple(r for r in selection.options if c.get(c.get(r).variable).control=='user_choice')
        adopted=tuple(ref(r) for r in payload['adopted']) if 'adopted' in payload else adoptable
        require(bool(adopted),'future_no_controllable_choice')
        self.check_current(c,snapshot,selection,adopted)
        identity=uuid4().hex;at=datetime.now(timezone.utc)
        origin=canonical(dict(schema=SCHEMA,catalog=dumps((c.scope,c.records)),selection=dumps(selection),
            scenario=payload.get('scenario'),adopted=[wire_ref(r) for r in adopted],identity=identity,gradual=payload.get('gradual',[])))
        # Expand before constructing the nonempty, closed command value.
        from types import SimpleNamespace
        context=dict(owner_id=snapshot.world.owner_id,world_id=snapshot.world.world_id,mode=ChangeMode.MERGE,replacement_scope=None,created_at=at,future_origin=origin)
        ops=expansion(snapshot,SimpleNamespace(**context))
        touched={op.expected_ref for op in ops if op.expected_ref}
        change=CandidateChangeSet(Ref('future/change/'+identity,1),context['owner_id'],context['world_id'],self.w.store.session_generation,
            snapshot.world.ref,snapshot.world.state_ref,snapshot.world.plan_ref,snapshot.revision,ChangeMode.MERGE,ops,None,
            tuple(r for r in snapshot.world.members if r not in touched),(),metadata('Explicit intended Future choices',ObservationRole.CURRENT,at,identity),None,(),at,origin)
        preview=self.w.store.preview(self.w.context.owner_id,change,Ref('future/preview/'+identity,1))
        if self.pending:self.cancel({})
        self.pending=dict(preview=preview,grant=None,selection=selection,adopted=adopted,summary=self.describe(c,selection,adopted,payload.get('gradual',[])))
        return self.view()

    @staticmethod
    def describe(c,selection,adopted,gradual):
        return [dict(option=wire_ref(r),event=c.get(c.get(c.get(r).variable).node).title,label=payment_label(c.get(r).label,c.get(r).effects[0]) if c.get(r).effects else c.get(r).label,
            adopted=r in adopted,control=c.get(c.get(r).variable).control,absence=all(e.absence for e in c.get(r).effects),
            estimate=_wire(c.get(r).effects[0].planning) if c.get(r).effects else None,
            assumptions=list(c.get(r).assumptions),gradual=wire_ref(r) in gradual) for r in selection.options]

    def exact(self,payload):
        p=self.pending
        require(p is not None and p['preview'].ref.id==payload['preview_id'] and p['preview'].digest==payload['digest'],'future_review_expired')
        c,_,s=self.x.memory.read();require(s.world.ref==p['preview'].before_world.ref,'world_input_changed')
        self.check_current(c,s,p['selection'],p['adopted'])
        return p

    def authorize(self,payload):
        fields(payload,'preview_id digest',('preview_id','digest'));p=self.exact(payload)
        if p['grant'] is None:p['grant']=self.w.store.authorize(self.w.context.owner_id,p['preview'],Ref('future/authorization/'+uuid4().hex,1),datetime.now(timezone.utc))
        return self.view()

    def apply(self,payload):
        # Keep exact Future-head validation and financial commit in the same
        # store lock; direct FS1 retention cannot race between these steps.
        with self.w.store._lock:
            return self._apply(payload)

    def _apply(self,payload):
        fields(payload,'preview_id digest authorization_id',('preview_id','digest','authorization_id'))
        from .i4_commit import _Operation
        identity=sentence(payload['authorization_id']);operation=self.w._load_operation(identity)
        if operation:
            require(operation.proposal_id=='future_adoption' and (operation.preview_id,operation.preview_digest)==(payload['preview_id'],payload['digest']),'future_review_expired')
            if self.w._receipt(operation):return dict(stage='applied',operation=self.w._status(operation,recovered=True))
        p=self.exact(payload)
        require(p['grant'] is not None and p['grant'].ref.id==identity,'future_review_expired')
        if operation is None:
            cmd=FinancialWorldCommand('i4/'+identity,self.w.context.owner_id,p['preview'].change_set,p['preview'].ref,p['preview'].digest,p['grant'].ref,max(datetime.now(timezone.utc),p['grant'].granted_at))
            operation=_Operation(cmd,'','future_adoption',payload_digest(cmd.change_set),payload['preview_id'],payload['digest'],identity)
            self.w._operations[identity]=operation
        self.w._execute_command(operation)
        return dict(stage='applied',operation=self.w._status(operation))

    def cancel(self,payload):
        fields(payload,'')
        if self.pending:
            from .store import Rejected
            try:self.w.store.cancel(self.w.context.owner_id,self.pending['preview'].ref)
            except Rejected:pass
        self.pending=None
        return dict(stage='idle')

    def view(self):
        p=self.pending
        return dict(stage='authorized' if p['grant'] else 'preview',summary=p['summary'],preview=_preview_view(p['preview'],self.w.store.snapshot),authorization=dict(id=p['grant'].ref.id) if p['grant'] else None)

    def listing(self):
        s=self.w.store.snapshot;previous=choices(s)
        if not previous:return []
        items=self.w.plan_reality.summary()['items'];result=[]
        for row in previous.values():
            d=row['decision'];live=next((s.get(r) for r in s.world.members if r.record_kind=='world_decision' and r.ref.id==d.ref.id),None)
            current=live==d
            item=next((i for i in items if row['plan'] and i['ref']['ref']['id']==row['plan'].ref.id),None)
            result.append(dict(option=wire_ref(row['option'].meta.ref),variable=wire_ref(row['variable'].meta.ref),node=wire_ref(row['node'].meta.ref),
                label=row['option'].label,event=row['node'].title,current=current,absence=row['plan'] is None,
                plan=item,reference=row['reference'].label if row['reference'] else None,
                reference_estimate=_wire(row['reference'].effects[0].planning) if row['reference'] and row['reference'].effects else None,receipt=_wire(row['receipt'].ref),
                assumptions=self.describe(*decode_origin(canonical(row['origin']))[1:4],row['origin']['gradual'])))
        return result

    def space(self):
        result=self.x.listing();adoptions=self.listing();result['adoptions']=adoptions
        if not adoptions:return result
        catalog,_,_=self.x.memory.read()
        for row in result['records']:
            if row['ref']['kind']!='scenario':continue
            affected=[]
            for selected in row['selected']:
                o=catalog.get(ref(selected['ref']))
                a=next((a for a in adoptions if a['variable']['id']==o.variable.id),None)
                if a and (not a['current'] or a['option']!=selected['ref'] or a['plan'] and a['plan']['events']):affected.append(selected['label'])
            if affected:
                row['status']='stale';row['reasons']=list(set(row['reasons']+['future_path_changed']))
                row['changed_items']+=affected
                row['result']=dict(status='stale',buckets=[],reasons=row['reasons'],financial_authority=False)
        return result

    def interpret(self,payload):
        fields(payload,'text node scenario roots options proposal_id',('text',));source=sentence(payload['text'])
        if re.search(r'\b(?:if|maybe|might|whether)\b|如果|假设|可能|是否|[?？]',source,re.I):return dict(stage='unhandled')
        from .plan_reality import meaning
        kind,_=meaning(source)
        c,_,snapshot=self.x.memory.read()
        rows=[o for o in c.latest.values() if type(o) is FutureOption and not o.meta.archived and c.get(o.variable).control=='user_choice']
        node=ref(payload['node']) if payload.get('node') else None
        from .actual_report import parse_actual_report, matches_report
        report=parse_actual_report(source)
        if report:
            # The visible If draft is a validated overlay, not retained memory.
            # Reading it supplies candidates only. Its existing explicit Keep
            # boundary must still complete before an authoritative plan review.
            pending_refs=set()
            if payload.get('proposal_id'):
                interaction=getattr(self.w,'compound_if',None)
                require(interaction is not None and interaction.x is self.x,'future_review_expired')
                c,_,snapshot,operation=interaction._catalog(sentence(payload['proposal_id']))
                pending_refs={r.meta.ref for r in operation.records}
            # Browser focus/current selection never overrides an explicit named
            # quoted option. Date-only successors are one semantic choice.
            from .future_timing import timing_predecessor
            selected=[o for o in c.latest.values() if type(o) is FutureOption and not o.meta.archived
                      and c.get(o.variable).control=='user_choice' and len(o.effects)==1 and o.effects[0].effect_type=='cost'
                      and not o.effects[0].absence and matches_report(report,o.label,o.effects[0].planning)]
            predecessors={timing_predecessor(c,o) for o in selected}
            selected=[o for o in selected if o.meta.ref not in predecessors]
            current=self.listing()
            matches=[a for a in current if a['current'] and a['plan'] and
                     any(a['option']==wire_ref(o.meta.ref) for o in selected)]
            if not matches:
                candidates=[]
                for option in selected:
                    target_node=c.get(c.get(option.variable).node)
                    candidate=dict(label=target_node.title+' · '+option.label,
                        payload=dict(roots=[wire_ref(target_node.meta.ref)],options=[wire_ref(option.meta.ref)]))
                    # Itemized components can depend on an already selected
                    # structural parent. Preserve that exact zero-effect context
                    # for review; never select a financial/external condition.
                    context={ref(r) for r in payload.get('options',[])}
                    needed=set(c.get(option.variable).active_when)|set(option.requires)
                    ancestor=target_node
                    while ancestor:
                        needed.update(ancestor.active_when)
                        top=ancestor
                        ancestor=c.get(ancestor.parent) if ancestor.parent else None
                    structural=[r for r in sorted(needed & context,key=lambda r:(r.id,r.version))
                        if not c.get(r).effects and c.get(c.get(r).variable).control=='user_choice']
                    if structural:
                        candidate['payload']=dict(roots=[wire_ref(top.meta.ref)],
                            options=[wire_ref(r) for r in (*structural,option.meta.ref)],adopted=[wire_ref(option.meta.ref)])
                    if pending_refs:
                        # Preserve the whole visible draft (including unselected
                        # independent items), rather than finalizing a filtered
                        # Keep and silently discarding the remaining work.
                        candidate.update(keep_payload=dict(proposal_id=payload['proposal_id']),keep_scope='current_future')
                    candidates.append(candidate)
                return dict(stage='clarify',reason='actual_plan_required',question='future_which_choice',
                    source_text=source,choices=candidates)
            if pending_refs:
                # Even an existing plan's eventual actual Apply changes the
                # financial basis of unrelated drafts. Offer the same explicit
                # Keep before entering Reality, rather than expiring that work.
                return dict(stage='clarify',reason='actual_plan_required',question='future_which_choice',
                    source_text=source,choices=[dict(label=a['event']+' · '+a['label'],
                    payload=dict(roots=[a['node']],options=[a['option']]),
                    keep_payload=dict(proposal_id=payload['proposal_id']),keep_scope='current_future',
                    continue_actual=True) for a in matches])
            keys=[a['plan']['key'] for a in matches]
            from .i3_workflow import I3Workflow
            def router(text,read,request_id):
                self.w.reality_routing.active_request=request_id
                return self.w.plan_reality.route(text,read,request_id,keys)
            return dict(stage='reality',routing=I3Workflow.interpret(self.w,source,route=router)['routing'])
        if node:rows=[o for o in rows if c.is_descendant(c.get(o.variable).node,node)]
        if kind in ('actual','actual_complete','refund','replacement','commit','complete'):
            adopted=self.listing()
            matches=[a for a in adopted if a['current'] and a['plan'] and (node and c.is_descendant(ref(a['node']),node) or not node and (a['label'].casefold() in source.casefold() or a['event'].casefold() in source.casefold()))]
            if not matches and not node:
                # Bare report has explicit deictic wording; unrelated named real
                # events keep the ordinary Agent/Reality path.
                if not re.search(r'最后|终于|\b(?:finally|it|this|that)\b',source,re.I):return dict(stage='unhandled')
                matches=[a for a in adopted if a['current'] and a['plan']]
            if not matches:return dict(stage='unhandled')
            keys=[a['plan']['key'] for a in matches]
            from .i3_workflow import I3Workflow
            def router(text,read,request_id):
                self.w.reality_routing.active_request=request_id
                return self.w.plan_reality.route(text,read,request_id,keys) or dict(kind='clarification',reason='lifecycle_uncertain')
            return dict(stage='reality',routing=I3Workflow.interpret(self.w,source,route=router)['routing'])
        absent=bool(re.search(r'不买|没买|不去|不参加|\b(?:not (?:to )?buy|didn.t buy|no purchase|not (?:to )?proceed)\b',source,re.I))
        choose=bool(re.search(r'决定|就按|还是|选(?:择|这个|那个)|\b(?:decid\w*|choose|chosen|go with|proceed|switch)\b',source,re.I))
        if not (absent or choose):return dict(stage='unhandled')
        if not absent and re.search(r'\b(?:not|never|haven.t|don.t)\b|还没|没有决定|未决定|不决定',source,re.I):return dict(stage='unhandled')
        if not absent and payload.get('scenario') and re.search(r'方案|scenario|plan|this|这个',source,re.I):return self.prepare(dict(scenario=payload['scenario']))
        if not absent and payload.get('options') and re.search(r'方案|these|this|这个|这些',source,re.I):return self.prepare({k:payload[k] for k in ('roots','options')})
        if absent:rows=[o for o in rows if all(e.absence for e in o.effects)]
        else:
            # Generic condition words apply equally to equipment, services,
            # housing etc.; ties remain a question, never iteration order.
            used=r'二手|\b(?:used|second.hand)\b';new=r'新的|全新|\bnew\b'
            pattern=used if re.search(used,source,re.I) else new if re.search(new,source,re.I) else None
            matches=[o for o in rows if re.search(pattern,o.label,re.I)] if pattern else [o for o in rows if o.label.casefold() in source.casefold()]
            if matches:rows=matches
        options=[]
        for o in rows:
            n=c.get(c.get(o.variable).node)
            options.append(dict(label=n.title+' · '+o.label,payload=dict(roots=[wire_ref(n.meta.ref)],options=[wire_ref(o.meta.ref)])))
        if len(options)==1:return self.prepare(options[0]['payload'])
        return dict(stage='clarify',question='future_which_choice',choices=options)

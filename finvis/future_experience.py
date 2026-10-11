"""FS2 application preparation and presentation over FS1. No financial writer.

Drafts are process-local and version-pinned. Only explicit confirmation retains
an FS1 operation. Selection and comparison are pure; unknowns are never zero.
"""
from dataclasses import replace
from datetime import datetime, timezone
from uuid import uuid4
import re

from .future_space import (FutureRef, FutureMeta, FutureEvidence, WorldDependency, ContextBinding,
    FutureNode, FutureVariable, FutureOption, FutureEffect, FutureCatalog,
    ReferenceFrame, SavedScenario, FutureError, require)
from .future_memory import validity
from .decision_baseline import amount, typed_ref, digest, linked
from .simple_planning import price_from, amount_input
from .world_values import AmountEstimate, AmountPrecision
from .domain import Currency, Money
from .flow_direction import flow_direction, effect_direction
from .reviewed_proposals import _wire


def ref(value):
    require(type(value) is dict and set(value)=={'kind','id','version'}, 'future_request_invalid')
    return FutureRef(**value)


def sentence(value, optional=False):
    require(type(value) is str and len(value)<=1000 and (optional or bool(value.strip())), 'future_text_required')
    return value.strip()


def fields(payload, allowed, required=()):
    require(type(payload) is dict and set(payload)<=set(allowed.split()) and set(required)<=set(payload), 'future_request_invalid')


def wire_ref(value): return dict(kind=value.kind,id=value.id,version=value.version)


def hypothetical(text):
    return bool(re.search(r'\b(?:what if|if|suppose|imagine)\b|如果|假设|要是', text, re.I))


class FutureExperience:
    def __init__(self, memory):
        self.memory=memory
        self.pending={}

    def meta(self, kind, source, identity=None, previous=None, dependencies=()):
        now=datetime.now(timezone.utc)
        r=FutureRef(kind, identity or 'future/'+uuid4().hex, previous.version+1 if previous else 1)
        return FutureMeta(self.memory.scope,r,previous,now,
            (FutureEvidence('user_report','fs2/'+uuid4().hex,1,None,now,source),),tuple(dependencies))

    def listing(self):
        c,revision,snapshot=self.memory.read()
        rows=[]
        for row in c.latest.values():
            if row.meta.archived or not isinstance(row,(FutureNode,FutureVariable,FutureOption,SavedScenario)):continue
            if isinstance(row,FutureNode) and any(s.startswith('finvis.user-selected-budget.v0.1:') for s in row.context):continue
            v=validity(c,row,snapshot)
            item=dict(ref=wire_ref(row.meta.ref),status=v['status'],reasons=v['reasons'],missing=v['missing'],
                      evidence=[e.excerpt for e in row.meta.evidence])
            if isinstance(row,FutureNode):
                item.update(title=row.title,source_refs=[_wire(d.ref) for d in row.meta.dependencies],parent=wire_ref(row.parent) if row.parent else None,context=list(row.context),
                            active_when=[wire_ref(r) for r in row.active_when])
                item['current_sources']=[]
                for dep in row.meta.dependencies:
                    if dep.ref.record_kind not in ('world_decision','future_inflow','planned_expense'):continue
                    current=next((r for r in snapshot.world.members if r.record_kind==dep.ref.record_kind and r.ref.id==dep.ref.ref.id),None)
                    if current is None:continue
                    value=snapshot.get(current)
                    a=next((getattr(value,k) for k in ('proposed_amount','expected_amount','estimated_amount','amount') if getattr(value,k,None) is not None),None)
                    item['current_sources'].append(dict(ref=_wire(current),pinned_ref=_wire(dep.ref),changed=current!=dep.ref,amount=_wire(a),label=next((getattr(value,k) for k in ('subject','purpose','label') if getattr(value,k,None)),''),direction='INFLOW' if current.record_kind=='future_inflow' else 'OUTFLOW' if current.record_kind=='planned_expense' else 'UNKNOWN'))
            elif isinstance(row,FutureVariable):
                item.update(node=wire_ref(row.node),question=row.question,control=row.control,
                            reference=wire_ref(row.reference_option) if row.reference_option else None,
                            active_when=[wire_ref(r) for r in row.active_when])
            elif isinstance(row,FutureOption):
                item.update(variable=wire_ref(row.variable),label=row.label,effects=_wire(row.effects),directions=sorted({effect_direction(e.effect_type) for e in row.effects}),assumptions=list(row.assumptions),
                            requires=[wire_ref(r) for r in row.requires],excludes=[wire_ref(r) for r in row.excludes],structural=not row.effects)
                from .future_timing import timing_predecessor
                predecessor=timing_predecessor(c,row)
                if predecessor:item['timing_replaces']=wire_ref(predecessor)
            else:
                item.update(name=row.name,selected=[dict(ref=wire_ref(r),label=c.get(r).label,
                    event=c.get(c.get(c.get(r).variable).node).title) for r in row.selection.options],
                    roots=[wire_ref(r) for r in row.selection.roots],result=self.memory.inspect(row.selection,row.reference))
            item['changed_items']=[getattr(c.get(ref(a)), 'title', None) or getattr(c.get(ref(a)), 'label', None) or '' for a in [dict(kind=x['kind'],id=x['id'],version=x['version']) for x in v['affected']]]
            if isinstance(row,SavedScenario) and row.reference and item['result']['status']!='stale':
                item['result']['reference_buckets']=c.compile(c.get(row.reference).selection)['buckets']
            rows.append(item)
        return dict(future_revision=revision,financial_revision=snapshot.revision,records=rows,financial_authority=False)

    def _current(self,c,value,kind):
        row=c.get(ref(value));require(row.meta.ref.kind==kind,'future_request_invalid')
        require(c.latest[(kind,row.meta.ref.id)]==row and not row.meta.archived,'future_version_changed')
        return row

    def _comparison(self,c,snapshot,roots,options):
        selection=c.select(tuple(roots),tuple(options))
        refs=tuple(c.get(r).reference_option for r in selection.structure if r.kind=='variable' and c.get(r).reference_option)
        # Reference branches must obey the same activation rules as exploration.
        # Do not count an inactive component just because it has a reference.
        while True:
            chosen={c.get(r).variable.id:r for r in refs}
            active=[]
            for r in refs:
                v=c.get(c.get(r).variable);conditions=list(v.active_when);n=c.get(v.node)
                while n is not None:
                    conditions.extend(n.active_when);n=c.get(n.parent) if n.parent else None
                if not any(c.get(q).variable.id in chosen and chosen[c.get(q).variable.id]!=q for q in conditions):active.append(r)
            if tuple(active)==refs:break
            refs=tuple(active)
        # An absent reference remains incomplete; no arbitrary default option.
        basis=c.select(tuple(roots),refs)
        frame=ReferenceFrame(self.meta('reference','Explicitly reviewed comparison basis'),'Reference',basis)
        augmented=FutureCatalog(c.scope,(*c.records,frame))
        rows=[augmented.get(r) for r in (*augmented.consumed_structure(selection),*selection.options,frame.meta.ref)]
        states=[validity(augmented,row,snapshot) for row in rows]
        reasons=sorted({r for s in states for r in s['reasons']})
        if reasons: result=dict(status='stale',buckets=[],reasons=reasons,financial_authority=False)
        else:
            result=augmented.compile(selection,frame.meta.ref)
            if any(s['status']=='needs_information' for s in states):result['status']='needs_information'
            result['reference_buckets']=augmented.compile(basis)['buckets']
        return selection,frame,result

    def preview(self,payload):
        fields(payload,'roots options',('roots','options'))
        require(type(payload['roots']) is list and type(payload['options']) is list and len(payload['roots'])<=64 and len(payload['options'])<=128,'future_request_invalid')
        c,_,snapshot=self.memory.read()
        roots=[self._current(c,r,'node').meta.ref for r in payload['roots']]
        # Exact old options are permitted for inspection but remain stale.
        options=[ref(r) for r in payload['options']]
        from .compound_future import complete_conditionals
        selection=complete_conditionals(c,roots,options)
        options=[ref(r) for r in selection['options']]
        _,_,result=self._comparison(c,snapshot,roots,options)
        result['selection']=selection
        return result

    def prepare_scenario(self,payload):
        fields(payload,'roots options name',('roots','options','name'))
        name=sentence(payload['name'])
        c,_,snapshot=self.memory.read()
        self.preview(dict(roots=payload['roots'],options=payload['options']))
        selection,frame,result=self._comparison(c,snapshot,[ref(r) for r in payload['roots']],[ref(r) for r in payload['options']])
        require(result['status']!='stale','future_inputs_stale')
        record=SavedScenario(self.meta('scenario','Explicitly save '+name),name,selection,frame.meta.ref)
        return self._draft((frame,record),'save_scenario',dict(title=name,result=result,
            labels=[c.get(r).label for r in selection.options],meaning='scenario'))

    def _draft(self,rows,action,summary):
        require(len(self.pending)<256,'future_draft_capacity')
        identity='fs2/'+uuid4().hex
        op=self.memory.prepare(identity,tuple(rows),action)
        self.pending[identity]=op
        return dict(stage='review',proposal_id=identity,summary=summary,financial_authority=False)

    def confirm(self,payload):
        fields(payload,'proposal_id',('proposal_id',))
        identity=sentence(payload['proposal_id'])
        # Response loss/restart can recover the same acknowledgement, never a
        # second operation or resurrected unconfirmed review.
        found=self.memory.lookup(identity)
        if found:return dict(stage='retained',acknowledgement=found)
        require(identity in self.pending,'future_review_expired')
        op=self.pending[identity]
        ack=self.memory.retain(op)
        self.pending.pop(identity,None)
        return dict(stage='retained',acknowledgement=ack)

    def prepare_timing(self,payload):
        from .future_timing import parse_timing
        from datetime import date
        fields(payload,'text node option timing disposition',('text','option','timing'))
        source=sentence(payload['text']);c,_,_=self.memory.read()
        old=self._current(c,payload['option'],'option')
        require(len(old.effects)==1 and old.effects[0].time_basis=='one_time' and not old.effects[0].absence,'one_time_timing_required')
        if payload.get('node'): require(c.get(old.variable).node==ref(payload['node']),'future_request_invalid')
        timing=parse_timing(payload['timing'])
        if payload.get('disposition') is not None:
            require(payload['disposition']=='pending','future_request_invalid')
            require(old.effects[0].money_timing is not None and payload['timing']==old.effects[0].money_timing.source_text,'future_request_invalid')
            timing=replace(old.effects[0].money_timing,pending_checked_on=date.today().isoformat())
        require(timing!=old.effects[0].money_timing,'future_unchanged')
        meta=self.meta('option',source,dependencies=old.meta.dependencies)
        require(len(old.meta.evidence)<32,'future_review_capacity')
        meta=replace(meta,contexts=old.meta.contexts,evidence=(*old.meta.evidence,*meta.evidence))
        updated=replace(old,meta=meta,effects=(replace(old.effects[0],money_timing=timing),))
        return self._draft((updated,),'keep',dict(title=old.label,meaning='money_timing',financial_authority=False))

    def prepare(self,payload):
        fields(payload,'text node parent source_ref label title amount currency question direction timing_source money_timing_text',('text',))
        source=sentence(payload['text'])
        c,_,snapshot=self.memory.read()
        node=self._current(c,payload['node'],'node') if payload.get('node') else None
        parent=self._current(c,payload['parent'],'node') if payload.get('parent') else None
        title=sentence(payload.get('title',''),True)
        label=sentence(payload.get('label',''),True)
        supplied=payload.get('amount')
        deps=();contexts=()
        direction=flow_direction(source)
        if payload.get('direction') is not None:
            require(payload['direction'] in ('INFLOW','OUTFLOW'),'future_request_invalid')
            require(direction in ('UNKNOWN',payload['direction']),'future_meaning_ambiguous')
            direction=payload['direction']
        if direction=='UNKNOWN' and not node and not payload.get('source_ref') and re.search(r'钱|款|\b(?:money|funds|amount)\b',source,re.I):
            return dict(stage='clarify',question='direction',draft=dict(payload),choices=[dict(value='INFLOW',label='+'),dict(value='OUTFLOW',label='−')])
        require(direction != 'TRANSFER','unsupported_future_source')
        typ='expected_inflow' if direction=='INFLOW' else 'cost'
        base_amount=None
        if payload.get('source_ref'):
            r=typed_ref(payload['source_ref']); require(r in snapshot.world.members,'world_input_changed')
            value=snapshot.get(r)
            existing=[n for n in c.latest.values() if isinstance(n,FutureNode) and not n.meta.archived and any(d.ref.record_kind==r.record_kind and d.ref.ref.id==r.ref.id for d in n.meta.dependencies)]
            require(len(existing)<=1,'future_meaning_ambiguous')
            if existing:
                require(any(d.ref==r for d in existing[0].meta.dependencies),'world_input_changed')
                if not hypothetical(source):return dict(stage='existing',ref=wire_ref(existing[0].meta.ref))
                next_payload=dict(payload,node=wire_ref(existing[0].meta.ref));next_payload.pop('source_ref')
                return self.prepare(next_payload)
            require(r.record_kind in ('world_decision','future_inflow','planned_expense'),'unsupported_future_source')
            title=getattr(value,'subject',None) or getattr(value,'purpose',None) or getattr(value,'label',None)
            a=getattr(value,'amount',None) or getattr(value,'expected_amount',None) or getattr(value,'estimated_amount',None)
            if r.record_kind=='world_decision': a=getattr(value,'proposed_amount',None) or a
            if a is not None:
                if hypothetical(source):base_amount=a
                else:supplied=amount_input(_wire(a))
            deps=(WorldDependency(r,digest(value)),)
            if r.record_kind=='world_decision':contexts=(ContextBinding(r,linked(snapshot,r)),)
            else:require(value.status.value in ('expected','planned','partially_realized'),'world_input_changed')
            typ='expected_inflow' if r.record_kind=='future_inflow' else 'cost'
        if not node and not parent and not title:
            matches=[n for n in c.latest.values() if isinstance(n,FutureNode) and not n.meta.archived and n.title.casefold() in source.casefold()]
            if len(matches)==1:node=matches[0]
            elif len(matches)>1:return dict(stage='clarify',question='which_event',choices=[dict(ref=wire_ref(n.meta.ref),title=n.title) for n in matches])
        if node:
            children=[n for n in c.latest.values() if isinstance(n,FutureNode) and n.parent and n.parent.id==node.meta.ref.id and not n.meta.archived]
            matches=[n for n in children if n.title.casefold() in source.casefold()]
            if len(matches)==1:node=matches[0]
            elif len(matches)>1:return dict(stage='clarify',question='which_part',choices=[dict(ref=wire_ref(n.meta.ref),title=n.title) for n in matches])
            elif not payload.get('question') and re.search(r'\b(?:also|add|additionally)\b|如果再|另外|还想',source,re.I):
                parent=node;node=None
                if not title:
                    part=re.sub(r'^(?:what if|if|如果|假设)\s*(?:I\s+)?(?:also\s+|additionally\s+|再|另外|还想)*(?:add\s+|buy\s+|attend\s+|go to\s+|去|看|买|参加)*','',source,flags=re.I)
                    part=re.split(r'\s+(?:for|at|costing)\s+|只要|大约|需要',part,flags=re.I)[0].strip(' ?？呢。')
                    if part and part!=source:title=part
            elif children and not any(isinstance(v,FutureVariable) and v.node.id==node.meta.ref.id for v in c.latest.values()):
                return dict(stage='clarify',question='which_part',choices=[dict(ref=wire_ref(n.meta.ref),title=n.title) for n in children])
        variables=[v for v in c.latest.values() if isinstance(v,FutureVariable) and node and v.node.id==node.meta.ref.id and not v.meta.archived]
        if payload.get('question'):
            requested=self._current(c,payload['question'],'variable')
            require(requested in variables,'future_request_invalid');variables=[requested]
        if len(variables)>1:return dict(stage='clarify',question='which_part',choices=[dict(ref=wire_ref(v.meta.ref),title=v.question) for v in variables])
        variable=variables[0] if variables else None
        if variable:
            typ='expected_inflow' if variable.control=='external_outcome' else 'cost'
            if payload.get('direction') and payload['direction']!=effect_direction(typ):
                raise FutureError('future_meaning_ambiguous')
            if direction=='INFLOW' and typ=='cost':
                raise FutureError('future_meaning_ambiguous')
        currency=payload.get('currency') or (base_amount.currency.value if base_amount else None)
        if currency is None and parent:
            descendants=[o for o in c.latest.values() if isinstance(o,FutureOption) and c.is_descendant(c.get(o.variable).node,parent.meta.ref)]
            cs={e.observed.currency.value for o in descendants for e in o.effects}
            if len(cs)==1:currency=next(iter(cs))
        if currency is not None:require(currency in ('CNY','SGD','EUR'),'currency_required')
        if variable:
            currencies={e.observed.currency.value for o in c.latest.values() if isinstance(o,FutureOption) and o.variable==variable.meta.ref for e in o.effects}
            if len(currencies)==1 and currency is None:currency=next(iter(currencies))
        absent=bool(re.search(r'\b(?:do not buy|don.t buy|not attend|cancelled|canceled|no purchase)\b|不买|不去|不参加|没了|取消',source,re.I))
        if re.search(r'不取消|没有取消|not cancel(?:led|ed)|not going to cancel',source,re.I):absent=False
        if absent and re.search(r'\b(?:and|also)\b|而且|同时|并且',source,re.I):
            raise FutureError('future_meaning_ambiguous')
        if absent and currency:supplied=dict(currency=currency,precision='exact',value='0',range_min=None,range_max=None)
        if supplied is None:
            supplied=price_from(source)
            if supplied is None and currency:
                # Only price-shaped bare numbers, never ages, dates or duration.
                m=re.search(r'(?:只要|需要|大约|左右|只需|for|costs?|about)\s*(\d+(?:\.\d{1,2})?)(?:\s*(?:元|块|呢|左右|\?|$))',source,re.I)
                if m:supplied=price_from(currency+' '+m.group(1))
        if not title and not node:
            if parent:
                return dict(stage='clarify',question='part_name',draft=dict(payload))
            return dict(stage='clarify',question='event_name',draft=dict(payload),choices=[dict(ref=wire_ref(n.meta.ref),title=n.title) for n in c.latest.values() if isinstance(n,FutureNode) and not n.meta.archived])
        if (node or base_amount) and not label:label=source
        if not node and not label:label=title
        a=amount(supplied) if supplied is not None else None
        if a is None and currency:a=AmountEstimate.unknown('Estimate not supplied',Currency(currency))
        if node and a is None:return dict(stage='clarify',question='currency',draft=dict(payload))
        rows=[]
        if node is None:
            node=FutureNode(replace(self.meta('node',source,dependencies=deps),contexts=contexts),title,'component' if parent else 'event',uuid4().hex,parent.meta.ref if parent else None)
            rows.append(node)
        if a is not None:
            if base_amount is not None:
                vm=self.meta('variable',source); bm=self.meta('option',title,dependencies=deps)
                variable=FutureVariable(vm,node.meta.ref,title,'external_outcome' if typ=='expected_inflow' else 'user_choice',bm.ref)
                bp=AmountEstimate.approximate(base_amount.amount) if base_amount.precision is AmountPrecision.EXACT_REPORTED else base_amount
                base=FutureOption(bm,variable.meta.ref,title,(FutureEffect(node.meta.ref.id,typ,base_amount,bp,'one_time',tuple('world:'+d.ref.record_kind+':'+d.ref.ref.id for d in deps) or (node.meta.ref.id,)),),(title,))
                rows.extend((variable,base));om=self.meta('option',source)
            elif variable is None:
                vm=self.meta('variable',source); om=self.meta('option',source)
                variable=FutureVariable(vm,node.meta.ref,title or node.title,'external_outcome' if typ=='expected_inflow' else 'user_choice',om.ref)
                rows.append(variable)
            else:om=self.meta('option',source)
            existing=[o for o in c.latest.values() if isinstance(o,FutureOption) and o.variable==variable.meta.ref]
            # Preserve the same scope/coverage/time basis as the reference; an
            # alternative cannot create a second additive effect for the item.
            basis=base if base_amount is not None else (c.get(variable.reference_option) if variable.reference_option and variable.reference_option in c.index else None)
            if payload.get('timing_source'):
                # Inline editing names the exact displayed option; its payment
                # meaning can differ from the variable's original reference.
                basis=self._current(c,payload['timing_source'],'option')
                require(basis.variable==variable.meta.ref,'future_request_invalid')
            require(not basis or len(basis.effects)==1,'compound_alternative_needs_review')
            prior=basis.effects[0] if basis else None
            planning=AmountEstimate.approximate(a.amount) if a.precision is AmountPrecision.EXACT_REPORTED and not absent else a
            payment_basis=prior.payment_basis if prior else None
            if payment_basis:
                # A quotation edit cannot allocate or erase historical money.
                require(not absent,'payment_allocation_requires_review')
                from .payment_amounts import payment_planning
                try:planning=payment_planning(a,payment_basis)
                except ValueError as error:raise FutureError(str(error)) from None
                require(len(basis.meta.evidence)+len(om.evidence)<=32,'future_review_capacity')
                om=replace(om,dependencies=basis.meta.dependencies,contexts=basis.meta.contexts,
                    evidence=(*basis.meta.evidence,*om.evidence))
            effect=FutureEffect(prior.slot if prior else node.meta.ref.id,typ,a,planning,prior.time_basis if prior else 'one_time',prior.coverage if prior else (tuple('world:'+d.ref.record_kind+':'+d.ref.ref.id for d in deps) or (node.meta.ref.id,)),absent,payment_basis=payment_basis)
            if payload.get('timing_source'):
                timing_source=self._current(c,payload['timing_source'],'option')
                require(timing_source.variable==variable.meta.ref and len(timing_source.effects)==1,'future_request_invalid')
                effect=replace(effect,money_timing=timing_source.effects[0].money_timing)
            if payload.get('money_timing_text'):
                from .future_timing import parse_timing
                effect=replace(effect,money_timing=parse_timing(payload['money_timing_text']))
            rows.append(FutureOption(om,variable.meta.ref,label,(effect,),(source,)))
        test=FutureCatalog(c.scope,(*c.records,*rows))
        summary=dict(title=node.title,label=label,meaning='option' if not any(isinstance(r,FutureNode) for r in rows) else 'event',amount=_wire(a) if a else None,parent=parent.title if parent else None,result=None)
        if a and (base_amount is not None or variable.reference_option in c.index):
            _,_,summary['result']=self._comparison(test,snapshot,[node.meta.ref],[rows[-1].meta.ref])
        return self._draft(rows,'keep',summary)

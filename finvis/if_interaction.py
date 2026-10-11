"""Transient If proposals over FS1, with explicit existing Future retention.

No new domain type, codec, database, financial command or writer is introduced.
The same exact catalog and selection drive composer proposals and manual trees.
"""
from dataclasses import replace
from hashlib import sha256
import json
from uuid import uuid4

from .future_experience import FutureExperience, fields, sentence, ref, wire_ref
from .future_space import (FutureNode, FutureVariable, FutureOption, FutureEffect,
    FutureCatalog, FutureError, SavedScenario, require)
from .future_memory import validate_operation, validity
from .decision_baseline import amount
from .world_values import AmountEstimate, AmountPrecision
from .domain import Currency
from .reviewed_proposals import _wire
from .flow_direction import effect_direction


class _OverlayMemory:
    """A nonpublishing catalog view; preparation still validates in real FS1."""
    def __init__(self, memory, operation):
        self.memory=memory;self.operation=operation;self.store=memory.store

    @property
    def scope(self):return self.memory.scope

    def read(self):
        c,revision,snapshot=self.memory.read()
        require(revision==self.operation.expected_revision,'future_head_changed')
        validate_operation(c,self.operation,snapshot)
        return FutureCatalog(c.scope,(*c.records,*self.operation.records)),revision,snapshot

    def prepare(self, identity, records, action, at=None):
        require(action=='keep','future_request_invalid')
        return self.memory.prepare(identity,(*self.operation.records,*records),action,at)


class IfInteraction:
    """Bounded process-local proposals; only Keep invokes FS1 confirmation."""
    def __init__(self, experience):
        self.x = experience
        self.selections = {}
        self.parents = {}
        self.keep_requests = {}

    def _catalog(self, identity):
        c, revision, snapshot = self.x.memory.read()
        require(identity in self.x.pending, 'future_review_expired')
        op = self.x.pending[identity]
        require(op.expected_revision == revision, 'future_head_changed')
        validate_operation(c, op, snapshot)
        return FutureCatalog(c.scope, (*c.records, *op.records)), revision, snapshot, op

    def _space(self, c, revision, snapshot, op):
        result = self.x.listing()
        pending = {r.meta.ref for r in op.records}
        for row in op.records:
            v = validity(c, row, snapshot)
            item = dict(ref=wire_ref(row.meta.ref), status=v['status'], reasons=v['reasons'],
                missing=v['missing'], changed_items=[], evidence=[e.excerpt for e in row.meta.evidence], transient=True)
            if isinstance(row, FutureNode):
                item.update(title=row.title, parent=wire_ref(row.parent) if row.parent else None,
                    context=list(row.context), source_refs=[_wire(d.ref) for d in row.meta.dependencies],
                    active_when=[wire_ref(r) for r in row.active_when])
            elif isinstance(row, FutureVariable):
                item.update(node=wire_ref(row.node), question=row.question, control=row.control,
                    reference=wire_ref(row.reference_option) if row.reference_option else None,
                    active_when=[wire_ref(r) for r in row.active_when])
            elif isinstance(row, FutureOption):
                item.update(variable=wire_ref(row.variable), label=row.label, effects=_wire(row.effects),directions=sorted({effect_direction(e.effect_type) for e in row.effects}),
                    assumptions=list(row.assumptions), requires=[wire_ref(r) for r in row.requires],
                    excludes=[wire_ref(r) for r in row.excludes], structural=not row.effects)
                from .future_timing import timing_predecessor
                predecessor=timing_predecessor(c,row)
                if predecessor:item['timing_replaces']=wire_ref(predecessor)
            else:
                continue
            result['records'].append(item)
        result.update(transient=True, proposal_id=op.operation_id,
            pending_refs=[wire_ref(r) for r in sorted(pending, key=lambda r:(r.kind,r.id))])
        return result

    def _selection(self, c, op, payload, new_records=None):
        roots = [ref(r) for r in payload.get('roots', [])]
        options = [ref(r) for r in payload.get('options', [])]
        require(all(isinstance(c.get(r), FutureOption) for r in options), 'future_request_invalid')
        # Fresh source exploration may add both a reference and an alternative.
        # They are exclusive: the last proposed state wins only within that
        # explicitly changed variable, never across unrelated variables.
        added = list({r.variable.id:r for r in (new_records if new_records is not None else op.records)
            if isinstance(r, FutureOption)}.values())
        # A proposed option replaces the current choice of its question only;
        # independent selected events are retained, never lost on a topic switch.
        owners = {r.variable.id for r in added}
        options = [r for r in options if c.get(r).variable.id not in owners]
        options.extend(r.meta.ref for r in added)
        chosen={c.get(r).variable.id:r for r in options}
        compatible=[]
        for r in options:
            variable=c.get(c.get(r).variable);node=c.get(variable.node)
            conditions=list(variable.active_when)
            while node:
                conditions.extend(node.active_when);node=c.get(node.parent) if node.parent else None
            if not any(c.get(a).variable.id in chosen and chosen[c.get(a).variable.id]!=a for a in conditions):
                compatible.append(r)
        options=compatible
        for option in added:
            node = c.get(c.get(option.variable).node)
            while node.parent:
                node = c.get(node.parent)
            if node.meta.ref not in roots:
                roots.append(node.meta.ref)
        if not roots:
            roots = [r.meta.ref for r in op.records if isinstance(r, FutureNode) and r.parent is None]
        return dict(roots=[wire_ref(r) for r in roots], options=[wire_ref(r) for r in options])

    def _view(self, identity, selection, summary=None):
        c, revision, snapshot, op = self._catalog(identity)
        from .compound_future import complete_conditionals
        selection = complete_conditionals(c, selection['roots'], selection['options'])
        roots = [self.x._current(c,r,'node').meta.ref for r in selection['roots']]
        options = [ref(r) for r in selection['options']]
        _, _, result = self.x._comparison(c, snapshot, roots, options)
        self.selections[identity] = selection
        return dict(stage='trial', proposal_id=identity, summary=summary, selection=selection,
            result=result, space=self._space(c,revision,snapshot,op), financial_authority=False)

    def propose(self, payload):
        fields(payload, 'text node parent source_ref label title amount currency question direction kind parts roots options proposal_id replace_last option timing disposition timing_source money_timing_text timings', ('text',))
        with self.x.memory.store._lock:
            require(len(self.selections) < 256, 'future_draft_capacity')
            require(type(payload.get('roots', [])) is list and type(payload.get('options', [])) is list
                and len(payload.get('roots', [])) <= 64 and len(payload.get('options', [])) <= 128, 'future_request_invalid')
            kind = payload.get('kind', 'alternative')
            require(kind in ('alternative', 'component', 'structure', 'timing', 'timing_batch'), 'future_request_invalid')
            require(type(payload.get('replace_last',False)) is bool,'future_request_invalid')
            previous=sentence(payload['proposal_id']) if payload.get('proposal_id') else None
            source_previous=previous
            if previous:
                self._catalog(previous)
                if payload.get('replace_last'):
                    require(previous in self.parents,'future_review_expired')
                    previous=self.parents[previous]
            else:
                require(not payload.get('replace_last'),'future_request_invalid')
            base_op=self._catalog(previous)[3] if previous else None
            x=FutureExperience(_OverlayMemory(self.x.memory,base_op)) if base_op else self.x
            if 'roots' not in payload and 'options' not in payload and previous in self.selections:
                payload=dict(payload,**self.selections[previous])
            if kind == 'timing_batch':
                review=self._timings(payload,x)
            elif kind == 'timing':
                request={k:v for k,v in payload.items() if k in ('text','node','option','timing','disposition')}
                review=x.prepare_timing(request)
            elif kind == 'structure':
                review = self._structure(payload,x)
            else:
                require('parts' not in payload, 'future_request_invalid')
                request = {k:v for k,v in payload.items() if k not in ('kind','roots','options','proposal_id','replace_last')}
                review = x.prepare(request)
            if review['stage'] != 'review':
                return review
            identity = review['proposal_id']
            if x is not self.x:self.x.pending[identity]=x.pending[identity]
            try:
                c, _, _, op = self._catalog(identity)
                if payload.get('replace_last'):
                    # Correcting a draft retracts that layer's proposed objects,
                    # not all earlier retained or independent Future thought.
                    payload=dict(payload,roots=[r for r in payload.get('roots',[]) if ref(r) in c.index],
                        options=[r for r in payload.get('options',[]) if ref(r) in c.index])
                new_records=op.records[len(base_op.records):] if base_op else op.records
                selection_records=new_records
                if kind=='timing_batch':
                    chosen={c.get(ref(r)).variable for r in payload.get('options',[])}
                    selection_records=tuple(r for r in new_records if isinstance(r,FutureOption) and r.variable in chosen)
                selected = self._selection(c, op, payload,selection_records)
                self.parents[identity]=previous
                result=self._view(identity, selected, review['summary'])
                result['supersedes_proposal_id']=source_previous
                return result
            except Exception:
                self.x.pending.pop(identity, None)
                self.selections.pop(identity, None)
                self.parents.pop(identity,None)
                raise

    def _timings(self,payload,x):
        """Prepare all existing timing actions privately; publish one If layer."""
        rows=payload.get('timings')
        require(type(rows) is list and 1<=len(rows)<=8,'future_request_invalid')
        require(not any(k in payload for k in ('node','option','amount','parts','timing','parent','title','question')),'future_request_invalid')
        prepared=[];seen=set()
        scratch=FutureExperience(x.memory)
        for row in rows:
            fields(row,'node option timing',('node','option','timing'))
            key=ref(row['option']);require(key not in seen,'future_request_invalid');seen.add(key)
            try:
                review=scratch.prepare_timing(dict(row,text=payload['text']))
            except FutureError as error:
                if str(error)=='future_unchanged':continue
                raise
            op=scratch.pending[review['proposal_id']]
            # Overlay preparation contains its basis; append only the newly
            # prepared option. Nothing enters the shared registry until all pass.
            prepared.append(op.records[-1])
        require(prepared,'future_unchanged')
        return x._draft(tuple(prepared),'keep',dict(title=payload['text'],meaning='money_timing',financial_authority=False))

    def _structure(self, payload, x):
        require('node' in payload and 'parts' in payload, 'future_request_invalid')
        require(not any(k in payload for k in ('parent','source_ref','amount','question')), 'future_request_invalid')
        source = sentence(payload['text'])
        parts = payload['parts']
        require(type(parts) is list and 1 <= len(parts) <= 16, 'future_parts_required')
        c, _, _ = x.memory.read()
        node = x._current(c,payload['node'],'node')
        variables = [r for r in c.latest.values() if isinstance(r,FutureVariable)
            and r.node.id == node.meta.ref.id and not r.meta.archived]
        require(len(variables) <= 1, 'future_meaning_ambiguous')
        variable = variables[0] if variables else None
        basis = c.get(variable.reference_option) if variable and variable.reference_option else None
        require(not basis or len(basis.effects) == 1, 'compound_alternative_needs_review')
        prior = basis.effects[0] if basis else None
        require(not any(e.payment_basis for o in c.latest.values()
            if isinstance(o,FutureOption) and variable and o.variable==variable.meta.ref
            for e in o.effects), 'payment_allocation_requires_review')
        currency = payload.get('currency') or (prior.observed.currency.value if prior else None)
        if currency is not None:
            require(currency in ('CNY','SGD','EUR'), 'currency_required')
        rows = []
        mode = None
        if variable:
            mode = FutureOption(x.meta('option',source),variable.meta.ref,
                sentence(payload.get('label') or ('按项目考虑' if any('\u4e00'<=ch<='\u9fff' for ch in source) else 'Consider the parts')),
                (), (source,))
            rows.append(mode)
        seen = set()
        for p in parts:
            fields(p,'title amount currency',('title',))
            title = sentence(p['title'])
            require(title.casefold() not in seen, 'future_meaning_ambiguous')
            seen.add(title.casefold())
            cc = p.get('currency') or currency
            supplied = p.get('amount')
            a = amount(supplied) if supplied is not None else None
            if a is None:
                if cc is None:
                    return dict(stage='clarify',question='currency',draft=dict(payload))
                require(cc in ('CNY','SGD','EUR'), 'currency_required')
                a = AmountEstimate.unknown('Estimate not supplied',Currency(cc))
            elif p.get('currency'):
                require(a.currency.value == p['currency'], 'currency_required')
            child = FutureNode(x.meta('node',source),title,'component',uuid4().hex,node.meta.ref,
                active_when=(mode.meta.ref,) if mode else ())
            vm = x.meta('variable',source); om = x.meta('option',source)
            child_variable = FutureVariable(vm,child.meta.ref,title,variable.control if variable else 'user_choice',om.ref)
            planning = AmountEstimate.approximate(a.amount) if a.precision is AmountPrecision.EXACT_REPORTED else a
            e = FutureEffect(child.meta.ref.id,prior.effect_type if prior else 'cost',a,planning,
                prior.time_basis if prior else 'one_time',(child.meta.ref.id,))
            option = FutureOption(om,vm.ref,title,(e,),(source,))
            rows.extend((child,child_variable,option))
        FutureCatalog(c.scope,(*c.records,*rows))
        return x._draft(rows,'keep',dict(title=node.title,label=mode.label if mode else node.title,
            meaning='structure',parent=node.title,parts=[p['title'] for p in parts],financial_authority=False))

    def preview(self, payload):
        fields(payload,'proposal_id roots options',('proposal_id','roots','options'))
        require(type(payload['roots']) is list and type(payload['options']) is list
            and len(payload['roots']) <= 64 and len(payload['options']) <= 128, 'future_request_invalid')
        with self.x.memory.store._lock:
            return self._view(sentence(payload['proposal_id']),dict(roots=payload['roots'],options=payload['options']))

    def inspect_space(self, proposal_id):
        """Read the same transient tree for semantic resolution, without save."""
        with self.x.memory.store._lock:
            c,revision,snapshot,op=self._catalog(sentence(proposal_id))
            return self._space(c,revision,snapshot,op)

    def keep(self, payload):
        fields(payload,'proposal_id roots options name',('proposal_id',))
        identity = sentence(payload['proposal_id'])
        with self.x.memory.store._lock:
            if any(k in payload for k in ('roots','options','name')):
                return self._keep_selection(identity,payload)
            result = self.x.confirm(payload)
            selection = self.selections.pop(identity,None)
            self.parents.pop(identity,None)
            return dict(result,selection=selection,space=self.x.listing(),financial_authority=False)

    def _keep_selection(self, identity, payload):
        """One explicit If save, atomically retaining its exact needed closure.

        Inactive/unselected independent draft alternatives are not silently
        retained. Necessary references, structure and activation evidence are
        retained alongside the single pinned Scenario in one existing FS1 op.
        """
        require(all(k in payload for k in ('roots','options','name')),'future_request_invalid')
        require(type(payload['roots']) is list and type(payload['options']) is list
            and len(payload['roots'])<=64 and len(payload['options'])<=128,'future_request_invalid')
        name=sentence(payload['name'])
        request_digest=sha256(json.dumps(payload,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
        found=self.x.memory.lookup(identity)
        if found:
            c,_,_=self.x.memory.read()
            scenarios=[c.get(ref(r)) for r in found['record_refs'] if r['kind']=='scenario']
            require(len(scenarios)==1 and any(e.identity=='if-keep/'+request_digest for e in scenarios[0].meta.evidence),
                'future_idempotency_conflict')
            selection=scenarios[0].selection
            return dict(stage='retained',acknowledgement=found,
                selection=dict(roots=[wire_ref(r) for r in selection.roots],options=[wire_ref(r) for r in selection.options]),
                space=self.x.listing(),financial_authority=False)
        if identity in self.keep_requests:
            require(self.keep_requests[identity]==request_digest,'future_idempotency_conflict')
        else:
            c,_,snapshot,op=self._catalog(identity)
            roots=tuple(self.x._current(c,r,'node').meta.ref for r in payload['roots'])
            options=tuple(ref(r) for r in payload['options'])
            require(bool(roots),'future_request_invalid')
            original=c.select(roots,options)
            needed=set(c.consumed_structure(original))|set(options)|set(roots)
            pending=list(needed)
            while pending:
                row=c.get(pending.pop());references=[]
                if isinstance(row,FutureNode):
                    references.extend(row.active_when)
                    if row.parent:references.append(row.parent)
                elif isinstance(row,FutureVariable):
                    references.extend((row.node,*row.active_when))
                    if row.reference_option:references.append(row.reference_option)
                elif isinstance(row,FutureOption):references.extend((row.variable,*row.requires,*row.excludes))
                for r in references:
                    if r not in needed:needed.add(r);pending.append(r)
            # Date edits are corrections of already-needed options, not
            # independent choices. An explicit Keep must preserve their exact
            # evidence lineage even when another alternative is selected.
            from .future_timing import timing_predecessor
            timing_links={row.meta.ref:timing_predecessor(c,row) for row in op.records if isinstance(row,FutureOption)}
            changed=True
            while changed:
                added={new for new,old in timing_links.items() if old in needed and new not in needed}
                changed=bool(added);needed.update(added)
            base,_,_=self.x.memory.read()
            records=tuple(row for row in op.records if row.meta.ref in needed)
            filtered=FutureCatalog(c.scope,(*base.records,*records))
            selection,frame,result=self.x._comparison(filtered,snapshot,roots,options)
            require(result['status']!='stale','future_inputs_stale')
            meta=self.x.meta('scenario','Explicitly save '+name)
            meta=replace(meta,evidence=tuple(replace(e,identity='if-keep/'+request_digest) for e in meta.evidence))
            scenario=SavedScenario(meta,name,selection,frame.meta.ref)
            combined=self.x.memory.prepare(identity,(*records,frame,scenario),'save_scenario')
            self.x.pending[identity]=combined
            self.keep_requests[identity]=request_digest
            self.selections[identity]=dict(roots=[wire_ref(r) for r in roots],options=[wire_ref(r) for r in options])
        result=self.x.confirm({'proposal_id':identity})
        selection=self.selections.pop(identity,None)
        self.parents.pop(identity,None)
        self.keep_requests.pop(identity,None)
        return dict(result,selection=selection,space=self.x.listing(),financial_authority=False)

    def cancel(self, payload):
        fields(payload,'proposal_id',('proposal_id',))
        identity = sentence(payload['proposal_id'])
        with self.x.memory.store._lock:
            require(self.x.memory.lookup(identity) is None,'future_already_retained')
            self.x.pending.pop(identity,None)
            self.selections.pop(identity,None)
            self.parents.pop(identity,None)
            self.keep_requests.pop(identity,None)
            return dict(stage='cancelled',financial_authority=False)

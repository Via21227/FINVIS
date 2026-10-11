"""Bounded bilingual lifecycle preparation. D/E and I4 remain the only writer.

Interpretation only proposes a meaning. Exact target, coverage and allocation
are reviewed before a registered preview, grant and receipt can exist.
"""
from dataclasses import replace
from datetime import date, datetime, timezone
from calendar import monthrange
from uuid import uuid4
import re

from .domain import Ref, Confidence, ConfidenceAssessment, RealitySource
from .world_values import ObservationMetadata, ObservationRole, FreshnessAssessment, FreshnessStatus, TemporalSpec
from .world_planning import PlannedExpense, PlannedExpenseStatus as S, ActualOccurrence, OccurrenceKind as K, SettlementStatus as SS
from .world_fulfillment import ExpenseFulfillment, OccurrenceLineage, ReplacementLink, capture_if_evidence, expense_summary, signed_amount, is_refund, refunds_for
from .world_settlement import SettleOccurrence, BalanceCoverage, BalanceBasis, propose_settlement
from .world_commands import CandidateChangeSet, ChangeMode, PutRecord, FinancialWorldCommand, record_ref
from .world_serialization import payload_digest
from .reviewed_proposals import _wire
from .decision_baseline import fields, text, amount
from .simple_planning import price_from
from .i3_workflow import I3Error, _preview_view
from .store import Rejected, StaleVersion

CUES = {
 'flight': r'\b(?:flight|airfare)\b|机票|航班',
 'transport': r'\b(?:transport|metro|subway|taxi|bus|commut(?:e|ing))\b|交通|地铁|打车|公交|通勤',
 'food': r'\b(?:food|dining|meal|lunch|dinner)\b|餐饮|餐费|午饭|晚饭|伙食',
 'accommodation': r'\b(?:hotel|accommodation)\b|酒店|住宿',
 'electronics': r'\b(?:laptop|computer|electronics|phone|camera)\b|电脑|手机|相机|电子产品',
 'training': r'\b(?:course|tuition|training|class)\b|课程|学费|培训',
 'service': r'\b(?:service|repair|cleaning)\b|服务|维修|保洁',
 'concert': r'\bconcert\b|演唱会|音乐会',
 'savings': r'\b(?:saving|savings|goal)\b|储蓄|目标',
}
def categories(source): return {k for k,p in CUES.items() if re.search(p,source,re.I)}
def matches(source,label):
    exact = re.search(r'(?<![a-z])'+re.escape(label.strip())+r'(?![a-z])',source,re.I)
    return bool(exact or categories(source)&categories(label))

def meaning(source):
    from .actual_report import parse_actual_report
    if parse_actual_report(source):return 'actual',None
    if re.search(r'\b(?:display|show|view)\b|显示|展示|想看|看进度',source,re.I):
        for key,pattern in [('numbers',r'\b(?:numbers|remaining)\b|数字|剩多少钱|只显示剩'),('details',r'\bdetails\b|详情'),('auto',r'\bauto\b|自动'),('progress',r'\bprogress\b|进度'),('trend',r'\btrend\b|趋势')]:
            if re.search(pattern,source,re.I): return 'display',key
    # Reports with negation, conditional/uncertain meaning or several actions
    # are not silently converted into confirmed financial facts.
    kinds=[]
    for key,pattern in [('refund',r'\brefunded?\b|退回来|退款|退了'),('replacement',r'\b(?:rebooked|replacement|replaced)\b|重新订|重新买|换订'),('actual',r'\b(?:paid|spent|bought|purchased)\b|买好了|买了|买到了|花了|支付了|付了'),('commit',r'\b(?:booked|ordered|reserved)\b|订好了|预订了|下单了'),('cancel',r'\bcancell?ed\b|取消了|不买了'),('complete',r'\b(?:finished|completed)\b|已完成|结束了|用完了')]:
        if re.search(pattern,source,re.I):kinds.append(key)
    if not kinds:
        return ('uncertain',None) if re.search(r'没|未|not|never',source,re.I) and re.search(r'支付|付款|paid|spent|booked',source,re.I) else (None,None)
    if re.search(r'\b(?:not|never|maybe|might|whether|correct|correction)\b|没|未|可能|是否|更正|修正|[?？]',source,re.I):return 'uncertain',None
    if 'replacement' in kinds and all(k in ('replacement','actual','commit') for k in kinds):return 'replacement',None
    if len(kinds)>1 and set(kinds)!= {'actual','complete'}:return 'uncertain',None
    return ('actual_complete' if set(kinds)=={'actual','complete'} else kinds[0]),None


def metadata(source, role, at, identity, old=None):
    return ObservationMetadata(RealitySource.USER_REPORT,(Ref('lifecycle/evidence/'+identity,1),),(),
        None,TemporalSpec.unknown('Reported event time; no calendar date inferred',source),at,role,
        Confidence(ConfidenceAssessment.UNASSESSED,'User reported; not independently verified'),
        FreshnessAssessment(FreshnessStatus.CURRENT,source,at),
        old.ref if old else None,'Explicit reviewed lifecycle update: '+source if old else None)


class PlanReality:
    def __init__(self, workflow):
        self.w=workflow
        self.proposals={}
        self.pending=None

    def targets(self, read):
        s=read.snapshot;rows=[]
        for ref in s.world.members:
            r=s.get(ref)
            if ref.record_kind=='planned_expense':
                rows.append(dict(key='plan:'+r.ref.id,label=r.purpose,kind='plan',ref=ref,amount=r.expected_amount,record=r))
        for ref in s.world.members:
            r=s.get(ref)
            if ref.record_kind=='earmark' and r.role.value=='goal':
                rows.append(dict(key='goal:'+r.ref.id,label=r.purpose,kind='goal',ref=ref,amount=r.allocation,record=r))
        # A component already backed by a plan uses that plan's exact identity.
        for versions in self.w.baselines.versions.values():
            b=versions[-1]
            if self.w.baselines.view(b)['status']=='stale':continue
            for c in b.components:
                if c.world_ref and c.world_ref.record_kind=='planned_expense':continue
                if any(o['label']==c.label and getattr(o['record'],'decision_ref',None)==b.decision_ref for o in rows if o['kind']=='plan'):continue
                rows.append(dict(key='component:'+b.ref.id+':'+c.id,label=c.label,kind='component',ref=b.decision_ref,
                    amount=c.planning_amount,baseline=b,record=c))
        for ref in s.world.members:
            if ref.record_kind!='world_decision':continue
            d=s.get(ref)
            if d.amount is None:continue
            if any((r['kind']=='plan' and getattr(r['record'],'decision_ref',None)==ref) or (r['kind']=='component' and r['ref']==ref) for r in rows):continue
            rows.append(dict(key='decision:'+d.ref.id,label=d.subject,kind='component',ref=ref,amount=d.amount,baseline=None,record=d))
        for line in self.w.if_lines.lines.values():
            if line.kind not in ('add','replace') or not line.planning_amount:continue
            if self.w.if_lines.view(line)['status']!='valid':continue
            if self.materialized(line.id):continue
            rows.append(dict(key='if:'+line.id,label=line.label,kind='if',ref=line.baseline.decision_ref,
                amount=line.planning_amount,record=line))
        return rows

    def materialized(self, identity):
        s=self.w.authority.read().snapshot
        return next((s.get(r) for r in s.world.members if r.record_kind=='actual_occurrence'
            and s.get(r).lineage and s.get(r).lineage.if_evidence and
            s.get(r).lineage.if_evidence.if_id==identity and
            s.get(r).lineage.if_evidence.source_generation==self.w.store.session_generation),None)

    def route(self, source, read, request_id, target_keys=None):
        kind,display=meaning(source)
        if kind is None:return None
        options=[r for r in self.targets(read) if kind=='display' or r['kind']!='goal']
        matched=[r for r in options if r['key'] in target_keys] if target_keys is not None else [r for r in options if matches(source,r['label'])]
        if kind=='display' and 'savings' in categories(source):
            matched=[r for r in options if r['kind']=='goal'] or matched
        currencies=[c for c,pattern in [('CNY',r'\bCNY\b|人民币'),('SGD',r'\bSGD\b|新币'),('EUR',r'\bEUR\b|欧元')] if re.search(pattern,source,re.I)]
        if len(currencies)==1:
            compatible=[r for r in matched if r['amount'].currency.value==currencies[0]]
            if compatible:matched=compatible
        if not matched:
            # An existing non-income financial target can be clarified without
            # asking the user for internal schema names. Unrelated reports keep
            # the established Candidate route.
            if kind not in ('display','uncertain') or not options:return None
            matched=options
        if kind=='uncertain':return dict(kind='clarification',reason='lifecycle_uncertain',source_text=source)
        if len(self.proposals)>=128:raise I3Error('session_capacity')
        if self.pending:self.cancel({})
        identity='lifecycle/proposal/'+uuid4().hex
        p=dict(id=identity,version=read.version,request_id=request_id,source=text(source),kind=kind,
            options=matched,answers={},display=display,confirm_plan=target_keys is not None)
        self.proposals[identity]=p
        return self.inspect(p)

    def from_claim(self, source, read, request_id, kind, money, target_id=None, account=None, label=None, timing=None):
        """Stage one source-validated claim through the existing settlement gates.

        Standalone is a transient review target, never a new plan or financial
        record. Exact balance, coverage, preview, grant and Apply remain required.
        Refund/replacement need an exact existing plan; no unrelated target guess.
        """
        if kind not in ('actual', 'inflow', 'refund', 'replacement'):
            raise I3Error('unsupported_transition')
        if money['currency'] not in ('CNY', 'SGD', 'EUR'):
            raise I3Error('amount_required', 400)
        wire_amount=dict(currency=money['currency'],precision=money['precision'],
            value=money['value'],range_min=money['lower'],range_max=money['upper'])
        estimate=amount(wire_amount)
        event_timing=self.claim_timing(timing,source)
        if target_id is None:
            if kind not in ('actual','inflow'):return None
            options=[dict(key='standalone:'+request_id,label=label or ('Reported inflow' if kind=='inflow' else 'Reported payment'),
                kind='standalone',ref=None,amount=estimate,record=None)]
        else:
            if kind=='inflow':return None
            options=[r for r in self.targets(read) if r['kind']=='plan' and r['ref'].ref.id==target_id]
            if not options:return None
        if len(self.proposals)>=128:raise I3Error('session_capacity')
        if self.pending:self.cancel({})
        identity='lifecycle/proposal/'+uuid4().hex
        answers={}
        if money['precision']!='unknown':answers['amount']=wire_amount
        if account is not None:
            positions=[read.snapshot.get(r) for r in read.snapshot.world.members if r.record_kind=='asset_position'
                and read.snapshot.get(r).currency.value==money['currency']
                and read.snapshot.get(read.snapshot.get(r).account_ref).label.casefold()==account.casefold()]
            if len(positions)==1:answers['position']=positions[0].ref.id
        p=dict(id=identity,version=read.version,request_id=request_id,source=text(source),kind=kind,
            options=options,answers=answers,display=None,confirm_plan=target_id is not None,validated_claim=True,event_timing=event_timing)
        self.proposals[identity]=p
        return self.inspect(p)

    @staticmethod
    def claim_timing(timing, source):
        """Preserve supplied precision; never turn relative wording into a date."""
        if timing is None:return None
        if type(timing) is not dict or set(timing)!={'text','iso','precision'}:
            raise I3Error('compound_item_unavailable',400)
        literal,iso,precision=timing['text'],timing['iso'],timing['precision']
        if precision=='unknown' and literal is None and iso is None:return None
        if type(literal) is not str or not literal or literal not in source:
            raise I3Error('compound_item_unavailable',400)
        if precision=='literal' and iso is None:
            return TemporalSpec.unknown('Reported time retained without resolving a calendar date',literal)
        if precision not in ('day','month') or type(iso) is not str or iso not in literal:
            raise I3Error('compound_item_unavailable',400)
        try:
            if precision=='day' and re.fullmatch(r'\d{4}-\d{2}-\d{2}',iso):
                return TemporalSpec.known_date(date.fromisoformat(iso),literal)
            if precision=='month' and re.fullmatch(r'\d{4}-\d{2}',iso):
                first=date.fromisoformat(iso+'-01')
                return TemporalSpec.date_window(first,date(first.year,first.month,monthrange(first.year,first.month)[1]),literal)
        except ValueError:
            pass
        raise I3Error('compound_item_unavailable',400)

    def current(self,p):
        if (self.w.reality_routing.active_request!=p['request_id'] or
            self.w._interpret_requests.get(p['request_id'],{}).get('status') not in ('ready','review')):
            raise I3Error('proposal_stopped')
        try:self.w.authority.read(p['version'])
        except StaleVersion:raise I3Error('world_changed') from None

    @staticmethod
    def option(value,label):return dict(value=value,label=label)

    def question(self,p,key,options=None):
        return dict(kind='lifecycle',id=p['id'],stage='question',source_text=p['source'],action=p['kind'],
            question=dict(key=key,options=options or []),target=p.get('target_label'),
            currency=p['target']['amount'].currency.value if p.get('target') else None)

    def inspect(self,p):
        a=p['answers'];opts=p['options']
        if 'target' not in a:
            if len(opts)!=1:return self.question(p,'target',[self.option(r['key'],r['label']) for r in opts])
            a['target']=opts[0]['key']
        target=next(r for r in opts if r['key']==a['target']);p['target_label']=target['label'];p['target']=target
        kind=p['kind'];s=self.w.store.snapshot
        if p.get('confirm_plan'):
            if 'same_plan' not in a:return self.question(p,'same_plan',[self.option('yes','same'),self.option('no','different')])
            if a['same_plan']=='no':return self.question(p,'different_event')
        if kind=='display':
            from .presentation_policy import recommend
            item=next((r for r in self.summary()['items'] if r['key']==target['key']),None)
            if item is None:return self.question(p,'display_unavailable')
            if p['display'] not in item['presentation']['choices']:return self.question(p,'display_unavailable')
            return dict(kind='lifecycle',id=p['id'],stage='display',target=target['label'],key=target['key'],
                        preference=p['display'],scope=self.summary()['scope'],source_text=p['source'])
        if target['kind']=='component':
            if kind in ('refund','replacement'):return self.question(p,'payment_missing')
            return dict(kind='lifecycle',id=p['id'],stage='save_plan',target=target['label'],estimate=_wire(target['amount']),source_text=p['source'],action=kind)
        if (target['kind']=='plan' and target['record'].fulfillment is None and target['record'].status is S.PLANNED
                and kind in ('actual','actual_complete') and re.search(r'\bbudget\b|预算|\b(?:installment|partial)\b|分期|部分付款',target['label']+' '+p['source'],re.I)):
            return dict(kind='lifecycle',id=p['id'],stage='enable_gradual',target=target['label'],estimate=_wire(target['amount']),source_text=p['source'],action=kind)
        if target['kind']=='if':
            if kind not in ('actual','actual_complete'):return self.question(p,'unsupported_transition')
            if 'same_event' not in a:return self.question(p,'same_event',[self.option('yes','same'),self.option('no','different')])
            if a['same_event']=='no':return self.question(p,'different_event')
        if kind in ('actual','actual_complete','inflow','commit','refund','replacement'):
            c=target['amount'].currency.value
            if 'amount' not in a:
                if p.get('validated_claim'):return self.question(p,'amount')
                from .actual_report import parse_actual_report
                report=parse_actual_report(p['source'])
                parsed=report['actual'] if report else price_from(p['source'])
                if parsed is None and not re.search(r'\b[A-Z]{3}\b|人民币|新币|美元|\$|¥',p['source']):
                    stripped=re.sub(r'\d+\s*(?:nights?|days?|晚|天)', '', p['source'], flags=re.I)
                    values=re.findall(r'(?<![\d.])\d+(?:,\d{3})*(?:\.\d{1,2})?(?![\d.a-zA-Z])', stripped)
                    if len(values)==1 and not re.search(r'每|per\s|\d+[kK万千]|[-–~至到]\s*\d',stripped):
                        parsed=price_from(('about ' if re.search(r'约|大概|about|approximately',stripped,re.I) else '')+c+' '+values[0])
                if parsed is not None and parsed['currency']==c and parsed['precision']!='unknown':a['amount']=parsed
                else:return self.question(p,'amount')
            if amount(a['amount']).currency!=target['amount'].currency:raise I3Error('currency_conflict')
        if kind in ('refund','replacement'):
            payments=[s.get(r) for r in s.world.members if r.record_kind=='actual_occurrence' and s.get(r).kind is K.EXPENSE
                and any(ref.record_kind=='planned_expense' and ref.ref.id==target['ref'].ref.id for ref in s.get(r).related_entity_refs)]
            if not payments:return self.question(p,'payment_missing')
            if 'payment' not in a:
                if len(payments)==1:a['payment']=payments[0].ref.id
                else:return self.question(p,'payment',[dict(value=o.ref.id,label=o.observation.freshness.rationale,amount=_wire(o.actual_amount)) for o in payments])
            p['payment']=next(o for o in payments if o.ref.id==a['payment'])
            if kind=='replacement' and 'consumed' not in a:return self.question(p,'consumed')
        if kind in ('actual','actual_complete','inflow','refund','replacement'):
            positions=[s.get(r) for r in s.world.members if r.record_kind=='asset_position' and s.get(r).currency==target['amount'].currency]
            options=[dict(value=r.ref.id,label=s.get(r.account_ref).label,amount=_wire(r.balance)) for r in positions]
            if 'position' not in a:
                named=[o for o in options if re.search(r'(?<![a-z])'+re.escape(o['label'])+r'(?![a-z])',p['source'],re.I)]
                if len(named)==1:a['position']=named[0]['value']
                else:return self.question(p,'destination' if kind in ('refund','inflow') else 'source',options)
            p['position']=next(o for o in positions if o.ref.id==a['position'])
            if kind not in ('refund','inflow'):
                marks=[s.get(r) for r in s.world.members if r.record_kind=='earmark' and s.get(r).position_ref==record_ref(p['position'])]
                if marks and 'earmark' not in a:return self.question(p,'earmark',[self.option('none','unallocated')]+[dict(value=m.ref.id,label=m.purpose,amount=_wire(m.allocation)) for m in marks])
                p['earmark']=next((record_ref(m) for m in marks if m.ref.id==a.get('earmark')),None)
            # Explain the existing settlement guard before asking about coverage.
            # History correction is not a new observation of today's balance.
            if p['position'].observation.freshness.status is not FreshnessStatus.CURRENT:
                return dict(kind='lifecycle',id=p['id'],stage='blocked',reason='current_balance_required',
                    target=target['label'],account=s.get(p['position'].account_ref).label,
                    source_text=p['source'],action=kind,balance=dict(_wire(p['position'].balance),currency=p['position'].currency.value))
            if 'basis' not in a:return self.question(p,'coverage',[self.option('apply_to_prior_balance','before'),self.option('already_in_reported_balance','included')])
        return dict(kind='lifecycle',id=p['id'],stage='ready',target=target['label'],source_text=p['source'],action=kind)

    def answer(self,payload):
        fields(payload,'proposal_id key value');p=self.proposals.get(text(payload['proposal_id']))
        if p is None:raise I3Error('proposal_missing')
        self.current(p);view=self.inspect(p)
        if view['stage']!='question' or payload['key']!=view['question']['key']:raise I3Error('answer_stale')
        key,value=payload['key'],payload['value'];options=view['question']['options']
        if options and value not in [o['value'] for o in options]:raise I3Error('answer_invalid')
        if key in ('amount','consumed'):
            parsed=amount(value)
            if parsed.currency!=p['target']['amount'].currency or parsed.precision.value=='unknown':raise I3Error('amount_required')
            if key=='consumed' and parsed.precision.value!='exact_reported':raise I3Error('allocation_required')
        elif not options:raise I3Error('edit_report')
        p['answers'][{'source':'position','destination':'position','coverage':'basis'}.get(key,key)]=value
        return self.inspect(p)

    def prepare(self,payload):
        fields(payload,'proposal_id');p=self.proposals.get(text(payload['proposal_id']))
        if p is None:raise I3Error('proposal_missing')
        self.current(p);v=self.inspect(p)
        if v['stage']=='blocked':raise I3Error(v['reason'])
        if v['stage'] not in ('ready','save_plan','enable_gradual'):raise I3Error('clarification_required')
        if self.pending and self.pending['id']==p['id']:return self.view()
        a=p['answers'];t=p['target'];s=self.w.store.snapshot;identity=uuid4().hex
        at=max(datetime.now(timezone.utc),*(r.recorded_at for r in s.receipts)) if s.receipts else datetime.now(timezone.utc)
        source=p['source'];kind=p['kind'];old=t['record'] if t['kind']=='plan' else None
        if v['stage'] in ('save_plan','enable_gradual') or kind in ('commit','cancel','complete'):
            meta=metadata(source,ObservationRole.PLANNED,at,identity,old)
            if v['stage']=='save_plan':
                meta=replace(meta,assumption_refs=(t['baseline'].ref if t['baseline'] else t['ref'].ref,))
                # Preserve exact component evidence as metadata, not a new ledger.
                from .persistence_evidence import canonical
                meta=replace(meta,freshness=replace(meta.freshness,rationale=canonical(dict(source=source,component=_wire(t['record']),baseline=_wire(t['baseline'].ref) if t['baseline'] else None))))
                new=PlannedExpense(Ref('lifecycle/plan/'+identity,1),s.world.owner_id,s.world.world_id,t['amount'],t['label'],meta.as_of,(),t['ref'],S.PLANNED,meta,
                    fulfillment=ExpenseFulfillment() if re.search(r'\bbudget\b|预算',t['label'],re.I) else None)
            else:
                if old is None:raise I3Error('unsupported_transition')
                fulfillment=old.fulfillment
                if v['stage']=='enable_gradual':fulfillment=ExpenseFulfillment()
                if kind=='commit':
                    if old.status is not S.PLANNED:raise I3Error('unsupported_transition')
                    fulfillment=replace(fulfillment or ExpenseFulfillment(),committed_amount=amount(a['amount']),commitment_evidence=meta.evidence_refs)
                new=replace(old,ref=Ref(old.ref.id,old.ref.version+1),observation=meta,fulfillment=fulfillment,
                    status=S.CANCELLED if kind=='cancel' else S.COMPLETED if kind=='complete' else old.status)
            op=PutRecord('lifecycle/'+identity,new,record_ref(old) if old else None)
            retained=tuple(r for r in s.world.members if not old or r!=record_ref(old))
            change=CandidateChangeSet(Ref('lifecycle/change/'+identity,1),s.world.owner_id,s.world.world_id,self.w.store.session_generation,
                s.world.ref,s.world.state_ref,s.world.plan_ref,s.revision,ChangeMode.CORRECT if old else ChangeMode.MERGE,
                (op,),None,retained,(),replace(meta,role=ObservationRole.CURRENT),None,(),at)
        else:
            meta=metadata(source,ObservationRole.ACTUAL,at,identity)
            if p.get('event_timing') is not None:meta=replace(meta,as_of=p['event_timing'])
            lineage=None;related=t['ref'] if t['kind']=='plan' else None
            if kind=='refund':lineage=OccurrenceLineage(refund_of_ref=record_ref(p['payment']))
            if kind=='replacement':lineage=OccurrenceLineage(replacement=ReplacementLink(record_ref(p['payment']),tuple(record_ref(r) for r in refunds_for(s,p['payment'])),amount(a['consumed'])))
            if t['kind']=='if':lineage=OccurrenceLineage(if_evidence=capture_if_evidence(self.w.if_lines,t['record'].id,meta.evidence_refs))
            # Legacy completion is not reinterpreted. Forward refunds/replacements
            # can link a prior payment without reopening its old plan.
            if kind in ('refund','replacement') and old and old.fulfillment is None:related=None
            pos=record_ref(p['position']);incoming=kind in ('refund','inflow')
            report=ActualOccurrence(Ref('lifecycle/actual/'+identity,1),s.world.owner_id,s.world.world_id,K.INFLOW if incoming else K.EXPENSE,
                amount(a['amount']),meta.as_of,(related,) if related else (t['ref'],) if t['kind']=='if' else (),SS.UNALLOCATED,meta,
                None if incoming else pos,pos if incoming else None,lineage=lineage)
            coverage=BalanceCoverage(record_ref(report),pos,BalanceBasis(a['basis']),meta,'User reviewed exact current balance and before/already-included coverage')
            op=SettleOccurrence('lifecycle/'+identity,report,None,coverage,related,p.get('earmark'),complete_plan=kind=='actual_complete')
            change=propose_settlement(s,self.w.store.session_generation,op,Ref('lifecycle/change/'+identity,1))
        preview=self.w.store.preview(self.w.context.owner_id,change,Ref('lifecycle/preview/'+identity,1))
        if self.pending:self.cancel({})
        staged,_,_=self.w.store._stage(change)
        staged_read=replace(self.w.authority.read(),snapshot=staged)
        after_items=self.summary(staged_read)['items']
        summary_after=next((r['summary'] for r in after_items if r['key']==t['key']),None)
        self.pending=dict(summary_after=summary_after,linked_payment=_wire(p['payment'].actual_amount) if p.get('payment') else None,id=p['id'],version=p['version'],preview=preview,grant=None,target=t['label'],action=v['stage'] if v['stage'] in ('save_plan','enable_gradual') else kind,
            estimated=t['amount'],source=source,request_id=p['request_id'])
        self.w._interpret_requests[p['request_id']]['status']='review'
        return self.view()

    def review_balance(self,payload):
        """Explicit question answer prepares a preview, never commits or grants."""
        from .current_balance_review import proposal
        from .history_workflow import HistoryWorkflow
        fields(payload,'proposal_id amount')
        p=self.proposals.get(text(payload['proposal_id']))
        if p is None:raise I3Error('proposal_missing')
        self.current(p);v=self.inspect(p)
        if v['stage']!='blocked' or v['reason']!='current_balance_required':raise I3Error('answer_stale')
        old=p['position'];balance=old.balance if payload['amount'] is None else HistoryWorkflow.amount(payload['amount'],old.currency)
        signature=balance
        if self.pending and self.pending['id']==p['id'] and self.pending['action']=='balance_review' and self.pending.get('balance_signature')==signature:return self.view()
        snapshot=self.w.store.snapshot;identity=uuid4().hex
        at=max(datetime.now(timezone.utc),*(r.recorded_at for r in snapshot.receipts)) if snapshot.receipts else datetime.now(timezone.utc)
        change=proposal(snapshot,self.w.store.session_generation,old,balance,at,identity,p['source'])
        preview=self.w.store.preview(self.w.context.owner_id,change,Ref('lifecycle/preview/'+identity,1))
        if self.pending:self.cancel({})
        self.pending=dict(id=p['id'],version=p['version'],preview=preview,grant=None,target=v['account'],
            action='balance_review',estimated=old.balance,source=p['source'],request_id=p['request_id'],
            balance_signature=signature,summary_after=None,linked_payment=None)
        self.w._interpret_requests[p['request_id']]['status']='review'
        return self.view()

    def balance_result(self,operation,recovered=False):
        """Receipt wins even if continuation is stale, stopped, or process-local."""
        result=dict(stage='applied',action='balance_review',operation=self.w._status(operation,recovered=recovered),resume=None)
        receipt,_=self.w._receipt(operation)
        pending=self.pending
        if not pending or pending['preview'].ref.id!=operation.preview_id:return result
        p=self.proposals.get(pending['id'])
        if p is None:return result
        result['source_text']=p['source']
        read=self.w.authority.read()
        if (read.snapshot.world.ref!=receipt.after_world_ref or
            self.w.reality_routing.active_request!=p['request_id'] or
            self.w._interpret_requests.get(p['request_id'],{}).get('status') not in ('ready','review')):
            result['resume_reason']='world_changed';return result
        # Refresh only after our exact committed review, never after another
        # writer's change. Re-resolve the same target identity; clear coverage.
        target=(p['target'] if p['target']['kind']=='standalone' else
            next((t for t in self.targets(read) if t['key']==p['target']['key'] and t['ref']==p['target']['ref']),None))
        if target is None:result['resume_reason']='world_changed';return result
        p['version']=read.version;p['options']=[target];p['answers'].pop('basis',None)
        self.pending=None
        result['resume']=self.inspect(p)
        return result

    def exact(self,preview_id,digest):
        p=self.pending
        if not p or p['preview'].ref.id!=preview_id or p['preview'].digest!=digest:raise I3Error('preview_stale')
        self.current(self.proposals[p['id']]);return p

    def authorize(self,payload):
        fields(payload,'preview_id digest');p=self.exact(**payload)
        if p['grant'] is None:p['grant']=self.w.store.authorize(self.w.context.owner_id,p['preview'],Ref('lifecycle/authorization/'+uuid4().hex,1),datetime.now(timezone.utc))
        return self.view()

    def apply(self,payload):
        fields(payload,'preview_id digest authorization_id')
        from .i4_commit import _Operation
        identity=text(payload['authorization_id']);operation=self.w._load_operation(identity)
        if operation:
            if operation.proposal_id!='lifecycle' or (operation.preview_id,operation.preview_digest)!=(payload['preview_id'],payload['digest']):raise I3Error('authorization_stale')
            if self.w._receipt(operation):
                if operation.command.change_set.ref.id.startswith('lifecycle/balance-review/'):return self.balance_result(operation,recovered=True)
                return dict(stage='applied',operation=self.w._status(operation,recovered=True))
        p=self.exact(payload['preview_id'],payload['digest'])
        if not p['grant'] or p['grant'].ref.id!=identity:raise I3Error('authorization_stale')
        if operation is None:
            command=FinancialWorldCommand('i4/'+identity,self.w.context.owner_id,p['preview'].change_set,p['preview'].ref,p['preview'].digest,p['grant'].ref,max(datetime.now(timezone.utc),p['grant'].granted_at))
            operation=_Operation(command,'','lifecycle',payload_digest(command.change_set),payload['preview_id'],payload['digest'],identity)
            self.w._operations[identity]=operation
        if hasattr(self.w,'compound'):self.w.compound.observe_lifecycle(p,operation)
        self.w._execute_command(operation)
        if p['action']=='balance_review':return self.balance_result(operation)
        return dict(stage='applied',operation=self.w._status(operation),action=p['action'],source_text=p['source'])

    def cancel(self,payload):
        fields(payload,'')
        if self.pending:
            try:self.w.store.cancel(self.w.context.owner_id,self.pending['preview'].ref)
            except Rejected:pass
            self.pending=None
        return dict(stage='idle')

    def view(self):
        if self.pending is None:return dict(stage='idle')
        p=self.pending;v=_preview_view(p['preview'],self.w.store.snapshot)
        return dict(stage='authorized' if p['grant'] else 'preview',id=p['id'],target=p['target'],action=p['action'],estimated=_wire(p['estimated']),source_text=p['source'],
            preview=v,summary_after=p['summary_after'],linked_payment=p['linked_payment'],authorization=dict(id=p['grant'].ref.id) if p['grant'] else None)

    def summary(self,read=None):
        from .presentation_policy import recommend
        read=read or self.w.authority.read();s=read.snapshot;items=[]
        for r in s.world.members:
            if r.record_kind!='planned_expense':continue
            p=s.get(r);v=expense_summary(s,p)
            events=[s.get(ref) for ref in v['occurrence_refs']]
            # Include standalone legacy forward replacements and their refunds by
            # explicit lineage only. Never group financial events by labels.
            seen={e.ref.id for e in events}
            while True:
                added=[s.get(ref) for ref in s.world.members if ref.record_kind=='actual_occurrence' and ref.ref.id not in seen
                    and s.get(ref).lineage and ((s.get(ref).lineage.replacement and s.get(ref).lineage.replacement.original_payment_ref.ref.id in seen)
                    or (s.get(ref).lineage.refund_of_ref and s.get(ref).lineage.refund_of_ref.ref.id in seen))]
                if not added:break
                events+=added;seen.update(e.ref.id for e in added)
            if len(events)!=len(v['occurrence_refs']):
                paid=[(e.actual_amount,1) for e in events if e.kind is K.EXPENSE];back=[(e.actual_amount,1) for e in events if is_refund(e)]
                v.update(gross_actual=signed_amount(paid,p.expected_amount.currency),refunded=signed_amount(back,p.expected_amount.currency),net_actual=signed_amount(paid+[(a,-1) for a,_ in back],p.expected_amount.currency))
                v['remaining_planning']=signed_amount([(p.expected_amount,1),*((a,-sign) for a,sign in paid),*((a,1) for a,_ in back)],p.expected_amount.currency)
            row=dict(key='plan:'+p.ref.id,label=p.purpose,ref=_wire(r),summary=_wire(v),events=[self.event_view(e,s) for e in events],
                semantic='net_history' if any(e.lineage and (e.lineage.refund_of_ref or e.lineage.replacement) for e in events) else 'budget' if p.fulfillment else 'purchase')
            row['presentation']=recommend(row);items.append(row)
        for ref in s.world.members:
            mark=s.get(ref)
            if ref.record_kind=='earmark' and mark.role.value=='goal':
                row=dict(key='goal:'+mark.ref.id,label=mark.purpose,ref=_wire(ref),semantic='goal',events=[],
                    summary=dict(estimated=None,target=None,accumulated=_wire(mark.allocation),status='goal',committed=None))
                row['presentation']=recommend(row);items.append(row)
        for ref in s.world.members:
            if ref.record_kind!='actual_occurrence':continue
            o=s.get(ref);proof=o.lineage.if_evidence if o.lineage else None
            if not proof:continue
            import json
            line=json.loads(proof.evidence_json)['if']
            row=dict(key='if:'+proof.if_id,label=line['label'],ref=_wire(ref),semantic='purchase',events=[self.event_view(o,s)],summary=dict(estimated=line['planning_amount'],net_actual=signed_amount([(o.actual_amount,1)],o.actual_amount.currency),status='completed',committed=None))
            row['presentation']=recommend(row);items.append(row)
        return dict(version=_wire(read.version),scope=s.world.owner_id+'/'+s.world.world_id,items=items)

    @staticmethod
    def event_view(o,s):
        receipt=next((r for r in s.receipts if record_ref(o) in r.inserted_refs),None)
        return dict(ref=_wire(record_ref(o)),amount=_wire(o.actual_amount),kind='refund' if is_refund(o) else 'replacement' if o.lineage and o.lineage.replacement else 'correction' if o.observation.corrects_ref else 'actual',
            at=o.observation.recorded_at.isoformat(),source=o.observation.freshness.rationale,lineage=_wire(o.lineage),
            position=_wire(o.destination_position_ref or o.source_position_ref),receipt=_wire(receipt.ref) if receipt else None)

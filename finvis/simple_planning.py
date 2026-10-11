"""Source-bound application preparation for D1. No authority or price research.

Literal language is only a proposal. Confirmation delegates to unchanged D1;
unclear fields are answered explicitly. Every proposal pins World and baseline.
"""
from copy import deepcopy
import re
from uuid import uuid4
from decimal import Decimal

from .decision_baseline import BaselineContext, fields, text, linked
from .world_values import AmountPrecision
from .if_interpretation import interpret as interpret_if
from .if_lines import IfError
from .reviewed_proposals import _wire

WINDOW = r'\b(?:next|this)\s+(?:spring|summer|autumn|fall|winter|month|year)\b|(?:明年|今年|下个?月)(?:春天|夏天|秋天|冬天|春季|夏季|秋季|冬季)?'


def category(source):
    groups = {'travel': r'\btrip\b|\btravel\b|\bholiday\b|旅行|旅游|出游|去.+(?:天|晚)',
              'accommodation': r'\bhotel\b|\baccommodation\b|酒店|住宿',
              'electronics': r'\bcamera\b|\bphone\b|\blaptop\b|\bcomputer\b|相机|手机|电脑',
              'training': r'\bcourse\b|\btraining\b|\bclass\b|课程|培训',
              'moving': r'\bmov(?:e|ing)\b|\bhome setup\b|搬家|家居'}
    matches = [k for k, p in groups.items() if re.search(p, source, re.I)]
    return matches[0] if len(matches) == 1 else 'other'


def context_from(source):
    result = dict(category=category(source), origin=None, destination=None, location=None,
                  country_code=None, start_date=None, end_date=None, date_note=None,
                  duration_days=None, nights=None, party_size=None, assumptions=[])
    # Keep date windows as words; do not invent a calendar date or country code.
    window = re.findall(WINDOW, source, re.I)
    if len({w.casefold() for w in window}) == 1: result['date_note'] = window[0]
    normalized = source
    for word, n in [('one',1),('two',2),('three',3),('four',4),('five',5),('six',6),('seven',7),('eight',8),('nine',9),('ten',10)]:
        normalized = re.sub(r'\b'+word+r'(?=[ -](?:days?|nights?|people|persons?)\b)',str(n),normalized,flags=re.I)
    for word, n in zip('一二三四五六七八九',range(1,10)):
        normalized = re.sub(r'(?<![一二三四五六七八九十百千万两])'+word+r'(?=天|晚|人)',str(n),normalized)
    for key, unit in [('duration_days',r'days?|天'),('nights',r'nights?|晚'),('party_size',r'people|persons?|人')]:
        values = re.findall(r'(?<![\d.零一二三四五六七八九十百千万两])(\d+)[ -]*(?:'+unit+r')(?![A-Za-z])',normalized,re.I)
        quantity=r'(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten|[一二三四五六七八九十两]+)'
        competing=re.search(quantity+r'\s*(?:or|或|到|至|[-–])\s*'+quantity+r'\s*(?:'+unit+r')',normalized,re.I)
        if len(set(values)) == 1 and not competing: result[key] = int(values[0])
    destination = re.findall(r'(?:\btrip to|\btravel to|\bgo to|\bvisit)\s+([A-Za-z][A-Za-z ]*?)(?=\s+(?:next|this|for|with|in|on|for about)|[.,;]|$)',source,re.I)
    if not destination:
        destination = re.findall(r'去([\u4e00-\u9fff]{2,12}?)(?=旅行|旅游|玩|[一二三四五六七八九十\d]+天|[，。；]|$)',source)
    if len(set(destination)) == 1: result['destination'] = destination[0].strip()
    if result['destination'] and result['duration_days'] and result['category']=='other': result['category']='travel'
    return result


def price_from(source):
    # Reuse the bounded amount/precision grammar, not the hypothetical action.
    result = interpret_if('What if I buy '+source, [], {})['draft']['amount']
    if result is None and re.search(r'unknown|未知|不清楚', source, re.I):
        currencies = [c for c in ('CNY','SGD','EUR') if re.search(r'\b'+c+r'\b',source,re.I)]
        if len(currencies) == 1:
            result = dict(currency=currencies[0],precision='unknown',value=None,range_min=None,range_max=None)
    return result


def amount_input(a):
    major = lambda v: str(Decimal(v['minor_units']) / 100)
    return dict(currency=(a.get('amount') or a.get('lower_bound') or {}).get('currency') or a.get('unknown_currency'),
        precision='exact' if a['precision']=='exact_reported' else a['precision'],
        value=major(a['amount']) if a.get('amount') else None,
        range_min=major(a['lower_bound']) if a.get('lower_bound') else None,
        range_max=major(a['upper_bound']) if a.get('upper_bound') else None)


def components_input(old):
    result=[]
    for c in old['components']:
        external=deepcopy(c['external'])
        if external: external['observed_amount']=amount_input(external['observed_amount'])
        result.append(dict(id=c['id'],label=c['label'],category=c['category'],basis=c['basis'],
            amount=amount_input(c['observed_amount']) if c['basis']=='user_assumption' else None,
            assumptions=c['assumptions'],world_ref=c['world_ref'],external=external))
    return result


class SimplePlanning:
    def __init__(self, workflow):
        self.w = workflow
        self.proposals = {}

    def prepare_if(self, payload):
        """Resolve D1 infrastructure for one reviewed Decision before D2 review.

        Only a single current, non-travel Decision cost is derived automatically.
        Existing rich evidence is reused, never silently flattened or refreshed.
        Caller holds the workflow lock; this operation cannot write Reality.
        """
        fields(payload, 'decision_id text')
        identity = text(payload['decision_id'])
        source = text(payload['text'], True)
        read = self.w.authority.read()
        snapshot = read.snapshot
        ref = next((r for r in snapshot.world.members if r.record_kind == 'world_decision'
                    and r.ref.id == identity), None) if snapshot else None
        if ref is None:
            raise IfError('choose_target')
        decision = snapshot.get(ref)
        old = self.w.baselines.latest(identity)
        if old and old['status'] in ('estimate_available', 'partially_available'):
            baseline = old
        else:
            reason = None
            basis = ' '.join(filter(None, (decision.subject, decision.question,
                                          decision.timing.source_text)))
            context = context_from(basis)
            if context['category'] == 'travel' or (old and old['context']['category'] == 'travel'):
                reason = 'evidence_needs_review' if old else 'planning_context_needed'
            elif decision.amount is None or decision.amount.precision is AmountPrecision.UNKNOWN:
                reason = 'amount_unknown'
            elif decision.observation.freshness.status.value == 'stale':
                reason = 'evidence_needs_review'
            elif any(r.record_kind == 'planned_expense' and snapshot.get(r).decision_ref == ref
                     for r in snapshot.world.members):
                reason = 'planning_context_needed'
            elif old and (len(old['components']) != 1 or old['components'][0]['basis'] != 'world'
                          or old['components'][0]['id'] != 'auto-reviewed-decision'
                          or old['buffer'] or old['explicit_dependencies']
                          or set(old['stale_reasons']) - {'world_inputs_changed'}):
                reason = 'evidence_needs_review'
            if reason:
                return dict(status='clarification_required', reason=reason, decision=decision.subject,
                            baseline=old, financial_authority=False)
            request = dict(decision_ref=_wire(ref), expected_version=old['ref']['version'] if old else 0,
                           context=context, components=[dict(id='auto-reviewed-decision',
                           label=decision.subject, category=context['category'], basis='world',
                           amount=None, assumptions=[], world_ref=_wire(ref), external=None)],
                           dependency_refs=[], scope_complete=False, buffer=None)
            baseline = self.w.baselines.establish(request)
        proposal = self.w.if_lines.propose(dict(baseline_ref=baseline['ref'], text=source)) if source else None
        return dict(status='ready', decision=decision.subject, baseline=baseline,
                    proposal=proposal, financial_authority=False)

    def route(self, source, read, request_id):
        # Actual observations and mixed financial updates stay in Agent/G/E.
        if re.search(r'\b(?:balance|paid|spent|received|cancelled|canceled|clear|delete|replace|account|salary)\b|余额|支付|已花|到账|取消|清空|删除|账户|工资', source, re.I):
            return None
        if re.search(r'\b(?:afford|affordability|advice|recommend|correct|update)\b|负担|建议|修正|更正|更新',source,re.I):
            return None
        decisions = [dict(ref=_wire(r),label=read.snapshot.get(r).subject)
                     for r in read.snapshot.world.members if r.record_kind == 'world_decision']
        if not decisions: return None
        explicit = re.search(r'baseline|planning (?:context|estimate)|基线|规划(?:背景|估算)|酒店|\bhotel\b',source,re.I)
        exact = [d for d in decisions if d['label'].casefold() in source.casefold()]
        dest = context_from(source)['destination']
        matched = exact or ([d for d in decisions if dest and dest.casefold() in d['label'].casefold()])
        if not explicit and not matched: return None
        return dict(kind='planning',source_text=text(source),decisions=matched or decisions,request_id=request_id)

    def _active(self, request_id):
        if request_id is not None and (type(request_id) is not str or not request_id or len(request_id)>128):
            raise ValueError('Bounded interpretation identity required')
        if request_id is not None and (self.w.reality_routing.active_request != request_id or
                self.w._interpret_requests.get(request_id,{}).get('status') != 'ready'):
            raise IfError('proposal_stopped')

    def prepare(self, payload):
        fields(payload,'decision_id text request_id')
        self._active(payload['request_id'])
        text(payload['decision_id'])
        read = self.w.authority.read()
        ref = next((r for r in read.snapshot.world.members if r.record_kind == 'world_decision'
                    and r.ref.id == payload['decision_id']),None)
        if ref is None: raise IfError('choose_target')
        source = text(payload['text'],True)
        if source and re.search(r'\b(?:not|never|if|paid|spent|received|cancelled|canceled)\b|如果|没有|未支付|已支付|已花|已收到|取消',source,re.I):
            raise IfError('planning_needs_clarification')
        decision = read.snapshot.get(ref)
        old = self.w.baselines.latest(ref.ref.id)
        # Authoritative Decision context supplies missing fields, never a price
        # from an unrelated record. Explicit new wording takes precedence.
        basis = decision.subject+' '+decision.question+' '+(decision.timing.source_text or '')
        for r in linked(read.snapshot,ref): basis += ' '+read.snapshot.get(r).statement
        context = context_from(basis)
        if decision.timing.date_value:
            context['start_date']=decision.timing.date_value.isoformat()
        elif decision.timing.window_start:
            context['start_date']=decision.timing.window_start.isoformat()
            context['end_date']=decision.timing.window_end.isoformat()
        if old: context = deepcopy(old['context'])
        if source:
            parsed = context_from(source)
            for k,v in parsed.items():
                if v is not None and k not in ('category','assumptions'):
                    context[k]=v
                    if k=='date_note': context['start_date']=context['end_date']=None
            # An ambiguous newly stated value cannot silently retain an old one.
            for k,unit in [('duration_days',r'days?|天'),('nights',r'nights?|晚'),('party_size',r'people|persons?|人')]:
                if parsed[k] is None and re.search(r'(?:\d|[一二三四五六七八九十]|one|two|three|four|five|six|seven|eight|nine|ten)[ -]*(?:'+unit+r')',source,re.I): context[k]=None
            if parsed['date_note'] is None and re.search(WINDOW,source,re.I):
                context['date_note']=context['start_date']=context['end_date']=None
            if parsed['category'] not in ('other','accommodation'): context['category']=parsed['category']
            context['assumptions'] = list(dict.fromkeys([*context['assumptions'],source]))
        components, dependencies = [], []
        if old:
            if old['status']=='stale': raise IfError('baseline_not_current')
            dependencies = deepcopy(old['explicit_dependencies'])
            components = components_input(old)
        price = price_from(source) if source else None
        if price:
            components = [dict(id='stated-estimate',label=source,category=category(source),
                basis='user_assumption',amount=price,assumptions=[source],world_ref=None,external=None)]
        if not components:
            costs = [r for r in read.snapshot.world.members if r.record_kind == 'planned_expense'
                     and read.snapshot.get(r).status.value == 'planned'
                     and read.snapshot.get(r).expected_amount.precision is not AmountPrecision.UNKNOWN
                     and (read.snapshot.get(r).decision_ref == ref or
                          decision.subject.casefold() == read.snapshot.get(r).purpose.casefold())]
            if not costs and decision.amount is not None and decision.amount.precision is not AmountPrecision.UNKNOWN: costs=[ref]
            if len(costs) == 1:
                components=[dict(id='reviewed-estimate',label=getattr(read.snapshot.get(costs[0]),'purpose',decision.subject),
                    category=context['category'],basis='world',amount=None,assumptions=[],world_ref=_wire(costs[0]),external=None)]
        if source and re.search(r'\bcontext\b|\bin mind\b|作为背景|参考',source,re.I):
            dependencies += [_wire(r) for r in read.snapshot.world.members if r.record_kind=='future_inflow'
                             and read.snapshot.get(r).purpose.casefold() in source.casefold() and _wire(r) not in dependencies]
        questions = list(BaselineContext.parse(context).missing({c['category'] for c in components}))
        if not components or (source and re.search(r'\b[A-Z]{3}\b|人民币|新币|元|\$|¥',source) and not price): questions.append('estimate')
        if old and (price or 'estimate' in questions): questions.append('cost_scope')
        draft=dict(decision_ref=_wire(ref),expected_version=old['ref']['version'] if old else 0,
                   context=context,components=components,dependency_refs=dependencies,
                   scope_complete=old['scope_complete'] if old and not price else False,
                   buffer=dict(amount=amount_input(old['buffer']),reason=old['buffer_reason']) if old and old['buffer'] else None)
        if len(self.proposals)>=128: raise IfError('session_capacity')
        identity='planning-review/'+uuid4().hex
        self.proposals[identity]=dict(version=read.version,draft=draft,questions=questions,
            request_id=payload['request_id'],previous=old,source=source or basis,decision=decision.subject)
        display=[]
        for c in components:
            if c['basis']=='world':
                from .decision_baseline import typed_ref
                record=read.snapshot.get(typed_ref(c['world_ref']))
                display.append(_wire(record.amount if c['world_ref']['record_kind']=='world_decision' else record.expected_amount))
            else: display.append(c['amount'] or c['external']['observed_amount'])
        return dict(id=identity,draft=deepcopy(draft),questions=questions,source_text=source or basis,
                    decision=decision.subject,previous=old,amounts=display,
                    context_labels=[read.snapshot.get(r).purpose for r in read.snapshot.world.members if _wire(r) in dependencies and hasattr(read.snapshot.get(r),'purpose')])

    def confirm(self,payload):
        fields(payload,'proposal_id answers')
        p=self.proposals.get(text(payload['proposal_id']))
        if p is None: raise IfError('proposal_missing')
        self._active(p['request_id'])
        if self.w.authority.read().version != p['version']: raise IfError('world_changed')
        answers=payload['answers']
        if type(answers) is not dict or set(answers)!=set(p['questions']):
            raise ValueError('Answer only the required questions')
        draft=deepcopy(p['draft'])
        for k in p['questions']:
            v=answers[k]
            if k=='estimate':
                source=text(v);price=price_from(source)
                if price is None: raise IfError('amount_or_unknown')
                draft['components']=[dict(id='clarified-estimate',label=p['decision'],category=draft['context']['category'],
                    basis='user_assumption',amount=price,assumptions=[source],world_ref=None,external=None)]
            elif k=='cost_scope':
                if v not in ('replace','add'): raise ValueError('Explicit component scope required')
                if v=='add':
                    old=components_input(p['previous'])
                    draft['components'][0]['id']='stated-'+uuid4().hex
                    draft['components']=old+draft['components']
            else:
                draft['context']['date_note' if k=='dates' else k]=v
        if BaselineContext.parse(draft['context']).missing({c['category'] for c in draft['components']}):
            raise ValueError('Required planning context remains unresolved')
        result=self.w.baselines.establish(draft)
        return dict(baseline=result)

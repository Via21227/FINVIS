"""Bounded bilingual intent routing, followed by explicit exact-target review.

No model verdict or financial writer. Other reports retain the existing Agent
Candidate/reconciliation path, including ActualOccurrence source/coverage gates.
"""
import re
from uuid import uuid4

from .decision_baseline import fields, text
from .if_interpretation import hypothetical
from .if_lines import IfError
from .reviewed_proposals import _wire
from .world_planning import FutureInflowStatus
from .world_serialization import payload_digest


class RealityRouting:
    def __init__(self, workflow):
        self.w = workflow
        self.proposals = {}
        self.active_request = None

    def route(self, source, read, request_id):
        self.active_request = request_id
        if hypothetical(source):
            source = text(source)
            decisions = [dict(ref=_wire(r), label=read.snapshot.get(r).subject)
                         for r in read.snapshot.world.members if r.record_kind == 'world_decision']
            # Exact shared subject language can narrow the chooser. A tie or
            # absent overlap stays an explicit choice, never collection order.
            def terms(value):
                generic = {'what','used','second','hand','cheaper','instead','alternative',
                           'option','choose','considering','purchase','buying','price','cost'}
                words = {w.casefold() for w in re.findall(r'[A-Za-z]{4,}', value)} - generic
                han = re.findall(r'[\u4e00-\u9fff]+', value)
                pairs = {part[i:i+2] for part in han for i in range(len(part)-1)}
                return words | (pairs - {'如果','假如','假设','二手','只需','需要','考虑','购买','便宜','方案','选项'})
            source_terms = terms(source)
            scores = [(len(source_terms & terms(d['label'])), d) for d in decisions]
            best = max((score for score, _ in scores), default=0)
            if best and sum(score == best for score, _ in scores) == 1:
                decisions = [d for score, d in scores if score == best]
            return dict(kind='hypothetical', source_text=source,
                decisions=decisions)
        lifecycle = self.w.plan_reality.route(source, read, request_id)
        if lifecycle is not None:
            return lifecycle
        cancelled = re.search(r'\bcancell?ed\b|取消|不发了|不会到账', source, re.I)
        if not cancelled:
            return self.w.simple_planning.route(source, read, request_id)
        # Routing is intentionally conservative. Uncertain/negative reports cannot
        # become a confirmed cancellation even if a model would guess otherwise.
        uncertain = re.search(r'\b(?:not|never|might|may|could|maybe|whether|cancel|isn.t|wasn.t|hasn.t)\b|未|没|可能|会不会|是否|并非|不取消|[?？]', source, re.I)
        source = text(source)
        options = [dict(ref=_wire(r), label=read.snapshot.get(r).purpose)
                   for r in read.snapshot.world.members if r.record_kind == 'future_inflow'
                   and read.snapshot.get(r).status is FutureInflowStatus.EXPECTED]
        # Do not map cancellation of a booking/expense into an inflow. Exact
        # label match or an explicit income cue opens target clarification.
        income = re.search(r'\b(?:bonus|income|payment|inflow|salary|refund|grant)\b|奖金|收入|工资|退款|补助|到账', source, re.I)
        if not income and not any(o['label'].casefold() in source.casefold() for o in options):
            return None
        if uncertain:
            return dict(kind='clarification', source_text=source, reason='cancellation_not_confirmed')
        if len(self.proposals) >= 128:
            raise IfError('session_capacity')
        identity = 'reality-review/'+uuid4().hex
        self.proposals[identity] = (read.version, source, options, request_id)
        return dict(kind='inflow_cancellation', id=identity, source_text=source, targets=options)

    def prepare(self, payload):
        fields(payload, 'proposal_id target')
        proposal = self.proposals.get(text(payload['proposal_id']))
        if proposal is None:
            raise IfError('proposal_missing')
        version, source, options, request_id = proposal
        if self.w._interpret_requests[request_id]['status'] != 'ready' or self.active_request != request_id:
            raise IfError('proposal_stopped')
        read = self.w.authority.read()
        if read.version != version:
            raise IfError('world_changed')
        if payload['target'] not in [o['ref'] for o in options]:
            raise IfError('choose_target')
        snapshot = read.snapshot
        binding = dict(current=_wire(read.version), source_revision=snapshot.revision,
                       source_root=_wire(snapshot.world.ref), source_digest=payload_digest(snapshot))
        result = self.w.history_actions.prepare(dict(binding=binding, action='remove',
            target=payload['target'], field=None, value=None, reason=source))
        # Interpretation has advanced into the existing History review. Stop is
        # now too late; cancellation belongs to that exact preview/grant flow.
        self.w._interpret_requests[request_id]['status'] = 'review'
        return dict(history_action=result)

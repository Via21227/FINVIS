"""Compile one explicit current-balance review through existing D/I4 authority.

History edits remain historical. This is a new user-reported observation, bound
by its exact position and World version; no settlement or freshness inference.
"""
from dataclasses import replace
from .domain import Ref, RealitySource, Confidence, ConfidenceAssessment
from .world_commands import CandidateChangeSet, ChangeMode, PutRecord, record_ref, ref_key
from .world_values import ObservationRole, FreshnessAssessment, FreshnessStatus, TemporalSpec, TypedRef, AmountPrecision
from .i3_workflow import I3Error


def proposal(snapshot, generation, position, balance, at, identity, source):
    if balance.currency is not position.currency or balance.precision is AmountPrecision.UNKNOWN:
        raise I3Error('amount_required')
    account=snapshot.get(position.account_ref)
    evidence=Ref('lifecycle/balance-evidence/'+identity,1)
    rationale='User explicitly reviewed current balance of '+account.label+' before resuming: '+source
    meta=replace(position.observation, source=RealitySource.USER_REPORT,
        evidence_refs=(*position.observation.evidence_refs,evidence), assumption_refs=(),
        observed_at=at, as_of=TemporalSpec.known_date(at.date()), recorded_at=at,
        role=ObservationRole.CURRENT, confidence=Confidence(ConfidenceAssessment.UNASSESSED,'User reported; not independently verified'),
        freshness=FreshnessAssessment(FreshnessStatus.CURRENT,rationale,at),
        corrects_ref=position.ref, correction_reason='Explicit current balance review; prior observation retained')
    new=replace(position,ref=Ref(position.ref.id,position.ref.version+1),balance=balance,observation=meta)
    operations=[PutRecord('balance-review/'+identity,new,record_ref(position))]
    touched={record_ref(position)}
    # Carry live earmark references forward without promoting their freshness,
    # changing allocations, or rewriting historical occurrence/receipt links.
    for r in snapshot.world.members:
        if r.record_kind!='earmark':continue
        old=snapshot.get(r)
        if old.position_ref!=record_ref(position):continue
        observation=replace(old.observation,recorded_at=at,corrects_ref=old.ref,
            correction_reason='Position reference advanced by explicit balance review; allocation unchanged')
        updated=replace(old,ref=Ref(old.ref.id,old.ref.version+1),position_ref=record_ref(new),observation=observation)
        operations.append(PutRecord('balance-review/earmark/'+old.ref.id,updated,r));touched.add(r)
    state=snapshot.get(TypedRef('world_state',snapshot.world.state_ref))
    observation=replace(meta,corrects_ref=None,correction_reason=None,
        freshness=FreshnessAssessment(FreshnessStatus.UNKNOWN,'Only one named balance was reviewed; no whole-World freshness assertion',at))
    return CandidateChangeSet(Ref('lifecycle/balance-review/'+identity,1),snapshot.world.owner_id,snapshot.world.world_id,
        generation,snapshot.world.ref,snapshot.world.state_ref,snapshot.world.plan_ref,snapshot.revision,
        ChangeMode.CORRECT,tuple(operations),None,tuple(sorted((r for r in snapshot.world.members if r not in touched),key=ref_key)),
        (evidence,),observation,state.coverage_note+' Current balance reviewed only for '+account.label+'.',(),at)

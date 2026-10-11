"""Committed evidence capsule; serialization never supplies executable consent."""
from dataclasses import dataclass
from hashlib import sha256
import json
from .agent_reconciliation import ReviewedChanges, review_json, _data
from .domain import Ref
from .world_commands import HistoryAction
from .world_replay import _strict_pairs
from .store import Rejected

SCHEMA='finvis.committed-evidence.v0.1'


def canonical(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)


def strict_json(text):
    def reject(_): raise ValueError('Noninteger JSON number')
    return json.loads(text,object_pairs_hook=_strict_pairs,parse_float=reject,parse_constant=reject)


@dataclass(frozen=True)
class CommitEvidence:
    reviewed: ReviewedChanges
    proposal_id: str = ''

    def __post_init__(self):
        if type(self.reviewed) is not ReviewedChanges or type(self.proposal_id) is not str:
            raise ValueError('Typed committed review required')


def capsule(command, evidence=None):
    if evidence is not None and type(evidence) is not CommitEvidence:
        raise Rejected('Unsupported committed evidence')
    history=any(type(op) is HistoryAction for op in command.change_set.operations)
    value=dict(schema_version=SCHEMA,origin='history' if history else 'domain',review=None,
               candidate_id='',proposal_id='',review_digest='')
    if evidence is not None:
        review=evidence.reviewed
        value.update(origin='reviewed',review=strict_json(review_json(review)),
            candidate_id=review.candidate_id,proposal_id=evidence.proposal_id,
            review_digest=sha256(review_json(review.request).encode()).hexdigest())
    text=canonical(value)
    validate_capsule(command,text)
    return text


def validate_capsule(command,text):
    value=strict_json(text)
    if type(value) is not dict or set(value)!={'schema_version','origin','review','candidate_id','proposal_id','review_digest'} or value['schema_version']!=SCHEMA or canonical(value)!=text:
        raise Rejected('Invalid committed evidence envelope')
    if any(type(value[f]) is not str for f in ('origin','candidate_id','proposal_id','review_digest')):
        raise Rejected('Invalid operation evidence identity')
    bound=tuple(r for r in command.change_set.candidate_refs if r.id.startswith('reviewed-interpretation/'))
    if value['origin']=='reviewed':
        r=value['review']
        names=('ref','candidate_id','candidate_digest','generation','revision','facts','request','bindings','outcomes','issues')
        if type(r) is not dict or set(r)!={'schema_version','value'} or r['schema_version']!='finvis.agent-review.v0.1':
            raise Rejected('Invalid review envelope')
        r=r['value']
        if type(r) is not dict or set(r)!={*names,'change_set'}:
            raise Rejected('Invalid review fields')
        digest=sha256(canonical(dict(schema_version='finvis.agent-review.v0.1',value=[r[k] for k in names])).encode()).hexdigest()
        if (command.change_set.candidate_refs!=(Ref('reviewed-interpretation/'+digest,1),) or
            r['change_set']!=_data(command.change_set) or r['generation']!=command.change_set.session_generation or
            r['revision']!=command.change_set.expected_revision or r['candidate_id']!=value['candidate_id'] or
            sha256(canonical(dict(schema_version='finvis.agent-review.v0.1',value=r['request'])).encode()).hexdigest()!=value['review_digest']):
            raise Rejected('Committed review binding mismatch')
    else:
        history=any(type(op) is HistoryAction for op in command.change_set.operations)
        if value['origin']!=('history' if history else 'domain') or bound or value['review'] is not None or any(value[f] for f in ('candidate_id','proposal_id','review_digest')):
            raise Rejected('Required committed review evidence missing')
    return value

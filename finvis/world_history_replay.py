"""Slice F v0.2: original financial steps across explicit Host epochs.

Reuses F's D/E replay, never registers live consent or writes a database.
The v0.1 codec and its single-generation contract remain unchanged.
"""
from dataclasses import dataclass
from typing import Optional, Tuple
import json

from .domain import Ref, nonempty
from .store import Rejected
from .world_commands import WorldSnapshot
from .world_store import WorldStore
from .world_serialization import payload_digest, serialize_world_mutation, deserialize_world_mutation
from .world_replay import (WorldReplayStep, MigrationEvidence, WorldReplayPackage,
    _history_parts, _replay_step, _derived_digest, _strict_pairs,
    serialize_world_replay, deserialize_world_replay)

SCHEMA = 'finvis.world-replay.v0.2'
CONTRACT = 'finvis.world-execution.v0.1'


@dataclass(frozen=True)
class ReplayEpoch:
    generation: str
    start_revision: int
    start_world_ref: Ref
    previous_generation: Optional[str]
    contract: str = CONTRACT

    def __post_init__(self):
        nonempty(self.generation, 'generation')
        if type(self.generation) is not str or type(self.start_revision) is not int or self.start_revision < 0 or type(self.start_world_ref) is not Ref:
            raise ValueError('Invalid replay epoch')
        if self.previous_generation is not None and (type(self.previous_generation) is not str or not self.previous_generation):
            raise ValueError('Invalid preceding epoch')
        if self.contract != CONTRACT:
            raise ValueError('Unsupported execution contract')


@dataclass(frozen=True)
class WorldReplayHistory:
    initial_snapshot: WorldSnapshot
    steps: Tuple[WorldReplayStep, ...]
    epochs: Tuple[ReplayEpoch, ...]
    final_snapshot_digest: str
    final_derived_digest: str
    migration_evidence: Optional[MigrationEvidence] = None

    def __post_init__(self):
        if type(self.initial_snapshot) is not WorldSnapshot or self.initial_snapshot.revision or self.initial_snapshot.commands or self.initial_snapshot.receipts or self.initial_snapshot.authorizations:
            raise ValueError('Replay initial closure required')
        if type(self.steps) is not tuple or any(type(s) is not WorldReplayStep for s in self.steps):
            raise ValueError('Exact F steps required')
        if type(self.epochs) is not tuple or not self.epochs or any(type(e) is not ReplayEpoch for e in self.epochs):
            raise ValueError('Replay epoch chain required')
        for digest in (self.final_snapshot_digest, self.final_derived_digest):
            if type(digest) is not str or len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest):
                raise ValueError('Invalid replay digest')
        if self.migration_evidence is not None and type(self.migration_evidence) is not MigrationEvidence:
            raise ValueError('Invalid migration evidence')
        worlds = (self.initial_snapshot.world, *(s.after_world for s in self.steps))
        previous, revision, seen = None, 0, set()
        for index, epoch in enumerate(self.epochs):
            if (epoch.generation in seen or epoch.previous_generation != previous or
                epoch.start_revision < revision or epoch.start_revision > len(self.steps) or
                (index == 0 and epoch.start_revision != 0) or
                worlds[epoch.start_revision].ref != epoch.start_world_ref):
                raise ValueError('Incoherent replay epoch chain')
            seen.add(epoch.generation); previous = epoch.generation; revision = epoch.start_revision
        for index, step in enumerate(self.steps):
            epoch = next(e for e in reversed(self.epochs) if e.start_revision <= index)
            if {step.command.change_set.session_generation, step.authorization.session_generation,
                step.receipt.session_generation} != {epoch.generation}:
                raise ValueError('Replay step generation mismatch')
        if self.migration_evidence is not None:
            # Reuse F's existing migration-to-initial binding checks.
            initial = self.initial_snapshot
            WorldReplayPackage(Ref('initial-check', 1), self.epochs[0].generation,
                initial, (), initial.world.ref, initial.world.state_ref, initial.world.plan_ref,
                0, payload_digest(initial), _derived_digest(initial), self.migration_evidence)


def snapshot_prefix(snapshot, revision):
    """Same canonical prefix bytes in either generation envelope."""
    if type(revision) is not int or not 0 <= revision <= snapshot.revision:
        raise Rejected('history_revision_invalid')
    initial, steps = _history_parts(snapshot)
    steps = steps[:revision]
    return WorldSnapshot((*initial.records, *(r for s in steps for r in s.introduced_records)),
        (initial.world, *(s.after_world for s in steps)), tuple(s.authorization for s in steps),
        tuple(s.command for s in steps), tuple(s.receipt for s in steps), revision)


def export_history_replay(snapshot, epochs, migration_evidence=None):
    initial, steps = _history_parts(snapshot)
    return WorldReplayHistory(initial, steps, tuple(epochs), payload_digest(snapshot),
                              _derived_digest(snapshot), migration_evidence)


def replay_history(package):
    if type(package) is not WorldReplayHistory:
        raise Rejected('Versioned replay history required')
    store = WorldStore._restore_replay_initial(package.initial_snapshot, package.epochs[0].generation)
    for index, step in enumerate(package.steps):
        epoch = next(e for e in reversed(package.epochs) if e.start_revision <= index)
        # Isolated verifier only; historical grant registration never escapes.
        if epoch.generation != store.session_generation:
            store._generation = epoch.generation
            store._previews.clear(); store._authorizations.clear(); store._cancelled.clear()
        _replay_step(store, step)
    result = store.snapshot
    if payload_digest(result) != package.final_snapshot_digest or _derived_digest(result) != package.final_derived_digest:
        raise Rejected('History replay result mismatch')
    return result


def dumps_history_replay(value):
    if type(value) is not WorldReplayHistory:
        raise ValueError('Expected v0.2 replay history')
    payload = dict(schema_version=SCHEMA, initial_snapshot=serialize_world_mutation(value.initial_snapshot),
        steps=[serialize_world_replay(s) for s in value.steps], epochs=[dict(generation=e.generation,
            start_revision=e.start_revision, start_world_ref=dict(id=e.start_world_ref.id,version=e.start_world_ref.version),
            previous_generation=e.previous_generation, contract=e.contract) for e in value.epochs],
        final_snapshot_digest=value.final_snapshot_digest, final_derived_digest=value.final_derived_digest,
        migration_evidence=serialize_world_replay(value.migration_evidence) if value.migration_evidence else None)
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def loads_history_replay(text):
    def reject(_): raise ValueError('Noninteger replay number')
    v=json.loads(text, object_pairs_hook=_strict_pairs, parse_float=reject, parse_constant=reject)
    if type(v) is not dict or set(v) != {'schema_version','initial_snapshot','steps','epochs','final_snapshot_digest','final_derived_digest','migration_evidence'} or v['schema_version'] != SCHEMA:
        raise ValueError('Unknown replay history envelope')
    if type(v['epochs']) is not list or type(v['steps']) is not list:
        raise ValueError('Invalid replay collections')
    epochs=[]
    for e in v['epochs']:
        if type(e) is not dict or set(e) != {'generation','start_revision','start_world_ref','previous_generation','contract'}:
            raise ValueError('Invalid epoch fields')
        ref=e['start_world_ref']
        if type(ref) is not dict or set(ref) != {'id','version'}: raise ValueError('Invalid epoch root')
        epochs.append(ReplayEpoch(e['generation'],e['start_revision'],Ref(**ref),e['previous_generation'],e['contract']))
    return WorldReplayHistory(deserialize_world_mutation(v['initial_snapshot']),
        tuple(deserialize_world_replay(s) for s in v['steps']),tuple(epochs),v['final_snapshot_digest'],
        v['final_derived_digest'],deserialize_world_replay(v['migration_evidence']) if v['migration_evidence'] is not None else None)

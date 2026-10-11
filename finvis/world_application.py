"""Opt-in I1 application read boundary. The running demo does not instantiate it.

No Candidate, preview, grant, commit, HTTP route or global selected context.
"""

from dataclasses import dataclass, replace
from enum import Enum
from typing import Optional, Protocol, Tuple, Union

from .domain import PlanStatus, Ref, nonempty
from .store import Snapshot, Rejected, StaleVersion
from .world_commands import WorldSnapshot
from .world_domain import WorldAssetBundle, WorldState
from .world_planning import WorldPlan, WorldPlanningBundle, PlanEvaluationStatus
from .world_values import ObservationMetadata, ObservationRole, TypedRef
from .world_store import WorldStore, initial_world
from .world_projection import WorldProjection, ProjectionError, project_world


class ContextKind(Enum):
    DEMO = 'demo'
    USER = 'user'


@dataclass(frozen=True)
class WorldContext:
    kind: ContextKind
    context_id: str
    owner_id: str
    world_id: str

    def __post_init__(self):
        if type(self.kind) is not ContextKind:
            raise ValueError('Explicit context kind required')
        for name in ('context_id', 'owner_id', 'world_id'):
            if type(getattr(self, name)) is not str:
                raise ValueError('Context identity must be text')
            nonempty(getattr(self, name), name)
        if not self.context_id.startswith(self.kind.value + ':') or not self.context_id.split(':', 1)[1]:
            raise ValueError('Context ID must be namespaced by kind')


@dataclass(frozen=True)
class ReadVersion:
    context: WorldContext
    session_generation: str
    revision: int
    world_ref: Optional[Ref]
    state_ref: Ref
    plan_ref: Ref

    def __post_init__(self):
        if type(self.context) is not WorldContext:
            raise ValueError('Version requires complete context identity')
        if type(self.session_generation) is not str or not self.session_generation.strip():
            raise ValueError('Version requires session generation')
        if type(self.revision) is not int or self.revision < 0:
            raise ValueError('Version revision must be a nonnegative integer')
        if type(self.state_ref) is not Ref or type(self.plan_ref) is not Ref:
            raise ValueError('Version requires exact State and Plan refs')
        if self.context.kind is ContextKind.USER:
            if type(self.world_ref) is not Ref:
                raise ValueError('User version requires a world ref')
        elif self.world_ref is not None:
            raise ValueError('Legacy Demo has no FinancialWorld root')


class UserWorldSource(Protocol):
    """Read port. A source's generation is stable for its lifetime.

    snapshot returns one immutable atomic publication; a restarted source gets
    a new authority instance/generation. No storage or write API is required.
    """
    @property
    def snapshot(self) -> WorldSnapshot:
        ...

    @property
    def session_generation(self) -> str:
        ...


@dataclass(frozen=True)
class UserWorldRead:
    context: WorldContext
    initialized: bool
    version: Optional[ReadVersion]
    snapshot: Optional[WorldSnapshot]
    projection: Optional[WorldProjection]
    reasons: Tuple[str, ...] = ()


@dataclass(frozen=True)
class LegacyDemoRead:
    """Explicit compatibility snapshot; never a User World projection."""
    context: WorldContext
    version: ReadVersion
    snapshot: Snapshot
    compatibility: str = 'legacy_demo'


class UserWorldAuthority:
    """Read handle over one source, never a second stored financial snapshot."""
    def __init__(self, context: WorldContext, source: Optional[UserWorldSource] = None):
        if type(context) is not WorldContext or context.kind is not ContextKind.USER:
            raise ValueError('User authority requires an explicit User context')
        self._context = context
        self._source = source

    @property
    def context(self):
        return self._context

    @classmethod
    def empty(cls, context: WorldContext, session_generation: str, observation: ObservationMetadata):
        """Explicit trusted bootstrap, never startup migration or a user API.

        Caller supplies initialization provenance/time; no account, currency,
        balance, Goal, Decision or synthetic report is manufactured.
        """
        if type(context) is not WorldContext or context.kind is not ContextKind.USER:
            raise ValueError('Empty bootstrap requires a User context')
        if (type(observation) is not ObservationMetadata or observation.role is not ObservationRole.CURRENT
                or observation.corrects_ref is not None):
            raise ValueError('Explicit current initialization metadata required')
        state = WorldState(Ref(context.world_id + '/state', 1), context.owner_id, context.world_id,
            (), (), (), observation, 'No asset observations recorded; asset coverage is unknown')
        plan = WorldPlan(Ref(context.world_id + '/plan', 1), context.owner_id, context.world_id,
            TypedRef('world_state', state.ref), None, (), (), (), (), PlanStatus.DRAFT,
            PlanEvaluationStatus.PENDING, replace(observation, role=ObservationRole.PLANNED))
        bundle = WorldPlanningBundle(WorldAssetBundle(state, (), (), ()), plan, (), (), (), (), ())
        store = WorldStore(initial_world(bundle, Ref(context.world_id, 1)), session_generation)
        return cls(context, store)

    def read(self, expected: Optional[ReadVersion] = None) -> UserWorldRead:
        if expected is not None and type(expected) is not ReadVersion:
            raise ValueError('Expected version must be a complete ReadVersion')
        if self._source is None:
            if expected is not None:
                raise StaleVersion('User World is not initialized')
            return UserWorldRead(self.context, False, None, None, None, ('user_world_not_initialized',))
        try:
            generation = self._source.session_generation
            if type(generation) is not str:
                raise ValueError('Invalid generation')
            nonempty(generation, 'session_generation')
            snapshot = self._source.snapshot  # Exactly one financial read.
            if generation != self._source.session_generation:
                raise StaleVersion('User source session changed during read')
            if type(snapshot) is not WorldSnapshot:
                raise ProjectionError('invalid_user_source')
            root = snapshot.world
            if root.owner_id != self.context.owner_id or root.world_id != self.context.world_id:
                raise ProjectionError('wrong_user_authority')
            version = ReadVersion(self.context, generation, snapshot.revision, root.ref, root.state_ref, root.plan_ref)
            if expected is not None and expected != version:
                raise StaleVersion('Context/session/world/state/plan/revision changed')
            projection = project_world(snapshot)
            return UserWorldRead(self.context, True, version, snapshot, projection)
        except (StaleVersion, ProjectionError):
            raise
        except Exception as error:
            # No demo/last-good fallback on storage or projection failure.
            raise ProjectionError('user_read_failed') from error


class ApplicationReads:
    """Explicit per-request context dispatch, not a process-global UI switch.

    The demo object remains separately owned by its existing application. Its
    lock supplies a coherent snapshot/generation pair across Demo reset.
    """
    def __init__(self, demo, demo_context: WorldContext, user: UserWorldAuthority):
        from .application import DemoApplication
        if not isinstance(demo, DemoApplication) or type(demo_context) is not WorldContext:
            raise ValueError('Explicit legacy Demo application/context required')
        if demo_context.kind is not ContextKind.DEMO or not isinstance(user, UserWorldAuthority):
            raise ValueError('Distinct Demo and User authority required')
        if demo_context.world_id == user.context.world_id or demo_context.context_id == user.context.context_id:
            raise ValueError('Demo/User audit namespaces must be distinct')
        self._demo, self._demo_context, self._user = demo, demo_context, user
        self.read(demo_context)  # Verify legacy ownership without invoking view/provider.

    @property
    def contexts(self):
        return (self._demo_context, self._user.context)

    def read(self, context: WorldContext, expected: Optional[ReadVersion] = None) -> Union[LegacyDemoRead, UserWorldRead]:
        if type(context) is not WorldContext:
            raise Rejected('Explicit context required')
        if context == self._user.context:
            return self._user.read(expected)
        if context != self._demo_context:
            raise Rejected('Unknown authority context')
        if expected is not None and type(expected) is not ReadVersion:
            raise ValueError('Expected version must be a complete ReadVersion')
        with self._demo._lock:
            snapshot = self._demo.store.snapshot
            if any(getattr(r, 'owner_id', context.owner_id) != context.owner_id for r in snapshot.records):
                raise ProjectionError('wrong_demo_owner')
            version = ReadVersion(context, self._demo.generation, snapshot.revision, None,
                                  snapshot.current_state_ref, snapshot.current_plan_ref)
            if expected is not None and expected != version:
                raise StaleVersion('Demo context/session/revision changed')
            return LegacyDemoRead(context, version, snapshot)

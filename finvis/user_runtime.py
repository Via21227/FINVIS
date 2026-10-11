"""P2 Host lifecycle for exactly one P1 User authority; no financial importer.

The selection file contains identity only. It distinguishes first use from a
missing selected database; it never overrides P1's financial truth.
"""
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from threading import RLock
from uuid import uuid4
import os

from .platform_storage import private_mkdir, durable_write
from .agent_understanding import AgentUnderstandingService
from .domain import Confidence, ConfidenceAssessment, RealitySource, Ref
from .engine_inputs import manifest_from_read
from .i3_workflow import I3Error
from .i4_commit import I4Workflow
from .persistence_evidence import canonical, strict_json
from .persistent_world import (PersistentWorldStore, StorageLocation,
    StorageUnavailable, StorageIntegrityError, StorageBusy, _private)
from .world_application import ContextKind, UserWorldAuthority, WorldContext
from .world_values import (ObservationMetadata, ObservationRole, TemporalSpec,
    FreshnessAssessment, FreshnessStatus)

SELECTION_SCHEMA = 'finvis.user-store-selection.v0.1'


def runtime_location(test_data_root=None):
    """Explicit test override restricted to a child of an OS temp directory."""
    if test_data_root is None:
        return StorageLocation.production()
    root = StorageLocation.temporary(Path(test_data_root)).directory
    return StorageLocation.temporary(root / 'Application Support' / 'FINVIS' / 'UserWorld')


def storage_scope(location):
    return sha256(str(location.directory).encode()).hexdigest()


class UserRuntime:
    def __init__(self, provider, location=None):
        self.location = location or StorageLocation.production()
        self.provider = provider
        self.workflow = None
        self.future_memory = None
        self._store = None
        self._lock = RLock()
        self.state = 'starting'
        self.error = None
        self._recover()

    @property
    def selection_path(self):
        return self.location.directory.parent / 'user-world-selection.json'

    def status(self):
        return {'state': self.state, 'retention': 'durable_local', 'code': self.error}

    def _read_selection(self):
        path = self.selection_path
        if not path.exists() and not path.is_symlink():
            return None
        _private(path.parent, True)
        _private(path)
        value = strict_json(path.read_text())
        fields = {'schema_version','phase','context_id','owner_id','world_id','store_id','activation_id'}
        if (type(value) is not dict or set(value) != fields or
                value['schema_version'] != SELECTION_SCHEMA or
                value['phase'] not in ('initializing', 'ready')):
            raise StorageIntegrityError('Invalid selection metadata')
        for field in ('context_id','owner_id','world_id'):
            if type(value[field]) is not str or not value[field]:
                raise StorageIntegrityError('Missing selected identity')
        WorldContext(ContextKind.USER, value['context_id'], value['owner_id'], value['world_id'])
        for field in ('store_id', 'activation_id'):
            if (value['phase'] == 'initializing' and value[field] is not None or
                    value['phase'] == 'ready' and (type(value[field]) is not str or not value[field])):
                raise StorageIntegrityError('Invalid selected store identity')
        return value

    def _write_selection(self, value, initial=False):
        path = self.selection_path
        private_mkdir(path.parent)
        _private(path.parent, True)
        durable_write(path,canonical(value).encode('utf-8'),replace=not initial)

    def _bind_ready(self, selected, store):
        expected = (selected['context_id'], selected['owner_id'], selected['world_id'])
        actual = (store.context.context_id, store.context.owner_id, store.context.world_id)
        if expected != actual:
            raise StorageIntegrityError('Selected context does not match the database')
        if selected['phase'] == 'ready':
            if (selected['store_id'], selected['activation_id']) != store.storage_identity:
                raise StorageIntegrityError('Selected store identity mismatch')
        else:
            if store.snapshot.revision != 0:
                raise StorageIntegrityError('Unfinished initialization has financial history')
            self._write_selection(dict(selected, phase='ready',
                store_id=store.storage_identity[0], activation_id=store.storage_identity[1]))

    def _publish_ready(self, store):
        authority = UserWorldAuthority(store.context, store)
        read = authority.read()
        manifest_from_read(read)  # Rebuild existing I6.1 inputs, no Engine evaluation.
        workflow = I4Workflow(authority, AgentUnderstandingService(self.provider), store)
        workflow.home()
        workflow.history()
        from .future_memory import FutureMemoryService
        future_memory = FutureMemoryService(store)
        future_memory.read()  # Verify retained exploration before exposing the runtime.
        self.future_memory = future_memory
        from .future_experience import FutureExperience
        self.future_experience = FutureExperience(future_memory)
        from .if_interaction import IfInteraction
        self.if_interaction = IfInteraction(self.future_experience)
        workflow.compound_if = self.if_interaction
        from .future_adoption import FutureAdoption
        workflow.future_adoption = FutureAdoption(workflow, self.future_experience)
        self.workflow = workflow
        self.state = 'ready'
        self.error = None

    def _failed(self, error):
        self.workflow = None
        self.future_memory = None
        self.state = 'recovery_required'
        self.error = ('user_storage_locked' if isinstance(error, StorageBusy) else
            'user_storage_integrity' if isinstance(error, (StorageIntegrityError, ValueError, TypeError)) else
            'user_storage_unavailable' if isinstance(error, (StorageUnavailable, OSError)) else
            'user_recovery_failed')
        if self._store is not None:
            self._store.close()
            self._store = None

    def _recover(self):
        try:
            self.location.__post_init__()
            selected = self._read_selection()
            directory = self.location.directory
            if selected is None:
                # Do not silently adopt a P1 file or an interrupted initialization.
                if directory.exists() and any(directory.iterdir()):
                    raise StorageIntegrityError('Unselected existing User storage')
                self.state = 'first_run'
                return
            self.state = 'validating'
            self._store = PersistentWorldStore.open(self.location)
            self._bind_ready(selected, self._store)
            self.state = 'rebuilding'
            self._publish_ready(self._store)
        except Exception as error:
            self._failed(error)

    def ensure(self):
        """Only an explicit User action may create the first empty database."""
        with self._lock:
            self.require_available()
            if self.workflow is not None:
                return self.workflow
            # Another Host may have initialized since our startup read.
            self._recover()
            self.require_available()
            if self.workflow is not None:
                return self.workflow
            try:
                identity = uuid4().hex
                context = WorldContext(ContextKind.USER, 'user:' + identity,
                    'user/' + identity, 'user-world/' + identity)
                selected = dict(schema_version=SELECTION_SCHEMA, phase='initializing',
                    context_id=context.context_id, owner_id=context.owner_id,
                    world_id=context.world_id, store_id=None, activation_id=None)
                self._write_selection(selected, initial=True)
                now = datetime.now(timezone.utc)
                observation = ObservationMetadata(RealitySource.USER_REPORT, (),
                    (Ref('empty-world-initialization/' + identity, 1),), None,
                    TemporalSpec.unknown('No asset observation submitted'), now,
                    ObservationRole.CURRENT,
                    Confidence(ConfidenceAssessment.UNASSESSED, 'Empty User World initialization'),
                    FreshnessAssessment(FreshnessStatus.UNKNOWN, 'No financial observations', now))
                self._store = PersistentWorldStore.create(context, observation, self.location)
                self._bind_ready(selected, self._store)
                self._publish_ready(self._store)
            except Exception as error:
                self._failed(error)
                self.require_available()
            return self.workflow

    def require_available(self):
        if self.state == 'recovery_required':
            raise I3Error(self.error, 503)
        if self.state == 'closed':
            raise I3Error('user_storage_unavailable', 503)

    def close(self):
        with self._lock:
            if self._store is not None:
                self._store.close()
                self._store = None
            self.workflow = None
            self.future_memory = None
            self.state = 'closed'

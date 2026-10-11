"""P1 durable WorldStore; P2 installs it behind the application User authority.

D/E stage financial meaning; SQLite accepts the complete publication before RAM.
No database path is opened merely by importing this module.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from threading import RLock
from uuid import uuid4
import os
import sqlite3
import stat
import sys
import tempfile

from .platform_storage import (application_directory, check_private, private_mkdir,
    acquire_lock, release_lock, durable_empty_file, durable_write, sync_file, sqlite_supported)

from .store import Rejected, StaleVersion
from .domain import Ref
from .world_application import WorldContext, ContextKind, UserWorldAuthority
from .world_commands import FinancialWorldCommand, WorldSnapshot, record_ref
from .world_store import WorldStore
from .world_serialization import _encode, _decode, payload_digest
from .world_replay import WorldReplayStep, _derived_digest
from .world_serialization import SCHEMA as MUTATION_SCHEMA
from .world_history_replay import (ReplayEpoch, WorldReplayHistory, CONTRACT,
    export_history_replay, replay_history, snapshot_prefix)
from .persistence_evidence import canonical, strict_json, capsule, validate_capsule
from .persistence_schema import (VERSION, APPLICATION_ID, APP_VERSION, FUTURE_APP_VERSION,
    CORE_CODECS, FUTURE_CODECS, MIGRATIONS, schema_statements, migrate)


class StorageUnavailable(Rejected):
    """Outcome cannot currently be proven; never means not committed."""


class StorageIntegrityError(StorageUnavailable):
    pass


class StorageBusy(StorageUnavailable):
    pass


SYSTEM_TEMP_ROOT = Path(tempfile.gettempdir()).resolve()


@dataclass(frozen=True)
class StorageLocation:
    directory: Path
    testing: bool = False
    competition_id: str = None

    def __post_init__(self):
        if type(self.testing) is not bool or not isinstance(self.directory, Path) or not self.directory.is_absolute():
            raise ValueError('Explicit absolute storage location required')
        if self.directory != self.directory.resolve():
            raise ValueError('Symlinked storage path is unsupported')
        if self.competition_id is not None:
            import re
            if self.testing or not isinstance(self.competition_id, str) or not re.fullmatch(r'[0-9a-f]{32}', self.competition_id):
                raise ValueError('Invalid competition instance')
            expected = competition_directory()/'instances'/self.competition_id/'state'/'UserWorld'
            if self.directory != expected:
                raise ValueError('Competition storage must use its isolated instance')
        elif self.testing:
            roots=(Path('/private/tmp'),SYSTEM_TEMP_ROOT)
            if not any(root in self.directory.parents for root in roots):
                raise ValueError('Test database must be inside an isolated temporary directory')
        elif self.directory != production_directory():
            raise ValueError('Production storage must use local Application Support')

    @classmethod
    def production(cls):
        return cls(production_directory())

    @classmethod
    def temporary(cls, directory):
        return cls(Path(directory), True)

    @classmethod
    def competition(cls, instance_id):
        return cls(competition_directory()/'instances'/instance_id/'state'/'UserWorld', competition_id=instance_id)

    @property
    def database(self):
        return self.directory/'financial-world.sqlite3'


def competition_directory():
    return application_directory('FINVIS Competition')


def production_directory():
    return application_directory('FINVIS')/'UserWorld'


def _dump(value): return canonical(_encode(value))


def _load(text):
    value=_decode(strict_json(text))
    if _dump(value)!=text: raise ValueError('Noncanonical persisted value')
    return value


def _hash(text): return sha256(text.encode()).hexdigest()


def _private(path, directory=False):
    try:check_private(path,directory)
    except OSError:raise StorageUnavailable('Unsafe storage ownership/type/permissions') from None


class PersistentWorldStore(WorldStore):
    """Create a deliberate empty User baseline, or reopen one verified ledger.

    create/open are Core APIs only. Neither imports a Demo or a RAM snapshot.
    All inherited previews/grants remain ephemeral. close never saves pending work.
    """
    def __init__(self, *args, **kwargs):
        raise TypeError('Use explicit PersistentWorldStore.create/open')

    @classmethod
    def create(cls, context, observation, location=None):
        if type(context) is not WorldContext or context.kind is not ContextKind.USER:
            raise Rejected('Explicit User context required')
        generation=uuid4().hex
        initial=UserWorldAuthority.empty(context,generation,observation)._source.snapshot
        self=cls._connect(location or StorageLocation.production(),create=True)
        try:
            self._context=context
            self._store_id=uuid4().hex; self._activation_id=uuid4().hex
            def initialize():
                self._db.execute('INSERT INTO roots VALUES (?,?,?,?)',(0,initial.world.ref.id,initial.world.ref.version,_dump(initial.world)))
                self._insert_records(initial.records,0,0)
                self._db.execute('INSERT INTO metadata VALUES (1,?,?,?,?,?,?,?,?)',
                    (self._store_id,self._activation_id,context.context_id,context.owner_id,context.world_id,
                     0,payload_digest(initial),_derived_digest(initial)))
                self._insert_epoch(0,ReplayEpoch(generation,0,initial.world.ref,None))
            migrate(self._db,validate=initialize)
            self._sync_directory()
            self._hydrate(self._verify(),generation)
            return self
        except BaseException:
            self.close(); raise

    @classmethod
    def open(cls, location=None):
        self=cls._connect(location or StorageLocation.production(),create=False)
        try:
            snapshot=self._verify()
            if self._db.execute('PRAGMA user_version').fetchone()[0] < VERSION:
                # Verify v1 fully before any DDL. migrate also proves byte-identical
                # financial tables inside the transaction and retains a private backup.
                def validate_upgrade():
                    sql={row[0] for row in self._db.execute("SELECT sql FROM sqlite_master WHERE sql IS NOT NULL")}
                    if sql != schema_statements(VERSION):
                        raise StorageIntegrityError('Unexpected upgraded schema')
                    from .future_memory import verify_memory
                    verify_memory(self,snapshot)
                migrate(self._db,validate=validate_upgrade,
                    backup_path=self._location.directory/('schema-v1-before-v2-'+uuid4().hex+'.sqlite3'),
                    checkpoint=self._checkpoint)
                self._sync_directory()
                snapshot=self._verify()
            generation=uuid4().hex
            epoch=ReplayEpoch(generation,snapshot.revision,snapshot.world.ref,self._epochs[-1].generation)
            self._db.execute('BEGIN IMMEDIATE')
            try:
                self._check_head(snapshot)
                self._insert_epoch(len(self._epochs),epoch)
                self._db.execute('COMMIT')
            except BaseException:
                if self._db.in_transaction:self._db.execute('ROLLBACK')
                raise
            self._epochs=(*self._epochs,epoch)
            self._hydrate(snapshot,generation)
            return self
        except BaseException:
            self.close(); raise

    @classmethod
    def _connect(cls,location,create):
        if type(location) is not StorageLocation: raise ValueError('StorageLocation required')
        # Recheck paths at the actual open, not only when the value was made.
        location.__post_init__()
        self=cls.__new__(cls)
        self._db=None;self._lock_fd=None;self._lock=RLock();self._location=location
        self._closed=False;self._unavailable=False;self._execution_evidence=None
        try:
            if create:
                private_mkdir(location.directory)
            _private(location.directory,True)
            path=location.database
            if create and path.exists(): raise StorageUnavailable('Database already exists; never overwrite')
            if not create:
                _private(path)
                if path.stat().st_size==0: raise StorageIntegrityError('Database is empty, not a new User World')
            lockpath=location.directory/'authority.lock'
            try:self._lock_fd=acquire_lock(lockpath,create=create,blocking=False)
            except BlockingIOError:raise StorageBusy('Another Host owns this authority') from None
            if create:durable_empty_file(path)
            for suffix in ('','-wal','-shm'):
                file=Path(str(path)+suffix)
                if file.exists() or file.is_symlink():_private(file)
            self._db=sqlite3.connect(path.as_uri()+'?mode=rw',uri=True,isolation_level=None,timeout=0.25,check_same_thread=False)
            if not create:
                self._check_version()
            self._configure()
            return self
        except (OSError,sqlite3.Error) as error:
            self.close()
            raise StorageUnavailable('Storage cannot be opened') from None
        except BaseException:
            self.close();raise

    def _configure(self):
        version=sqlite3.sqlite_version_info
        fixed=sqlite_supported(version)
        if not fixed:raise StorageUnavailable('SQLite build lacks required WAL correction')
        self._db.execute('PRAGMA journal_mode=WAL')
        self._db.execute('PRAGMA synchronous=FULL')
        self._db.execute('PRAGMA foreign_keys=ON')
        self._db.execute('PRAGMA trusted_schema=OFF')
        if sys.platform=='darwin':self._db.execute('PRAGMA fullfsync=ON')
        self._effective_configuration()

    def _effective_configuration(self):
        expected={'journal_mode':'wal','synchronous':2,'foreign_keys':1,'trusted_schema':0}
        if sys.platform=='darwin':expected['fullfsync']=1
        actual={key:self._db.execute('PRAGMA '+key).fetchone()[0] for key in expected}
        if actual!=expected:raise StorageUnavailable('Required durable configuration unavailable')
        return actual

    def _check_version(self):
        if self._db.execute('PRAGMA application_id').fetchone()[0]!=APPLICATION_ID or self._db.execute('PRAGMA user_version').fetchone()[0] not in tuple(m.version for m in MIGRATIONS):
            raise StorageIntegrityError('Unsupported or incomplete storage schema')

    def _sync_directory(self):
        # Native namespace publication is already durable; flush the SQLite file.
        sync_file(self._location.database)

    def _insert_epoch(self,index,epoch):
        self._db.execute('INSERT INTO epochs VALUES (?,?,?,?,?,?)',
            (index,epoch.generation,epoch.start_revision,epoch.previous_generation,epoch.contract,datetime.now(timezone.utc).isoformat()))

    def _insert_records(self,records,revision,start):
        for ordinal,record in enumerate(records,start):
            ref=record_ref(record)
            self._db.execute('INSERT INTO records VALUES (?,?,?,?,?,?)',
                (ordinal,ref.record_kind,ref.ref.id,ref.ref.version,revision,_dump(record)))
            self._checkpoint('record_written')

    def _hydrate(self,snapshot,generation):
        self._snapshot=snapshot;self._generation=generation
        self._previews={};self._authorizations={};self._cancelled=set();self._before_publish=None
        self._unavailable=False

    def _verify(self):
        """Reconstruct AND execute original F steps under one database read view."""
        own=not self._db.in_transaction
        if own:self._db.execute('BEGIN')
        try:
            self._check_version();self._effective_configuration()
            if self._db.execute('PRAGMA integrity_check').fetchall()!=[('ok',)] or self._db.execute('PRAGMA foreign_key_check').fetchall():
                raise ValueError('Database integrity failure')
            sql={row[0] for row in self._db.execute("SELECT sql FROM sqlite_master WHERE sql IS NOT NULL")}
            storage_version=self._db.execute('PRAGMA user_version').fetchone()[0]
            if sql!=schema_statements(storage_version):raise ValueError('Unexpected storage schema')
            migrations=self._db.execute('SELECT * FROM migrations ORDER BY version').fetchall()
            if len(migrations)!=storage_version:raise ValueError('Invalid migration ledger')
            for step,row in zip(MIGRATIONS,migrations):
                app=APP_VERSION if step.version==1 else FUTURE_APP_VERSION
                codecs=CORE_CODECS if step.version==1 else FUTURE_CODECS
                if row[:4]!=(step.version,step.name,step.digest,app) or row[5:7]!=(step.version-1,codecs):
                    raise ValueError('Invalid migration ledger')
                when=datetime.fromisoformat(row[4])
                if when.tzinfo is None:raise ValueError('Invalid migration timestamp')
                if step.version==1 and row[7] is not None:raise ValueError('Invalid initial migration')
                if step.version>1 and row[7]!=row[8]:raise ValueError('Migration changed financial head')
            meta=self._db.execute('SELECT * FROM metadata').fetchall()
            if len(meta)!=1:raise ValueError('One authority required')
            _,store_id,activation,context_id,owner,world,head,initial_digest,initial_derived=meta[0]
            if not store_id or not activation:raise ValueError('Missing durable identity')
            context=WorldContext(ContextKind.USER,context_id,owner,world)
            roots=self._db.execute('SELECT revision,ref_id,ref_version,payload FROM roots ORDER BY revision').fetchall()
            if [r[0] for r in roots]!=list(range(head+1)):raise ValueError('Incomplete root sequence')
            worlds=tuple(_load(r[3]) for r in roots)
            if any(w.ref!=Ref(r[1],r[2]) or w.owner_id!=owner or w.world_id!=world for r,w in zip(roots,worlds)):
                raise ValueError('Root identity mismatch')
            rows=self._db.execute('SELECT * FROM records ORDER BY ordinal').fetchall()
            if [r[0] for r in rows]!=list(range(len(rows))):raise ValueError('Incomplete record sequence')
            records=[];introduced={i:[] for i in range(head+1)}
            for ordinal,kind,identity,version,revision,text in rows:
                record=_load(text);ref=record_ref(record)
                if (ref.record_kind,ref.ref.id,ref.ref.version)!=(kind,identity,version):raise ValueError('Record index mismatch')
                records.append(record);introduced[revision].append(record)
            initial=WorldSnapshot(tuple(introduced[0]),(worlds[0],))
            if initial.world.members or len(initial.records)!=2:
                raise ValueError('P1 genesis must be an empty User World, not an imported fixture')
            if payload_digest(initial)!=initial_digest or _derived_digest(initial)!=initial_derived:raise ValueError('Initial digest mismatch')
            if strict_json(migrations[0][8])!=dict(revision=0,snapshot_digest=initial_digest):
                raise ValueError('Migration initial head mismatch')
            raw_epochs=self._db.execute('SELECT * FROM epochs ORDER BY ordinal').fetchall()
            if [e[0] for e in raw_epochs]!=list(range(len(raw_epochs))):raise ValueError('Invalid epoch ordering')
            epochs=tuple(ReplayEpoch(e[1],e[2],worlds[e[2]].ref,e[3],e[4]) for e in raw_epochs)
            commits=self._db.execute('SELECT * FROM commits ORDER BY revision').fetchall()
            if [c[0] for c in commits]!=list(range(1,head+1)):raise ValueError('Incomplete receipt sequence')
            steps=[];evidences={}
            for rev,cid,aid,gen,digest,command,authorization,receipt,evidence,edigest,sdigest,ddigest in commits:
                c,a,r=_load(command),_load(authorization),_load(receipt)
                if c.command_id!=cid or c.authorization_ref.id!=aid or payload_digest(c)!=digest or c.change_set.session_generation!=gen or _hash(evidence)!=edigest:
                    raise ValueError('Commit index mismatch')
                validate_capsule(c,evidence)
                evidences[cid]=evidence
                steps.append(WorldReplayStep(MUTATION_SCHEMA,a,c,r,worlds[rev],tuple(introduced[rev])))
            final_digest=commits[-1][-2] if commits else initial_digest
            final_derived=commits[-1][-1] if commits else initial_derived
            package=WorldReplayHistory(initial,tuple(steps),epochs,final_digest,final_derived)
            snapshot=replay_history(package)
            if tuple(records)!=snapshot.records or snapshot.revision!=head:raise ValueError('Stored record order/head mismatch')
            for c in commits:
                prefix=snapshot_prefix(snapshot,c[0])
                if payload_digest(prefix)!=c[-2] or _derived_digest(prefix)!=c[-1]:raise ValueError('Historical result digest mismatch')
            if hasattr(self,'_store_id') and (store_id,activation)!=(self._store_id,self._activation_id):
                raise ValueError('Durable authority identity changed')
            self._store_id=store_id;self._activation_id=activation;self._context=context
            self._epochs=epochs;self._evidences=evidences
            for row in migrations[1:]:
                basis=strict_json(row[8])
                if type(basis) is not dict or set(basis)!={'revision','snapshot_digest'} or type(basis['revision']) is not int or not 0<=basis['revision']<=head:
                    raise ValueError('Invalid migration financial basis')
                if payload_digest(snapshot_prefix(snapshot,basis['revision']))!=basis['snapshot_digest']:
                    raise ValueError('Migration financial basis mismatch')
            if storage_version>=2:
                from .future_memory import verify_memory
                verify_memory(self,snapshot)
            return snapshot
        except StorageUnavailable:raise
        except (ValueError,TypeError,KeyError,IndexError,AttributeError,Rejected) as error:
            raise StorageIntegrityError('Durable authority verification failed') from None
        except sqlite3.Error:
            raise StorageUnavailable('Durable authority cannot be read') from None
        finally:
            if own and self._db.in_transaction:self._db.execute('ROLLBACK')

    def _check_head(self,before):
        head=self._db.execute('SELECT store_id,activation_id,head_revision FROM metadata WHERE singleton=1').fetchone()
        root=self._db.execute('SELECT payload FROM roots WHERE revision=?',(before.revision,)).fetchone()
        if head!=(self._store_id,self._activation_id,before.revision) or root is None or _load(root[0])!=before.world:
            raise StorageIntegrityError('Durable head differs from serving authority')
        last=self._db.execute('SELECT generation FROM epochs ORDER BY ordinal DESC LIMIT 1').fetchone()
        expected_generation=getattr(self,'_generation',self._epochs[-1].generation)
        if last!=(expected_generation,):raise StorageIntegrityError('Durable epoch changed')

    def _checkpoint(self,stage):
        """No-op test seam. Only test subclasses simulate failures/process death."""

    def _publish(self,candidate):
        before=self._snapshot
        c=candidate.commands[-1];a=candidate.authorizations[-1];r=candidate.receipts[-1]
        evidence=self._execution_evidence
        self._checkpoint('before_transaction')
        self._db.execute('BEGIN IMMEDIATE')
        try:
            self._effective_configuration();self._check_head(before)
            if self._db.execute('SELECT 1 FROM commits WHERE command_id=? OR authorization_id=?',(c.command_id,a.ref.id)).fetchone():
                raise Rejected('Conflicting durable operation identity')
            rev=candidate.revision
            self._db.execute('INSERT INTO roots VALUES (?,?,?,?)',(rev,candidate.world.ref.id,candidate.world.ref.version,_dump(candidate.world)))
            self._insert_records(candidate.records[len(before.records):],rev,len(before.records))
            self._db.execute('INSERT INTO commits VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                (rev,c.command_id,a.ref.id,c.change_set.session_generation,payload_digest(c),_dump(c),_dump(a),_dump(r),
                 evidence,_hash(evidence),payload_digest(candidate),_derived_digest(candidate)))
            self._checkpoint('receipt_written')
            if self._db.execute('UPDATE metadata SET head_revision=? WHERE singleton=1 AND head_revision=?',(rev,before.revision)).rowcount!=1:
                raise StaleVersion('Durable head changed')
            if self._db.execute('PRAGMA foreign_key_check').fetchall():raise StorageIntegrityError('Durable references invalid')
            self._checkpoint('before_commit')
            self._db.execute('COMMIT')
            self._checkpoint('after_commit')
            self._snapshot=candidate
            self._evidences[c.command_id]=evidence
            self._checkpoint('after_publication')
        except BaseException:
            if self._db.in_transaction:self._db.execute('ROLLBACK')
            raise

    def _ready(self):
        if self._closed or self._unavailable:raise StorageUnavailable('Authority requires verified reopen')

    @property
    def snapshot(self):
        with self._lock:
            self._ready()
            return self._snapshot

    @property
    def context(self):return self._context

    @property
    def storage_identity(self):return self._store_id,self._activation_id

    @property
    def configuration(self):
        with self._lock:
            self._ready();return self._effective_configuration()

    def preview(self,*args):
        with self._lock:
            self._ready();return super().preview(*args)

    def authorize(self,*args):
        with self._lock:
            self._ready();return super().authorize(*args)

    def cancel(self,*args):
        with self._lock:
            self._ready();return super().cancel(*args)

    def execute(self,command,evidence=None):
        with self._lock:
            self._ready()
            if type(command) is not FinancialWorldCommand:raise Rejected('FinancialWorldCommand required')
            encoded=capsule(command,evidence)
            # Receipt proof can survive a prior generation; it never executes consent.
            receipt=self.lookup(command.command_id,payload_digest(command))
            if receipt is not None:
                if self._evidences[command.command_id]!=encoded:raise Rejected('Committed evidence identity conflict')
                return receipt
            self._execution_evidence=encoded
            try:
                return super().execute(command)
            except Exception as error:
                # Disk, never RAM identity, proves the outcome near COMMIT.
                try:
                    recovered=self._verify()
                    self._snapshot=recovered
                    found=next((r for r in recovered.receipts if r.command_id==command.command_id),None)
                    if found is not None:
                        if found.command_digest!=payload_digest(command):raise StorageIntegrityError('Receipt mismatch')
                        return found
                except Exception:
                    self._unavailable=True
                    raise StorageUnavailable('Commit outcome cannot be verified; reopen required') from None
                if isinstance(error,sqlite3.OperationalError):raise StorageBusy('Durable write unavailable; no commit found') from None
                if isinstance(error,sqlite3.Error):raise StorageIntegrityError('Durable transaction rejected') from None
                raise
            finally:self._execution_evidence=None

    def _verified_current(self):
        try:
            snapshot = self._verify()
            self._check_head(self._snapshot)
            if snapshot != self._snapshot:
                raise StorageIntegrityError('Authority changed outside the writer')
            return snapshot
        except (StorageUnavailable, sqlite3.Error):
            self._unavailable = True
            raise StorageUnavailable('Authority requires verified reopen') from None

    def lookup(self, command_id, command_digest=None):
        """Receipt proof in this verified store/activation; None is not global proof."""
        with self._lock:
            self._ready()
            snapshot = self._verified_current()
            receipt = next((r for r in snapshot.receipts if r.command_id == command_id), None)
            if receipt is not None and command_digest is not None and receipt.command_digest != command_digest:
                raise Rejected('Command ID reused with different content')
            return receipt

    def operation(self, authorization_id):
        with self._lock:
            self._ready()
            snapshot = self._verified_current()
            for command, receipt in zip(snapshot.commands, snapshot.receipts):
                if receipt.authorization_ref.id == authorization_id:
                    return {'command': command, 'receipt': receipt,
                            'evidence': strict_json(self._evidences[command.command_id]),
                            'store_id': self._store_id, 'activation_id': self._activation_id}
            return None

    def export_replay(self):
        with self._lock:
            self._ready()
            snapshot = self._verified_current()
            return export_history_replay(snapshot, self._epochs)

    def close(self):
        with self._lock:
            try:
                if self._db is not None:self._db.close()
            finally:
                self._db=None;self._closed=True
                if self._lock_fd is not None:
                    release_lock(self._lock_fd);self._lock_fd=None

    def __enter__(self):return self
    def __exit__(self,*_):self.close()


def competition_world_guard(location, *, initializing=False):
    """External sentinel prevents missing initialized storage masquerading as first use."""
    base = location.directory.parent.parent
    _private(base, True)
    _private(location.directory.parent, True)
    marker = base/'world-required'
    if marker.exists() or marker.is_symlink():
        _private(marker)
        if marker.read_text() != 'competition-world-v1\n':
            raise StorageIntegrityError('Invalid competition initialization marker')
        if not location.database.is_file() or not (location.directory.parent/'user-world-selection.json').is_file():
            raise StorageIntegrityError('Initialized competition state is missing')
    elif initializing:
        durable_write(marker,b'competition-world-v1\n')

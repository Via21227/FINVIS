"""SQLite v1 financial authority and v2 isolated Future Memory contract."""
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
import sqlite3
from .platform_storage import durable_empty_file, sync_file

VERSION = 2
APPLICATION_ID = 0x46495631
APP_VERSION = 'finvis.persistence.p1.v0.1'


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    statements: tuple

    @property
    def digest(self):
        return sha256(json.dumps((self.version,self.name,self.statements),separators=(',',':')).encode()).hexdigest()


V1 = Migration(1, 'initial-durable-authority', (
    'CREATE TABLE migrations (version INTEGER PRIMARY KEY CHECK(version>0), name TEXT NOT NULL, digest TEXT NOT NULL, application TEXT NOT NULL, recorded_at TEXT NOT NULL, from_version INTEGER NOT NULL, codecs TEXT NOT NULL, before_head TEXT, after_head TEXT NOT NULL)',
    'CREATE TABLE roots (revision INTEGER PRIMARY KEY CHECK(revision>=0), ref_id TEXT NOT NULL, ref_version INTEGER NOT NULL CHECK(ref_version>0), payload TEXT NOT NULL, UNIQUE(ref_id,ref_version))',
    'CREATE TABLE epochs (ordinal INTEGER PRIMARY KEY CHECK(ordinal>=0), generation TEXT NOT NULL UNIQUE, start_revision INTEGER NOT NULL REFERENCES roots(revision), predecessor TEXT REFERENCES epochs(generation), contract TEXT NOT NULL, recorded_at TEXT NOT NULL)',
    'CREATE TABLE records (ordinal INTEGER PRIMARY KEY CHECK(ordinal>=0), kind TEXT NOT NULL, identity TEXT NOT NULL, version INTEGER NOT NULL CHECK(version>0), introduced_revision INTEGER NOT NULL REFERENCES roots(revision), payload TEXT NOT NULL, UNIQUE(kind,identity,version))',
    'CREATE TABLE commits (revision INTEGER PRIMARY KEY REFERENCES roots(revision) CHECK(revision>0), command_id TEXT NOT NULL UNIQUE, authorization_id TEXT NOT NULL UNIQUE, generation TEXT NOT NULL REFERENCES epochs(generation), command_digest TEXT NOT NULL, command TEXT NOT NULL, authorization TEXT NOT NULL, receipt TEXT NOT NULL, evidence TEXT NOT NULL, evidence_digest TEXT NOT NULL, snapshot_digest TEXT NOT NULL, derived_digest TEXT NOT NULL)',
    'CREATE TABLE metadata (singleton INTEGER PRIMARY KEY CHECK(singleton=1), store_id TEXT NOT NULL UNIQUE, activation_id TEXT NOT NULL, context_id TEXT NOT NULL, owner_id TEXT NOT NULL, world_id TEXT NOT NULL, head_revision INTEGER NOT NULL REFERENCES roots(revision), initial_digest TEXT NOT NULL, initial_derived_digest TEXT NOT NULL)',
    'CREATE INDEX records_revision ON records(introduced_revision,ordinal)',
    # No DELETE or UPDATE of committed history through ordinary storage SQL.
    *(f'CREATE TRIGGER immutable_{table}_{action.lower()} BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT, \'immutable financial evidence\'); END'
      for table in ('roots','records','commits','epochs','migrations') for action in ('UPDATE','DELETE')),
))
FUTURE_APP_VERSION = 'finvis.persistence.fs1.v1'
CORE_CODECS = 'mutation.v0.1;settlement.v0.1;history-action.v0.1;replay.v0.2'
FUTURE_CODECS = CORE_CODECS + ';future-space.v1;future-ack.v1'
EMPTY_FUTURE_DIGEST = sha256(b'finvis.future-memory.v1:empty').hexdigest()
V2 = Migration(2, 'durable-future-memory', (
    'CREATE TABLE future_operations (revision INTEGER PRIMARY KEY CHECK(revision>0), operation_id TEXT NOT NULL UNIQUE, payload TEXT NOT NULL, acknowledgement TEXT NOT NULL)',
    'CREATE TABLE future_versions (kind TEXT NOT NULL, identity TEXT NOT NULL, version INTEGER NOT NULL CHECK(version>0), introduced_revision INTEGER NOT NULL REFERENCES future_operations(revision), payload TEXT NOT NULL, digest TEXT NOT NULL, PRIMARY KEY(kind,identity,version))',
    'CREATE TABLE future_head (singleton INTEGER PRIMARY KEY CHECK(singleton=1), revision INTEGER NOT NULL CHECK(revision>=0), digest TEXT NOT NULL)',
    'CREATE INDEX future_versions_revision ON future_versions(introduced_revision)',
    *(f"CREATE TRIGGER immutable_{table}_{action.lower()} BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT, 'immutable future memory'); END"
      for table in ('future_versions','future_operations') for action in ('UPDATE','DELETE')),
    "INSERT INTO future_head VALUES (1,0,'" + EMPTY_FUTURE_DIGEST + "')",
))
MIGRATIONS = (V1, V2)


def schema_statements(version):
    return {sql for migration in MIGRATIONS if migration.version <= version
            for sql in migration.statements if sql.startswith('CREATE ')}



def safety_backup(connection, path):
    """Internal upgrade prerequisite only, no restore or backup UI."""
    durable_empty_file(path)
    target=sqlite3.connect(str(path),isolation_level=None)
    try:
        connection.backup(target)
        if target.execute('PRAGMA integrity_check').fetchall()!=[('ok',)] or target.execute('PRAGMA foreign_key_check').fetchall():
            raise ValueError('Migration backup failed validation')
    finally:
        target.close()
    sync_file(path)


def financial_signature(connection):
    """Storage upgrade guard: original authority payloads must remain identical."""
    tables = ('metadata', 'roots', 'records', 'epochs', 'commits')
    content = [connection.execute('SELECT * FROM '+name+' ORDER BY 1').fetchall() for name in tables]
    return sha256(json.dumps(content,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()


def financial_head(connection):
    row=connection.execute('SELECT head_revision,initial_digest FROM metadata WHERE singleton=1').fetchone()
    if row is None: raise ValueError('Migration requires complete financial authority')
    revision,digest=row
    if revision:
        digest=connection.execute('SELECT snapshot_digest FROM commits WHERE revision=?',(revision,)).fetchone()[0]
    return json.dumps(dict(revision=revision,snapshot_digest=digest),sort_keys=True,separators=(',',':'))


def migrate(connection, target=VERSION, migrations=MIGRATIONS, validate=None,
            backup_path=None, checkpoint=None):
    """Deterministic registered DDL + version ledger, one transaction.

    validate runs before commit and may populate the brand-new baseline.
    Future upgrades must supply a safety backup and domain-equivalence validator.
    Test-only migration registrations never enter MIGRATIONS.
    """
    checkpoint = checkpoint or (lambda stage: None)
    current=connection.execute('PRAGMA user_version').fetchone()[0]
    if type(target) is not int or target<current or target<1:
        raise ValueError('Unsupported storage migration target')
    if connection.in_transaction: raise ValueError('Migration requires its own transaction')
    plan=[m for m in migrations if current<m.version<=target]
    if [m.version for m in plan]!=list(range(current+1,target+1)):
        raise ValueError('Unsupported migration path')
    if not plan: return
    if validate is None or not callable(validate): raise ValueError('Domain-equivalence validation required')
    if current:
        if backup_path is None: raise ValueError('Migration safety backup required')
        safety_backup(connection,backup_path)
        checkpoint('migration_after_backup')
    connection.execute('BEGIN IMMEDIATE')
    try:
        if connection.execute('PRAGMA user_version').fetchone()[0]!=current:
            raise ValueError('Migration basis changed')
        before_signature=financial_signature(connection) if current else None
        before_head=financial_head(connection) if current else None
        checkpoint('migration_before_ddl')
        for step in plan:
            for statement in step.statements: connection.execute(statement)
        checkpoint('migration_after_ddl')
        connection.execute('PRAGMA user_version='+str(target))
        connection.execute('PRAGMA application_id='+str(APPLICATION_ID))
        validate()
        if current and financial_signature(connection)!=before_signature:
            raise ValueError('Storage migration changed financial history')
        after_head=financial_head(connection)
        for step in plan:
            connection.execute('INSERT INTO migrations VALUES (?,?,?,?,?,?,?,?,?)',
                (step.version,step.name,step.digest,APP_VERSION if step.version==1 else FUTURE_APP_VERSION,datetime.now(timezone.utc).isoformat(),
                 step.version-1,CORE_CODECS if step.version==1 else FUTURE_CODECS,
                 before_head if step.version==1 else before_head or after_head,after_head))
        if connection.execute('PRAGMA foreign_key_check').fetchall(): raise ValueError('Migration reference failure')
        checkpoint('migration_before_commit')
        connection.execute('COMMIT')
        checkpoint('migration_after_commit')
    except BaseException:
        if connection.in_transaction: connection.execute('ROLLBACK')
        raise

"""P1 only: isolated synthetic databases; never opens the production location.

Temporary fixtures are intentionally retained (no automatic file deletion).
Subprocess workers use this same deterministic fixture and no provider/network.
"""
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from finvis.domain import Currency, Money, Ref
from finvis.store import Rejected
from finvis.world_application import WorldContext, ContextKind, UserWorldAuthority
from finvis.persistent_world import (PersistentWorldStore, StorageLocation, StorageUnavailable,
    StorageBusy, StorageIntegrityError, production_directory, _dump)
from finvis.persistence_schema import Migration, migrate, V1, VERSION, MIGRATIONS, financial_signature
from finvis.persistence_evidence import CommitEvidence, strict_json, canonical
from finvis.world_commands import (CandidateChangeSet, ChangeMode, FinancialWorldCommand,
    PutRecord, RetireRecord, ReplacementScope, HistoryAction, record_ref)
from finvis.world_history_replay import (export_history_replay, replay_history,
    dumps_history_replay, loads_history_replay, snapshot_prefix)
from finvis.world_replay import export_world_replay, replay_world, dumps_world_replay, loads_world_replay
from finvis.world_serialization import payload_digest
from finvis.world_values import TypedRef, AmountEstimate, ObservationRole
from finvis.world_planning import WorldDecision, ActualOccurrence, OccurrenceKind, SettlementStatus
from finvis.world_settlement import SettleOccurrence, BalanceCoverage, BalanceBasis, propose_settlement
from finvis.history_actions import proposal as history_proposal
from finvis.world_history import history_view
from finvis.world_store import WorldStore, validate_world
from tests.test_world_application import synthetic
from tests.test_world_assets import observation, NOW

CONTEXT=WorldContext(ContextKind.USER,'user:workshop','sample-workshop-owner','sample-workshop-world')


def location():
    return StorageLocation.temporary(Path(tempfile.mkdtemp(prefix='finvis-p1-')).resolve())


def changes(store, ops, name='seed', scope=None):
    snap=store.snapshot
    touched={(record_ref(op.record) if type(op) is PutRecord else op.expected_ref).record_kind + '/' +
             (record_ref(op.record) if type(op) is PutRecord else op.expected_ref).ref.id for op in ops}
    retained=tuple(ref for ref in snap.world.members if ref.record_kind+'/'+ref.ref.id not in touched)
    at=NOW+timedelta(minutes=snap.revision+1)
    return CandidateChangeSet(Ref('change/'+name,1),snap.world.owner_id,snap.world.world_id,
        store.session_generation,snap.world.ref,snap.world.state_ref,snap.world.plan_ref,snap.revision,
        ChangeMode.REPLACE_STATE if scope else ChangeMode.MERGE,tuple(ops),scope,retained,
        (Ref('reported/'+name,1),),replace(observation(name),recorded_at=at),
        'Reported workshop subset only' if any((record_ref(op.record) if type(op) is PutRecord else op.expected_ref).record_kind in ('account','asset_position','earmark') for op in ops) else None,(),at)


def prepare(store, change, name='seed'):
    owner=store.snapshot.world.owner_id
    preview=store.preview(owner,change,Ref('preview/'+name,1))
    grant=store.authorize(owner,preview,Ref('authorization/'+name,1),change.created_at)
    return FinancialWorldCommand('command/'+name,owner,change,preview.ref,preview.digest,grant.ref,change.created_at)


def seed(store, currencies=(Currency.CNY,Currency.SGD)):
    fixture=synthetic(currencies).snapshot
    next_state=TypedRef('world_state',Ref(store.snapshot.world.state_ref.id,store.snapshot.world.state_ref.version+1))
    records=[fixture.get(r) for r in fixture.world.members]
    records=[replace(r,source_state_ref=next_state) if type(r) is WorldDecision else r for r in records]
    return prepare(store,changes(store,[PutRecord('add/'+r.ref.id,r) for r in records]))


def add_future(store, name='later'):
    record=next(synthetic().snapshot.get(r) for r in synthetic().snapshot.world.members if r.record_kind=='future_inflow')
    record=replace(record,ref=Ref('future/'+name,1))
    return prepare(store,changes(store,[PutRecord('add',record)],name),name)


def live(store,kind,index=0):
    return [store.snapshot.get(r) for r in store.snapshot.world.members if r.record_kind==kind][index]


def history(store,action,revision=None,kind=None,field=None,value=None,name='history'):
    snap=store.snapshot
    source=snapshot_prefix(snap,snap.revision if revision is None else revision)
    intent=HistoryAction('history',action,source.revision,source.world.ref,payload_digest(source),
        record_ref(live(store,kind)) if kind else None,field,value,'Reviewed workshop correction')
    proposal=history_proposal(snap,store.session_generation,intent,NOW+timedelta(minutes=snap.revision+1),'change/'+name)
    return prepare(store,proposal,name)


def settlement(store, correction=False):
    pos=live(store,'asset_position')
    old=next((store.snapshot.get(r) for r in store.snapshot.world.members if r.ref.id=='actual/workshop-payment'),None)
    meta=replace(observation('payment',ObservationRole.ACTUAL),recorded_at=NOW+timedelta(minutes=store.snapshot.revision+1))
    amount=AmountEstimate.exact_reported(Money(1700 if correction else 2300,pos.currency))
    report=ActualOccurrence(Ref('actual/workshop-payment',2 if correction else 1),CONTEXT.owner_id,CONTEXT.world_id,
        OccurrenceKind.EXPENSE,amount,meta.as_of,(),SettlementStatus.UNALLOCATED,meta,record_ref(pos))
    if correction:
        report=replace(report,observation=replace(meta,corrects_ref=old.ref,correction_reason='Receipt amount corrected'))
    coverage=BalanceCoverage(record_ref(report),record_ref(pos),BalanceBasis.PRIOR,report.observation,'Explicit prior balance')
    op=SettleOccurrence('payment',report,record_ref(old) if correction else None,coverage,None,None,
                        old.balance_effect_ref if correction else None)
    return prepare(store,propose_settlement(store.snapshot,store.session_generation,op,Ref('change/payment'+str(correction),1)),
                   'payment'+str(correction))


class FaultStore(PersistentWorldStore):
    fault=None
    seen=0
    def _checkpoint(self,stage):
        if stage==self.fault:
            self.seen+=1
            if stage!='record_written' or self.seen==3:
                raise RuntimeError('injected '+stage)


class P1Tests(unittest.TestCase):
    def new(self,cls=PersistentWorldStore,context=CONTEXT):
        loc=location();store=cls.create(context,observation('explicit-empty'),loc)
        self.addCleanup(store.close)
        return store,loc

    def reopen(self,store,loc,cls=PersistentWorldStore):
        store.close();other=cls.open(loc);self.addCleanup(other.close);return other

    def assertRoundTrip(self,store,loc):
        before=store.snapshot
        package=loads_history_replay(dumps_history_replay(store.export_replay()))
        self.assertEqual(replay_history(package),before)
        other=self.reopen(store,loc)
        self.assertEqual(other.snapshot,before)
        return other

    def test_empty_baseline_and_effective_configuration(self):
        s,loc=self.new()
        self.assertEqual(s.snapshot.revision,0);self.assertEqual(s.snapshot.world.members,())
        self.assertEqual(len(s.snapshot.records),2)
        self.assertEqual(s.configuration['journal_mode'],'wal')
        self.assertEqual(s.configuration['synchronous'],2)
        self.assertEqual(s.configuration['foreign_keys'],1)
        if sys.platform=='darwin':self.assertEqual(s.configuration['fullfsync'],1)
        self.assertRoundTrip(s,loc)

    def test_local_path_permissions_and_separate_security_domain(self):
        s,loc=self.new()
        self.assertEqual(production_directory(),Path.home()/'Library/Application Support/FINVIS/UserWorld')
        self.assertNotIn('CloudDocs',str(StorageLocation.production().directory))
        self.assertEqual(loc.directory.stat().st_mode&0o777,0o700)
        for p in loc.directory.iterdir():self.assertEqual(p.stat().st_mode&0o077,0)
        tables={row[0] for row in s._db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertEqual(tables,{'roots','records','commits','metadata','epochs','migrations','future_versions','future_operations','future_head'})

    def test_location_rejects_source_override_and_temp_root(self):
        # The checkout itself can legitimately be staged below /private/tmp.
        for path in (Path.home(),Path('/private/tmp')):
            with self.assertRaises(ValueError):StorageLocation.temporary(path)
        with self.assertRaises(ValueError):StorageLocation(location().directory)

    def test_no_overwrite_or_implicit_create_on_open(self):
        s,loc=self.new();before=s.snapshot;s.close()
        with self.assertRaises(StorageUnavailable):PersistentWorldStore.create(CONTEXT,observation('x'),loc)
        missing=location()
        with self.assertRaises(StorageUnavailable):PersistentWorldStore.open(missing)
        self.assertFalse(missing.database.exists())
        other=PersistentWorldStore.open(loc);self.addCleanup(other.close);self.assertEqual(other.snapshot,before)

    def test_zero_length_and_foreign_database_fail_closed(self):
        for foreign in (False,True):
            loc=location()
            fd=os.open(loc.database,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600);os.close(fd)
            (loc.directory/'authority.lock').touch(mode=0o600)
            if foreign:
                db=sqlite3.connect(loc.database);db.execute('CREATE TABLE unrelated (value TEXT)');db.close()
            with self.assertRaises(StorageUnavailable):PersistentWorldStore.open(loc)

    def test_unsafe_permissions_rejected_without_chmod(self):
        s,loc=self.new();s.close();loc.database.chmod(0o644)
        with self.assertRaises(StorageUnavailable):PersistentWorldStore.open(loc)
        self.assertEqual(loc.database.stat().st_mode&0o777,0o644)

    def test_symlinked_database_rejected(self):
        s,loc=self.new();s.close();other=location()
        other.database.symlink_to(loc.database)
        with self.assertRaises(StorageUnavailable):PersistentWorldStore.open(other)

    def test_host_lock_rejects_second_writer(self):
        s,loc=self.new()
        with self.assertRaises(StorageBusy):PersistentWorldStore.open(loc)
        self.assertEqual(s.snapshot.revision,0)

    def test_generic_currency_worlds_survive_roundtrip(self):
        for currencies in ((Currency.CNY,),(Currency.SGD,),(Currency.CNY,Currency.SGD)):
            with self.subTest(currencies=currencies):
                s,loc=self.new();cmd=seed(s,currencies);receipt=s.execute(cmd)
                self.assertEqual(receipt.after_world_ref.version,2)
                other=self.assertRoundTrip(s,loc)
                self.assertEqual({live(other,'asset_position',i).currency for i in range(2*len(currencies))},set(currencies))
                kinds={r.record_kind for r in other.snapshot.world.members}
                self.assertTrue({'account','asset_position','earmark','future_inflow','planned_expense','world_decision','decision_context','actual_occurrence'}<=kinds)

    def test_duplicate_retry_across_restart_returns_original_receipt(self):
        s,loc=self.new();cmd=seed(s);receipt=s.execute(cmd);before=s.snapshot
        self.assertEqual(s.execute(cmd),receipt)
        other=self.reopen(s,loc)
        self.assertEqual(other.lookup(cmd.command_id,payload_digest(cmd)),receipt)
        self.assertEqual(other.execute(cmd),receipt);self.assertEqual(other.snapshot,before)
        self.assertEqual(other.operation(cmd.authorization_ref.id)['command'],cmd)
        self.assertEqual(other.operation(cmd.authorization_ref.id)['receipt'],receipt)

    def test_conflicting_idempotency_rejected(self):
        s,loc=self.new();cmd=seed(s);s.execute(cmd);s=self.reopen(s,loc);before=s.snapshot
        conflict=replace(cmd,recorded_at=cmd.recorded_at+timedelta(seconds=1))
        with self.assertRaises(Rejected):s.execute(conflict)
        self.assertEqual(s.snapshot,before)
        with self.assertRaises(Rejected):s.lookup(cmd.command_id,'0'*64)

    def test_duplicate_consumed_grant_cannot_create_second_command(self):
        s,loc=self.new();cmd=seed(s);s.execute(cmd);s=self.reopen(s,loc)
        with self.assertRaises(Rejected):s.execute(replace(cmd,command_id='different'))
        self.assertEqual(s.snapshot.revision,1)

    def test_pending_grant_is_absent_and_cannot_survive_restart(self):
        s,loc=self.new();cmd=seed(s);generation=s.session_generation
        self.assertEqual(s._db.execute('SELECT count(*) FROM commits').fetchone()[0],0)
        s=self.reopen(s,loc)
        self.assertNotEqual(s.session_generation,generation);self.assertFalse(s._authorizations);self.assertFalse(s._previews)
        with self.assertRaises(Rejected):s.execute(cmd)
        self.assertIsNone(s.lookup(cmd.command_id));self.assertIsNone(s.operation(cmd.authorization_ref.id))
        self.assertEqual(s.snapshot.revision,0)

    def test_validation_failure_before_sql_transaction(self):
        s,loc=self.new();cmd=seed(s);before=s.snapshot
        statements=[];s._db.set_trace_callback(statements.append)
        with self.assertRaises(Rejected):s.execute(replace(cmd,actor='wrong-owner'))
        self.assertFalse(any('BEGIN IMMEDIATE' in t for t in statements));self.assertEqual(s.snapshot,before)

    def test_failure_before_transaction_rolls_back(self):self.failure('before_transaction')
    def test_failure_mid_multi_record_write_rolls_back(self):self.failure('record_written')
    def test_failure_after_receipt_insert_rolls_back(self):self.failure('receipt_written')
    def test_failure_immediately_before_commit_rolls_back(self):self.failure('before_commit')

    def failure(self,stage):
        s,loc=self.new(FaultStore);cmd=seed(s);before=s.snapshot;s.fault=stage
        with self.assertRaises(RuntimeError):s.execute(cmd)
        self.assertEqual(s.snapshot,before)
        self.assertEqual(s._db.execute('SELECT count(*) FROM commits').fetchone()[0],0)
        self.assertEqual(s._db.execute('SELECT count(*) FROM roots').fetchone()[0],1)
        self.assertEqual(s._db.execute('SELECT count(*) FROM records').fetchone()[0],2)
        s=self.reopen(s,loc);self.assertEqual(s.snapshot,before)

    def test_failure_after_commit_recovers_from_disk(self):self.after_commit('after_commit')
    def test_failure_after_memory_publication_recovers_from_disk(self):self.after_commit('after_publication')

    def after_commit(self,stage):
        s,loc=self.new(FaultStore);cmd=seed(s);s.fault=stage
        receipt=s.execute(cmd)
        self.assertEqual(receipt.after_world_ref.version,2);self.assertEqual(s.snapshot.revision,1)
        s=self.reopen(s,loc);self.assertEqual(s.execute(cmd),receipt)

    def test_unknown_commit_outcome_freezes_until_verified_reopen(self):
        s,loc=self.new(FaultStore);cmd=seed(s);s.fault='after_commit';original=s._verify;calls=[]
        def verify():
            calls.append(1)
            if len(calls)>1:raise StorageUnavailable('injected unreadable outcome')
            return original()
        with patch.object(s,'_verify',side_effect=verify):
            with self.assertRaises(StorageUnavailable):s.execute(cmd)
        with self.assertRaises(StorageUnavailable):_ = s.snapshot
        s=self.reopen(s,loc);self.assertEqual(s.lookup(cmd.command_id).after_world_ref.version,2)

    def test_locked_database_keeps_complete_prior_authority(self):
        s,loc=self.new();cmd=seed(s);before=s.snapshot
        external=sqlite3.connect(loc.database,isolation_level=None);external.execute('BEGIN IMMEDIATE')
        try:
            with self.assertRaises(StorageBusy):s.execute(cmd)
        finally:external.execute('ROLLBACK');external.close()
        self.assertEqual(s.snapshot,before);self.assertEqual(s.execute(cmd).after_world_ref.version,2)

    def test_disk_write_error_rolls_back(self):
        s,loc=self.new();cmd=seed(s)
        with patch.object(s,'_checkpoint',side_effect=lambda stage: (_ for _ in ()).throw(sqlite3.OperationalError('disk full')) if stage=='receipt_written' else None):
            with self.assertRaises(StorageUnavailable):s.execute(cmd)
        self.assertEqual(s.snapshot.revision,0);self.assertEqual(s.execute(cmd).after_world_ref.version,2)

    def test_concurrent_identical_calls_only_commit_once(self):
        s,loc=self.new();cmd=seed(s)
        with ThreadPoolExecutor(max_workers=4) as pool:receipts=list(pool.map(lambda _:s.execute(cmd),range(4)))
        self.assertTrue(all(r==receipts[0] for r in receipts));self.assertEqual(s.snapshot.revision,1)

    def test_unique_and_append_only_constraints(self):
        s,loc=self.new();s.execute(seed(s));before=s.snapshot
        for sql in ("UPDATE roots SET ref_version=8", "DELETE FROM records", "INSERT INTO commits SELECT * FROM commits"):
            with self.assertRaises(sqlite3.IntegrityError):s._db.execute(sql)
        self.assertEqual(s.lookup('command/seed').after_world_ref.version,2);self.assertEqual(s.snapshot,before)

    def test_corrupt_root_metadata_fails_closed(self):
        s,loc=self.new();s.execute(seed(s));s._db.execute('UPDATE metadata SET initial_digest=?',('0'*64,));s.close()
        with self.assertRaises(StorageIntegrityError):PersistentWorldStore.open(loc)

    def test_corrupt_schema_and_receipt_evidence_fail_closed(self):
        for column,value in (('receipt','{}'),('evidence','{}'),('command_digest','0'*64),('snapshot_digest','0'*64)):
            s,loc=self.new();s.execute(seed(s))
            s._db.execute('DROP TRIGGER immutable_commits_update')
            s._db.execute('UPDATE commits SET '+column+'=?',(value,))
            s._db.execute(next(sql for sql in V1.statements if sql.startswith('CREATE TRIGGER immutable_commits_update ')))
            s.close()
            with self.assertRaises(StorageIntegrityError):PersistentWorldStore.open(loc)

    def test_corrupt_epoch_fails_closed(self):
        s,loc=self.new();s.execute(seed(s));s._db.execute('DROP TRIGGER immutable_epochs_update')
        s._db.execute("UPDATE epochs SET contract='unsupported'")
        s._db.execute(next(sql for sql in V1.statements if sql.startswith('CREATE TRIGGER immutable_epochs_update ')));s.close()
        with self.assertRaises(StorageIntegrityError):PersistentWorldStore.open(loc)

    def test_future_schema_fails_without_downgrade(self):
        s,loc=self.new();s._db.execute('PRAGMA user_version=99');s.close()
        with self.assertRaises(StorageIntegrityError):PersistentWorldStore.open(loc)
        db=sqlite3.connect(loc.database);self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],99);db.close()

    def test_configuration_drift_fails_before_any_write(self):
        s,loc=self.new();cmd=seed(s);s._db.execute('PRAGMA synchronous=OFF')
        with self.assertRaises(StorageUnavailable):s.execute(cmd)
        s=self.reopen(s,loc);self.assertEqual(s.snapshot.revision,0)

    def test_migration_audit_and_failed_upgrade_preserve_authority(self):
        s,loc=self.new();s.execute(seed(s));before=financial_signature(s._db)
        row=s._db.execute('SELECT * FROM migrations').fetchone();self.assertEqual(row[5],0)
        self.assertEqual(strict_json(row[-1])['revision'],0)
        for index,ddl in enumerate((('CREATE TABLE test_extension (a TEXT)', 'INVALID SQL'),('CREATE TABLE test_extension (a TEXT)',))):
            def fail():raise ValueError('injected validator failure')
            with self.assertRaises((ValueError,sqlite3.Error)):
                migrate(s._db,VERSION+1,(*MIGRATIONS,Migration(VERSION+1,'test-only',ddl)),validate=fail,backup_path=loc.directory/('failed-upgrade-'+str(index)+'.sqlite3'))
            self.assertEqual(financial_signature(s._db),before)
            self.assertEqual(s._db.execute('PRAGMA user_version').fetchone()[0],VERSION)
            self.assertFalse(s._db.execute("SELECT 1 FROM sqlite_master WHERE name='test_extension'").fetchall())
            backup=sqlite3.connect(loc.directory/('failed-upgrade-'+str(index)+'.sqlite3'))
            self.assertEqual(financial_signature(backup),before);backup.close()
        self.assertRoundTrip(s,loc)

    def test_successful_test_only_storage_upgrade_preserves_payloads(self):
        s,loc=self.new();s.execute(seed(s));before=financial_signature(s._db)
        migrate(s._db,VERSION+1,(*MIGRATIONS,Migration(VERSION+1,'test-only',('CREATE TABLE auxiliary (value TEXT)',))),validate=lambda:None,backup_path=loc.directory/'upgrade.sqlite3')
        self.assertEqual(financial_signature(s._db),before)
        ledger=s._db.execute('SELECT * FROM migrations WHERE version='+str(VERSION+1)).fetchone()
        self.assertEqual(ledger[-2],ledger[-1]);self.assertEqual(ledger[5],VERSION)
        s.close()
        with self.assertRaises(StorageIntegrityError):PersistentWorldStore.open(loc)

    def test_migration_cannot_change_financial_payload(self):
        s,loc=self.new();before=financial_signature(s._db)
        wrong=Migration(VERSION+1,'test-mutation',("UPDATE metadata SET initial_digest='corrupted'",))
        with self.assertRaises(ValueError):migrate(s._db,VERSION+1,(*MIGRATIONS,wrong),validate=lambda:None,backup_path=loc.directory/'protected.sqlite3')
        self.assertEqual(financial_signature(s._db),before)

    def test_upgrade_requires_registered_path_backup_and_validator(self):
        s,loc=self.new()
        for kwargs in ({'target':VERSION+1},{'target':0},{'target':VERSION+1,'migrations':(*MIGRATIONS,Migration(VERSION+1,'test',())),'validate':lambda:None}):
            with self.assertRaises(ValueError):migrate(s._db,**kwargs)
        self.assertEqual(s.snapshot.revision,0)

    def test_cross_epoch_history_edits_restore_and_multiple_restarts(self):
        s,loc=self.new();s.execute(seed(s));source=s.snapshot
        s.execute(history(s,'edit',kind='account',field='label',value='Renamed studio',name='edit'))
        first=s.snapshot;s=self.reopen(s,loc)
        s.execute(add_future(s));s=self.reopen(s,loc)
        s.execute(history(s,'restore',revision=1,name='restore'))
        final=s.snapshot;self.assertEqual(final.revision,4)
        self.assertEqual(final.records[:len(first.records)],first.records)
        self.assertEqual(payload_digest(snapshot_prefix(final,1)),payload_digest(source))
        self.assertEqual(final.commands[-1].change_set.operations[0].source_digest,payload_digest(source))
        s=self.assertRoundTrip(s,loc)
        read=UserWorldAuthority(CONTEXT,s).read()
        for revision in range(5):
            view=history_view(read,revision,replay_package=s.export_replay())
            self.assertEqual(view['detail']['revision'],revision);self.assertEqual(view['retention'],'durable_local')
        with self.assertRaises(Rejected):export_world_replay(s.snapshot,s.session_generation)

    def test_v01_replay_bytes_remain_exact_and_prefix_digest_unchanged(self):
        s,loc=self.new();s.execute(seed(s));package=export_world_replay(s.snapshot,s.session_generation)
        encoded=dumps_world_replay(package)
        self.assertEqual(dumps_world_replay(loads_world_replay(encoded)),encoded)
        self.assertEqual(replay_world(package),s.snapshot)
        self.assertEqual(snapshot_prefix(s.snapshot,0),package.initial_snapshot)
        self.assertEqual(dumps_history_replay(loads_history_replay(dumps_history_replay(s.export_replay()))),dumps_history_replay(s.export_replay()))

    def test_empty_epochs_do_not_rewrite_history(self):
        s,loc=self.new();s=self.reopen(s,loc);s=self.reopen(s,loc);s.execute(seed(s));s=self.reopen(s,loc)
        self.assertEqual(len(s.export_replay().epochs),4);self.assertRoundTrip(s,loc)

    def test_invalid_epoch_or_codec_rejected(self):
        s,loc=self.new();s.execute(seed(s));data=json.loads(dumps_history_replay(s.export_replay()))
        for mutation in ('future','epoch','extra','duplicate'):
            damaged=json.loads(json.dumps(data))
            if mutation=='future':damaged['schema_version']='finvis.world-replay.v9'
            if mutation=='epoch':damaged['epochs'][0]['generation']='wrong'
            if mutation=='extra':damaged['unexpected']=1
            if mutation=='duplicate':damaged['epochs']*=2
            with self.assertRaises((Rejected,ValueError)):replay_history(loads_history_replay(json.dumps(damaged)))

    def test_settlement_correction_lineage_survives_sessions(self):
        s,loc=self.new();s.execute(seed(s));s.execute(settlement(s));before=s.snapshot
        s=self.reopen(s,loc);s.execute(settlement(s,True));s=self.assertRoundTrip(s,loc)
        self.assertEqual(s.snapshot.records[:len(before.records)],before.records)
        effects=[r for r in s.snapshot.records if record_ref(r).record_kind=='balance_effect']
        self.assertEqual(len(effects),2);self.assertIsNotNone(effects[-1].previous_effect_ref)
        validate_world(s.snapshot)

    def test_replacement_retains_history(self):
        s,loc=self.new();s.execute(seed(s));before=s.snapshot
        account=live(s,'account',1)
        # Replace a separate account whose reported position has no occurrence linkage.
        account=replace(account,ref=Ref('replacement/container',1),label='New workshop jar')
        original=live(s,'account',0);pos=live(s,'asset_position',0)
        marks=[s.snapshot.get(r) for r in s.snapshot.world.members if r.record_kind=='earmark' and s.snapshot.get(r).position_ref==record_ref(pos)]
        scope=ReplacementScope((record_ref(original),),(pos.currency,),(record_ref(pos),),tuple(record_ref(r) for r in marks))
        newpos=replace(pos,ref=Ref('replacement/position',1),account_ref=record_ref(account))
        ops=[RetireRecord('retire/'+r.ref.id,r) for r in (*scope.earmark_refs,*scope.position_refs,*scope.account_refs)]
        ops += [PutRecord('new/account',account),PutRecord('new/position',newpos)]
        s.execute(prepare(s,changes(s,ops,'replace',scope),'replace'))
        s=self.assertRoundTrip(s,loc);self.assertEqual(s.snapshot.records[:len(before.records)],before.records)
        self.assertNotIn(record_ref(original),s.snapshot.world.members)

    def test_demo_reset_cannot_touch_durable_user(self):
        from finvis.application import DemoApplication
        s,loc=self.new();s.execute(seed(s));before=s.snapshot;identity=s.storage_identity
        app=DemoApplication();app.command('reset',dict(generation=app.generation,revision=app.store.snapshot.revision))
        self.assertEqual(s.snapshot,before);self.assertEqual(s.storage_identity,identity)
        self.assertNotIsInstance(app.store,PersistentWorldStore)
        with self.assertRaises(Rejected):PersistentWorldStore.create(replace(CONTEXT,kind=ContextKind.DEMO,context_id="demo:test"),observation('x'),location())

    def test_reviewed_evidence_is_required_atomic_and_survives_restart(self):
        from tests.test_agent_reconciliation import synthetic as review_fixture, setup, AT
        from tests.test_world_assets import OWNER
        s,loc=self.new(context=replace(CONTEXT,owner_id=OWNER))
        raw,text,request=review_fixture()
        _,bridge,_=setup(raw,text,s)
        reviewed=bridge.review(request,AT)
        self.assertIsNotNone(reviewed.change_set)
        cmd=prepare(s,reviewed.change_set,'reviewed')
        with self.assertRaises(Rejected):s.execute(cmd)
        evidence=CommitEvidence(reviewed,'proposal/workshop')
        receipt=s.execute(cmd,evidence);s=self.reopen(s,loc)
        result=s.operation(cmd.authorization_ref.id)
        self.assertEqual(result['receipt'],receipt)
        self.assertEqual(result['evidence']['candidate_id'],reviewed.candidate_id)
        self.assertNotIn('PRIVATE UNRELATED NOTE',canonical(result['evidence']))
        self.assertEqual(s.execute(cmd,evidence),receipt)
        with self.assertRaises(Rejected):s.execute(cmd,replace(evidence,proposal_id='conflict'))
        self.assertEqual(s.snapshot.revision,1)

    def test_reviewed_binding_tampering_rejected_before_commit(self):
        from tests.test_agent_reconciliation import synthetic as review_fixture, setup, AT
        from tests.test_world_assets import OWNER
        s,loc=self.new(context=replace(CONTEXT,owner_id=OWNER));raw,text,request=review_fixture()
        _,bridge,_=setup(raw,text,s);reviewed=bridge.review(request,AT);cmd=prepare(s,reviewed.change_set,'review')
        with self.assertRaises(Rejected):s.execute(cmd,CommitEvidence(replace(reviewed,candidate_id='different')))
        self.assertEqual(s.snapshot.revision,0)

    def test_real_process_death_at_transaction_boundaries(self):
        for stage in ('record_written','before_commit','after_commit','after_publication'):
            with self.subTest(stage=stage):
                s,loc=self.new();s.close()
                process=subprocess.run([sys.executable,'-B','-m','tests.test_persistent_world','crash',str(loc.directory),stage],
                    capture_output=True,text=True,timeout=30,env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1'))
                self.assertEqual(process.returncode,71,process.stderr)
                s=PersistentWorldStore.open(loc);self.addCleanup(s.close)
                committed=stage in ('after_commit','after_publication')
                self.assertEqual(s.snapshot.revision,int(committed))
                self.assertEqual(s.lookup('command/seed') is not None,committed)
                self.assertEqual(len(s.snapshot.commands),int(committed))
                self.assertEqual(replay_history(s.export_replay()),s.snapshot)
                if committed:
                    original=s.operation('authorization/seed')['command'];self.assertEqual(s.execute(original),s.lookup('command/seed'))
                else:self.assertEqual(len(s.snapshot.records),2)


    def test_external_epoch_change_invalidates_old_writer(self):
        from finvis.world_history_replay import ReplayEpoch
        s,loc=self.new();cmd=seed(s)
        s._insert_epoch(1,ReplayEpoch('unexpected-epoch',0,s.snapshot.world.ref,s.session_generation))
        with self.assertRaises(StorageUnavailable):s.execute(cmd)
        with self.assertRaises(StorageUnavailable):_ = s.snapshot
        s=self.reopen(s,loc);self.assertEqual(s.snapshot.revision,0)

    def test_every_durable_read_freezes_corrupt_authority(self):
        for action in ('operation','export_replay'):
            s,loc=self.new();s._db.execute("UPDATE metadata SET initial_digest='wrong'")
            with self.assertRaises(StorageUnavailable):
                s.operation('missing') if action=='operation' else s.export_replay()
            with self.assertRaises(StorageUnavailable):_ = s.snapshot

    def test_disk_commits_before_memory_is_published(self):
        s,loc=self.new();cmd=seed(s);observed=[]
        def checkpoint(stage):
            if stage=='after_commit':
                connection=sqlite3.connect(loc.database)
                observed.append((s._snapshot.revision,connection.execute('SELECT head_revision FROM metadata').fetchone()[0],
                                 connection.execute('SELECT count(*) FROM commits').fetchone()[0]))
                connection.close()
        with patch.object(s,'_checkpoint',side_effect=checkpoint):s.execute(cmd)
        self.assertEqual(observed,[(0,1,1)])

    def test_other_reader_never_sees_partial_transaction(self):
        s,loc=self.new();cmd=seed(s);observed=[]
        def checkpoint(stage):
            if stage=='receipt_written':
                connection=sqlite3.connect(loc.database)
                observed.append(tuple(connection.execute('SELECT (SELECT count(*) FROM roots),(SELECT count(*) FROM records),(SELECT count(*) FROM commits)').fetchone()))
                connection.close()
        with patch.object(s,'_checkpoint',side_effect=checkpoint):s.execute(cmd)
        self.assertEqual(observed,[(1,2,0)])
        self.assertEqual(s.snapshot.revision,1)

    def test_settlement_write_failure_preserves_all_prior_lineage(self):
        for correction in (False,True):
            s,loc=self.new(FaultStore);s.execute(seed(s))
            if correction:s.execute(settlement(s))
            before=s.snapshot;cmd=settlement(s,correction);s.fault='record_written';s.seen=0
            with self.assertRaises(RuntimeError):s.execute(cmd)
            self.assertEqual(s.snapshot,before)
            s=self.reopen(s,loc);self.assertEqual(s.snapshot,before)

    def test_forward_removal_remains_append_only_after_restart(self):
        s,loc=self.new();s.execute(seed(s));before=s.snapshot
        s=self.reopen(s,loc);s.execute(history(s,'remove',kind='future_inflow',name='remove'))
        s=self.assertRoundTrip(s,loc)
        self.assertEqual(s.snapshot.records[:len(before.records)],before.records)
        self.assertEqual(s.snapshot.revision,2)

    def test_tampered_review_binding_fails_recovery_even_with_new_capsule_hash(self):
        from hashlib import sha256
        from tests.test_agent_reconciliation import synthetic as review_fixture, setup, AT
        from tests.test_world_assets import OWNER
        s,loc=self.new(context=replace(CONTEXT,owner_id=OWNER));raw,text,request=review_fixture()
        _,bridge,_=setup(raw,text,s);reviewed=bridge.review(request,AT);cmd=prepare(s,reviewed.change_set,'review')
        s.execute(cmd,CommitEvidence(reviewed));capsule=s.operation(cmd.authorization_ref.id)['evidence']
        capsule['review']['value']['candidate_id']='altered'
        encoded=canonical(capsule);s._db.execute('DROP TRIGGER immutable_commits_update')
        s._db.execute('UPDATE commits SET evidence=?,evidence_digest=?',(encoded,sha256(encoded.encode()).hexdigest()))
        s._db.execute(next(sql for sql in V1.statements if sql.startswith('CREATE TRIGGER immutable_commits_update ')));s.close()
        with self.assertRaises(StorageIntegrityError):PersistentWorldStore.open(loc)

    def test_initialization_crash_leaves_no_false_empty_success(self):
        loc=location()
        process=subprocess.run([sys.executable,'-B','-m','tests.test_persistent_world','initialize-crash',str(loc.directory)],
            capture_output=True,text=True,timeout=30)
        self.assertEqual(process.returncode,71,process.stderr)
        with self.assertRaises(StorageUnavailable):PersistentWorldStore.open(loc)
        with self.assertRaises(StorageUnavailable):PersistentWorldStore.create(CONTEXT,observation('retry'),loc)

    def test_crash_expires_unconsumed_consent(self):
        from finvis.world_serialization import loads_world_mutation
        s,loc=self.new();s.close()
        process=subprocess.run([sys.executable,'-B','-m','tests.test_persistent_world','pending-crash',str(loc.directory)],
            capture_output=True,text=True,timeout=30)
        self.assertEqual(process.returncode,71,process.stderr)
        command=loads_world_mutation((loc.directory/'test-only-pending.json').read_text())
        s=PersistentWorldStore.open(loc);self.addCleanup(s.close)
        with self.assertRaises(Rejected):s.execute(command)
        self.assertIsNone(s.lookup(command.command_id));self.assertEqual(s.snapshot.revision,0)

    def test_review_evidence_survives_real_post_commit_crash(self):
        from tests.test_world_assets import OWNER
        s,loc=self.new(context=replace(CONTEXT,owner_id=OWNER));s.close()
        process=subprocess.run([sys.executable,'-B','-m','tests.test_persistent_world','review-crash',str(loc.directory)],
            capture_output=True,text=True,timeout=30)
        self.assertEqual(process.returncode,71,process.stderr)
        s=PersistentWorldStore.open(loc);self.addCleanup(s.close)
        result=s.operation('authorization/reviewed')
        self.assertEqual(result['receipt'].after_world_ref.version,2)
        self.assertEqual(result['evidence']['origin'],'reviewed')
        self.assertTrue(result['evidence']['review']['value']['bindings'])
        self.assertNotIn('PRIVATE UNRELATED NOTE',canonical(result['evidence']))

    def test_crash_during_test_migration_keeps_original_schema(self):
        s,loc=self.new();s.execute(seed(s));before=s.snapshot;s.close()
        process=subprocess.run([sys.executable,'-B','-m','tests.test_persistent_world','migration-crash',str(loc.directory)],
            capture_output=True,text=True,timeout=30)
        self.assertEqual(process.returncode,71,process.stderr)
        s=PersistentWorldStore.open(loc);self.addCleanup(s.close)
        self.assertEqual(s.snapshot,before)
        self.assertEqual(s._db.execute('PRAGMA user_version').fetchone()[0],VERSION)
        self.assertFalse(s._db.execute("SELECT 1 FROM sqlite_master WHERE name='not_published'").fetchall())

    def test_missing_sidecar_metadata_or_corrupt_bytes_never_become_demo(self):
        s,loc=self.new();s.close()
        # Corrupt only this throwaway fixture, preserve its file for inspection.
        with loc.database.open('r+b') as f:f.write(b'invalid-header!!!')
        with self.assertRaises(StorageUnavailable):PersistentWorldStore.open(loc)
        self.assertNotEqual(loc.database.read_bytes()[:16],b'SQLite format 3\x00')

if __name__=='__main__' and len(sys.argv)>1 and sys.argv[1] in ('crash','initialize-crash','pending-crash','review-crash','migration-crash'):
    mode=sys.argv[1]
    class CrashStore(PersistentWorldStore):
        def _checkpoint(self,stage):
            target='record_written' if mode=='initialize-crash' else 'after_commit' if mode=='review-crash' else sys.argv[3] if mode=='crash' else None
            if stage==target:os._exit(71)
    loc=StorageLocation.temporary(Path(sys.argv[2]))
    if mode=='initialize-crash':CrashStore.create(CONTEXT,observation('initialize'),loc)
    else:
        target=CrashStore.open(loc)
        if mode=='pending-crash':
            from finvis.world_serialization import dumps_world_mutation
            with (loc.directory/'test-only-pending.json').open('w') as f:
                f.write(dumps_world_mutation(seed(target)));f.flush();os.fsync(f.fileno())
            os._exit(71)
        elif mode=='review-crash':
            from tests.test_agent_reconciliation import synthetic as review_fixture, setup, AT
            raw,text,request=review_fixture();_,bridge,_=setup(raw,text,target)
            reviewed=bridge.review(request,AT);cmd=prepare(target,reviewed.change_set,'reviewed')
            target.execute(cmd,CommitEvidence(reviewed))
        elif mode=='migration-crash':
            migrate(target._db,VERSION+1,(*MIGRATIONS,Migration(VERSION+1,'test-only',('CREATE TABLE not_published (x TEXT)',))),
                validate=lambda:os._exit(71),backup_path=loc.directory/'before-crash.sqlite3')
        else:target.execute(seed(target))
elif __name__=='__main__':unittest.main()

"""Explicit competition-only archive-and-empty; never a financial deletion API."""
import os
import re
import secrets
import stat
import time
from datetime import datetime, timezone
from uuid import uuid4

from .i3_workflow import I3Error
from .persistent_world import StorageIntegrityError, _private
from .user_runtime import UserRuntime
from .platform_storage import durable_move

JOURNAL = 'competition-reset.json'


def instance_directory(location, identity):
    location.__post_init__()
    base = location.directory.parent.parent
    if (not isinstance(identity, str) or not re.fullmatch(r'[0-9a-f]{32}', identity)
            or base.name != identity or base.parent.name != 'instances'
            or location.directory != base/'state'/'UserWorld'
            or (not location.testing and location.competition_id != identity)):
        raise StorageIntegrityError('Competition reset scope mismatch')
    if base != base.resolve():
        raise StorageIntegrityError('Aliased competition instance')
    _private(base, True)
    from .competition_launcher import read_json
    if (base/'instance.json').lstat().st_nlink != 1:
        raise StorageIntegrityError('Aliased competition manifest')
    value = read_json(base/'instance.json')
    if (set(value) != {'schema', 'id', 'port', 'created_at'} or value['schema'] != 1
            or value['id'] != identity or type(value['port']) is not int):
        raise StorageIntegrityError('Competition reset instance mismatch')
    return base


def recovery_guard(location, identity):
    """Interrupted clear never becomes an implicit first run on restart."""
    base = instance_directory(location, identity)
    path = base/JOURNAL
    if not path.exists() and not path.is_symlink():
        return 'initial'
    from .competition_launcher import read_json
    if path.lstat().st_nlink != 1:
        raise StorageIntegrityError('Aliased competition journal')
    value = read_json(path)
    if (set(value) != {'schema', 'instance', 'reset_id', 'phase', 'created_at'}
            or value['schema'] != 1 or value['instance'] != identity
            or not isinstance(value['reset_id'], str)
            or not re.fullmatch(r'[0-9a-f]{32}', value['reset_id'])
            or value['phase'] != 'complete'):
        raise StorageIntegrityError('Competition clear recovery required')
    backup = base/'backups'/('clear-'+value['reset_id'])
    _private(base/'backups', True)
    if backup != backup.resolve():
        raise StorageIntegrityError('Aliased competition backup')
    _private(backup, True)
    _private(backup/'state', True)
    _private(base/'state', True)
    return value['reset_id']


def private_tree(path):
    """No symlink/hardlink aliases may reach outside this instance."""
    if path != path.resolve():
        raise StorageIntegrityError('Aliased competition clear path')
    _private(path, True)
    for directory, dirs, files in os.walk(path, followlinks=False):
        from pathlib import Path
        for name in dirs+files:
            child=Path(directory)/name
            info=child.lstat()
            if stat.S_ISLNK(info.st_mode) or (stat.S_ISREG(info.st_mode) and info.st_nlink != 1):
                raise StorageIntegrityError('Aliased competition clear file')
            _private(child, stat.S_ISDIR(info.st_mode))


class CompetitionReset:
    def __init__(self, server):
        self.server=server
        self.pending=None
        self.generation=recovery_guard(server.user_runtime.location, server.competition_id)

    def _checkpoint(self, stage):
        """Fault-injection seam used only by synthetic tests."""

    def action(self, action, payload, csrf, generation):
        s=self.server
        with s._user_lock:
            if (not secrets.compare_digest(csrf, s.csrf)
                    or generation != self.generation):
                raise I3Error('competition_reset_stale',409)
            if s._competition_active:
                raise I3Error('competition_reset_busy',409)
            if action=='prepare':
                if payload:raise ValueError()
                self.pending=dict(id=secrets.token_urlsafe(32), stage='prepared',
                                  expires=time.monotonic()+300, sequence=s._competition_sequence)
                return dict(stage='prepared', confirmation_id=self.pending['id'])
            p=self.pending
            if (not p or time.monotonic()>p['expires']
                    or p['sequence']!=s._competition_sequence):
                self.pending=None
                raise I3Error('competition_reset_stale',409)
            if action=='confirm':
                if set(payload)!={'confirmation_id','phrase'}:raise ValueError()
                if (p['stage']!='prepared' or payload['confirmation_id']!=p['id']
                        or payload['phrase']!='CLEAR'):
                    raise I3Error('competition_reset_confirmation',409)
                p.update(stage='confirmed', grant=secrets.token_urlsafe(32))
                return dict(stage='confirmed', authorization=p['grant'])
            if action!='apply' or set(payload)!={'confirmation_id','authorization'}:raise ValueError()
            if (p['stage']!='confirmed' or payload['confirmation_id']!=p['id']
                    or payload['authorization']!=p['grant']):
                raise I3Error('competition_reset_confirmation',409)
            self.pending=None  # single-use, including failures; never silently retry
            return self._apply()

    def _apply(self):
        s=self.server
        base=instance_directory(s.user_runtime.location,s.competition_id)
        private_tree(base/'state')
        _private(base/'backups',True)
        marker=base/'world-required'
        if marker.exists() or marker.is_symlink():
            _private(marker)
            if marker.is_symlink() or marker.lstat().st_nlink!=1:
                raise StorageIntegrityError('Aliased competition marker')
        from .competition_launcher import private_dir, write_json
        reset_id=uuid4().hex
        backup=base/'backups'/('clear-'+reset_id)
        private_dir(backup)
        journal=dict(schema=1,instance=s.competition_id,reset_id=reset_id,
                     phase='preparing',created_at=datetime.now(timezone.utc).isoformat())
        location=s.user_runtime.location
        try:
            write_json(base/JOURNAL,journal)
            self._checkpoint('journal')
            s.user_runtime.close()
            s.user_workflow=None
            durable_move(base/'state',backup/'state')
            self._checkpoint('state_archived')
            if marker.exists():
                durable_move(marker,backup/'world-required')
            self._checkpoint('marker_archived')
            private_dir(base/'state')
            self._checkpoint('empty_state')
            runtime=UserRuntime(s.app.agent.provider,location)
            if runtime.state!='first_run':
                runtime.close()
                raise StorageIntegrityError('Fresh competition state unavailable')
            write_json(base/JOURNAL,dict(journal,phase='complete'))
            s.user_runtime=runtime
            self.generation=reset_id
            s.csrf=secrets.token_urlsafe(32)
            s._competition_sequence+=1
            return dict(stage='cleared',generation=reset_id,archived=True)
        except Exception:
            s.user_workflow=None
            s.user_runtime._failed(StorageIntegrityError('Competition clear interrupted'))
            raise I3Error('competition_reset_recovery_required',503) from None

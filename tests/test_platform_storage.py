"""Platform contract tests. Win32 doubles are not native Windows acceptance."""
from pathlib import Path, PureWindowsPath
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import ctypes
from ctypes import wintypes
import os
import unittest
from unittest.mock import patch, Mock
from finvis import platform_storage as p


class PortableStorageTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve()/'private';p.private_mkdir(self.root)
    def test_publish_replace_and_preserve_previous(self):
        f=self.root/'entry';p.durable_write(f,b'one')
        with self.assertRaises(FileExistsError):p.durable_write(f,b'two')
        self.assertEqual(f.read_bytes(),b'one')
        p.durable_write(f,b'three',replace=True);self.assertEqual(f.read_bytes(),b'three')
        p.check_private(f)
    def test_archive_never_overwrites_destination(self):
        a,b=self.root/'a',self.root/'b';p.private_mkdir(a);p.private_mkdir(b)
        p.durable_write(a/'evidence',b'unchanged')
        with self.assertRaises((FileExistsError,OSError)):p.durable_move(a,b)
        self.assertEqual((a/'evidence').read_bytes(),b'unchanged')
    def test_concurrent_initialization_has_one_winner(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        f=self.root/'identity';gate=Barrier(2)
        def publish(value):
            gate.wait()
            try:p.durable_write(f,value);return value
            except FileExistsError:return None
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(publish,(b'first',b'second')))
        winners=[v for v in results if v is not None]
        self.assertEqual(len(winners),1);self.assertEqual(f.read_bytes(),winners[0])
    def test_regular_file_sync_and_empty_creation(self):
        f=self.root/'empty';p.durable_empty_file(f);p.sync_file(f)
        self.assertEqual(f.read_bytes(),b'')
        with self.assertRaises(FileExistsError):p.durable_empty_file(f)
    def test_second_authority_rejected_then_released(self):
        f=self.root/'lock';lease=p.acquire_lock(f)
        try:
            with self.assertRaises(BlockingIOError):p.acquire_lock(f)
        finally:p.release_lock(lease)
        lease=p.acquire_lock(f,create=False);p.release_lock(lease)
    @unittest.skipIf(p.WINDOWS,'POSIX permissions; Windows ACL branch tested separately')
    def test_unsafe_existing_mode_not_repaired(self):
        f=self.root/'entry';p.durable_write(f,b'one');f.chmod(0o644)
        with self.assertRaises(OSError):p.durable_write(f,b'two',replace=True)
        self.assertEqual(f.read_bytes(),b'one');self.assertEqual(f.stat().st_mode&0o777,0o644)
    @unittest.skipIf(p.WINDOWS,'Requires local symlink privilege; reparse branch tested separately')
    def test_symlink_never_written(self):
        f=self.root/'entry';p.durable_write(f,b'one');alias=self.root/'alias';alias.symlink_to(f)
        with self.assertRaises(OSError):p.durable_write(alias,b'two',replace=True)
        self.assertEqual(f.read_bytes(),b'one')
    def test_flush_failure_preserves_incomplete_initialization_for_recovery(self):
        f=self.root/'entry'
        with patch.object(p.os,'fsync',side_effect=OSError('synthetic flush failure')):
            with self.assertRaises(OSError):p.durable_write(f,b'one')
        # POSIX intentionally preserves its original exclusive partial marker.
        # Recovery can reject it; a failed initialize never silently becomes new.
        if not p.WINDOWS:self.assertTrue(f.exists())
    def test_move_failure_preserves_existing_content(self):
        f=self.root/'entry';p.durable_write(f,b'one')
        with patch.object(p,'durable_move',side_effect=OSError('synthetic move failure')):
            with self.assertRaises(OSError):p.durable_write(f,b'two',replace=True)
        self.assertEqual(f.read_bytes(),b'one')
    def test_sqlite_gate_is_exact(self):
        for v in ((3,51,3),(3,52,0),(3,50,7),(3,50,9),(3,44,6)):self.assertTrue(p.sqlite_supported(v))
        for v in ((3,51,2),(3,50,6),(3,49,9),(3,45,0),(3,44,5)):self.assertFalse(p.sqlite_supported(v))


class WindowsBoundaryTests(unittest.TestCase):
    def native(self):
        n=object.__new__(p._Windows);n.c=ctypes;n.w=wintypes
        n.validate=Mock();n.k=SimpleNamespace(MoveFileExW=Mock(return_value=1));n._ok=lambda x:x if x else (_ for _ in ()).throw(OSError('native failure'))
        return n
    def test_windows_names_reject_unc_devices_ads_and_ambiguous_components(self):
        for name in ('relative/path',r'\\server\share\FINVIS',r'\\?\C:\FINVIS',r'C:\FINVIS\a:stream',r'C:\FINVIS\bad.',r'C:\FINVIS\bad ',r'C:\FINVIS\..\other',r'C:\FINVIS\.\other',r'C:\FINVIS\NUL.txt',r'C:\FINVIS\com1'):
            with self.subTest(name=name),self.assertRaises(OSError):p.validate_windows_name(name)
        self.assertEqual(str(p.validate_windows_name(r'C:\Synthetic Profile\Local\FINVIS')),r'C:\Synthetic Profile\Local\FINVIS')
    def test_native_move_write_through_without_copy(self):
        n=self.native();a=PureWindowsPath(r'C:\private\a');b=PureWindowsPath(r'C:\private\b')
        n.move(a,b);n.k.MoveFileExW.assert_called_once_with(str(a),str(b),8)
        n.move(a,b,True);self.assertEqual(n.k.MoveFileExW.call_args.args[2],9)
    def test_native_cross_volume_and_failure_rejected(self):
        n=self.native()
        with self.assertRaises(OSError):n.move(PureWindowsPath('C:/a'),PureWindowsPath('D:/a'))
        n.k.MoveFileExW.assert_not_called()
        n.k.MoveFileExW.return_value=0
        with self.assertRaises(OSError):n.move(PureWindowsPath('C:/a'),PureWindowsPath('C:/b'))
    def test_native_directory_create_security_then_durable_publication(self):
        n=self.native();n.k.CreateDirectoryW=Mock(return_value=1);n.k.LocalFree=Mock();n._security=lambda:(None,ctypes.c_int(1));n.check=Mock()
        n.mkdir(PureWindowsPath('C:/private/instance'))
        args=n.k.MoveFileExW.call_args.args
        self.assertTrue(PureWindowsPath(args[0]).name.startswith('.finvis-'))
        self.assertEqual(args[1:],('C:\\private\\instance',8))
        self.assertEqual(n.check.call_args.args,(PureWindowsPath('C:/private/instance'),True))
    def test_windows_dispatch_uses_native_move_and_no_directory_fsync(self):
        native=Mock()
        with patch.object(p,'WINDOWS',True),patch.object(p,'_windows',return_value=native),patch.object(p,'_posix_sync_directory') as sync:
            p.durable_move(Path('/source'),Path('/target'))
        native.move.assert_called_once();sync.assert_not_called()
    def test_native_lock_sharing_failure_is_not_ignored(self):
        n=self.native();n._open=Mock(side_effect=BlockingIOError());n.check_handle=Mock()
        with self.assertRaises(BlockingIOError):n.lock(PureWindowsPath('C:/private/lock'),True,False)
        n.check_handle.assert_not_called()
    def test_native_lock_checks_acl_before_return(self):
        n=self.native();n._open=Mock(return_value=9);n.check_handle=Mock(side_effect=OSError('unsafe ACL'));n.k.CloseHandle=Mock()
        with self.assertRaises(OSError):n.lock(PureWindowsPath('C:/private/lock'),True,False)
        n.k.CloseHandle.assert_called_once_with(9)
    def acl_native(self, *, owner='owner', principal='owner', ace_type=0, ace_flags=0, mask=0x1f01ff, attributes=0, links=1, null_dacl=False):
        n=self.native();n.sid='owner';n.w=SimpleNamespace(DWORD=ctypes.c_uint32)
        class Info(ctypes.Structure):
            _fields_=[('attributes',ctypes.c_uint32),('links',ctypes.c_uint32)]
        n.INFO=Info
        acl=(ctypes.c_ushort*4)(2,12,1,0)
        ace=(ctypes.c_ubyte*24)();ace[0]=ace_type;ace[1]=ace_flags
        ctypes.cast(ctypes.addressof(ace)+4,ctypes.POINTER(ctypes.c_uint32))[0]=mask
        def info(handle,pointer):pointer._obj.attributes=attributes;pointer._obj.links=links;return 1
        def security(handle,kind,flags,op,gp,dp,sp,sdp):
            op._obj.value=101;dp._obj.value=None if null_dacl else ctypes.addressof(acl);sdp._obj.value=202;return 0
        def get_ace(dacl,index,pointer):pointer._obj.value=ctypes.addressof(ace);return 1
        n.k.GetFileInformationByHandle=info;n.k.LocalFree=Mock()
        n.a=SimpleNamespace(GetSecurityInfo=security,GetAce=get_ace)
        n._sid_string=lambda pointer:owner if getattr(pointer,'value',pointer)==101 else principal
        n.buffers=(acl,ace)
        return n
    def test_native_acl_accepts_only_owner_full_access(self):
        n=self.acl_native();n.check_handle(1);n.k.LocalFree.assert_called_once()
    def test_native_acl_rejects_owner_null_foreign_and_inherit_only(self):
        for kwargs in ({'owner':'other'},{'null_dacl':True},{'principal':'other'},{'ace_type':1},{'ace_flags':8},{'mask':0x120089}):
            with self.subTest(kwargs=kwargs),self.assertRaises(OSError):self.acl_native(**kwargs).check_handle(1)
    def test_native_handle_rejects_reparse_hardlink_and_wrong_type(self):
        for kwargs in ({'attributes':0x400},{'links':2},{'attributes':0x10}):
            with self.subTest(kwargs=kwargs),self.assertRaises(OSError):self.acl_native(**kwargs).check_handle(1)
        self.acl_native(attributes=0x10).check_handle(1,True)
    def volume_native(self, *, attributes=0,fs_name='NTFS',flags=8,drive_type=3):
        n=self.native();del n.validate;n.w=SimpleNamespace(DWORD=ctypes.c_uint32)
        n.k.GetFileAttributesW=lambda value:attributes
        def volume(path,buffer,size):buffer.value='C:\\';return 1
        def info(path,name,size,serial,maxlen,outflags,fs,fssize):outflags._obj.value=flags;fs.value=fs_name;return 1
        n.k.GetVolumePathNameW=volume;n.k.GetVolumeInformationW=info;n.k.GetDriveTypeW=lambda path:drive_type
        return n
    def test_native_volume_rejects_reparse_network_and_acl_less_filesystems(self):
        with patch.object(p,'validate_windows_name'):
            self.volume_native().validate(Path('/private/tmp'))
            for kwargs in ({'attributes':0x400},{'fs_name':'FAT32'},{'flags':0},{'drive_type':4}):
                with self.subTest(kwargs=kwargs),self.assertRaises(OSError):self.volume_native(**kwargs).validate(Path('/private/tmp'))
    def test_browser_environment_never_inherits_key(self):
        from finvis.competition_launcher import browser_environment
        with patch.dict(os.environ,{'OPENAI_API_KEY':'synthetic-only','FINVIS_OPENAI_ENABLED':'1','SystemRoot':'C:\\Windows'}):
            env=browser_environment(Path('/synthetic/instance'))
        self.assertNotIn('OPENAI_API_KEY',env);self.assertNotIn('FINVIS_OPENAI_ENABLED',env)
        self.assertEqual(env['SystemRoot'],'C:\\Windows');self.assertEqual(env['TMP'],env['TEMP'])
    def test_windows_socket_exclusive_before_bind(self):
        from finvis import web
        server=object.__new__(web.DemoServer);server.socket=Mock()
        with patch.object(web,'WINDOWS',True),patch.object(web.socket,'SO_EXCLUSIVEADDRUSE',-5,create=True),patch.object(web.ThreadingHTTPServer,'server_bind') as bind:
            server.server_bind()
        self.assertFalse(server.allow_reuse_address)
        server.socket.setsockopt.assert_called_once_with(web.socket.SOL_SOCKET,-5,1);bind.assert_called_once()
    def test_windows_launcher_is_relative_utf8_and_non_destructive(self):
        text=(Path(__file__).resolve().parents[1]/'Start FINVIS Competition.cmd').read_text()
        self.assertIn('cd /d "%~dp0"',text);self.assertIn('"PYTHONUTF8=1"',text)
        self.assertIn('-m finvis.competition_launcher',text)
        for forbidden in ('OPENAI_API_KEY','del ','rmdir','taskkill','powershell'):
            self.assertNotIn(forbidden,text)
    def test_launcher_sqlite_preflight_precedes_registry(self):
        from finvis import competition_launcher as l
        with patch.object(l.sqlite3,'sqlite_version_info',(3,40,0)),patch.object(l,'Registry') as registry,patch('sys.argv',['launcher']),patch('builtins.print'):
            self.assertEqual(l.main(),1)
        registry.assert_not_called()

if __name__=='__main__':unittest.main()

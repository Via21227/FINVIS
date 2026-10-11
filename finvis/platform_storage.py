"""Local storage primitives: POSIX permissions or Windows NTFS owner ACLs.

Windows namespace durability is established at each mutation (flushed file plus
MoveFileExW WRITE_THROUGH), not by pretending directory fsync exists. Native
Windows execution must be separately accepted; unsupported filesystems fail closed.
"""
from pathlib import Path, PureWindowsPath
import os
import stat
import sys
import time
from uuid import uuid4


WINDOWS = os.name == 'nt'


def validate_windows_name(value):
    p = PureWindowsPath(value)
    if not p.is_absolute() or len(p.drive) != 2 or p.drive[1] != ':' or not p.drive[0].isalpha():
        raise OSError('Only a local drive path is supported')
    raw_parts=str(value).replace('/', '\\').split('\\')[1:]
    reserved={'CON','PRN','AUX','NUL','CLOCK$',*(f'COM{i}' for i in range(1,10)),*(f'LPT{i}' for i in range(1,10))}
    for part in raw_parts:
        if part in ('.', '..') or part.endswith((' ', '.')) or ':' in part or part.split('.')[0].upper() in reserved:
            raise OSError('Ambiguous Windows storage path')
    return p


class _Windows:
    """Small stdlib-only Win32 boundary; no shell or administrator requirement."""
    def __init__(self):
        import ctypes as c
        from ctypes import wintypes as w
        self.c, self.w = c, w
        self.k = c.WinDLL('kernel32', use_last_error=True)
        self.a = c.WinDLL('advapi32', use_last_error=True)
        self.shell = c.WinDLL('shell32', use_last_error=True)
        self.ole = c.WinDLL('ole32', use_last_error=True)
        class SA(c.Structure):
            _fields_ = [('nLength', w.DWORD), ('lpSecurityDescriptor', c.c_void_p), ('bInheritHandle', w.BOOL)]
        class INFO(c.Structure):
            _fields_ = [('attributes', w.DWORD), ('created', w.FILETIME), ('accessed', w.FILETIME),
                        ('written', w.FILETIME), ('volume', w.DWORD), ('sizeHigh', w.DWORD),
                        ('sizeLow', w.DWORD), ('links', w.DWORD), ('indexHigh', w.DWORD), ('indexLow', w.DWORD)]
        self.SA, self.INFO = SA, INFO
        signatures = [
            (self.k, 'CreateFileW', [w.LPCWSTR,w.DWORD,w.DWORD,c.POINTER(SA),w.DWORD,w.DWORD,w.HANDLE],w.HANDLE),
            (self.k, 'CreateDirectoryW', [w.LPCWSTR,c.POINTER(SA)],w.BOOL),
            (self.k, 'MoveFileExW', [w.LPCWSTR,w.LPCWSTR,w.DWORD],w.BOOL),
            (self.k, 'CloseHandle', [w.HANDLE],w.BOOL),
            (self.k, 'FlushFileBuffers', [w.HANDLE],w.BOOL),
            (self.k, 'GetFileInformationByHandle', [w.HANDLE,c.POINTER(INFO)],w.BOOL),
            (self.k, 'GetFileAttributesW', [w.LPCWSTR],w.DWORD),
            (self.k, 'GetVolumePathNameW', [w.LPCWSTR,w.LPWSTR,w.DWORD],w.BOOL),
            (self.k, 'GetVolumeInformationW', [w.LPCWSTR,w.LPWSTR,w.DWORD,c.POINTER(w.DWORD),c.POINTER(w.DWORD),c.POINTER(w.DWORD),w.LPWSTR,w.DWORD],w.BOOL),
            (self.k, 'GetDriveTypeW', [w.LPCWSTR],w.UINT),
            (self.k, 'GetCurrentProcess', [],w.HANDLE),
            (self.k, 'LocalFree', [c.c_void_p],c.c_void_p),
            (self.a, 'OpenProcessToken', [w.HANDLE,w.DWORD,c.POINTER(w.HANDLE)],w.BOOL),
            (self.a, 'GetTokenInformation', [w.HANDLE,c.c_int,c.c_void_p,w.DWORD,c.POINTER(w.DWORD)],w.BOOL),
            (self.a, 'ConvertSidToStringSidW', [c.c_void_p,c.POINTER(w.LPWSTR)],w.BOOL),
            (self.a, 'ConvertStringSecurityDescriptorToSecurityDescriptorW', [w.LPCWSTR,w.DWORD,c.POINTER(c.c_void_p),c.POINTER(w.DWORD)],w.BOOL),
            (self.a, 'GetSecurityInfo', [w.HANDLE,c.c_int,w.DWORD,c.POINTER(c.c_void_p),c.POINTER(c.c_void_p),c.POINTER(c.c_void_p),c.POINTER(c.c_void_p),c.POINTER(c.c_void_p)],w.DWORD),
            (self.a, 'GetAce', [c.c_void_p,w.DWORD,c.POINTER(c.c_void_p)],w.BOOL),
            (self.shell, 'SHGetKnownFolderPath', [c.c_void_p,w.DWORD,w.HANDLE,c.POINTER(w.LPWSTR)],c.c_long),
            (self.ole, 'CoTaskMemFree', [c.c_void_p],None),
        ]
        for lib, name, args, result in signatures:
            f = getattr(lib,name); f.argtypes=args; f.restype=result
        self.sid = self._current_sid()

    def _ok(self, value):
        if not value: raise self.c.WinError(self.c.get_last_error())
        return value

    def _sid_string(self, sid):
        out=self.w.LPWSTR()
        self._ok(self.a.ConvertSidToStringSidW(sid,self.c.byref(out)))
        try:return out.value
        finally:self.k.LocalFree(out)

    def _current_sid(self):
        c,w=self.c,self.w; token=w.HANDLE();size=w.DWORD()
        self._ok(self.a.OpenProcessToken(self.k.GetCurrentProcess(),8,c.byref(token)))
        try:
            self.a.GetTokenInformation(token,1,None,0,c.byref(size))
            if not size.value:raise OSError('Token owner unavailable')
            buf=c.create_string_buffer(size.value)
            self._ok(self.a.GetTokenInformation(token,1,buf,size,c.byref(size)))
            return self._sid_string(c.cast(buf,c.POINTER(c.c_void_p))[0])
        finally:self.k.CloseHandle(token)

    def _security(self):
        c=self.c;sd=c.c_void_p()
        # Protected owner-only ACL; descendants inherit the same user permission.
        self._ok(self.a.ConvertStringSecurityDescriptorToSecurityDescriptorW(
            'O:'+self.sid+'D:P(A;OICI;FA;;;'+self.sid+')',1,c.byref(sd),None))
        return sd,self.SA(c.sizeof(self.SA),sd,False)

    def appdata(self):
        from uuid import UUID
        c=self.c;guid=(c.c_ubyte*16).from_buffer_copy(UUID('F1B32785-6FBA-4FCF-9D55-7B8E7F157091').bytes_le)
        out=self.w.LPWSTR()
        if self.shell.SHGetKnownFolderPath(guid,0,None,c.byref(out)) != 0:
            raise OSError('Local application storage unavailable')
        try:return Path(out.value)
        finally:self.ole.CoTaskMemFree(out)

    def validate(self,path):
        validate_windows_name(str(path))
        c,w=self.c,self.w
        # Reject every reparse component, including junctions and cloud redirects.
        for p in reversed((path,*path.parents)):
            attr=self.k.GetFileAttributesW(str(p))
            if attr==0xffffffff:
                if c.get_last_error() in (2,3):continue
                raise c.WinError(c.get_last_error())
            if attr & 0x400:raise OSError('Reparse storage path rejected')
        existing=path
        while not existing.exists():existing=existing.parent
        volume=c.create_unicode_buffer(32768)
        self._ok(self.k.GetVolumePathNameW(str(existing),volume,len(volume)))
        fs=c.create_unicode_buffer(64);flags=w.DWORD()
        self._ok(self.k.GetVolumeInformationW(volume,None,0,None,None,c.byref(flags),fs,len(fs)))
        if self.k.GetDriveTypeW(volume)!=3 or fs.value!='NTFS' or not flags.value & 8:
            raise OSError('Local NTFS with persistent ACLs is required')

    def _open(self,path,access=0x20000,share=7,disposition=3,security=False,directory=False):
        self.validate(path)
        sd,sa=self._security() if security else (None,None)
        try:
            handle=self.k.CreateFileW(str(path),access,share,self.c.byref(sa) if sa else None,
                disposition,0x00200000 | (0x02000000 if directory else 0),None)
            if handle == self.c.c_void_p(-1).value:
                code=self.c.get_last_error()
                if code in (32,33):raise BlockingIOError(code,'Storage is already in use')
                raise self.c.WinError(code)
            return handle
        finally:
            if sd:self.k.LocalFree(sd)

    def check_handle(self,handle,directory=False):
        c,w=self.c,self.w;info=self.INFO()
        self._ok(self.k.GetFileInformationByHandle(handle,c.byref(info)))
        if bool(info.attributes & 0x10)!=directory or info.attributes & 0x400 or (not directory and info.links!=1):
            raise OSError('Unsafe storage type or alias')
        owner=c.c_void_p();dacl=c.c_void_p();sd=c.c_void_p()
        code=self.a.GetSecurityInfo(handle,1,5,c.byref(owner),None,c.byref(dacl),None,c.byref(sd))
        if code:raise c.WinError(code)
        try:
            if not owner or self._sid_string(owner)!=self.sid or not dacl:
                raise OSError('Unsafe storage owner or ACL')
            count=c.cast(dacl,c.POINTER(c.c_ushort))[2]
            effective=False
            for index in range(count):
                ace=c.c_void_p();self._ok(self.a.GetAce(dacl,index,c.byref(ace)))
                head=c.cast(ace,c.POINTER(c.c_ubyte))
                if head[0]!=0 or self._sid_string(ace.value+8)!=self.sid:
                    raise OSError('Storage grants access to another principal')
                mask=c.cast(ace.value+4,c.POINTER(w.DWORD))[0]
                effective |= not bool(head[1]&8) and mask & 0x1f01ff == 0x1f01ff
            if not effective:raise OSError('Storage owner lacks required access')
        finally:self.k.LocalFree(sd)

    def check(self,path,directory=False):
        h=self._open(path,directory=directory)
        try:self.check_handle(h,directory)
        finally:self.k.CloseHandle(h)

    def move(self,source,target,replace=False):
        self.validate(source);self.validate(target)
        if source.drive.casefold()!=target.drive.casefold():raise OSError('Cross-volume storage move rejected')
        self._ok(self.k.MoveFileExW(str(source),str(target),8 | (1 if replace else 0)))

    def mkdir(self,path):
        self.validate(path)
        temp=path.with_name('.finvis-'+uuid4().hex+'.pending')
        sd,sa=self._security()
        try:self._ok(self.k.CreateDirectoryW(str(temp),self.c.byref(sa)))
        finally:self.k.LocalFree(sd)
        self.check(temp,True)
        self.move(temp,path)
        self.check(path,True)

    def exclusive_fd(self,path):
        import msvcrt
        h=self._open(path,0xc0020000,0,1,True)
        try:
            self.check_handle(h)
            fd=msvcrt.open_osfhandle(h,os.O_RDWR|os.O_BINARY)
        except BaseException:self.k.CloseHandle(h);raise
        return fd

    def lock(self,path,create,blocking):
        while True:
            try:
                h=self._open(path,0xc0020000,0,4 if create else 3,True)
                try:self.check_handle(h)
                except BaseException:self.k.CloseHandle(h);raise
                return h
            except BlockingIOError:
                if not blocking:raise
                time.sleep(.05)


_WIN = None

def _windows():
    global _WIN
    if _WIN is None:_WIN=_Windows()
    return _WIN


def application_directory(name):
    return (_windows().appdata()/name if WINDOWS else Path.home()/'Library'/'Application Support'/name)


def check_private(path,directory=False):
    path=Path(path)
    if WINDOWS:return _windows().check(path,directory)
    info=path.lstat()
    if info.st_uid!=os.getuid() or info.st_mode&0o077 or not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)):
        raise OSError('Unsafe storage ownership/type/permissions')


def _posix_sync_directory(path):
    fd=os.open(str(path),os.O_RDONLY)
    try:os.fsync(fd)
    finally:os.close(fd)


def private_mkdir(path):
    path=Path(path)
    if path!=path.resolve():raise OSError('Aliased storage directory')
    if not WINDOWS:
        created=[];parent=path
        while not parent.exists():created.append(parent);parent=parent.parent
        path.mkdir(mode=0o700,parents=True,exist_ok=True);check_private(path,True)
        for directory in reversed(created):_posix_sync_directory(directory.parent)
        return
    if path.exists():check_private(path,True);return
    if not path.parent.exists():private_mkdir(path.parent)
    _windows().mkdir(path)


def exclusive_fd(path):
    path=Path(path)
    if WINDOWS:return _windows().exclusive_fd(path)
    return os.open(str(path),os.O_CREAT|os.O_EXCL|os.O_RDWR|os.O_NOFOLLOW,0o600)


def durable_move(source,target,replace=False):
    source,target=Path(source),Path(target)
    if WINDOWS:
        _windows().move(source,target,replace)
    else:
        if replace:os.replace(source,target)
        else:
            if target.exists() or target.is_symlink():raise FileExistsError(str(target))
            os.rename(source,target)
        _posix_sync_directory(target.parent)
        if source.parent!=target.parent:_posix_sync_directory(source.parent)


def durable_write(path,data,replace=False):
    """Publish private metadata; failures remain visible for fail-closed recovery."""
    path=Path(path)
    if replace and (path.exists() or path.is_symlink()):check_private(path)
    if not WINDOWS and not replace:
        # Preserve the original POSIX O_EXCL initialization gate, including
        # a visible incomplete file on failure. A check followed by rename
        # would allow two first-run hosts to replace each other's identity.
        with os.fdopen(exclusive_fd(path),'wb') as out:
            out.write(data);out.flush();os.fsync(out.fileno())
        _posix_sync_directory(path.parent)
        check_private(path)
        return
    temp=path.with_name('.finvis-'+uuid4().hex+'.pending')
    fd=exclusive_fd(temp)
    with os.fdopen(fd,'wb') as out:
        out.write(data);out.flush();os.fsync(out.fileno())
    durable_move(temp,path,replace)
    check_private(path)


def durable_empty_file(path):
    durable_write(path,b'')


def sync_file(path):
    """Flush an existing regular file; Windows os.fsync maps to _commit."""
    path=Path(path);check_private(path)
    if WINDOWS:
        h=_windows()._open(path,0xc0020000,7)
        try:_windows().check_handle(h);_windows()._ok(_windows().k.FlushFileBuffers(h))
        finally:_windows().k.CloseHandle(h)
    else:
        fd=os.open(str(path),os.O_RDONLY|os.O_NOFOLLOW)
        try:os.fsync(fd)
        finally:os.close(fd)
        _posix_sync_directory(path.parent)


def acquire_lock(path,create=True,blocking=False):
    path=Path(path)
    if WINDOWS:return ('windows',_windows().lock(path,create,blocking))
    import fcntl
    fd=os.open(str(path),os.O_RDWR|os.O_NOFOLLOW|(os.O_CREAT if create else 0),0o600)
    try:
        check_private(path)
        fcntl.flock(fd,fcntl.LOCK_EX|(0 if blocking else fcntl.LOCK_NB))
        return ('posix',fd)
    except BaseException:os.close(fd);raise


def release_lock(lease):
    platform,handle=lease
    if platform=='windows':_windows()._ok(_windows().k.CloseHandle(handle))
    else:
        import fcntl
        try:fcntl.flock(handle,fcntl.LOCK_UN)
        finally:os.close(handle)


def sqlite_supported(version):
    return version>=(3,51,3) or (3,50,7)<=version<(3,51,0) or (3,44,6)<=version<(3,45,0)

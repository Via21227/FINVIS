"""One Core, private persistent competition instances. No model calls or seed data."""
import argparse
import errno
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import signal
import socket
import stat
import subprocess
import tempfile
import sys
import sqlite3
import urllib.request
from uuid import uuid4
from threading import Thread

from .platform_storage import (WINDOWS, private_mkdir, check_private, durable_write,
    acquire_lock, release_lock, sqlite_supported)
from .persistent_world import StorageLocation, competition_directory
from .user_runtime import storage_scope
from .web import DemoServer, WEB_ROOT, source_fingerprint

CHROME = Path('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')


def private_dir(path):
    try:private_mkdir(path)
    except OSError:raise ValueError('Unsafe competition directory permissions') from None


def read_json(path):
    try:check_private(path)
    except FileNotFoundError:raise
    except OSError:raise ValueError('Unsafe competition manifest') from None
    return json.loads(path.read_text(encoding='utf-8'))


def write_json(path, value):
    if path.exists() or path.is_symlink():read_json(path)
    durable_write(path,(json.dumps(value,indent=2)+'\n').encode('utf-8'),replace=True)


class Registry:
    def __init__(self, test_root=None):
        self.testing = test_root is not None
        self.root = Path(test_root) if self.testing else competition_directory()
        if self.testing:
            StorageLocation.temporary(self.root/'path-validation')
        private_dir(self.root)
        private_dir(self.root/'instances')

    @contextmanager
    def locked(self):
        lease=acquire_lock(self.root/'registry.lock',create=True,blocking=True)
        try:yield
        finally:release_lock(lease)

    def load(self, identity):
        if not isinstance(identity, str) or not re.fullmatch(r'[0-9a-f]{32}', identity):
            raise ValueError('Invalid instance identity')
        directory = self.root/'instances'/identity
        if directory != directory.resolve(): raise ValueError('Symlinked instance')
        value = read_json(directory/'instance.json')
        if set(value) != {'schema', 'id', 'port', 'created_at'} or value['schema'] != 1 or value['id'] != identity or type(value['port']) is not int or not 1024 <= value['port'] <= 65535:
            raise ValueError('Invalid instance manifest')
        return value

    def choose(self, *, new=False, identity=None):
        with self.locked():
            current = self.root/'current.json'
            if identity:
                value = self.load(identity)
            elif not new and current.exists():
                selected = read_json(current)
                if set(selected) != {'instance'}: raise ValueError('Invalid current manifest')
                value = self.load(selected['instance'])
            else:
                if not new and any((self.root/'instances').iterdir()):
                    raise ValueError('Current instance missing; choose --instance explicitly')
                used = {self.load(p.name)['port'] for p in (self.root/'instances').iterdir()}
                for _ in range(64):
                    with socket.socket() as sock:
                        sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
                    if port not in used: break
                else: raise ValueError('No unreserved instance port')
                identity = uuid4().hex
                value = {'schema': 1, 'id': identity, 'port': port, 'created_at': datetime.now(timezone.utc).isoformat()}
                private_dir(self.root/'instances'/identity)
                for name in ('state', 'browser', 'logs', 'backups', 'tmp'):
                    private_dir(self.root/'instances'/identity/name)
                write_json(self.root/'instances'/identity/'instance.json', value)
            write_json(current, {'instance': value['id']})
            return value

    def paths(self, value):
        directory = self.root/'instances'/value['id']
        # Never silently recreate a missing initialized World: UserRuntime owns recovery.
        for name in ('state', 'browser', 'logs', 'backups', 'tmp'):
            if not (directory/name).is_dir(): raise ValueError('Instance directory missing; no reset performed')
            private_dir(directory/name)
        location = (StorageLocation.temporary(directory/'state'/'UserWorld') if self.testing
                    else StorageLocation.competition(value['id']))
        return directory, location


def compatible_health(value, location, health):
    stamp = source_fingerprint()
    return (health.get('app') == 'finvis-phase3' and health.get('root') == str(WEB_ROOT.parent)
            and health.get('user_storage') == 'durable-p2'
            and health.get('storage_scope') == storage_scope(location)
            and health.get('competition_id') == value['id']
            and health.get('source_fingerprint') == health.get('source_current') == stamp)


def existing_health(port):
    # Never follow redirects or environment proxies to another service.
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs): return None
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    try:
        with opener.open(f'http://127.0.0.1:{port}/api/health', timeout=2) as response:
            return json.loads(response.read(8192))
    except Exception:
        return None


def browser_environment(directory):
    # Credentials/server toggles must never be inherited by the browser process.
    allowed = ('HOME', 'USER', 'LOGNAME', 'PATH', 'LANG', 'LC_ALL', 'DISPLAY', '__CF_USER_TEXT_ENCODING',
               'SystemRoot', 'WINDIR', 'USERPROFILE', 'LOCALAPPDATA', 'APPDATA', 'PROGRAMFILES', 'PROGRAMFILES(X86)')
    return {**{k: os.environ[k] for k in allowed if k in os.environ}, 'TMPDIR': str(directory/'tmp'), 'TMP': str(directory/'tmp'), 'TEMP': str(directory/'tmp')}


def chrome_path():
    if not WINDOWS:return CHROME
    candidates=[Path(os.environ[name])/'Google'/'Chrome'/'Application'/'chrome.exe'
                for name in ('PROGRAMFILES','PROGRAMFILES(X86)','LOCALAPPDATA') if os.environ.get(name)]
    for path in candidates:
        if path.is_absolute() and path.is_file():return path
    raise ValueError('Chrome unavailable')


def open_browser(directory, url):
    chrome=chrome_path()
    if not chrome.is_file(): raise ValueError('Chrome is required for an isolated browser profile')
    subprocess.Popen([str(chrome), '--user-data-dir='+str(directory/'browser'), '--no-first-run',
                      '--no-default-browser-check', '--disable-sync', '--disable-background-networking',
                      '--disable-breakpad', url], env=browser_environment(directory),
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=not WINDOWS)


def main():
    parser = argparse.ArgumentParser(description='FINVIS Competition Demo — independent persistent instances')
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--new', action='store_true', help='Create a fresh instance; preserve all previous instances')
    group.add_argument('--instance', help='Resume a listed instance')
    group.add_argument('--list', action='store_true', help='List instance identities without financial contents')
    parser.add_argument('--no-browser', action='store_true')
    parser.add_argument('--test-root', type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    server = None
    thread = None
    try:
        if sys.version_info < (3,9):raise ValueError('Python 3.9 or newer is required')
        if not sqlite_supported(sqlite3.sqlite_version_info):raise ValueError('SQLite WAL correction unavailable')
        os.umask(0o077)
        registry = Registry(args.test_root)
        if args.list:
            with registry.locked():
                for path in sorted((registry.root/'instances').iterdir()):
                    item = registry.load(path.name)
                    print(item['id'], item['created_at'])
            return 0
        if not args.no_browser and not chrome_path().is_file(): raise ValueError('Chrome unavailable')
        value = registry.choose(new=args.new, identity=args.instance)
        directory, location = registry.paths(value)
        url = f"http://127.0.0.1:{value['port']}"
        health = existing_health(value['port'])
        if health is not None:
            if not compatible_health(value, location, health): raise ValueError('Port occupied or stale source; stop the owning instance first')
            if not args.no_browser: open_browser(directory, url)
            print('比赛版已运行：'+url, flush=True)
            return 0
        os.environ['TMPDIR'] = str(directory/'tmp')
        os.environ['TMP'] = os.environ['TEMP'] = str(directory/'tmp')
        tempfile.tempdir = str(directory/'tmp')
        server = DemoServer(('127.0.0.1', value['port']), user_location=location, competition_id=value['id'])
        if server.user_runtime.state not in ('first_run', 'ready'):
            raise ValueError('Saved competition state cannot be verified; no reset performed')
        thread = Thread(target=server.serve_forever, kwargs={'poll_interval': 0.2}, daemon=True)
        thread.start()
        if not compatible_health(value, location, existing_health(value['port']) or {}):
            raise ValueError('Competition readiness failed')
        if not args.no_browser: open_browser(directory, url)
        print('FINVIS Competition Demo: '+url+'\n实例：'+value['id']+'\n数据：'+str(directory)+'\n保留此窗口；Control+C 停止服务。已保存的数据不会重置。', flush=True)
        signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
        while thread.is_alive(): thread.join(0.5)
    except KeyboardInterrupt:
        print('比赛服务已停止；已保存的数据保留。', flush=True)
    except Exception as error:
        # No exception body, environment or submitted financial text is logged.
        messages = {
            'Port occupied or stale source; stop the owning instance first': '此实例地址已有其他服务或旧版比赛服务。请先停止所属服务，再重新启动；不会换端口。',
            'Python 3.9 or newer is required': '需要 Python 3.9 或更新版本。',
            'SQLite WAL correction unavailable': 'Python 内置 SQLite 需要已修复版本：3.51.3+、3.50.7–3.50.x 或 3.44.6–3.44.x。未创建比赛数据。',
            'Chrome unavailable': '需要 Google Chrome 来使用隔离的比赛浏览器配置。',
            'Instance directory missing; no reset performed': '比赛实例目录缺失。为保护已有数据，启动已停止；可用 --new 另建实例。',
            'Current instance missing; choose --instance explicitly': '当前实例记录缺失。请用 --list 查看，再用 --instance 选择；不会自动重置。',
        }
        message = messages.get(str(error), '请检查实例路径、权限或保存状态；未重置数据。')
        if isinstance(error, OSError) and error.errno == errno.EADDRINUSE:
            message = '此实例端口已被占用。请先停止所属服务；不会切换端口或结束其他进程。'
        print('无法启动比赛版（'+type(error).__name__+'）：'+message, flush=True)
        return 1
    finally:
        if server is not None:
            if thread is not None:
                server.shutdown(); thread.join(5)
            server.server_close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

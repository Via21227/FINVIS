"""Every HTTP test gets a non-production P2 data root, retained for inspection."""
from pathlib import Path
import tempfile
from finvis.web import DemoServer as RealServer
from finvis.user_runtime import runtime_location


def DemoServer(address=('127.0.0.1', 0), app=None):
    root = Path(tempfile.mkdtemp(prefix='finvis-p2-http-')).resolve()
    return RealServer(address, app, user_location=runtime_location(root))

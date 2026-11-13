"""Small filesystem primitives for local Codex coordination."""

import os
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

from .store import ResearchError


@contextmanager
def file_lock(path: Path, *, timeout: float = 3):
    """Use a kernel-released lock, so process exit leaves no stale lease."""
    with path.open("a+b", buffering=0) as lock:
        if os.name == "nt":
            import msvcrt

            def change(acquire):
                # Windows byte-range locks can extend past EOF. Avoid racing to
                # write a marker while another process already owns byte zero.
                lock.seek(0)
                mode = msvcrt.LK_NBLCK if acquire else msvcrt.LK_UNLCK
                msvcrt.locking(lock.fileno(), mode, 1)
        else:
            import fcntl

            def change(acquire):
                mode = fcntl.LOCK_EX | fcntl.LOCK_NB if acquire else fcntl.LOCK_UN
                fcntl.flock(lock.fileno(), mode)

        deadline = time.monotonic() + timeout
        while True:
            try:
                change(True)
                break
            except OSError as error:
                if time.monotonic() >= deadline:
                    raise ResearchError(f"cannot lock {path.name}") from error
                time.sleep(0.025)
        try:
            yield
        finally:
            change(False)


def atomic_write(path: Path, data: bytes):
    """Publish a complete file in one replacement on the same filesystem."""
    handle, temp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(data)
        os.replace(temp, path)
    finally:
        Path(temp).unlink(missing_ok=True)


def publish_new(path: Path, data: bytes):
    """Publish fully written bytes without replacing an existing artifact."""
    handle, temp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temp, path)
    finally:
        Path(temp).unlink(missing_ok=True)

"""Transactional event log and immutable byte store. No target-project imports."""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


class ResearchError(ValueError):
    pass


def now():
    return datetime.now(timezone.utc).isoformat()


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(encode(value).encode("utf-8")).hexdigest()


def file_hash(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", value):
        raise ResearchError("identifier must contain 1..128 letters, digits, dots, underscores or hyphens")
    return value


def _sqlite_lock_conflict(error):
    code = getattr(error, "sqlite_errorcode", None)
    if code is not None:
        return (code & 255) in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}
    # Python 3.10 exposes SQLite's message without its numeric result code.
    return str(error).partition(":")[0] in {
        "database is locked", "database table is locked", "database schema is locked",
    }


class Store:
    def __init__(self, root, create=False, *, read_only=False):
        self.root = Path(root).resolve()
        if read_only and create:
            raise ResearchError("cannot create a read-only workspace")
        if create:
            self.root.mkdir(parents=True, exist_ok=True)
        self.db_path = self.inside("research.sqlite3")
        if not create and not self.db_path.is_file():
            raise ResearchError(f"workspace does not exist: {self.root}")
        self.db = sqlite3.connect(self.db_path.as_uri() + "?mode=ro", uri=True,
                                  timeout=30, isolation_level=None) if read_only else sqlite3.connect(
                                      self.db_path, timeout=30, isolation_level=None)
        self.pending = None
        self.db.row_factory = sqlite3.Row
        if read_only:
            return
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS records (
                kind TEXT NOT NULL, id TEXT NOT NULL, body TEXT NOT NULL,
                PRIMARY KEY(kind, id));
            CREATE TABLE IF NOT EXISTS events (
                seq INTEGER PRIMARY KEY, timestamp TEXT NOT NULL,
                operation TEXT NOT NULL, actor TEXT NOT NULL,
                previous TEXT NOT NULL, changes TEXT NOT NULL, hash TEXT NOT NULL);
            CREATE TRIGGER IF NOT EXISTS immutable_events_update BEFORE UPDATE ON events
                BEGIN SELECT RAISE(ABORT, 'events are append-only'); END;
            CREATE TRIGGER IF NOT EXISTS immutable_events_delete BEFORE DELETE ON events
                BEGIN SELECT RAISE(ABORT, 'events are append-only'); END;
        """)

    def close(self):
        self.db.close()

    @contextmanager
    def snapshot(self):
        """Keep multi-query reads consistent while other processes commit."""
        own = not self.db.in_transaction
        if own:
            self.db.execute("BEGIN")
        try:
            yield
        finally:
            if own:
                self.db.execute("ROLLBACK")

    @contextmanager
    def transaction(self, operation, actor, *, before_commit=None):
        if not isinstance(actor, str) or not actor.strip():
            raise ResearchError("actor is required")
        self.db.execute("BEGIN IMMEDIATE")
        self.pending = []
        try:
            yield self
            self._commit(operation, actor, before_commit)
        except BaseException:
            self.db.execute("ROLLBACK")
            raise
        finally:
            self.pending = None

    def _commit(self, operation, actor, before_commit=None, metrics=None):
        started = time.monotonic()
        if self.pending:
            last = self.db.execute("SELECT seq,hash FROM events ORDER BY seq DESC LIMIT 1").fetchone()
            event = {"seq": last["seq"] + 1 if last else 1, "timestamp": now(),
                     "operation": operation, "actor": actor, "previous": last["hash"] if last else "0" * 64,
                     "changes": self.pending}
            self.db.execute("INSERT INTO events VALUES (?,?,?,?,?,?,?)", (
                event["seq"], event["timestamp"], operation, actor, event["previous"],
                encode(self.pending), digest(event)))
        if metrics is not None:
            metrics["event_prepare_seconds"] = time.monotonic() - started
        # In particular, a seal that expires while serializing its event rolls
        # back all records and the event. Nothing can become sealed afterward.
        if before_commit:
            before_commit()
        started = time.monotonic()
        self.db.execute("COMMIT")
        if metrics is not None:
            metrics["commit_seconds"] = time.monotonic() - started

    @contextmanager
    def audited_transaction(self, operation, actor, *, guard, metrics, max_attempts=32):
        """Audit a WAL read snapshot, then upgrade that SAME snapshot to writer.

        No audit cache or tail-only comparison: a concurrent commit invalidates
        the upgrade (SQLITE_BUSY_SNAPSHOT), so we roll back and audit afresh.
        The no-op UPDATE acquires write ownership without changing projections.
        Only acquisition is retried; the caller's body is executed at most once.
        """
        if not isinstance(actor, str) or not actor.strip() or self.db.in_transaction:
            raise ResearchError("audited transaction requires actor and no active transaction")
        old_timeout = self.db.execute("PRAGMA busy_timeout").fetchone()[0]
        metrics.update(audit_seconds=0.0, lock_wait_seconds=0.0, retry_backoff_seconds=0.0,
                       attempts=0, audit_verify_bytes=False)
        self.db.execute("PRAGMA busy_timeout=50")
        locked_at = None
        try:
            for attempt in range(max_attempts):
                guard()
                self.db.execute("BEGIN")
                try:
                    metrics["attempts"] += 1
                    started = time.monotonic()
                    integrity = self.audit(verify_bytes=False)
                    metrics["audit_seconds"] += time.monotonic() - started
                    if not integrity["passed"]:
                        raise ResearchError("workspace integrity failed: " + "; ".join(integrity["errors"]))
                    guard()
                    started = time.monotonic()
                    try:
                        self.db.execute("UPDATE records SET body=body WHERE 0")
                    finally:
                        metrics["lock_wait_seconds"] += time.monotonic() - started
                    locked_at = time.monotonic()
                    metrics["audited_events"] = integrity["events"]
                    metrics["audited_tail_hash"] = integrity["tail_hash"]
                    guard()
                    break
                except sqlite3.OperationalError as exc:
                    self.db.execute("ROLLBACK")
                    if not _sqlite_lock_conflict(exc):
                        raise
                    guard()
                    if attempt == max_attempts - 1:
                        raise ResearchError("batch seal write acquisition exhausted bounded retries") from exc
                    delay = min(0.01 * (attempt + 1), 0.05)
                    time.sleep(delay)
                    metrics["retry_backoff_seconds"] += delay
                except BaseException:
                    self.db.execute("ROLLBACK")
                    raise
            self.pending = []
            try:
                yield self
                started = time.monotonic()
                self._commit(operation, actor, guard, metrics)
                metrics["event_and_commit_seconds"] = time.monotonic() - started
            except BaseException:
                if self.db.in_transaction:
                    self.db.execute("ROLLBACK")
                raise
            finally:
                self.pending = None
        finally:
            if locked_at is not None:
                metrics["write_lock_seconds"] = time.monotonic() - locked_at
            self.db.execute("PRAGMA busy_timeout=" + str(old_timeout))

    def get(self, kind, key):
        row = self.db.execute("SELECT body FROM records WHERE kind=? AND id=?", (kind, key)).fetchone()
        if row is None:
            raise ResearchError(f"missing {kind}: {key}")
        return json.loads(row[0])

    def exists(self, kind, key):
        return self.db.execute("SELECT 1 FROM records WHERE kind=? AND id=?", (kind, key)).fetchone() is not None

    def all(self, kind=None):
        if kind is None:
            rows = self.db.execute("SELECT kind,id,body FROM records ORDER BY kind,id")
            return [{"kind": r[0], "id": r[1], "value": json.loads(r[2])} for r in rows]
        return [json.loads(r[0]) for r in self.db.execute("SELECT body FROM records WHERE kind=? ORDER BY id", (kind,))]

    def put(self, kind, key, value, new=False):
        if self.pending is None:
            raise ResearchError("write outside transaction")
        identifier(key)
        if new and self.db.execute("SELECT 1 FROM records WHERE kind=? AND id=?", (kind, key)).fetchone():
            raise ResearchError(f"{kind} already exists: {key}")
        encoded = encode(value)
        self.db.execute("INSERT INTO records VALUES (?,?,?) ON CONFLICT(kind,id) DO UPDATE SET body=excluded.body",
                        (kind, key, encoded))
        self.pending.append({"kind": kind, "id": key, "value": json.loads(encoded)})

    def inside(self, relative):
        path = (self.root / relative).resolve()
        if not path.is_relative_to(self.root):
            raise ResearchError("workspace path escape")
        return path

    def blob(self, sha):
        if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{64}", sha):
            raise ResearchError("invalid SHA-256")
        return self.inside(Path("objects") / sha[:2] / sha)

    def freeze(self, source, *, max_bytes=None):
        """Copy and hash the bytes actually read; publish atomically without overwrites."""
        folder = self.inside("objects/.incoming")
        folder.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=folder)
        h, size = hashlib.sha256(), 0
        try:
            with os.fdopen(fd, "wb") as out, Path(source).open("rb") as src:
                for block in iter(lambda: src.read(1024 * 1024), b""):
                    if max_bytes is not None and size + len(block) > max_bytes:
                        raise ResearchError("artifact exceeds staging byte limit")
                    out.write(block)
                    h.update(block)
                    size += len(block)
                out.flush()
                os.fsync(out.fileno())
            sha = h.hexdigest()
            destination = self.blob(sha)
            destination.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.link(tmp, destination)
            except FileExistsError:
                if file_hash(destination) != sha:
                    raise ResearchError(f"corrupt existing object: {sha}")
            return {"sha256": sha, "size": size, "id": sha}
        finally:
            Path(tmp).unlink(missing_ok=True)

    def verify_blob(self, sha):
        record = self.get("artifact", sha)
        path = self.blob(sha)
        if not path.is_file() or path.stat().st_size != record["size"] or file_hash(path) != sha:
            raise ResearchError(f"artifact missing or changed: {sha}")
        return path

    def audit(self, verify_bytes=True):
        with self.snapshot():
            return self._audit(verify_bytes)

    def _audit(self, verify_bytes):
        errors, replay, previous, seq = [], {}, "0" * 64, 0
        for row in self.db.execute("SELECT * FROM events ORDER BY seq"):
            event = {k: row[k] for k in ("seq", "timestamp", "operation", "actor", "previous")}
            event["changes"] = json.loads(row["changes"])
            if row["seq"] != seq + 1 or row["previous"] != previous or digest(event) != row["hash"]:
                errors.append(f"event chain damaged at {row['seq']}")
            for change in event["changes"]:
                replay[(change["kind"], change["id"])] = change["value"]
            previous, seq = row["hash"], row["seq"]
        # A policy gate may run inside an atomic multi-record intake. Include
        # the transaction's legitimate pending changes, which are not events yet.
        for change in self.pending or []:
            replay[(change["kind"], change["id"])] = change["value"]
        actual = {(r["kind"], r["id"]): r["value"] for r in self.all()}
        if replay != actual:
            errors.append("projection differs from event replay")
        if verify_bytes:
            for artifact in self.all("artifact"):
                try:
                    self.verify_blob(artifact["id"])
                except ResearchError as exc:
                    errors.append(str(exc))
        return {"passed": not errors, "events": seq, "tail_hash": previous,
                "artifacts": len(self.all("artifact")), "errors": errors}

    def export_events(self):
        return [{**dict(r), "changes": json.loads(r["changes"])}
                for r in self.db.execute("SELECT * FROM events ORDER BY seq")]

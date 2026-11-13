"""Atomic, hash-bound external seal batches; no mathematical acceptance.

Preflight is read-only. Submit may leave unregistered content-addressed blobs
after a rejection; these are not artifacts or a seal, and are never deleted as
another concurrent task may have legitimately referenced the same bytes.
"""
import json
from pathlib import Path, PureWindowsPath
import re
import tempfile
import time

from . import external_deadline as deadline, research_return
from .literature import _verify_handoff
from .research import allowed_source
from .store import ResearchError, digest, encode, file_hash, now

MAX_ARTIFACTS = 1000


class SealPreflightError(ResearchError):
    def __init__(self, report):
        self.report = report
        super().__init__("batch seal preflight failed: " + encode(report["errors"]))


def _task(w, p, actor, *, submitting):
    task = w.store.get("task", p["task"])
    stages = {"seal"} if submitting else {"exploration", "seal"}
    if task["status"] != "running" or not task.get("external_deadline_policy") or task.get("external_stage") not in stages:
        raise ResearchError("batch seal requires a running managed task in " + "/".join(sorted(stages)))
    if actor not in {task["dispatched_by"], w.store.get("worker_literature", task["id"])["actor"]}:
        raise ResearchError("only worker or coordinator may preflight or submit seal")
    if p.get("contract_sha256") != task["contract_artifact"]:
        raise ResearchError("batch seal must bind the frozen contract_sha256")
    deadline.ensure_live(task)
    return task


def _path(w, task, name):
    # Do not resolve an aliased task/return root into another task and then
    # accidentally treat that other directory as the permitted boundary.
    root = w.store.root / "tasks" / task["id"] / "return"
    if root.resolve() != root:
        raise ResearchError("task return root cannot be a symlink or junction alias")
    if not isinstance(name, str) or not 0 < len(name) <= 1024 or Path(name).is_absolute() or PureWindowsPath(name).is_absolute():
        raise ResearchError("output path must be return-relative")
    path = (root / name).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise ResearchError("output must be an existing file within this task return directory")
    allowed_source(w, path)
    return path


def _inspect(w, p, actor, *, submitting=False):
    task = _task(w, p, actor, submitting=submitting)
    _verify_handoff(w, task)
    entries = p.get("artifacts")
    if not isinstance(entries, list) or not 0 < len(entries) <= MAX_ARTIFACTS:
        raise ResearchError(f"artifacts must contain 1..{MAX_ARTIFACTS} entries of {{path, sha256}}")
    errors, rows, paths, hashes = [], [], {}, {}
    excluded = {*task["inputs"], task["contract_artifact"], task["prompt_artifact"]}
    release = w.store.get("exploration_release", task["id"])["created_at"]
    for index, entry in enumerate(entries):
        try:
            if not isinstance(entry, dict):
                raise ResearchError("artifact entry must be an object")
            expected = entry.get("sha256")
            if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected):
                raise ResearchError("expected sha256 is required")
            path = _path(w, task, entry.get("path"))
            size = path.stat().st_size
            if task["output_limit_bytes"] is not None and size > task["output_limit_bytes"]:
                errors.append({"index": index, "code": "file_output_limit", "path": entry["path"], "bytes": size})
                continue
            actual = file_hash(path)
            row = {"path": str(path), "relative_path": entry["path"], "sha256": actual, "size": size}
            rows.append(row)
            if actual != expected:
                errors.append({"index": index, "code": "sha256_mismatch", "expected": expected, "actual": actual})
            if str(path) in paths:
                errors.append({"index": index, "code": "duplicate_path", "other_index": paths[str(path)]})
            if actual in hashes:
                errors.append({"index": index, "code": "duplicate_sha256", "other_index": hashes[actual], "sha256": actual})
            paths[str(path)], hashes[actual] = index, index
            if actual in excluded:
                errors.append({"index": index, "code": "original_input", "sha256": actual})
            if w.store.exists("artifact", actual):
                record = w.store.get("artifact", actual)
                w.store.verify_blob(actual)
                if record["imported_at"] < release:
                    errors.append({"index": index, "code": "historical_sha256", "sha256": actual,
                                   "first_imported_at": record["imported_at"]})
                own_root = w.store.root / "tasks" / task["id"] / "return"
                original_source = Path(record["source"]).resolve()
                allowed_actors = {task["dispatched_by"], w.store.get("worker_literature", task["id"])["actor"]}
                if not original_source.is_relative_to(own_root) or record["actor"] not in allowed_actors:
                    errors.append({"index": index, "code": "foreign_output_provenance", "sha256": actual,
                                   "first_actor": record["actor"], "first_source": record["source"]})
        except (OSError, ResearchError, ValueError) as exc:
            errors.append({"index": index, "code": "invalid_output", "error": str(exc)})
    if not errors and research_return.enabled(task):
        selected = {row["relative_path"]: row for row in rows}
        try:
            research_return.check_seal(task, {name: row["sha256"] for name, row in selected.items()},
                                       lambda name: json.loads(Path(selected[name]["path"]).read_text(encoding="utf-8-sig")),
                                       lambda aid: json.loads(w.store.verify_blob(aid).read_text(encoding="utf-8-sig")))
        except (OSError, ResearchError, TypeError, ValueError) as exc:
            errors.append({"code": "invalid_research_continuation", "error": str(exc)})
    total = sum(r["size"] for r in rows)
    manifest = {**deadline.bindings(task), "sealed_by": actor, "created_at": now(), "artifacts": [
        {"path": r["path"], "artifact_id": r["sha256"], "sha256": r["sha256"]} for r in rows]}
    manifest_size = len((encode(manifest) + "\n").encode("utf-8"))
    if task["output_limit_bytes"] is not None and total + manifest_size > task["output_limit_bytes"]:
        errors.append({"code": "total_output_limit", "bytes": total, "manifest_bytes": manifest_size,
                       "limit": task["output_limit_bytes"]})
    deadline.ensure_live(task)
    return {"passed": not errors, "task": task["id"], "bindings": deadline.bindings(task),
            "rows": rows, "output_bytes": total, "manifest": manifest,
            "manifest_bytes": manifest_size, "errors": errors}, task


def preflight(w, p, actor):
    started = time.monotonic()
    with w.store.snapshot():
        report, _ = _inspect(w, p, actor)
    report.update(read_only=True, staged=False, preflight_seconds=time.monotonic() - started,
                  integrity_audit="not performed by preflight; full chain/projection audit is mandatory on submit")
    return report


def _register(w, frozen, source, purpose, actor):
    key = frozen["id"]
    if not w.store.exists("artifact", key):
        w.store.put("artifact", key, {**frozen, "source": source, "purpose": purpose,
                                      "imported_at": now(), "actor": actor}, new=True)
    receipt_id = digest({"artifact": key, "source": source, "purpose": purpose})
    if not w.store.exists("import", receipt_id):
        w.store.put("import", receipt_id, {"id": receipt_id, "artifact": key, "source": source,
                                           "purpose": purpose, "imported_at": now(), "actor": actor}, new=True)


def submit(w, p, actor):
    metrics = {"started_at": now()}
    started = time.monotonic()
    try:
        # Unlike preflight, submit persists an already-expired task's terminal
        # failure independently of this operation's eventual rollback.
        if deadline.expire(w, p["task"]):
            raise ResearchError("external phase deadline exceeded; late outputs are archive-only")
        with w.store.snapshot():
            report, task = _inspect(w, p, actor, submitting=True)
        metrics["preflight_seconds"] = time.monotonic() - started
        if not report["passed"]:
            raise SealPreflightError(report)
        frozen = []
        staging_started = time.monotonic()
        for row in report["rows"]:
            deadline.ensure_live(task)
            entry = w.store.freeze(row["path"], max_bytes=task["output_limit_bytes"])
            if entry["sha256"] != row["sha256"] or entry["size"] != row["size"]:
                raise ResearchError("output changed while staging: " + row["relative_path"])
            frozen.append(entry)
        manifest = report["manifest"]
        manifest_bytes = (encode(manifest) + "\n").encode("utf-8")
        if task["output_limit_bytes"] is not None and report["output_bytes"] + len(manifest_bytes) > task["output_limit_bytes"]:
            raise ResearchError("batch outputs plus seal manifest exceed output_limit_bytes")
        incoming = w.store.inside("objects/.incoming")
        incoming.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=incoming, delete=False) as f:
            manifest_path = Path(f.name)
            f.write(manifest_bytes)
        try:
            manifest_frozen = w.store.freeze(manifest_path, max_bytes=task["output_limit_bytes"])
        finally:
            manifest_path.unlink(missing_ok=True)
        metrics["staging_seconds"] = time.monotonic() - staging_started
        metrics["output_bytes"] = report["output_bytes"]
        metrics["manifest_bytes"] = len(manifest_bytes)
        # Guard the original unsealed task even after our pending projection
        # becomes sealed; ensure_live on that projection would skip the deadline.
        guard = lambda: deadline.ensure_live(task)
        with w.store.audited_transaction("task.submit_seal_batch", actor, guard=guard, metrics=metrics):
            current, live = _inspect(w, p, actor, submitting=True)
            if not current["passed"]:
                raise SealPreflightError(current)
            if current["rows"] != report["rows"] or current["bindings"] != report["bindings"]:
                raise ResearchError("batch sources or bindings changed before commit")
            # Rehash the actual pinned object bytes under the final snapshot.
            # The full audit above verifies all events/projections, not every
            # unrelated object; handoff/selected outputs are byte-checked here.
            for entry in [*frozen, manifest_frozen]:
                path = w.store.blob(entry["id"])
                if path.stat().st_size != entry["size"] or file_hash(path) != entry["id"]:
                    raise ResearchError("staged object changed before commit")
            for entry, row in zip(frozen, report["rows"]):
                _register(w, entry, row["path"], task["id"] + ": batch sealed output", actor)
            _register(w, manifest_frozen, str(w.store.blob(manifest_frozen["id"])),
                      task["id"] + ": batch seal manifest", actor)
            artifacts = [r["sha256"] for r in report["rows"]]
            # Existing gate supplies exact contract/candidate/input/freshness
            # checks and produces the same external_seal receipt shape.
            result = deadline.submit_seal(w, {"task": task["id"], "artifact_ids": artifacts,
                                             "seal_manifest_artifact": manifest_frozen["id"]}, actor)
            guard()
        metrics.update(finished_at=now(), total_seconds=time.monotonic() - started)
        result["batch_metrics"] = metrics
        return result
    except (ResearchError, OSError):
        # No failed submit can roll back its overdue terminal state. This does
        # not grant any extra time and never turns failed tasks into completed.
        deadline.expire(w, p["task"])
        raise

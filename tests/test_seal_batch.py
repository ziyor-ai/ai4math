import copy
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time

import pytest

from ai4math import external_deadline as deadline
from ai4math.cli import main
from ai4math.execution import start_external
from ai4math.seal_batch import SealPreflightError
from ai4math.store import ResearchError, digest, encode, file_hash, now
from ai4math.workbench import Workbench
from test_strategy import node, route, decide, coordinator_literature, worker_receipt


@pytest.fixture
def workspace(tmp_path):
    w = Workbench.create(tmp_path / "workspace", "batch", "Batch fixture")
    node(w, "a")
    w.apply("goal.set", {"node": "a"})
    route(w, "a", "a", max_runs=10)
    decide(w, "direction", "switch", "a")
    coordinator_literature(w, "direction", "a")
    yield w
    w.store.close()


def managed(w, key="managed", phase="seal", count=3, route_id="a"):
    w.apply("task.add", {"id": key, "route": route_id, "role": "explore", "argv": [sys.executable, "-c", "pass"],
        "timeout_seconds": 240, "output_limit_bytes": 2000000,
        "phase_budgets": dict.fromkeys(deadline.PHASES, 60), "external_supervision": "host"})
    dispatch = start_external(w, key)
    ready = w.apply("task.literature_ready", worker_receipt(w, key, dispatch), "worker")
    release = {"task": key, "worker_receipt_sha256": ready["worker_receipt_sha256"], "assessment": "Reviewed fixture"}
    if phase != "review":
        w.apply("task.release_exploration", release)
    if phase == "seal":
        w.apply("task.begin_seal", {"task": key}, "worker")
    root = w.store.inside("tasks/" + key + "/return")
    root.mkdir(exist_ok=True)
    rows = []
    for i in range(count):
        path = root / (str(i) + ".txt")
        path.write_text(key + ":" + str(i) + ":" + "x" * 100, encoding="utf-8")
        rows.append({"path": path.name, "sha256": file_hash(path)})
    return {"task": key, "contract_sha256": dispatch["contract_sha256"], "artifacts": rows}, release


def tail(w):
    return w.store.db.execute("SELECT count(*) FROM events").fetchone()[0]


def test_readonly_preflight_then_one_atomic_event_and_old_finish(workspace):
    w = workspace
    p, _ = managed(w)
    before = tail(w)
    objects = set(w.store.root.glob("objects/*/*"))
    report = w.apply("task.preflight_seal_batch", p, "worker")
    assert report["passed"] and report["read_only"] and not report["staged"]
    assert tail(w) == before and set(w.store.root.glob("objects/*/*")) == objects
    result = w.apply("task.submit_seal_batch", p, "worker")
    assert tail(w) == before + 1
    assert result["external_stage"] == "sealed" and result["acceptance"] == "pending"
    assert result["batch_metrics"]["attempts"] == 1
    assert all(result["batch_metrics"][k] >= 0 for k in ("audit_seconds", "lock_wait_seconds", "write_lock_seconds", "commit_seconds"))
    task = w.store.get("task", "managed")
    assert task["status"] == "running" and task["stop_state"] == "requested"
    with pytest.raises(ResearchError):
        w.apply("task.submit_seal_batch", p, "worker")
    w.apply("task.confirm_stop", {"task": "managed", "confirmed": True, "evidence": "fixture contains no process"})
    done = w.apply("task.finish_external", {"task": "managed", "status": "completed", "summary": "Reviewed",
        "artifact_ids": result["receipt"]["output_artifacts"], "seal_manifest_artifact": result["receipt"]["artifact_id"]})
    assert done["evidence_level"] == "unknown" and w.audit()["passed"]


def test_all_conflicts_return_together_without_import(workspace):
    w = workspace
    p, _ = managed(w)
    task = w.store.get("task", "managed")
    root = w.store.inside("tasks/managed/return")
    old = task["inputs"][0]
    (root / "old.txt").write_bytes(w.store.verify_blob(old).read_bytes())
    p["artifacts"] += [{"path": "old.txt", "sha256": old}, p["artifacts"][0],
                        {"path": "../input.json", "sha256": task["contract_artifact"]},
                        {"path": "1.txt", "sha256": "0" * 64}]
    before = tail(w)
    objects = set(w.store.root.glob("objects/*/*"))
    report = w.apply("task.preflight_seal_batch", p, "worker")
    assert {e["code"] for e in report["errors"]} >= {"original_input", "historical_sha256", "duplicate_path", "duplicate_sha256", "invalid_output", "sha256_mismatch"}
    with pytest.raises(SealPreflightError) as error:
        w.apply("task.submit_seal_batch", p, "worker")
    assert len(error.value.report["errors"]) == len(report["errors"])
    assert tail(w) == before and set(w.store.root.glob("objects/*/*")) == objects


def test_output_limit_includes_manifest_and_preflight_cli_is_readonly(workspace, tmp_path, capsys):
    w = workspace
    p, _ = managed(w)
    path = w.store.inside("tasks/managed/return/0.txt")
    path.write_bytes(b"a" * 2000000)
    p["artifacts"] = [{"path": "0.txt", "sha256": file_hash(path)}]
    before = tail(w)
    spec = tmp_path / "batch.json"
    spec.write_text(json.dumps(p), encoding="utf-8")
    assert main(["--workspace", str(w.store.root), "--actor", "worker", "seal-external", "managed", "--file", str(spec)]) == 2
    report = json.loads(capsys.readouterr().out)
    assert any(e["code"] == "total_output_limit" for e in report["errors"])
    assert tail(w) == before


@pytest.mark.parametrize("change", ["source", "object"])
def test_changed_staged_bytes_cannot_commit(workspace, monkeypatch, change):
    w = workspace
    p, _ = managed(w)
    before = tail(w)
    freeze = w.store.freeze
    def altered(source, **kwargs):
        result = freeze(source, **kwargs)
        if Path(source).name == "0.txt":
            target = Path(source) if change == "source" else w.store.blob(result["id"])
            target.write_bytes(b"changed")
        return result
    monkeypatch.setattr(w.store, "freeze", altered)
    with pytest.raises(ResearchError):
        w.apply("task.submit_seal_batch", p, "worker")
    assert tail(w) == before and not w.store.exists("external_seal", "managed")
    assert not any(w.store.exists("artifact", e["sha256"]) for e in p["artifacts"])


@pytest.mark.parametrize("where", ["audit", "commit"])
def test_expiry_during_batch_rolls_back_all_outputs_but_persists_failure(workspace, monkeypatch, where):
    w = workspace
    p, _ = managed(w)
    stamp = w.store.get("task", "managed")["phase_timing"]["seal"]["deadline_at"]
    if where == "audit":
        original = w.store.audit
        def auditing(*args, **kwargs):
            result = original(*args, **kwargs)
            monkeypatch.setattr(deadline, "now", lambda: stamp)
            return result
        monkeypatch.setattr(w.store, "audit", auditing)
    else:
        import ai4math.store as store
        original = store.digest
        def hashing(value):
            result = original(value)
            if isinstance(value, dict) and value.get("operation") == "task.submit_seal_batch":
                monkeypatch.setattr(deadline, "now", lambda: stamp)
            return result
        monkeypatch.setattr(store, "digest", hashing)
    with pytest.raises(ResearchError, match="deadline exceeded"):
        w.apply("task.submit_seal_batch", p, "worker")
    assert w.store.get("task", "managed")["status"] == "unknown_timeout"
    assert not w.store.exists("external_seal", "managed")
    assert not any(w.store.exists("artifact", e["sha256"]) for e in p["artifacts"])
    assert w.store.audit()["passed"]


def test_preflight_expired_is_readonly(workspace, monkeypatch):
    w = workspace
    p, _ = managed(w)
    task = w.store.get("task", "managed")
    monkeypatch.setattr(deadline, "now", lambda: task["phase_timing"]["seal"]["deadline_at"])
    before = tail(w)
    with pytest.raises(ResearchError):
        w.apply("task.preflight_seal_batch", p, "worker")
    assert tail(w) == before and w.store.get("task", "managed") == task


def test_entry_limit_and_aliased_return_root(workspace, tmp_path):
    w = workspace
    p, _ = managed(w, count=0)
    p["artifacts"] = [{"path": "missing", "sha256": "0" * 64}] * 1001
    before = tail(w)
    with pytest.raises(ResearchError, match="1..1000"):
        w.apply("task.preflight_seal_batch", p, "worker")
    root = w.store.root / "tasks/managed/return"
    root.rmdir()
    other = w.store.root / "tasks/other/return"
    other.mkdir(parents=True)
    (other / "x").write_bytes(b"other task source")
    if os.name == "nt":
        result = subprocess.run(["cmd", "/c", "mklink", "/J", str(root), str(other)], capture_output=True)
        assert result.returncode == 0
    else:
        root.symlink_to(other, target_is_directory=True)
    try:
        p["artifacts"] = [{"path": "x", "sha256": file_hash(other / "x")}]
        report = w.apply("task.preflight_seal_batch", p, "worker")
        assert not report["passed"] and "alias" in report["errors"][0]["error"]
        assert tail(w) == before
    finally:
        if os.name == "nt":
            root.rmdir()
        else:
            root.unlink()


def test_noop_update_really_owns_writer_and_stale_snapshot_rejected(workspace):
    a = workspace.store.db
    b = sqlite3.connect(workspace.store.db_path, isolation_level=None, timeout=0.01)
    try:
        a.execute("BEGIN")
        a.execute("SELECT count(*) FROM records").fetchone()
        a.execute("UPDATE records SET body=body WHERE 0")
        with pytest.raises(sqlite3.OperationalError):
            b.execute("BEGIN IMMEDIATE")
        a.execute("ROLLBACK")
        a.execute("BEGIN")
        a.execute("SELECT count(*) FROM records").fetchone()
        b.execute("BEGIN IMMEDIATE")
        b.execute("UPDATE records SET body=? WHERE kind='project'", ('{"changed":true}',))
        b.execute("COMMIT")
        with pytest.raises(sqlite3.OperationalError) as error:
            a.execute("UPDATE records SET body=body WHERE 0")
        code = getattr(error.value, "sqlite_errorcode", None)
        if code is None:
            assert str(error.value) == "database is locked"
        else:
            assert code == sqlite3.SQLITE_BUSY_SNAPSHOT
        a.execute("ROLLBACK")
    finally:
        b.close()


def test_concurrent_snapshot_commit_forces_full_reaudit(workspace, monkeypatch):
    w = workspace
    p, _ = managed(w)
    other = Workbench(w.store.root)
    original = w.store.audit
    calls = []
    def auditing(*args, **kwargs):
        result = original(*args, **kwargs)
        calls.append(result["tail_hash"])
        if len(calls) == 1:
            with other.store.transaction("fixture.change", "fixture"):
                other.store.put("fixture", "change", {"id": "change"}, new=True)
        return result
    monkeypatch.setattr(w.store, "audit", auditing)
    try:
        result = w.apply("task.submit_seal_batch", p, "worker")
        assert result["batch_metrics"]["attempts"] == 2 and len(set(calls)) == 2
    finally:
        other.store.close()


def test_cross_task_first_import_race_is_not_fresh_own_output(parallel_workspace, monkeypatch):
    w = parallel_workspace
    a, _ = managed(w, "owner", route_id="a", count=1)
    b, _ = managed(w, "copier", route_id="b", count=1)
    source = w.store.inside("tasks/owner/return/0.txt")
    w.store.inside("tasks/copier/return/0.txt").write_bytes(source.read_bytes())
    b["artifacts"][0]["sha256"] = a["artifacts"][0]["sha256"]
    assert w.apply("task.preflight_seal_batch", b, "worker")["passed"]
    other = Workbench(w.store.root)
    audit = w.store.audit
    raced = []
    def auditing(*args, **kwargs):
        result = audit(*args, **kwargs)
        if not raced:
            raced.append(True)
            other.apply("task.submit_seal_batch", a, "worker")
        return result
    monkeypatch.setattr(w.store, "audit", auditing)
    try:
        with pytest.raises(SealPreflightError) as error:
            w.apply("task.submit_seal_batch", b, "worker")
        assert any(e["code"] == "foreign_output_provenance" for e in error.value.report["errors"])
        assert w.store.exists("external_seal", "owner") and not w.store.exists("external_seal", "copier")
    finally:
        other.store.close()


def test_full_audit_rejects_projection_corruption(workspace):
    w = workspace
    p, _ = managed(w)
    w.store.db.execute("UPDATE records SET body='{}' WHERE kind='project'")
    before = tail(w)
    with pytest.raises(ResearchError, match="integrity failed"):
        w.apply("task.submit_seal_batch", p, "worker")
    assert tail(w) == before and not w.store.exists("external_seal", "managed")


def test_partial_registration_error_rolls_back_every_record(workspace, monkeypatch):
    import ai4math.seal_batch as batch
    w = workspace
    p, _ = managed(w)
    before = tail(w)
    register = batch._register
    def failing(*args, **kwargs):
        register(*args, **kwargs)
        raise ResearchError("injected failure after first pending artifact")
    monkeypatch.setattr(batch, "_register", failing)
    with pytest.raises(ResearchError, match="injected"):
        w.apply("task.submit_seal_batch", p, "worker")
    assert tail(w) == before
    assert not any(w.store.exists("artifact", r["sha256"]) for r in p["artifacts"])
    assert not w.store.exists("external_seal", "managed")


def test_existing_own_output_preserves_original_import_time(workspace):
    w = workspace
    p, _ = managed(w)
    source = w.store.inside("tasks/managed/return/0.txt")
    old = w.apply("artifact.import", {"path": str(source), "purpose": "own fresh output"}, "worker")
    p.update(created_at="2000-01-01T00:00:00+00:00", deadline_at="2099-01-01T00:00:00+00:00")
    result = w.apply("task.submit_seal_batch", p, "worker")
    assert w.store.get("artifact", old["id"]) == old
    assert result["receipt"]["checked_at"] < "2099" and result["receipt"]["checked_at"] > "2000"


def test_busy_acquisition_has_bounded_retries(workspace):
    w = workspace
    other = sqlite3.connect(w.store.db_path, isolation_level=None)
    metrics = {}
    other.execute("BEGIN IMMEDIATE")
    try:
        with pytest.raises(ResearchError, match="bounded retries"):
            with w.store.audited_transaction("fixture.busy", "fixture", guard=lambda: None,
                                             metrics=metrics, max_attempts=3):
                raise AssertionError("must not enter writer body")
        assert metrics["attempts"] == 3 and not w.store.db.in_transaction
        assert w.store.db.execute("PRAGMA busy_timeout").fetchone()[0] == 30000
    finally:
        other.execute("ROLLBACK")
        other.close()


def test_staging_growth_is_bounded_and_corrupt_handoff_rejected(workspace):
    w = workspace
    p, _ = managed(w)
    huge = w.store.inside("tasks/managed/return/0.txt")
    with pytest.raises(ResearchError, match="byte limit"):
        w.store.freeze(huge, max_bytes=1)
    task = w.store.get("task", "managed")
    w.store.blob(task["contract_artifact"]).write_bytes(b"corrupt input")
    before = tail(w)
    with pytest.raises(ResearchError, match="artifact missing or changed"):
        w.apply("task.submit_seal_batch", p, "worker")
    assert tail(w) == before and not w.store.exists("external_seal", "managed")


def history(w, count=7240):
    """Synthetic valid history, only in pytest's isolated workspace."""
    last = w.store.db.execute("SELECT seq,hash FROM events ORDER BY seq DESC LIMIT 1").fetchone()
    seq, previous = last
    w.store.db.execute("BEGIN IMMEDIATE")
    for i in range(count):
        value = {"id": "history", "index": i, "padding": "x" * 1600}
        changes = [{"kind": "fixture", "id": "history", "value": value}]
        event = {"seq": seq + 1, "timestamp": now(), "operation": "fixture.history", "actor": "fixture",
                 "previous": previous, "changes": changes}
        previous = digest(event)
        w.store.db.execute("INSERT INTO events VALUES (?,?,?,?,?,?,?)", (seq + 1, event["timestamp"],
            event["operation"], event["actor"], event["previous"], encode(changes), previous))
        seq += 1
    w.store.db.execute("INSERT INTO records VALUES (?,?,?)", ("fixture", "history", encode(value)))
    w.store.db.execute("COMMIT")


@pytest.fixture
def parallel_workspace(tmp_path):
    w = Workbench.create(tmp_path / "workspace", "parallel-batch", "Independent concurrent fixtures")
    for key in ("a", "b", "c", "d", "e"):
        node(w, key)
    node(w, "goal", ["a", "b", "c", "d", "e"])
    w.apply("goal.set", {"node": "goal"})
    for key in ("a", "b", "c", "d", "e"):
        route(w, key, key)
    decide(w, "parallel", "switch", "a", parallel_routes=["b", "c", "d", "e"])
    coordinator_literature(w, "parallel", "a", "b", "c", "d", "e")
    yield w
    w.store.close()


def test_three_process_batches_200_outputs_and_control_with_large_history(parallel_workspace, tmp_path):
    w = parallel_workspace
    payloads = [managed(w, "batch" + str(i), count=200, route_id=key)[0] for i, key in enumerate(("a", "b", "c"))]
    managed(w, "control", phase="exploration", count=0, route_id="d")
    _, release = managed(w, "review", phase="review", count=0, route_id="e")
    history(w)
    assert w.store.audit()["passed"]
    barrier = tmp_path / "go"
    processes = []
    code = """import json,sys,time,pathlib
from ai4math.workbench import Workbench
root,spec,result,barrier=sys.argv[1:]
w=Workbench(root)
while not pathlib.Path(barrier).exists(): time.sleep(.01)
try:
 r=w.apply('task.submit_seal_batch',json.loads(pathlib.Path(spec).read_text()),'worker')
 pathlib.Path(result).write_text(json.dumps(r))
finally:w.store.close()
"""
    for i, payload in enumerate(payloads):
        spec = tmp_path / (str(i) + ".json")
        spec.write_text(json.dumps(payload), encoding="utf-8")
        result = tmp_path / (str(i) + "-result.json")
        process = subprocess.Popen([sys.executable, "-B", "-c", code, str(w.store.root), str(spec), str(result), str(barrier)],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        processes.append((process, result))
    started = time.monotonic()
    barrier.write_text("go")
    controls = []
    try:
        for operation, payload in [("task.begin_seal", {"task": "control"}), ("task.release_exploration", release)]:
            t = time.monotonic()
            w.apply(operation, payload)
            controls.append(time.monotonic() - t)
        t = time.monotonic()
        with w.store.transaction("fixture.expire", "fixture"):
            expired = w.store.get("task", "control")
            expired["phase_timing"]["seal"]["deadline_at"] = "2000-01-01T00:00:00+00:00"
            w.store.put("task", "control", expired)
        assert deadline.expire(w, "control")
        controls.append(time.monotonic() - t)
        results = []
        for process, path in processes:
            stdout, stderr = process.communicate(timeout=30)
            assert process.returncode == 0, (stdout, stderr)
            results.append(json.loads(path.read_text()))
        elapsed = time.monotonic() - started
        assert elapsed < 20 and max(controls) < 8
        assert all(r["external_stage"] == "sealed" and r["batch_metrics"]["audited_events"] > 7240 for r in results)
        assert max(r["batch_metrics"]["write_lock_seconds"] for r in results) < 3
        assert w.store.audit()["passed"]
        print(json.dumps({"three_process_elapsed_seconds": elapsed, "control_seconds": controls,
                          "batch_metrics": [r["batch_metrics"] for r in results]}))
    finally:
        for process, _ in processes:
            if process.poll() is None:
                process.kill()
                process.wait()

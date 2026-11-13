import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta

import pytest

from ai4math import external_deadline as deadline
from ai4math.execution import start_external
from ai4math.external_supervisor import OwnedProcess, supervise_external
from ai4math.store import ResearchError
from ai4math.workbench import Workbench
from test_strategy import node, route, decide, coordinator_literature, worker_receipt, literature_artifact, seal


def prepare(tmp_path, mode="host", budgets=None):
    w = Workbench.create(tmp_path / "workspace", "deadline-test", "Deadline fixture")
    node(w, "a")
    w.apply("goal.set", {"node": "a"})
    route(w, "a", "a")
    decide(w, "direction", "switch", "a")
    coordinator_literature(w, "direction", "a")
    budgets = budgets or dict.fromkeys(deadline.PHASES, 60)
    payload = {"id": "managed", "route": "a", "role": "explore", "argv": [sys.executable, "-c", "pass"],
               "timeout_seconds": sum(budgets.values()), "output_limit_bytes": 2000,
               "phase_budgets": budgets, "external_supervision": mode}
    w.apply("task.add", payload)
    return w, payload


def advance(w, dispatch, to="exploration"):
    ready = w.apply("task.literature_ready", worker_receipt(w, "managed", dispatch), "worker")
    release = {"task": "managed", "worker_receipt_sha256": ready["worker_receipt_sha256"], "assessment": "Checked fixture"}
    if to == "review":
        return release
    w.apply("task.release_exploration", release)
    if to == "seal":
        w.apply("task.begin_seal", {"task": "managed"})
    return release


def test_new_external_task_has_no_default_deadline(tmp_path):
    w, payload = prepare(tmp_path)
    try:
        data = {key: value for key, value in payload.items()
                if key not in {"phase_budgets", "timeout_seconds", "output_limit_bytes"}}
        task = w.apply("task.add", {**data, "id": "unbounded"})
        assert task["timeout_seconds"] is None and task["output_limit_bytes"] is None
        assert not task["external_deadline_policy"]["managed_codex_required"]
        start_external(w, "unbounded")
        dispatched = w.store.get("task", "unbounded")
        assert dispatched["external_deadline_at"] is None
        assert not deadline.overdue(dispatched)
        assert "deadline_at" not in dispatched["phase_timing"]["literature"]
    finally:
        w.store.close()


@pytest.mark.parametrize("phase", deadline.PHASES)
def test_expired_phase_persists_failure_even_when_operation_rejected(tmp_path, monkeypatch, phase):
    w, _ = prepare(tmp_path)
    try:
        dispatch = start_external(w, "managed")
        operation, actor = "task.literature_ready", "worker"
        payload = worker_receipt(w, "managed", dispatch)
        if phase != "literature":
            ready = w.apply(operation, payload, actor)
            operation, actor = "task.release_exploration", "human"
            payload = {"task": "managed", "worker_receipt_sha256": ready["worker_receipt_sha256"], "assessment": "Reviewed"}
        if phase in {"first_check", "seal"}:
            w.apply(operation, payload, actor)
            operation, payload = "task.begin_seal", {"task": "managed"}
        if phase == "seal":
            w.apply(operation, payload, actor)
            operation, payload = "task.finish_external", {"task": "managed", "status": "completed", "summary": "late", "artifact_ids": []}
        task = w.store.get("task", "managed")
        monkeypatch.setattr(deadline, "now", lambda: task["phase_timing"][phase]["deadline_at"])
        with pytest.raises(ResearchError, match="deadline exceeded"):
            w.apply(operation, payload, actor)
        failed = w.store.get("task", "managed")
        assert failed["status"] == "unknown_timeout" and failed["evidence_level"] == "unknown"
        assert failed["stop_state"] == "requested"
        assert w.store.get("route", "a")["runs"] == 1
        with pytest.raises(ResearchError):
            w.apply("task.finish_external", {"task": "managed", "status": "completed", "summary": "cannot revive", "artifact_ids": []})
        assert w.store.get("task", "managed")["status"] == "unknown_timeout"
        assert w.audit()["passed"]
    finally:
        w.store.close()


def test_budgets_are_explicit_exact_and_not_worker_clock(tmp_path, monkeypatch):
    w, payload = prepare(tmp_path)
    try:
        for change in ({"phase_budgets": None}, {"timeout_seconds": 10000}, {"external_supervision": "magic"}):
            with pytest.raises(ResearchError):
                w.apply("task.add", {**payload, "id": "bad", **change})
        dispatch = start_external(w, "managed")
        before = w.store.get("task", "managed")
        ready = w.apply("task.literature_ready", {**worker_receipt(w, "managed", dispatch),
            "started_at": "2099-01-01T00:00:00+00:00", "deadline_at": "2099-01-01T00:00:00+00:00"}, "worker")
        current = w.store.get("task", "managed")
        assert current["phase_timing"]["literature"] == before["phase_timing"]["literature"]
        assert current["external_deadline_at"] == before["external_deadline_at"]
        assert current["phase_timing"]["coordinator_review"]["started_at"] < "2099"
        with pytest.raises(ResearchError):
            w.apply("task.literature_ready", worker_receipt(w, "managed", dispatch), "worker")
    finally:
        w.store.close()


def test_hash_bound_timely_seal_separates_worker_stop_from_acceptance(tmp_path, monkeypatch):
    w, _ = prepare(tmp_path)
    try:
        dispatch = start_external(w, "managed")
        advance(w, dispatch)
        output = literature_artifact(w, "new-output", "worker")
        w.apply("task.begin_seal", {"task": "managed"}, "worker")
        with pytest.raises(ResearchError):
            w.apply("task.begin_seal", {"task": "managed"}, "worker")
        task = w.store.get("task", "managed")
        manifest = {**deadline.bindings(task), "artifacts": [{"artifact_id": output, "sha256": output}]}
        path = tmp_path / "seal.json"
        def freeze(data):
            path.write_text(json.dumps(data), encoding="utf-8")
            return w.apply("artifact.import", {"path": str(path), "purpose": "seal fixture"}, "worker")["id"]
        for altered in ({"candidate_digest": "wrong"}, {"input_artifact_ids": []},
                        {"artifacts": [{"artifact_id": output, "sha256": "0" * 64}]}):
            with pytest.raises(ResearchError):
                w.apply("task.submit_seal", {"task": "managed", "artifact_ids": [output],
                    "seal_manifest_artifact": freeze({**manifest, **altered})}, "worker")
        manifest_id = freeze(manifest)
        submission = {"task": "managed", "artifact_ids": [output], "seal_manifest_artifact": manifest_id}
        result = w.apply("task.submit_seal", submission, "worker")
        assert result["external_stage"] == "sealed" and result["acceptance"] == "pending"
        monkeypatch.setattr(deadline, "now", lambda: "2099-01-01T00:00:00+00:00")
        assert not deadline.expire(w, "managed")
        with pytest.raises(ResearchError, match="confirmed physical stop"):
            w.apply("task.finish_external", {**submission, "status": "completed", "summary": "Reviewed"})
        w.apply("task.confirm_stop", {"task": "managed", "confirmed": True, "evidence": "host interrupt tool acknowledged worker stop"})
        result = w.apply("task.finish_external", {**submission, "status": "completed", "summary": "Reviewed"})
        assert result["status"] == "completed" and result["evidence_level"] == "unknown"
        assert w.audit()["passed"]
    finally:
        w.store.close()


def test_transition_cannot_cross_deadline_during_verification(tmp_path, monkeypatch):
    from ai4math import literature
    w, _ = prepare(tmp_path)
    try:
        dispatch = start_external(w, "managed")
        payload = worker_receipt(w, "managed", dispatch)
        real_verify = literature._verify_handoff
        def slow_verify(w, task):
            real_verify(w, task)
            monkeypatch.setattr(deadline, "now", lambda: task["phase_timing"]["literature"]["deadline_at"])
        monkeypatch.setattr(literature, "_verify_handoff", slow_verify)
        with pytest.raises(ResearchError, match="deadline exceeded"):
            w.apply("task.literature_ready", payload, "worker")
        assert w.store.get("task", "managed")["status"] == "unknown_timeout"
        assert not w.store.exists("worker_literature", "managed")
    finally:
        w.store.close()


def test_process_supervisor_times_out_and_stops_worker_tree(tmp_path):
    w, _ = prepare(tmp_path, "process", dict.fromkeys(deadline.PHASES, 1))
    try:
        with pytest.raises(ResearchError, match="supervise-external"):
            start_external(w, "managed")
        marker = tmp_path / "escaped.txt"
        child = "import time,pathlib; time.sleep(2); pathlib.Path(" + repr(str(marker)) + ").write_text('alive')"
        worker = "import subprocess,sys,time; subprocess.Popen([sys.executable,'-c'," + repr(child) + "]); time.sleep(60)"
        result = supervise_external(w, "managed", [sys.executable, "-c", worker])
        assert result["status"] == "unknown_timeout" and result["stop_state"] == "confirmed"
        time.sleep(1.4)
        assert not marker.exists()
        assert w.audit()["passed"]
    finally:
        w.store.close()


def test_process_early_exit_is_failure_and_not_completed(tmp_path):
    w, _ = prepare(tmp_path, "process")
    try:
        result = supervise_external(w, "managed", [sys.executable, "-c", "pass"])
        assert result["status"] == "failed" and result["stop_state"] == "confirmed"
        assert "before registered seal" in result["failure_reason"]
    finally:
        w.store.close()

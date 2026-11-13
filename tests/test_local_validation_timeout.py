"""Local checker clock policy in disposable workspaces; no mathematical acceptance."""
import json
import sys
import time
from types import SimpleNamespace

import pytest

from ai4math import execution
from ai4math.store import ResearchError
from ai4math.workbench import Workbench


@pytest.fixture
def workspace(tmp_path):
    w = Workbench.create(tmp_path / "ws", "local-validator-clock", "Synthetic runtime tests")
    w.apply("node.add", {"id": "goal", "statement": "Synthetic target", "domain": "test only",
                         "quantifier": "universal"})
    w.apply("goal.set", {"node": "goal"})
    w.apply("route.add", {"id": "route", "node": "goal", "rationale": "runtime test",
                          "next_question": "clock policy?", "stop_condition": "one execution",
                          "max_runs": 1, "stagnation_limit": 1})
    w.apply("route.status", {"id": "route", "status": "active", "reason": "test only"})
    yield w
    w.store.close()


def checker_task(w, code, *, timeout=None, output_limit=4096, profile="trusted_local"):
    source = w.store.root / "checker.py"
    source.write_text(code, encoding="utf-8")
    checker = w.apply("artifact.import", {"path": str(source), "purpose": "synthetic checker"})["id"]
    payload = {"id": "check", "route": "route", "role": "validate",
               "argv": [sys.executable, "{validator}", "{input}"], "validator_artifact": checker,
               "inputs": [], "timeout_seconds": timeout, "output_limit_bytes": output_limit,
               "execution_profile": profile}
    if profile == "fresh_local_v1":
        payload["request_context"] = {"trial_id": "clock-test", "arm": "A", "request_id": "request-check"}
    return w.apply("task.add", payload, "independent-checker")


@pytest.mark.parametrize("profile", ["trusted_local", "fresh_local_v1"])
def test_null_local_validation_has_no_implicit_clock_and_preserves_contract(workspace, monkeypatch, profile):
    w = workspace
    task = checker_task(w, "import time; time.sleep(0.05); print('synthetic execution only')", profile=profile)
    exported = execution.export_task(w, "check")
    assert exported["contract"]["timeout_seconds"] is None
    if profile == "fresh_local_v1":
        assert task["request_identity"]["timeout_seconds"] is None
    # Model elapsed time far beyond ordinary default limits without delaying the test.
    ticks = iter(range(0, 10000000, 3600))
    monkeypatch.setattr(execution, "time", SimpleNamespace(monotonic=lambda: next(ticks), sleep=time.sleep))
    result = execution.run_task(w, "check", "independent-checker")
    assert result["status"] == "completed" and result["exit_code"] == 0
    assert result["elapsed_seconds"] >= 3600 and result["timeout_seconds"] is None
    assert w.store.get("route", "route")["runs"] == 1
    assert w.node_status("goal") == "open" and not w.store.all("claim")
    assert "result_artifact" not in result
    frozen = json.loads(w.store.verify_blob(result["contract_artifact"]).read_text(encoding="utf-8"))
    assert frozen == exported["contract"]
    with pytest.raises(ResearchError, match="not pending"):
        execution.run_task(w, "check", "independent-checker")


def test_null_local_validation_retains_output_limit_and_does_not_import_result(workspace):
    w = workspace
    checker_task(w, "from pathlib import Path\nimport os,time\n"
                    "Path('result.json').write_text('{}')\n"
                    "os.write(1,b'x'*65536)\ntime.sleep(30)\n", output_limit=1024)
    result = execution.run_task(w, "check", "independent-checker")
    assert result["status"] == "output_limit" and result["exit_code"] is not None
    assert result["captured_bytes"] <= 1024
    assert "result_artifact" not in result
    assert w.store.get("route", "route")["runs"] == 1
    assert w.node_status("goal") == "open"


def test_positive_local_validation_timeout_remains_a_hard_limit(workspace):
    w = workspace
    checker_task(w, "import time; time.sleep(30)", timeout=1)
    result = execution.run_task(w, "check", "independent-checker")
    assert result["status"] == "unknown_timeout" and result["exit_code"] is not None
    assert result["timeout_seconds"] == 1 and "result_artifact" not in result
    assert w.store.get("route", "route")["runs"] == 1
    assert w.node_status("goal") == "open"


def test_null_local_validation_interrupt_stops_child_and_requires_explicit_recovery(workspace, monkeypatch):
    w = workspace
    checker_task(w, "import time; time.sleep(30)")
    children = []
    original = execution.subprocess.Popen

    def record_child(*args, **kwargs):
        child = original(*args, **kwargs)
        children.append(child)
        return child

    def interrupt(_seconds):
        raise KeyboardInterrupt

    monkeypatch.setattr(execution.subprocess, "Popen", record_child)
    monkeypatch.setattr(execution, "time", SimpleNamespace(monotonic=time.monotonic, sleep=interrupt))
    with pytest.raises(KeyboardInterrupt):
        execution.run_task(w, "check", "independent-checker")
    assert children and all(child.poll() is not None for child in children)
    assert w.store.get("task", "check")["status"] == "running"
    assert w.store.get("route", "route")["runs"] == 1
    w.apply("task.recover", {"task": "check", "reason": "test observed all child exits",
                              "process_confirmed_stopped": True})
    assert w.store.get("task", "check")["status"] == "interrupted"
    with pytest.raises(ResearchError, match="not pending"):
        execution.run_task(w, "check", "independent-checker")


def test_null_local_validator_cannot_be_externally_dispatched(workspace):
    w = workspace
    checker_task(w, "print('not dispatched')")
    with pytest.raises(ResearchError, match="external dispatch supports"):
        execution.start_external(w, "check", "independent-checker")
    assert w.store.get("task", "check")["status"] == "pending"
    assert w.store.get("route", "route")["runs"] == 0


@pytest.mark.parametrize("role", ["explore", "produce", "review"])
def test_ordinary_tasks_default_to_no_cutoffs(workspace, role):
    task = workspace.apply("task.add", {"id": "ordinary", "route": "route", "role": role,
                          "argv": [sys.executable, "-c", "print('complete')"]})
    assert task["timeout_seconds"] is None and task["output_limit_bytes"] is None
    assert execution.run_task(workspace, "ordinary")["status"] == "completed"


@pytest.mark.parametrize("timeout", [0, -1, True, False, 1.5, "null"])
def test_local_validation_rejects_invalid_nonnull_timeouts(workspace, timeout):
    with pytest.raises(ResearchError, match="timeout_seconds"):
        checker_task(workspace, "print('not run')", timeout=timeout)
    assert not workspace.store.all("task")
    assert workspace.store.get("route", "route")["runs"] == 0


def test_omitting_limits_preserves_validator_binding(workspace):
    with pytest.raises(ResearchError, match="required field: validator_artifact"):
        workspace.apply("task.add", {"id": "check", "route": "route", "role": "validate",
                                    "argv": [sys.executable, "{validator}", "{input}"]})
    assert not workspace.store.all("task")

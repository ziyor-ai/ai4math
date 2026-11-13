"""Explicit unbounded output on new native validators and managed Codex jobs."""
import json
import sys
from concurrent.futures import Future
from pathlib import Path
from types import SimpleNamespace

import pytest

from ai4math.codex_live import EventLog, read_json
from ai4math.codex_runner import AppServer, ResearchSession
from ai4math.codex_worker_tools import WorkerTools
from ai4math.execution import export_task, run_task
from ai4math.external_supervisor import supervise_external
from ai4math.store import ResearchError, file_hash
from test_codex_monitored_contract import setup, dispatch, release
from test_local_validation_timeout import workspace, checker_task

LARGE = 2_100_001


@pytest.mark.parametrize("profile", ["trusted_local", "fresh_local_v1"])
def test_null_validation_retains_large_streams_and_result(workspace, profile):
    w = workspace
    code = ("import os,json\nfrom pathlib import Path\n"
            f"os.write(1,b'a'*{LARGE})\nos.write(2,b'b'*{LARGE})\n"
            f"Path('result.json').write_text(json.dumps({{'fixture':'c'*{LARGE}}}),encoding='utf-8')\n")
    t = checker_task(w, code, output_limit=None, profile=profile)
    contract = export_task(w, "check")["contract"]
    assert contract["output_limit_bytes"] is None
    if profile == "fresh_local_v1":
        assert t["request_identity"]["output_limit_bytes"] is None
    out = run_task(w, "check", "independent-checker")
    assert out["status"] == "completed" and out["captured_bytes"] == 2 * LARGE
    assert w.store.inside("tasks/check/stdout.log").read_bytes() == b"a" * LARGE
    assert w.store.inside("tasks/check/stderr.log").read_bytes() == b"b" * LARGE
    assert w.store.verify_blob(out["result_artifact"]).stat().st_size > LARGE
    assert w.node_status("goal") == "open" and not w.store.all("validation")
    assert w.store.get("route", "route")["runs"] == 1


@pytest.mark.parametrize("limit", [None, 1024])
def test_app_server_capture_uses_optional_cap_with_complete_null_raw(tmp_path, limit):
    line = (json.dumps({"method": "fixture", "params": {"text": "x" * LARGE}}) + "\n").encode()
    program = ("import sys,json\n"
               f"sys.stdout.buffer.write((json.dumps({{'method':'fixture','params':{{'text':'x'*{LARGE}}}}})+'\\n').encode());sys.stdout.flush()\n"
               f"sys.stderr.buffer.write(b'e'*{LARGE});sys.stderr.flush()\n"
               "sys.stdin.buffer.read()\n")
    wire = AppServer([sys.executable, "-c", program], tmp_path, tmp_path, limit)
    if limit is None:
        assert wire.close() == 0
        assert (tmp_path / "app-server.stdout.raw").read_bytes() == line
        assert (tmp_path / "app-server.stderr.raw").read_bytes() == b"e" * LARGE
        assert wire.captured == len(line) + LARGE and not wire.overflow.is_set()
        assert wire.messages.get_nowait()["params"]["text"] == "x" * LARGE
    else:
        with pytest.raises(ResearchError, match="did not close cleanly"):
            wire.close()
        assert wire.overflow.is_set()
        assert sum((tmp_path / f"app-server.{name}.raw").stat().st_size for name in ["stdout", "stderr"]) <= limit


def test_null_managed_broker_write_run_and_seal_preserve_large_outputs(tmp_path):
    w, payload = setup(tmp_path)
    payload["output_limit_bytes"] = None
    try:
        w.apply("task.add", payload)
        handoff = dispatch(w)
        assert handoff["contract"]["output_limit_bytes"] is None
        release(w, handoff)
        task = w.store.get("task", "managed")
        tools = WorkerTools(w.store.root, task, tmp_path)
        cap = tmp_path / "capture"
        cap.mkdir()
        tools.execute("write", {"path": "large.txt", "text": "y" * LARGE}, cap)
        assert (tools.return_dir / "large.txt").read_bytes() == b"y" * LARGE
        result = tools.execute("run", {"argv": ["python", "-c", f"import os;os.write(1,b'z'*{LARGE})"]}, cap)
        assert result["exit_code"] == 0 and result["bytes"] == LARGE
        assert result["truncated"] and len(result["output"]) == 9000
        assert (cap / "output.raw").read_bytes() == b"z" * LARGE
        assert tools.tool_bytes == LARGE and result["sha256"] == file_hash(cap / "output.raw")
        receipt = tools.return_dir / "return.json"
        receipt.write_text('{"fixture":"large-output-only"}', encoding="utf-8")
        seal = {"task": "managed", "contract_sha256": task["contract_artifact"],
                "artifacts": [{"path": name, "sha256": file_hash(tools.return_dir / name)}
                              for name in ["large.txt", "return.json"]]}
        before = w.apply("task.preflight_seal_batch", seal, "worker")
        assert before["passed"] and before["output_bytes"] > LARGE
        w.apply("task.begin_seal", {"task": "managed"}, "worker")
        submitted = w.apply("task.submit_seal_batch", seal, "worker")
        assert submitted["external_stage"] == "sealed"
        assert file_hash(w.store.verify_blob(seal["artifacts"][0]["sha256"])) == seal["artifacts"][0]["sha256"]
        assert w.store.get("route", "a")["runs"] == 1
    finally:
        w.store.close()


def test_integer_managed_broker_still_rejects_oversized_write_and_run(tmp_path):
    w, payload = setup(tmp_path)
    try:
        w.apply("task.add", payload)
        handoff = dispatch(w)
        release(w, handoff)
        task = w.store.get("task", "managed")
        tools = WorkerTools(w.store.root, task, tmp_path)
        cap = tmp_path / "capture"
        cap.mkdir()
        with pytest.raises(ResearchError, match="nonempty text required"):
            tools.execute("write", {"path": "large.txt", "text": "y" * LARGE}, cap)
        assert not (tools.return_dir / "large.txt").exists()
        with pytest.raises(ResearchError, match="worker tool output limit exceeded"):
            tools.execute("run", {"argv": ["python", "-c", f"import os;os.write(1,b'z'*{LARGE})"]}, cap)
        assert (cap / "output.raw").stat().st_size == payload["output_limit_bytes"]
        assert tools.tool_bytes > payload["output_limit_bytes"]
    finally:
        w.store.close()


def test_null_session_keeps_full_result_and_heartbeat_without_limit(tmp_path, monkeypatch):
    log = EventLog(tmp_path, "test")
    wire = SimpleNamespace(send=lambda _message: None, captured=LARGE,
                           proc=SimpleNamespace(pid=123, poll=lambda: None))
    tools = SimpleNamespace(task={"output_limit_bytes": None}, tool_bytes=LARGE,
                            source_bytes=0, current_process=None, root=tmp_path)
    session = ResearchSession(wire, log, tools, None)
    cap = tmp_path / "tool"
    cap.mkdir()
    future = Future()
    future.set_result({"text": "r" * LARGE})
    session.tool_future = future
    session.tool_info = (1, "read", cap, "call")
    monkeypatch.setattr("ai4math.codex_runner.refresh_report", lambda _root: "fixture")
    try:
        session.finish_tool()
        assert read_json(cap / "result.full.json")["result"]["text"] == "r" * LARGE
        assert read_json(cap / "result.json")["success"] is False  # Preview request was too large.
        session.pulse()
        assert read_json(tmp_path / "heartbeat.json")["near_output_limit"] is False
        assert tools.tool_bytes > LARGE
    finally:
        session.pool.shutdown()


def test_null_native_supervisor_captures_large_output_then_honors_stop(tmp_path):
    w, payload = setup(tmp_path)
    payload["output_limit_bytes"] = None
    try:
        w.apply("task.add", payload)
        code = ("import os,sys,time\n"
                f"os.write(1,b's'*{LARGE})\n"
                f"sys.path.insert(0,{str(Path(__file__).resolve().parents[1])!r})\n"
                "from ai4math.workbench import Workbench\n"
                f"w=Workbench({str(w.store.root)!r})\n"
                "w.apply('task.codex_stop',{'task':'managed','reason':'null output fixture stop'})\n"
                "time.sleep(30)\n")
        result = supervise_external(w, "managed", [sys.executable, "-B", "-c", code], managed_codex=True)
        assert result["status"] == "interrupted" and result["stop_state"] == "confirmed"
        assert w.store.inside("tasks/managed/supervisor.stdout.log").read_bytes() == b"s" * LARGE
        assert w.store.get("route", "a")["runs"] == 1
    finally:
        w.store.close()


@pytest.mark.parametrize("role", ["explore", "produce", "review"])
def test_unmanaged_roles_keep_full_output(workspace, role):
    workspace.apply("task.add", {"id": "ordinary", "route": "route", "role": role,
                    "argv": [sys.executable, "-c", f"import sys; sys.stdout.write('x' * {LARGE})"],
                    "timeout_seconds": 10})
    assert run_task(workspace, "ordinary")["status"] == "completed"
    assert workspace.store.inside("tasks/ordinary/stdout.log").stat().st_size == LARGE


@pytest.mark.parametrize("limit", [0, -1, True, 1.5, "null"])
def test_invalid_nonnull_output_limits_remain_rejected(workspace, limit):
    with pytest.raises(ResearchError, match="output_limit_bytes"):
        checker_task(workspace, "print('not run')", output_limit=limit)
    assert not workspace.store.all("task")



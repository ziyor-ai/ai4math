"""Complete a worker handoff using the captured evidence and frozen bindings."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from ._atomic import file_lock, publish_new
from .codex_live import atomic_json, read_json, workspace
from .mcp_task import BROKER_LOCK, BROKER_STATE, HOSTS
from .store import ResearchError, file_hash, identifier, now


def _write_new(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(data)


def finish(tools, log, root, tid, capture):
    """Record the capture, then seal the frozen handoff. Returns the exit code."""
    if not tools.handoff:
        raise ResearchError("exploration ended without a complete output handoff")
    receipt = {**capture}
    receipt.setdefault("task", tid)
    receipt.setdefault("contract_sha256", tools.task["contract_artifact"])
    receipt["sealed_at"] = now()
    _write_new(log.directory / "capture-receipt.json",
               json.dumps(receipt, ensure_ascii=False).encode("utf-8"))
    log.emit("capture_closed", receipt_sha256=file_hash(log.directory / "capture-receipt.json"),
             tool_bytes=tools.tool_bytes, source_bytes=tools.source_bytes)
    with workspace(root, read_only=False) as w:
        actor = tools.task["dispatched_by"]
        report = w.apply("task.preflight_seal_batch", tools.handoff, actor)
        _write_new(log.directory / "seal-preflight.json", json.dumps(report, ensure_ascii=False).encode("utf-8"))
        if not report["passed"]:
            raise ResearchError("seal preflight failed: " + json.dumps(report["errors"], ensure_ascii=False))
        w.apply("task.begin_seal", {"task": tid}, actor)
        sealed = w.apply("task.submit_seal_batch", tools.handoff, actor)
    _write_new(log.directory / "seal-result.json", json.dumps(sealed, ensure_ascii=False).encode("utf-8"))
    log.emit("sealed", receipt=sealed.get("receipt", {}).get("artifact_id"))
    from .codex_runner import refresh_report
    refresh_report(root)
    return 0


def execution_package(live, replicates=1):
    """Derive the probe execution record from the broker captures.

    Every run action already stores argv.json and output.raw next to its
    request; this lifts them into the sealed receipt so a forged 4-byte output
    no longer passes as a real execution.
    """
    tools = Path(live) / "tools"
    rows = []
    for folder in sorted(tools.iterdir()) if tools.is_dir() else []:
        request = folder / "request.json"
        if not request.is_file():
            continue
        payload = json.loads(request.read_text(encoding="utf-8"))
        if (payload.get("arguments") or {}).get("action") != "run":
            continue
        argv_path, raw_path, result_path = folder / "argv.json", folder / "output.raw", folder / "result.json"
        result = read_json(result_path) if result_path.is_file() else {}
        if result.get("success") is False and not argv_path.exists():
            continue  # A rejected request did not start a subprocess.
        if not argv_path.is_file() or not raw_path.is_file():
            raise ResearchError("run capture is missing argv or output bytes: " + folder.name)
        outcome = result.get("result") if isinstance(result.get("result"), dict) else {}
        sha, size = file_hash(raw_path), raw_path.stat().st_size
        if result.get("success") is True and (outcome.get("sha256") != sha or outcome.get("bytes") != size):
            raise ResearchError("run capture differs from its broker result: " + folder.name)
        rows.append({"call": folder.name,
                     "argv": json.loads(argv_path.read_text(encoding="utf-8")),
                     "exit_code": outcome.get("exit_code"),
                     "stdout_sha256": sha, "stdout_bytes": size})
    if not rows:
        raise ResearchError("a sealed probe needs at least one captured run (argv, output, exit code)")
    if replicates > 1:
        finished = [row for row in rows if type(row["exit_code"]) is int]
        if len(finished) < replicates:
            raise ResearchError("this round requires " + str(replicates) +
                                " independent executions; only " + str(len(finished)) + " captured")
        digests = {row["stdout_sha256"] for row in finished[-replicates:]}
        if len(digests) != 1:
            raise ResearchError("independent executions disagree (stdout digests differ); refuse to seal")
    return rows


def seal_subagent(root, tid, actor, *, host_run_id=None):
    """Seal native subagent evidence from the broker and coordinator's report."""
    live = Path(root).resolve(strict=True) / "tasks" / identifier(tid) / "live"
    if live.resolve() != live or (live / BROKER_LOCK).resolve() != live / BROKER_LOCK:
        raise ResearchError("native subagent capture paths must be canonical")
    if not live.is_dir():
        raise ResearchError("subagent task has no broker captures")
    with file_lock(live / BROKER_LOCK):
        sealed = _seal_subagent(root, tid, actor, live, host_run_id)
    from .codex_runner import refresh_report
    refresh_report(root)
    return sealed


def _seal_subagent(root, tid, actor, live, host_run_id):
    with workspace(root) as w:
        task = w.store.get("task", tid)
    if actor not in {task.get("dispatched_by"), task.get("codex_workflow", {}).get("worker_actor")}:
        raise ResearchError("only the worker or dispatching coordinator may seal a native subagent handoff")
    preflight = live.parent / "subagent-preflight.json"
    prepared = read_json(preflight) if preflight.is_file() else {}
    host = prepared.get("host")
    if host is not None and host not in HOSTS:
        raise ResearchError("invalid native subagent host in preflight")
    if host is not None and actor != task["dispatched_by"]:
        raise ResearchError("native subagent host provenance must be reported by the dispatching coordinator")
    if host_run_id is not None and (not host or not isinstance(host_run_id, str)
                                    or not 1 <= len(host_run_id.strip()) <= 256):
        raise ResearchError("a host run ID must be bound to a declared subagent host")
    handoff_path = live / "handoff.json"
    state_file = live / BROKER_STATE
    state = read_json(state_file) if state_file.is_file() else None
    if state is not None:
        if (state.get("in_flight") is not None or state["binding"]["task"] != tid
                or state["binding"]["contract_sha256"] != task.get("contract_artifact")):
            raise ResearchError("worker broker handoff has interrupted or mismatched state")
        worker = state["worker"]
        if worker["handoff"] is None:
            raise ResearchError("subagent task has no committed broker handoff")
        expected_handoff = {**worker["handoff"], "broker_capture": {
            "tool_bytes": worker["tool_bytes"], "source_bytes": worker["source_bytes"]}}
        if not handoff_path.exists():
            publish_new(handoff_path, json.dumps(expected_handoff, ensure_ascii=False).encode("utf-8"))
    if not handoff_path.is_file():
        raise ResearchError("subagent task has no frozen handoff; run worker-task handoff first")
    handoff = read_json(handoff_path)
    if state is not None and handoff != expected_handoff:
        raise ResearchError("subagent handoff differs from committed worker broker state")
    calls = sorted(p.name for p in (live / "tools").iterdir() if p.is_dir()) if (live / "tools").is_dir() else []
    if state is not None and calls != [f"{number:05d}" for number in range(state["next_call"])]:
        raise ResearchError("committed broker capture sequence is incomplete")
    replicates = task.get("codex_workflow", {}).get("replicates", 1)
    executions = execution_package(live, replicates)
    capture = {"task": tid, "driver": "subagent", "captured_by": actor,
               "contract_sha256": task["contract_artifact"],
               "host_provenance": {"host": host, "run_id": host_run_id,
                                   "trust_basis": "coordinator-reported host and worker identifier" if host else "unknown"},
               "tool_calls": len(calls), "first_call": calls[0] if calls else None,
               "last_call": calls[-1] if calls else None,
               "executions": executions,
               "executions_sha256": hashlib.sha256(json.dumps(executions, sort_keys=True).encode("utf-8")).hexdigest(),
               "note": "executions are derived from broker captures"}
    receipt = live / "capture-receipt.json"
    if receipt.exists():
        recorded = read_json(receipt)
        if "host_provenance" in recorded and any(recorded.get(key) != value for key, value in capture.items()):
            raise ResearchError("native subagent capture receipt differs on retry")
    else:
        publish_new(receipt, json.dumps({**capture, "sealed_at": now()}, ensure_ascii=False).encode("utf-8"))
    result_path = live / "seal-result.json"
    if task.get("external_stage") == "sealed":
        recorded_seal = task["seal_receipt"]
        if sorted(item["sha256"] for item in handoff["artifacts"]) != sorted(recorded_seal["output_artifacts"]):
            raise ResearchError("native subagent handoff differs from the recorded seal")
        if result_path.is_file():
            result = read_json(result_path)
            if result.get("receipt") != recorded_seal:
                raise ResearchError("local native subagent seal result differs from the registry")
            return result
        result = {"task": tid, "external_stage": "sealed", "acceptance": "pending", "receipt": recorded_seal}
        atomic_json(result_path, result)
        return result
    with workspace(root, read_only=False) as w:
        report = w.apply("task.preflight_seal_batch", handoff, actor)
        atomic_json(live / "seal-preflight.json", report)
        if not report["passed"]:
            raise ResearchError("seal preflight failed: " + json.dumps(report["errors"], ensure_ascii=False))
        if task.get("external_stage") == "exploration":
            w.apply("task.begin_seal", {"task": tid}, actor)
        sealed = w.apply("task.submit_seal_batch", handoff, actor)
    atomic_json(result_path, sealed)
    return sealed

"""Server-clock external phase gates; no worker-supplied timestamps are trusted.

This module enforces acceptance deadlines. Physical stopping is a separate,
explicit supervisor responsibility (see external_supervisor).
"""
from datetime import datetime, timedelta
import json
from pathlib import Path

from .store import ResearchError, digest, now

PHASES = ("literature", "coordinator_review", "first_check", "seal")
STAGES = {"literature": "literature", "awaiting_coordinator_review": "coordinator_review",
          "exploration": "first_check", "seal": "seal"}


def task_fields(task, payload):
    codex = "codex_workflow" in payload
    gated = task.get("literature", {}).get("deadline_contract_version")
    if codex:
        if task["role"] not in {"explore", "produce"} or not gated:
            raise ResearchError("codex_workflow requires a literature-gated research task")
        if payload.get("external_supervision") != "process" or "phase_budgets" in payload:
            raise ResearchError("codex_workflow requires process supervision and no phase_budgets")
    if not gated:
        return
    if "phase_budgets" not in payload:
        mode = payload.get("external_supervision", "host")
        if mode not in {"host", "process"}:
            raise ResearchError("external_supervision must be host or process")
        task["external_deadline_policy"] = {
            "version": 2, "supervision": mode, "clock": "server_utc",
            "start": "before_worker_dispatch", "worker_timestamps_accepted": False,
            "late_outputs": "archive_only", "stage_deadlines": False,
            "total_timeout_seconds": task["timeout_seconds"], "managed_codex_required": codex,
        }
        task["literature"]["deadline_contract_version"] = 2
        return
    budgets = payload.get("phase_budgets")
    if not isinstance(budgets, dict) or set(budgets) != set(PHASES) or any(
            type(value) is not int or value <= 0 for value in budgets.values()):
        raise ResearchError("phase_budgets must explicitly give positive integer seconds for literature, coordinator_review, first_check, seal")
    mode = payload.get("external_supervision")
    if sum(budgets.values()) != task["timeout_seconds"]:
        raise ResearchError("timeout_seconds must equal the sum of phase_budgets; stages cannot expand the total budget")
    if mode not in {"host", "process"}:
        raise ResearchError("external_supervision must explicitly be host or process")
    task["external_deadline_policy"] = {"version": 1, "phase_budgets": dict(budgets),
        "supervision": mode, "clock": "server_utc", "start": "before_worker_dispatch",
        "worker_timestamps_accepted": False, "late_outputs": "archive_only"}


def enter(task, stage):
    if not task.get("external_deadline_policy"):
        return
    phase = STAGES[stage]
    history = task.setdefault("phase_timing", {})
    if phase in history:
        raise ResearchError("external phase cannot restart or refund its budget")
    started = now()
    if task["external_deadline_policy"].get("version") == 2:
        history[phase] = {"started_at": started}
        task["active_phase"] = phase
        if phase == "literature":
            seconds = task["timeout_seconds"]
            task["external_deadline_at"] = ((datetime.fromisoformat(started) + timedelta(seconds=seconds)).isoformat()
                                            if seconds is not None else None)
        return
    seconds = task["external_deadline_policy"]["phase_budgets"][phase]
    history[phase] = {"started_at": started,
                      "deadline_at": (datetime.fromisoformat(started) + timedelta(seconds=seconds)).isoformat()}
    task["active_phase"] = phase
    if phase == "literature":
        task["external_deadline_at"] = (datetime.fromisoformat(started) + timedelta(
            seconds=sum(task["external_deadline_policy"]["phase_budgets"].values()))).isoformat()
    history[phase]["deadline_at"] = min(history[phase]["deadline_at"], task["external_deadline_at"])


def overdue(task):
    if task.get("external_deadline_policy") and task["status"] == "running" and task.get("external_stage") != "sealed":
        if task["external_deadline_policy"].get("version") == 2:
            cutoff = task.get("external_deadline_at")
            return cutoff is not None and datetime.fromisoformat(now()) >= datetime.fromisoformat(cutoff)
        return datetime.fromisoformat(now()) >= datetime.fromisoformat(
            task["phase_timing"][task["active_phase"]]["deadline_at"])
    return False


def ensure_live(task):
    if overdue(task):
        kind = "total" if task.get("external_deadline_policy", {}).get("version") == 2 else "phase"
        raise ResearchError("external " + kind + " deadline exceeded; late outputs are archive-only")
    if task.get("codex_control", {}).get("held"):
        raise ResearchError("managed Codex task is held; coordinator must explicitly release hold")


def fail(w, task, reason, status="unknown_timeout"):
    if task["status"] != "running":
        return task
    task.update(status=status, evidence_level="unknown", finished_at=now(), failure_reason=reason,
                stop_requested_at=now(), stop_state="requested")
    w.store.put("task", task["id"], task)
    from .strategy import finish_round
    finish_round(w, task)
    return task


def expire(w, task_id, actor="deadline-supervisor"):
    # Own transaction: a rejected operation must not roll back the timeout.
    # WAL readers do not contend with a worker write transaction. Only an actual
    # overdue candidate takes the write lock, with a second check under it.
    if not overdue(w.store.get("task", task_id)):
        return False
    with w.store.transaction("task.deadline_check", actor):
        task = w.store.get("task", task_id)
        if overdue(task):
            if not w.store.audit(verify_bytes=False)["passed"]:
                raise ResearchError("workspace integrity failed during deadline check")
            kind = "total" if task["external_deadline_policy"].get("version") == 2 else "phase"
            fail(w, task, kind + " deadline exceeded: " + task["active_phase"])
        else:
            return False
    from .research_summary import refresh_after_transition
    refresh_after_transition(w)
    return True


def begin_seal(w, p, actor):
    task = w.store.get("task", p["task"])
    if not task.get("external_deadline_policy") or task["status"] != "running" or task.get("external_stage") != "exploration":
        raise ResearchError("begin_seal requires a running managed exploration task")
    if actor not in {task["dispatched_by"], w.store.get("worker_literature", task["id"])["actor"]}:
        raise ResearchError("only the worker or coordinator may begin seal")
    ensure_live(task)
    task["external_stage"] = "seal"
    enter(task, "seal")
    w.store.put("task", task["id"], task)
    return task


def bindings(task):
    return {"task_id": task["id"], "contract_sha256": task["contract_artifact"],
            "statement_digest": task["node_snapshot"]["statement_digest"],
            "input_artifact_ids": sorted(task["inputs"]),
            "candidate_digest": task.get("exploration_context", {}).get("candidate", {}).get("candidate_digest")}


def check_manifest(w, task, artifacts, manifest_id):
    if task.get("external_stage") != "seal":
        raise ResearchError("managed research must enter seal before completion")
    if not manifest_id:
        raise ResearchError("managed completion requires seal_manifest_artifact")
    from .literature import _verify_handoff
    _verify_handoff(w, task)
    manifest = json.loads(w.store.verify_blob(manifest_id).read_text(encoding="utf-8-sig"))
    expected = bindings(task)
    if any(manifest.get(key) != value for key, value in expected.items()):
        raise ResearchError("seal manifest must bind exact task, contract, original inputs and candidate")
    rows = manifest.get("artifacts")
    if not isinstance(rows, list) or not rows or any(not isinstance(row, dict) for row in rows):
        raise ResearchError("seal manifest needs artifact hash rows")
    if sorted(row.get("artifact_id", "") for row in rows) != sorted(artifacts) or len(set(artifacts)) != len(artifacts):
        raise ResearchError("seal manifest output set differs from completion")
    from . import research_return
    if research_return.enabled(task):
        root = w.store.root / "tasks" / task["id"] / "return"
        try:
            selected = {Path(row["path"]).relative_to(root).as_posix(): row["artifact_id"] for row in rows}
        except (KeyError, TypeError, ValueError) as exc:
            raise ResearchError("research continuation seal needs exact return artifact paths") from exc
        if len(selected) != len(rows):
            raise ResearchError("research continuation seal has duplicate return artifact paths")
        research_return.check_seal(task, selected,
            lambda name: json.loads(w.store.verify_blob(selected[name]).read_text(encoding="utf-8-sig")),
            lambda aid: json.loads(w.store.verify_blob(aid).read_text(encoding="utf-8-sig")))
    release_at = w.store.get("exploration_release", task["id"])["created_at"]
    seal = task["phase_timing"]["seal"]
    for row in rows:
        key = row["artifact_id"]
        w.store.verify_blob(key)
        record = w.store.get("artifact", key)
        if row.get("sha256") != key:
            raise ResearchError("seal manifest artifact SHA-256 mismatch")
        cutoff = seal.get("deadline_at") or task.get("external_deadline_at")
        if key in {*task["inputs"], task["contract_artifact"], task["prompt_artifact"]} or not (
                release_at <= record["imported_at"] and (cutoff is None or record["imported_at"] < cutoff)):
            raise ResearchError("seal output fails original-input exclusion or freshness check")
    imported = w.store.get("artifact", manifest_id)["imported_at"]
    cutoff = seal.get("deadline_at") or task.get("external_deadline_at")
    if not (seal["started_at"] <= imported and (cutoff is None or imported < cutoff)):
        raise ResearchError("seal manifest must be freshly frozen within the seal budget")
    ensure_live(task)
    return {"artifact_id": manifest_id, "sha256": manifest_id, "checked_at": now(),
            "bindings_sha256": digest(expected), "trust_basis": "framework byte binding; no mathematical validation"}


def confirm_stop(w, p, actor):
    task = w.store.get("task", p["task"])
    if not task.get("external_deadline_policy") or task["status"] == "pending" or (
            task["status"] == "running" and task.get("external_stage") != "sealed"):
        raise ResearchError("stop confirmation requires a terminal managed external task")
    if actor != task["dispatched_by"] or p.get("confirmed") is not True or not isinstance(p.get("evidence"), str) or not p["evidence"].strip():
        raise ResearchError("coordinator must confirm physical stop with host/process evidence")
    record = {"id": task["id"], "actor": actor, "confirmed_at": now(), "evidence": p["evidence"],
              "trust_basis": "coordinator attestation; actor label is not authentication"}
    w.store.put("external_stop", task["id"], record, new=True)
    task.update(stop_state="confirmed", stop_confirmation=record["id"])
    w.store.put("task", task["id"], task)
    return record


def submit_seal(w, p, actor):
    task = w.store.get("task", p["task"])
    if task["status"] != "running" or not task.get("external_deadline_policy"):
        raise ResearchError("seal submission requires a running managed task")
    if actor not in {task["dispatched_by"], w.store.get("worker_literature", task["id"])["actor"]}:
        raise ResearchError("only worker or coordinator may submit seal")
    ensure_live(task)
    artifacts = p.get("artifact_ids")
    if not isinstance(artifacts, list) or not artifacts or any(not isinstance(a, str) for a in artifacts):
        raise ResearchError("seal submission needs frozen artifact_ids")
    from .literature import check_completed_outputs
    check_completed_outputs(w, task, artifacts)
    receipt = check_manifest(w, task, artifacts, p.get("seal_manifest_artifact"))
    ensure_live(task)
    receipt.update(id=task["id"], output_artifacts=artifacts, submitted_by=actor)
    w.store.put("external_seal", task["id"], receipt, new=True)
    task.update(external_stage="sealed", seal_receipt=receipt, sealed_at=now(),
                stop_requested_at=now(), stop_state="requested")
    w.store.put("task", task["id"], task)
    return {"task": task["id"], "external_stage": "sealed", "acceptance": "pending", "receipt": receipt}


def _codex_control_task(w, p, actor):
    task = w.store.get("task", p["task"])
    if task.get("external_deadline_policy", {}).get("version") != 2 or task["status"] != "running":
        raise ResearchError("Codex control requires a running managed v2 task")
    if actor != task["dispatched_by"] or not isinstance(p.get("reason"), str) or not p["reason"].strip():
        raise ResearchError("only the dispatching coordinator may control Codex, with an explicit reason")
    return task


def codex_hold(w, p, actor):
    task = _codex_control_task(w, p, actor)
    if type(p.get("held")) is not bool or task.get("external_stage") == "sealed":
        raise ResearchError("hold requires held boolean and an unsealed task")
    if overdue(task):
        raise ResearchError("external total deadline exceeded; hold cannot extend time")
    task["codex_control"] = {"held": p["held"], "reason": p["reason"], "actor": actor, "updated_at": now(),
                             "scope": "stop new managed dispatch; not physical suspension or clock extension"}
    w.store.put("task", task["id"], task)
    return task["codex_control"]


def codex_stop(w, p, actor):
    task = _codex_control_task(w, p, actor)
    return fail(w, task, "coordinator requested Codex stop: " + p["reason"], "interrupted")


def dispatch_hold(w, p, actor):
    if type(p.get("held")) is not bool or not isinstance(p.get("reason"), str) or not p["reason"].strip():
        raise ResearchError("dispatch hold requires held boolean and explicit reason")
    record = {"id": "main", "held": p["held"], "reason": p["reason"], "actor": actor, "updated_at": now(),
              "scope": "new managed Codex dispatch only; existing tasks and clocks unchanged"}
    w.store.put("codex_dispatch", "main", record)
    return record


HANDLERS = {"task.begin_seal": begin_seal, "task.submit_seal": submit_seal, "task.confirm_stop": confirm_stop,
            "task.codex_hold": codex_hold, "task.codex_stop": codex_stop, "codex.dispatch_hold": dispatch_hold}

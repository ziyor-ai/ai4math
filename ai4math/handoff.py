"""Uniform handoff record for every finished task.

External research workers write return/return.json; local producers and
validators leave result.json plus output artifacts.  This module writes one
handoff.json per finished task and reads any of the three layouts, so an audit
does not need a per-role special case.
"""

import json

from .store import ResearchError, now

SCHEMA = "handoff/v1"
FILE = "handoff.json"
FIELDS = ("schema", "task", "role", "status", "summary", "artifacts", "evidence_level", "actor", "finished_at")


def path(store, task_id):
    return store.inside("tasks") / task_id / FILE


def write(store, task, actor):
    """Write the uniform record; derived data only, the registry stays authoritative."""
    artifacts = list(task.get("output_artifacts") or [])
    if task.get("result_artifact") and task["result_artifact"] not in artifacts:
        artifacts.append(task["result_artifact"])
    payload = {"schema": SCHEMA, "task": task["id"], "role": task["role"], "status": task["status"],
               "summary": task.get("external_summary") or task.get("execution_error") or task.get("recovery_reason"),
               "artifacts": artifacts, "evidence_level": task.get("evidence_level", "unknown"),
               "actor": actor, "finished_at": task.get("finished_at") or now()}
    if "validation_outcome" in task:
        payload["validation_outcome"] = task["validation_outcome"]
    target = path(store, task["id"])
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload


def read(store, task_id):
    """Normalise whichever handoff layout the task used."""
    folder = store.inside("tasks") / task_id
    uniform = folder / FILE
    if uniform.is_file():
        return json.loads(uniform.read_text(encoding="utf-8"))
    returned = folder / "return" / "return.json"
    if returned.is_file():
        body = json.loads(returned.read_text(encoding="utf-8"))
        return {"schema": "return/v1", "task": task_id, "summary": body.get("summary"),
                "evidence_level": body.get("suggested_evidence_level"), "artifacts": sorted(
                    name for name in (body.get("artifacts") or []) if isinstance(name, str)),
                "detail": body}
    result = folder / "result.json"
    if result.is_file():
        body = json.loads(result.read_text(encoding="utf-8"))
        return {"schema": "result/v1", "task": task_id, "status": body.get("status"),
                "summary": body.get("summary"), "detail": body}
    raise ResearchError("task has no handoff record: " + task_id)

"""Export and restore an explicitly chosen, auditable workspace event prefix."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from ._atomic import publish_new
from .store import ResearchError, Store, digest, encode, file_hash


def require(ok, message):
    if not ok:
        raise ResearchError(message)


def _projection(events):
    records, previous = {}, "0" * 64
    require(isinstance(events, list) and events, "checkpoint needs an event prefix")
    for seq, row in enumerate(events, 1):
        event = {k: row[k] for k in ("seq", "timestamp", "operation", "actor", "previous", "changes")}
        require(event["seq"] == seq and event["previous"] == previous and digest(event) == row["hash"],
                f"checkpoint event chain differs at {seq}")
        for change in event["changes"]:
            records[change["kind"], change["id"]] = change["value"]
        previous = row["hash"]
    require(("project", "main") in records, "checkpoint is not a research workspace")
    require(not any(kind == "task" and (r["status"] == "running" or r.get("stop_state") == "requested")
                    for (kind, _), r in records.items()),
            "checkpoint needs a quiescent prefix with no running task or unconfirmed stop")
    return records, previous


def _ledger_anchor(ledger):
    state = ledger.state()
    return {"root": str(ledger.root), "plan_sha256": ledger.plan_hash, "head_sha256": state["events"][-1]["sha256"],
            "event_count": len(state["events"])}


def _verify_ledgers(anchors, ledgers):
    require(set(anchors) == set(ledgers), "restore needs the original ledgers named by the checkpoint")
    for role, anchor in anchors.items():
        ledger = ledgers[role]
        events = ledger.state()["events"]
        count = anchor["event_count"]
        require(str(ledger.root) == anchor["root"] and ledger.plan_hash == anchor["plan_sha256"] and 0 < count <= len(events) and
                events[count - 1]["sha256"] == anchor["head_sha256"],
                f"{role} ledger is not a continuation of the checkpoint")


def _copy_object(source, target, size, sha):
    require(source.is_file() and source.stat().st_size == size and file_hash(source) == sha,
            f"checkpoint object missing or changed: {sha}")
    target.parent.mkdir(parents=True, exist_ok=True)
    with source.open("rb") as input, target.open("xb") as output:
        shutil.copyfileobj(input, output)
    require(target.stat().st_size == size and file_hash(target) == sha, f"checkpoint object copy differs: {sha}")


def export_checkpoint(workbench, destination, at, *, ledgers=None):
    store = workbench.store
    destination = Path(destination).resolve()
    require(type(at) is int and at > 0 and not destination.is_relative_to(store.root),
            "choose a positive event number and a destination outside the workspace")
    with store.snapshot():
        audit = store.audit(verify_bytes=False)
        require(audit["passed"] and at <= audit["events"], "workspace event audit or requested event failed")
        events = [{**dict(row), "changes": json.loads(row["changes"])}
                  for row in store.db.execute("SELECT * FROM events WHERE seq <= ? ORDER BY seq", (at,))]
    records, tail = _projection(events)
    objects = {key: rec["size"] for (kind, key), rec in records.items() if kind == "artifact"}
    anchors = {role: _ledger_anchor(ledger) for role, ledger in (ledgers or {}).items()}
    manifest = {"schema": "ai4math-checkpoint-v1", "event": at, "event_tail": tail,
                "events_sha256": digest(events), "project_id": records["project", "main"]["id"],
                "objects": objects, "ledgers": anchors}
    destination.mkdir()
    for sha, size in objects.items():
        _copy_object(store.blob(sha), destination / "objects" / sha[:2] / sha, size, sha)
    publish_new(destination / "events.json", encode(events).encode("utf-8"))
    publish_new(destination / "manifest.json", encode(manifest).encode("utf-8"))
    return {"path": str(destination), "manifest_sha256": file_hash(destination / "manifest.json"),
            "event": at, "event_tail": tail, "objects": len(objects), "ledgers": anchors}


def restore_checkpoint(source, destination, manifest_sha256, *, actor, ledgers=None, allowed_roots=()):
    source, destination = Path(source).resolve(), Path(destination).resolve()
    require(not destination.is_relative_to(source), "restore into a new workspace outside the checkpoint")
    manifest_path = source / "manifest.json"
    require(file_hash(manifest_path) == manifest_sha256, "checkpoint manifest differs from supplied anchor")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    require(manifest["schema"] == "ai4math-checkpoint-v1", "unsupported checkpoint")
    events = json.loads((source / "events.json").read_text(encoding="utf-8"))
    records, tail = _projection(events)
    objects = {key: rec["size"] for (kind, key), rec in records.items() if kind == "artifact"}
    require(digest(events) == manifest["events_sha256"] and tail == manifest["event_tail"] and
            len(events) == manifest["event"] and objects == manifest["objects"] and
            records["project", "main"]["id"] == manifest["project_id"], "checkpoint manifest and event prefix differ")
    ledgers = ledgers or {}
    _verify_ledgers(manifest["ledgers"], ledgers)
    roots = [Path(root) for root in allowed_roots]
    require(all(root.is_absolute() and root.is_dir() for root in roots), "restore source roots must be explicit directories")
    destination.mkdir()
    store = Store(destination, create=True)
    try:
        for sha, size in objects.items():
            target = store.blob(sha)
            _copy_object(source / "objects" / sha[:2] / sha, target, size, sha)
        with store.transaction("checkpoint.restore", actor):
            store.db.executemany("INSERT INTO events VALUES (?,?,?,?,?,?,?)", [
                (r["seq"], r["timestamp"], r["operation"], r["actor"], r["previous"], encode(r["changes"]), r["hash"])
                for r in events])
            store.db.executemany("INSERT INTO records VALUES (?,?,?)",
                                 [(kind, key, encode(record)) for (kind, key), record in records.items()])
            require(store.audit()["passed"], "restored prefix failed integrity audit")
            checkpoint = {"manifest_sha256": manifest_sha256, "parent_event": manifest["event"],
                          "parent_tail": tail, "inherited_tasks": sorted(key for kind, key in records if kind == "task"),
                          "allowed_roots": sorted({str(root.resolve()) for root in roots}),
                          "ledgers": manifest["ledgers"]}
            store.put("checkpoint", manifest_sha256, checkpoint, new=True)
        return {"workspace": str(destination), **checkpoint, "audit": store.audit()}
    finally:
        store.close()


def inherited(store, task_id):
    return any(task_id in cp["inherited_tasks"] for cp in store.all("checkpoint"))


def bind_resumption(store, task, parent_id):
    parent = store.get("task", parent_id)
    require(inherited(store, parent_id), "resume_from must name an inherited checkpoint task")
    require(parent["status"] in {"pending", "failed", "interrupted", "unknown_timeout", "output_limit"},
            "inherited task status cannot be resumed")
    require(not any(row.get("resume_from") == parent_id for row in store.all("task")),
            "inherited task already has a continuation")
    def candidate_id(row):
        return row.get("exploration_context", {}).get("candidate", {}).get("id")

    require(all(task.get(field) == parent.get(field) for field in
                ("route", "node", "role", "inputs", "validator_artifact", "program_artifact", "method")) and
            task["node_snapshot"]["statement_digest"] == parent["node_snapshot"]["statement_digest"] and
            candidate_id(task) == candidate_id(parent),
            "continuation changed the inherited task's mathematical binding")
    task["resume_from"] = parent_id

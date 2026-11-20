"""Append-only research ledger: the coordinator's externalized memory.

One line per finished round.  `open-round` refuses to open a new round unless
the coordinator cites the current ledger digest and attaches an independently
imported direction note, so direction cannot drift with a compacted context.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .store import ResearchError, now

FILE = "research-ledger.jsonl"
KEYS = ("question", "method", "observed", "falsified", "open_obligations")


def path(root):
    return Path(root) / FILE


def digest(root):
    target = path(root)
    if not target.is_file():
        return ""
    return hashlib.sha256(target.read_bytes()).hexdigest()


def entries(root):
    target = path(root)
    if not target.is_file():
        return []
    return [json.loads(line) for line in target.read_text(encoding="utf-8").splitlines() if line.strip()]


def append(root, entry, actor):
    missing = [key for key in KEYS if not isinstance(entry.get(key), str) or not entry[key].strip()]
    if missing:
        raise ResearchError("ledger entry needs " + ", ".join(missing))
    record = {"at": now(), "by": actor, **{key: entry[key] for key in KEYS}}
    record.update({key: value for key, value in entry.items()
                   if key not in KEYS and key not in {"at", "by"}})
    with path(root).open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, ensure_ascii=False) + "\n")
    return digest(root)


def record_task(w, tid, actor):
    """Distil a finished task's frozen return into one ledger line."""
    task = w.store.get("task", tid)
    if task["status"] != "completed" or task.get("external_stage") != "sealed":
        raise ResearchError("only a sealed, completed task can enter the ledger")
    out = Path(w.store.verify_blob(task["output_artifacts"][0]))
    try:
        payload = json.loads(out.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        payload = {}
    probe = payload.get("first_probe") if isinstance(payload, dict) else None
    observed = (payload.get("summary") or payload.get("next_step") or "") if isinstance(payload, dict) else ""
    entry = {
        "question": task.get("learning_question") or task["id"],
        "method": payload.get("method", "") if isinstance(payload, dict) else "",
        "observed": observed or (probe or {}).get("outcome", "") or "see sealed artifacts",
        "falsified": payload.get("falsified", "") if isinstance(payload, dict) else "",
        "open_obligations": payload.get("next_step", "") if isinstance(payload, dict) else "",
    }
    for key in ("method", "falsified", "open_obligations"):
        if not entry[key]:
            entry[key] = "not stated by the worker"
    from . import review as reviews
    rows = reviews.status(w, tid)
    entry["task"] = tid
    entry["demo"] = bool(task.get("thin_inputs_ack"))
    entry["route"] = task.get("route")
    entry["reviews"] = {role: (rows[role] or {}).get("verdict") for role in reviews.ROLES}
    try:
        from datetime import datetime
        started = datetime.fromisoformat(task["started_at"])
        finished = datetime.fromisoformat(task["finished_at"])
        entry["duration_seconds"] = round((finished - started).total_seconds(), 1)
    except (KeyError, TypeError, ValueError):
        entry["duration_seconds"] = None
    return append(w.store.root, entry, actor)

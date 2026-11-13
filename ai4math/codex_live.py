"""Local, resumable progress projection for Codex-managed research tasks."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile
import time
from uuid import uuid4

from .store import ResearchError, now
from .workbench import Workbench


@contextmanager
def workspace(root, *, read_only=True):
    w = Workbench(root, read_only=read_only)
    try:
        yield w
    finally:
        w.store.close()


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".write-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as out:
            json.dump(value, out, ensure_ascii=False)
            out.write("\n")
        os.replace(tmp, path)
    finally:
        Path(tmp).unlink(missing_ok=True)


def event_rows(path):
    """A reader may observe a partial last line while the owner is appending."""
    try:
        with Path(path).open("rb") as stream:
            for line in stream:
                if not line.endswith(b"\n"):
                    break
                yield json.loads(line)
    except FileNotFoundError:
        return


class EventLog:
    """Single writer per task. Live observations are separate from proof records."""
    def __init__(self, directory, task):
        self.directory = Path(directory)
        self.task = task
        self.path = self.directory / "events.jsonl"
        self.seq = 0
        self.thread_id = self.turn_id = None
        self.last_event_at = self.last_progress_at = now()
        self.directory.mkdir(parents=True, exist_ok=True)
        with self.path.open("xb"):
            pass

    def emit(self, kind, **data):
        self.seq += 1
        self.last_event_at = now()
        if kind == "progress":
            self.last_progress_at = self.last_event_at
        row = {"seq": self.seq, "at": self.last_event_at, "task": self.task, "kind": kind,
               "thread_id": self.thread_id, "turn_id": self.turn_id, "data": data}
        with self.path.open("ab") as stream:
            stream.write((json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8"))
        return row

    def heartbeat(self, **data):
        atomic_json(self.directory / "heartbeat.json", {
            "at": now(), "seq": self.seq, "task": self.task, "thread_id": self.thread_id,
            "turn_id": self.turn_id, "last_event_at": self.last_event_at,
            "last_progress_at": self.last_progress_at, **data,
        })


def _elapsed(value):
    if not value:
        return None
    return max(0, int((datetime.now(timezone.utc) - datetime.fromisoformat(value)).total_seconds()))


def _task_view(w, task):
    tid = task["id"]
    live = w.store.inside(Path("tasks") / tid / "live")
    try:
        heartbeat = read_json(live / "heartbeat.json")
    except FileNotFoundError:
        heartbeat = {}
    stage, status = task.get("external_stage"), task["status"]
    held = task.get("codex_control", {}).get("held", False)
    state = status
    if status == "running":
        state = "held" if held else stage
        if stage == "sealed":
            state = "ready_for_intake" if task.get("stop_state") == "confirmed" else "stopping"
        elif stage != "awaiting_coordinator_review" and not held:
            age = _elapsed(heartbeat.get("at") or task["started_at"])
            silent = _elapsed(heartbeat.get("last_progress_at") or task["started_at"])
            if age is not None and age > 20:
                state = "monitor_stale"
            elif silent is not None and silent >= 900:
                state = "check_progress"
    action = {"awaiting_coordinator_review": "review and release the worker literature receipt",
              "ready_for_intake": "independently review sealed outputs and finish_external",
              "monitor_stale": "inspect the owned process and capture receipts",
              "check_progress": "inspect the latest checkpoint or ask the worker for an update"}.get(state)
    return {"task": tid, "node": task["node"], "route": task["route"], "status": status, "state": state,
            "stage": stage, "action": action, "elapsed_seconds": _elapsed(task.get("started_at")) if status == "running" else None,
            "total_deadline_at": task.get("external_deadline_at"), "worker_receipt_sha256": task.get("worker_receipt_sha256"),
            "stop_state": task.get("stop_state"), "failure_reason": task.get("failure_reason"),
            "heartbeat": heartbeat}


def queue_view(root, after=None, wait=0, limit=30):
    from .strategy import due, state
    after = {} if after is None else after
    if not isinstance(after, dict) or any(not isinstance(k, str) or type(v) is not int or v < 0 for k, v in after.items()):
        raise ResearchError("queue cursor must be a JSON object mapping task IDs to nonnegative sequences")
    if not 0 <= wait <= 60 or not 1 <= limit <= 100:
        raise ResearchError("queue wait must be 0..60 and limit 1..100")
    end = time.monotonic() + wait
    initial_states = None
    while True:
        with workspace(root) as w, w.store.snapshot():
            strategy = state(w)
            held = w.store.get("codex_dispatch", "main") if w.store.exists("codex_dispatch", "main") else {}
            tasks = [t for t in w.store.all("task") if t.get("codex_workflow")]
            views = [_task_view(w, t) for t in tasks if t["status"] == "running"]
            terminal = sorted((t for t in tasks if t.get("finished_at")), key=lambda t: t["finished_at"], reverse=True)[:12]
            batches = []
            for task in tasks:
                tid = task["id"]
                if not wait or task["status"] != "pending":
                    path = w.store.inside(Path("tasks") / tid / "live/events.jsonl")
                    events = [e for e in event_rows(path) if e["seq"] > after.get(tid, 0)]
                    if events:
                        batches.append(iter(events))
            # Keep each task's append order even if wall time moves backwards.
            heads = {i: next(stream) for i, stream in enumerate(batches)}
            rows = []
            while heads and len(rows) < limit:
                index = min(heads, key=lambda i: (heads[i]["at"], heads[i]["task"], heads[i]["seq"]))
                rows.append(heads.pop(index))
                if following := next(batches[index], None):
                    heads[index] = following
            cursor = dict(after)
            for row in rows:
                cursor[row["task"]] = row["seq"]
            priority = {"awaiting_coordinator_review": 0, "monitor_stale": 1, "ready_for_intake": 2, "check_progress": 3}
            views.sort(key=lambda v: (priority.get(v["state"], 4), v["task"]))
            result = {"at": now(), "cursor": cursor, "events": rows, "tasks": views,
                      "recent_terminal": [_task_view(w, t) for t in terminal],
                      "review_due": due(strategy), "decision": strategy.get("decision") if strategy else None,
                      "dispatch_held": held.get("held", False),
                      "report": str(w.store.root / "reports/research-progress.html")}
            states = [(t["id"], t["status"], t.get("external_stage"), t.get("stop_state")) for t in tasks]
        if initial_states is None:
            initial_states = states
        if rows or states != initial_states or any(v["action"] for v in views) or time.monotonic() >= end:
            return result
        time.sleep(min(0.25, max(0, end - time.monotonic())))


def control_request(w, task_id, actor, action, *, turn=None, event=None, text=None, reason=None):
    task = w.store.get("task", task_id)
    if not task.get("codex_workflow") or task["status"] != "running" or task["dispatched_by"] != actor:
        raise ResearchError("live control needs the dispatching actor and a running Codex task")
    directory = w.store.inside(Path("tasks") / task_id / "live")
    if action == "steer":
        if not isinstance(turn, str) or not turn or type(event) is not int or not isinstance(text, str) or not 0 < len(text.strip()) <= 4000:
            raise ResearchError("steer needs the expected turn, triggering event sequence and 1..4000 characters")
        trigger = next((row for row in event_rows(directory / "events.jsonl") if row["seq"] == event), None)
        if trigger is None or trigger["turn_id"] != turn:
            raise ResearchError("triggering event must belong to the expected active turn")
    elif action != "interrupt" or not isinstance(reason, str) or not reason.strip() or len(reason) > 4000:
        raise ResearchError("interrupt requires an explicit stop reason of up to 4000 characters")
    rid = uuid4().hex
    data = {"id": rid, "action": action, "task": task_id, "actor": actor, "at": now(),
            "contract_sha256": task["contract_artifact"], "turn_id": turn, "event": event, "text": text, "reason": reason}
    atomic_json(directory / "requests" / f"{rid}.json", data)
    return data

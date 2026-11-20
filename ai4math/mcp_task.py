"""Task-scoped worker tools for MCP clients and one-shot CLI invocations."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from ._atomic import file_lock, publish_new
from .codex_live import atomic_json, read_json, workspace
from .codex_worker_tools import TOOL, TOOL_NAME, VIEW_BYTES, WorkerTools
from .research import allowed_source
from .store import ResearchError, file_hash, identifier, now

PROTOCOL_VERSION = "2024-11-05"
SERVER_INFO = {"name": "ai4math-task", "version": "1.0"}
BROKER_STATE = "worker-broker.json"
BROKER_LOCK = ".worker-broker.lock"
HOSTS = ("codex-cli", "codex-desktop", "claude-code")


def _write_new(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(data)


class TaskServer:
    """A broker whose phase and capture history survive client process exit."""

    def __init__(self, root, tid, library):
        self.root = Path(root).resolve(strict=True)
        self.tid = identifier(tid)
        self.library = Path(library).resolve(strict=True)
        self.directory = self.root / "tasks" / self.tid
        self.live = self.directory / "live"

    def _task(self):
        with workspace(self.root) as w:
            task = w.store.get("task", self.tid)
            profile = task.get("codex_workflow")
            if not profile or task["status"] != "running" or not task.get("contract_artifact"):
                raise ResearchError("worker broker requires a running task with a frozen contract")
            roots = profile["source_roots"]
            if not self.library.is_dir() or (roots and str(self.library) not in roots):
                raise ResearchError("worker library is outside the frozen source_roots")
            allowed_source(w, self.library)
        paths = (self.directory, self.live, self.live / BROKER_LOCK, self.live / BROKER_STATE, self.live / "tools")
        if any(path.resolve() != path for path in paths):
            raise ResearchError("worker broker paths must be canonical")
        for name, key in (("input.json", "contract_artifact"), ("PROMPT.md", "prompt_artifact")):
            path = self.directory / name
            if path.resolve() != path or not path.is_file() or file_hash(path) != task[key]:
                raise ResearchError(f"worker {name} differs from its frozen artifact")
        preflight = self.directory / "subagent-preflight.json"
        if preflight.is_file():
            prepared = read_json(preflight)
            if prepared.get("input_sha256") != task["contract_artifact"] or prepared.get("library") != str(self.library):
                raise ResearchError("worker broker call differs from native subagent preflight")
        return task

    def _restore(self, task):
        self.tools = WorkerTools(self.root, task, self.library)
        self.binding = {"task": self.tid, "contract_sha256": task["contract_artifact"], "library": str(self.library)}
        self.acknowledged, self.pending = set(), set()
        state = self.live / BROKER_STATE
        tool_dir = self.live / "tools"
        used = sorted(int(path.name) for path in tool_dir.iterdir() if path.name.isdigit()) if tool_dir.is_dir() else []
        self.calls = len(used)
        if used != list(range(self.calls)):
            raise ResearchError("worker broker capture sequence changed; review the retained captures")
        if state.is_file():
            saved = read_json(state)
            if saved.get("version") != 1 or saved.get("binding") != self.binding:
                raise ResearchError("worker broker state differs from the task, contract or library")
            if saved.get("in_flight") is not None or saved.get("next_call") != self.calls:
                raise ResearchError("worker broker action was interrupted or capture sequence changed; review the retained captures")
            self.tools.restore_broker_state(saved.get("worker"))
            self.acknowledged = set(saved["acknowledged"])
            self.pending = set(saved["pending"])
        elif used:
            self._restore_legacy(tool_dir, used)

    def _restore_legacy(self, tool_dir, used):
        """Restore complete, read-only legacy actions without inventing spent budgets."""
        legacy = self.live / "worker-state.json"
        if not legacy.is_file():
            raise ResearchError("worker broker state is missing for existing captures")
        for number in sorted(used):
            folder = tool_dir / f"{number:05d}"
            request_path, result_path = folder / "request.json", folder / "result.json"
            if not request_path.is_file() or not result_path.is_file():
                raise ResearchError("legacy worker capture is incomplete; review it before resuming")
            request = read_json(request_path)["arguments"]
            action = request["action"]
            if action not in {"contract", "read", "search", "literature_ready", "literature_updates"}:
                raise ResearchError("legacy worker state is incomplete for this task; finish it with the original broker")
            if action == "read":
                result = read_json(result_path)["result"]
                aid = result.get("artifact_id")
                if aid:
                    end = request["payload"].get("offset", 0) + len(result["text"])
                    self.tools.seen[aid] = max(self.tools.seen.get(aid, 0), end)
                    if result.get("next_offset") is None:
                        self.tools.complete_reads.add(aid)
        data = read_json(legacy)
        self.tools.read_inputs = set(data.get("read_inputs") or [])
        self.tools.queries = list(data.get("queries") or [])

    def _save_state(self, *, in_flight=None):
        atomic_json(self.live / BROKER_STATE, {"version": 1, "binding": self.binding,
            "next_call": self.calls, "in_flight": in_flight, "worker": self.tools.broker_state(),
            "acknowledged": sorted(self.acknowledged), "pending": sorted(self.pending)})

    def _publish_handoff(self):
        if self.tools.handoff is None:
            return
        path = self.live / "handoff.json"
        record = {**self.tools.handoff, "broker_capture": {
            "tool_bytes": self.tools.tool_bytes, "source_bytes": self.tools.source_bytes}}
        if path.exists():
            if read_json(path) != record:
                raise ResearchError("published handoff differs from committed worker broker state")
        else:
            publish_new(path, json.dumps(record, ensure_ascii=False).encode("utf-8"))

    def _drain_steers(self):
        requests = self.live / "requests"
        if not requests.is_dir():
            return []
        fresh = []
        for path in sorted(requests.glob("*.json")):
            if path.stem in self.acknowledged:
                continue
            try:
                row = read_json(path)
            except (OSError, ValueError):
                continue
            if not isinstance(row, dict) or row.get("action") != "steer":
                continue
            if row.get("id") != path.stem or not isinstance(row.get("text"), str):
                continue
            fresh.append({"id": row["id"], "text": row["text"][:1000]})
            self.pending.add(row["id"])
        return fresh

    def execute(self, arguments):
        if not isinstance(arguments, dict) or not isinstance(arguments.get("action"), str):
            raise ResearchError("ai4math_task needs action and payload")
        action = arguments["action"]
        payload = arguments.get("payload")
        if not isinstance(payload, dict):
            raise ResearchError("ai4math_task needs a payload object")
        task = self._task()
        self.live.mkdir(exist_ok=True)
        with file_lock(self.live / BROKER_LOCK):
            self._restore(task)
            self._publish_handoff()
            if self.tools.handoff is not None:
                raise ResearchError("worker handoff already frozen; finish the model turn")
            steers = self._drain_steers()
            if action == "handoff" and self.pending - self.acknowledged:
                raise ResearchError("acknowledge outstanding coordinator corrections with progress before handoff")
            call = self.calls
            self.calls += 1
            # If the process dies after this point, a restart needs review rather
            # than replaying an action with unknown side effects or byte costs.
            self._save_state(in_flight=call)
            capture = self.live / "tools" / f"{call:05d}"
            capture.mkdir(parents=True, exist_ok=False)
            _write_new(capture / "request.json",
                       json.dumps({"at": now(), "arguments": arguments}, ensure_ascii=False).encode("utf-8"))
            try:
                result = self.tools.execute(action, payload, capture)
                if action == "progress":
                    if steers:
                        result = {**result, "steers": steers}
                    ack = payload.get("ack")
                    if isinstance(ack, str) and ack in self.pending:
                        _write_new(self.live / "responses" / f"{ack}.json",
                                   json.dumps({"id": ack, "accepted": True}, ensure_ascii=False).encode("utf-8"))
                        self.acknowledged.add(ack)
                        self.pending.remove(ack)
            except (ResearchError, OSError, ValueError, KeyError, TypeError) as exc:
                _write_new(capture / "result.json", json.dumps(
                    {"success": False, "at": now(), "error": str(exc)}, ensure_ascii=False).encode("utf-8"))
                self._save_state()
                raise
            _write_new(capture / "result.json", json.dumps(
                {"success": True, "at": now(), "result": result}, ensure_ascii=False).encode("utf-8"))
            self._save_state()
            if action == "handoff":
                self._publish_handoff()
        text = json.dumps(result, ensure_ascii=False)
        if len(text.encode("utf-8")) > VIEW_BYTES:
            text = json.dumps({"error": "tool projection exceeded 24 KiB; use a narrower query"}, ensure_ascii=False)
        return text

    def handle(self, message):
        method = message.get("method")
        rid = message.get("id")
        params = message.get("params") or {}
        if method == "initialize":
            return {"jsonrpc": "2.0", "id": rid, "result": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": SERVER_INFO}}
        if method in {"notifications/initialized", "initialized"}:
            return None
        if method == "tools/list":
            return {"jsonrpc": "2.0", "id": rid, "result": {"tools": [TOOL]}}
        if method == "ping":
            return {"jsonrpc": "2.0", "id": rid, "result": {}}
        if method == "tools/call":
            name = params.get("name")
            if name != TOOL_NAME:
                return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32602, "message": "unknown tool"}}
            try:
                text = self.execute(params.get("arguments"))
            except (ResearchError, OSError, ValueError, KeyError, TypeError) as exc:
                return {"jsonrpc": "2.0", "id": rid, "result": {
                    "content": [{"type": "text", "text": json.dumps({"error": str(exc)}, ensure_ascii=False)}],
                    "isError": True}}
            return {"jsonrpc": "2.0", "id": rid, "result": {
                "content": [{"type": "text", "text": text}], "isError": False}}
        if rid is None:
            return None
        return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": "unsupported method"}}


def serve(root, tid, library, stdin=None, stdout=None):
    server = TaskServer(root, tid, library)
    source = stdin or sys.stdin.buffer
    sink = stdout or sys.stdout.buffer
    for raw in source:
        line = raw.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except ValueError:
            continue
        if not isinstance(message, dict):
            continue
        reply = server.handle(message)
        if reply is not None:
            sink.write((json.dumps(reply, ensure_ascii=False) + "\n").encode("utf-8"))
            sink.flush()
    return 0

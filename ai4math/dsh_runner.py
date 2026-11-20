"""ACP (DSH) worker driver: the same ai4math_task contract over a standard wire.

DSH exposes ACP over stdio: session/new mounts an MCP server, session/prompt runs
a turn, session/update streams semantic events and session/cancel stops a turn.
The worker tool itself is the MCP server in ai4math.mcp_task, so the phase checks
and byte budgets are the ones the Codex driver already enforces.
"""
from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import sys
import threading
from pathlib import Path

from .codex_live import EventLog, read_json, workspace
from .codex_worker_tools import WorkerTools, _write_new
from .store import ResearchError, digest, file_hash, now

ACP_PROTOCOL = 1


def runtime_command(executable):
    """Turn a dsh entry point into an argv prefix Popen can run on Windows."""
    path = Path(executable)
    suffix = path.suffix.lower()
    if suffix == ".js":
        node = os.environ.get("AI4MATH_DSH_NODE") or shutil.which("node") or "node"
        return [node, str(path)]
    if suffix in {".cmd", ".bat"}:
        return [os.environ.get("COMSPEC", "cmd.exe"), "/c", str(path)]
    if suffix == ".ps1":
        return ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(path)]
    return [str(path)]


class AcpWire:
    """Line-delimited JSON-RPC over stdio, with both raw streams retained."""

    def __init__(self, argv, cwd, live, limit):
        self.live = Path(live)
        self.limit = limit
        self.captured = 0
        self.counter = 0
        self.messages = queue.Queue()
        self.responses = {}
        self.error = None
        self.proc = subprocess.Popen(argv, cwd=cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE)
        self.threads = [threading.Thread(target=self._capture, args=(name, pipe), daemon=True)
                        for name, pipe in (("stdout", self.proc.stdout), ("stderr", self.proc.stderr))]
        for thread in self.threads:
            thread.start()

    def _capture(self, name, pipe):
        try:
            with (self.live / ("acp." + name + ".raw")).open("xb") as out:
                while True:
                    data = pipe.readline() if name == "stdout" else pipe.read1(32768)
                    if not data:
                        break
                    out.write(data)
                    out.flush()
                    self.captured += len(data)
                    if self.limit is not None and self.captured > self.limit:
                        self.error = "ACP stream limit exceeded"
                        break
                    if name == "stdout":
                        try:
                            message = json.loads(data)
                        except ValueError:
                            self.error = "ACP stdout line is not JSON"
                            break
                        self.messages.put(message)
        except OSError as exc:
            self.error = str(exc)
        finally:
            pipe.close()

    def send(self, message):
        raw = (json.dumps(message, ensure_ascii=False) + "\n").encode("utf-8")
        with (self.live / "acp.requests.jsonl").open("ab") as stream:
            stream.write(raw)
        self.proc.stdin.write(raw)
        self.proc.stdin.flush()

    def request(self, method, params):
        self.counter += 1
        self.send({"jsonrpc": "2.0", "id": self.counter, "method": method, "params": params})
        return self.counter

    def notify(self, method, params):
        self.send({"jsonrpc": "2.0", "method": method, "params": params})

    def poll(self, timeout=0.1):
        try:
            return self.messages.get(timeout=timeout)
        except queue.Empty:
            return None

    def close(self):
        try:
            self.proc.stdin.close()
        except OSError:
            pass
        try:
            code = self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            code = self.proc.wait(timeout=10)
        for thread in self.threads:
            thread.join(timeout=5)
        if self.error:
            raise ResearchError("ACP capture failed: " + self.error)
        return code


class AcpSession:
    """One ACP session owning the task prompt sequence."""

    def __init__(self, wire, log, tools, library):
        self.wire = wire
        self.log = log
        self.tools = tools
        self.library = library
        self.session_id = None
        self.turn_stops = []
        self.seen_controls = set()
        self.denied_permissions = 0

    def task(self):
        with workspace(self.tools.root) as w:
            return w.store.get("task", self.tools.tid)

    def event(self, message):
        if message is None:
            return
        if "method" not in message:
            self.wire.responses[message.get("id")] = message
            return
        method, params = message["method"], message.get("params") or {}
        if "id" in message:
            if method == "session/request_permission":
                self.denied_permissions += 1
                self.wire.send({"jsonrpc": "2.0", "id": message["id"],
                                "result": {"outcome": {"outcome": "cancelled"}}})
            else:
                self.wire.send({"jsonrpc": "2.0", "id": message["id"],
                                "error": {"code": -32601, "message": "unsupported client method"}})
            return
        if method != "session/update":
            return
        update = params.get("update") or {}
        kind = update.get("sessionUpdate")
        if kind in {"agent_message_chunk", "agent_message"}:
            content = update.get("content") or {}
            text = content.get("text") if isinstance(content, dict) else None
            if isinstance(text, str) and text.strip():
                self.log.emit("message", text=text[:1200], partial=kind.endswith("chunk"))
        elif kind in {"tool_call", "tool_call_update"}:
            status = update.get("status")
            name = update.get("title") or update.get("kind") or "tool"
            if status in {"pending", "in_progress"}:
                self.log.emit("tool_started", action=str(name)[:80])
            elif status in {"completed", "failed"}:
                self.log.emit("tool_completed", action=str(name)[:80], success=status == "completed")

    def controls(self):
        requests = self.log.directory / "requests"
        if not requests.is_dir():
            return
        for path in sorted(requests.glob("*.json")):
            if path.stem in self.seen_controls:
                continue
            self.seen_controls.add(path.stem)
            try:
                row = read_json(path)
            except (OSError, ValueError):
                continue
            if not isinstance(row, dict) or row.get("action") != "interrupt":
                continue
            if self.session_id:
                self.wire.notify("session/cancel", {"sessionId": self.session_id})
            self.log.emit("interrupt_accepted", request_id=row.get("id"))
            target = self.log.directory / "responses" / (str(row.get("id")) + ".json")
            _write_new(target, json.dumps({"id": row.get("id"), "accepted": True}).encode("utf-8"))

    def start(self, model):
        init = self.rpc("initialize", {"protocolVersion": ACP_PROTOCOL, "clientCapabilities": {}})
        self.log.emit("initialize", protocol=init.get("protocolVersion"))
        command = [model["python"], "-m", "ai4math", "--workspace", str(self.tools.root), "mcp-task",
                   "--task", self.tools.tid, "--library", str(self.library)]
        repo = str(Path(__file__).resolve().parents[1])
        env = [{"name": "PYTHONPATH", "value": repo}, {"name": "PYTHONIOENCODING", "value": "utf-8"}]
        mcp = [{"name": "ai4math", "command": command[0], "args": command[1:], "env": env}]
        # The task skill comes from the user-level skill root, so the session can
        # live in the coordinator's directory: that is what makes the worker
        # session show up next to the coordinator in the DSH workspace list.
        cwd = os.environ.get("AI4MATH_DSH_CWD") or str(Path.cwd())
        result = self.rpc("session/new", {"cwd": cwd, "mcpServers": mcp})
        self.session_id = result.get("sessionId")
        if not self.session_id:
            raise ResearchError("ACP session/new returned no sessionId")
        self.log.thread_id = self.session_id
        self.log.emit("thread_started")
        try:
            _write_new(self.log.directory / "dsh-session.json", json.dumps(
                {"session_id": self.session_id, "cwd": cwd}, ensure_ascii=False).encode("utf-8"))
        except FileExistsError:
            pass

    def rpc(self, method, params):
        rid = self.wire.request(method, params)
        while rid not in self.wire.responses:
            self.event(self.wire.poll())
        reply = self.wire.responses.pop(rid)
        if "error" in reply:
            raise ResearchError(method + ": " + json.dumps(reply["error"], ensure_ascii=False))
        return reply.get("result", {})

    def turn(self, instruction):
        if not self.session_id:
            raise ResearchError("ACP turn before session start")
        rid = self.wire.request("session/prompt", {"sessionId": self.session_id,
                                                   "prompt": [{"type": "text", "text": instruction}]})
        while rid not in self.wire.responses:
            self.event(self.wire.poll())
            self.controls()
        reply = self.wire.responses.pop(rid)
        if "error" in reply:
            raise ResearchError("session/prompt: " + json.dumps(reply["error"], ensure_ascii=False))
        stop = (reply.get("result") or {}).get("stopReason")
        self.turn_stops.append(stop)
        self.log.emit("turn_completed", status=stop)
        if stop != "end_turn":
            raise ResearchError("ACP turn stopped with " + str(stop))

    def wait_release(self):
        while True:
            task = self.task()
            self.event(self.wire.poll(timeout=0.25))
            self.controls()
            if task["status"] != "running":
                raise ResearchError("task stopped before exploration")
            if task["external_stage"] == "exploration" and not task.get("codex_control", {}).get("held"):
                with workspace(self.tools.root) as w, w.store.snapshot():
                    release = w.store.get("exploration_release", task["id"])
                    receipt = w.store.get("worker_literature", task["id"])
                if release["contract_sha256"] != task["contract_artifact"] or release["worker_receipt_sha256"] != digest(receipt):
                    raise ResearchError("release does not bind this contract and worker receipt")
                self.log.emit("exploration_released", release_sha256=digest(release))
                return
            if task["external_stage"] not in {"awaiting_coordinator_review", "exploration"}:
                raise ResearchError("literature turn ended without its required ready receipt")


def worker(root, tid, executable, library, model_python=None):
    """Owned ACP worker for one frozen task; mirrors codex_runner.worker."""
    from .codex_runner import turn_instructions
    with workspace(root) as w:
        task = w.store.get("task", tid)
    tools = WorkerTools(root, task, library)
    prep = read_json(tools.directory / "codex-preflight.json")
    if (prep.get("driver") != "acp" or prep["runtime_sha256"] != file_hash(executable)
            or prep.get("python_sha256") != file_hash(sys.executable) or prep["input_sha256"] != task["contract_artifact"]):
        raise ResearchError("owned runtime or contract changed after preflight")
    log = EventLog(tools.live, tid)
    profile = os.environ.get("AI4MATH_DSH_PROFILE", "acp")
    argv = runtime_command(executable) + ["--profile", profile]
    _write_new(log.directory / "launch.json", json.dumps({
        "argv": argv, "at": now(), "python": sys.version, "driver": "acp",
        "runtime_sha256": file_hash(executable), "contract_sha256": task["contract_artifact"]},
        ensure_ascii=False).encode("utf-8"))
    wire = AcpWire(argv, str(tools.directory), log.directory, task["output_limit_bytes"])
    session = AcpSession(wire, log, tools, library)
    try:
        session.start({"python": model_python or sys.executable})
        first, second = turn_instructions(tools)
        session.turn(first)
        session.wait_release()
        session.turn(second)
        # A model can stop right after writing return.json.  Do not lose the
        # prepared evidence: ask for the missing handoff a bounded number of times.
        attempts = 0
        while not tools.handoff and attempts < 2:
            attempts += 1
            session.turn("Your handoff is not registered yet. Call ai4math_task with action "
                         "handoff and the exact return/ paths now, then end the turn. Do not add new evidence.")
        try:
            exit_code = wire.close()
            close_error = None
        except (ResearchError, OSError, subprocess.SubprocessError) as exc:
            # A noisy ACP shutdown must never hide a prepared handoff: record it
            # and continue to the seal preflight.
            exit_code, close_error = None, str(exc)
        capture = {"task": tid, "contract_sha256": task["contract_artifact"], "driver": "acp",
                   "close_error": close_error,
                   "session_id": session.session_id, "acp_exit_code": exit_code,
                   "turn_stops": session.turn_stops, "denied_permissions": session.denied_permissions,
                   "tool_bytes": tools.tool_bytes, "source_bytes": tools.source_bytes,
                   "usage": None, "usage_scope": "unavailable", "provider_request_receipts": False,
                   "runtime_sha256": file_hash(executable),
                   "streams": {name: file_hash(log.directory / name) for name in (
                       "acp.stdout.raw", "acp.stderr.raw", "acp.requests.jsonl")}}
        from .worker_finish import finish
        return finish(tools, log, root, tid, capture)
    finally:
        if wire.proc.poll() is None:
            try:
                wire.close()
            except (OSError, ResearchError, subprocess.SubprocessError):
                pass

"""Owned App Server workers with live progress, steering, and sealed return."""
from __future__ import annotations

from collections import deque
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
from pathlib import Path
import queue
import shutil
import sqlite3
import subprocess
import sys
import threading
import time

from .codex_live import EventLog, atomic_json, read_json, workspace
from .codex_worker_tools import TOOL, TOOL_NAME, VIEW_BYTES, WorkerTools, _write_new, check_worker_profile
from .store import ResearchError, digest, file_hash, now
from . import __version__, research_return


RUNTIME_FILES = ("codex_runner.py", "codex_live.py", "codex_worker_tools.py", "external_supervisor.py",
                 "external_deadline.py", "execution.py", "literature.py", "literature_inbox.py", "knowledge.py",
                 "seal_batch.py", "workbench.py", "store.py", "evaluation.py", "_atomic.py", "research_return.py")


def runtime_hashes():
    package = Path(__file__).resolve().parent
    return {name: file_hash(package / name) for name in RUNTIME_FILES}


class AppServer:
    """Retain JSONL under the frozen optional byte limit while requests continue."""
    def __init__(self, argv, cwd, live, limit):
        self.live = live
        self.limit = limit
        self.captured = 0
        self.overflow = threading.Event()
        self.lock = threading.Lock()
        self.messages = queue.Queue()
        self.counter = 0
        self.responses = {}
        self.observed_error = None
        self.proc = subprocess.Popen(argv, cwd=cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.threads = [threading.Thread(target=self._capture, args=(name, pipe), daemon=True)
                        for name, pipe in (("stdout", self.proc.stdout), ("stderr", self.proc.stderr))]
        for thread in self.threads:
            thread.start()

    def _capture(self, name, pipe):
        try:
            with (self.live / f"app-server.{name}.raw").open("xb") as out:
                while True:
                    data = pipe.readline(-1 if self.limit is None else self.limit + 1) if name == "stdout" else pipe.read1(32768)
                    if not data:
                        break
                    with self.lock:
                        room = len(data) if self.limit is None else max(0, self.limit - self.captured)
                        out.write(data[:room])
                        out.flush()
                        self.captured += len(data)
                        if self.limit is not None and self.captured > self.limit:
                            self.overflow.set()
                            break
                    if name == "stdout":
                        message = json.loads(data)
                        if not isinstance(message, dict):
                            raise ResearchError("App Server line is not a JSON-RPC object")
                        self.messages.put(message)
        except (OSError, ValueError, ResearchError) as exc:
            self.observed_error = str(exc)
        finally:
            pipe.close()

    def send(self, message):
        raw = (json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        with (self.live / "app-server.requests.jsonl").open("ab") as stream:
            stream.write(raw)
        self.proc.stdin.write(raw)
        self.proc.stdin.flush()

    def request(self, method, params):
        self.counter += 1
        self.send({"id": self.counter, "method": method, "params": params})
        return self.counter

    def poll(self, timeout=0.1):
        if self.overflow.is_set():
            raise ResearchError("App Server stdout/stderr output limit exceeded")
        if self.observed_error:
            raise ResearchError("App Server capture failed: " + self.observed_error)
        try:
            return self.messages.get(timeout=timeout)
        except queue.Empty:
            if self.proc.poll() is not None:
                raise ResearchError(f"App Server exited unexpectedly with code {self.proc.returncode}")
            return None

    def close(self):
        try:
            self.proc.stdin.close()
        except OSError:
            pass
        try:
            code = self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            code = self.proc.wait(timeout=5)
        for thread in self.threads:
            thread.join(timeout=5)
        if any(thread.is_alive() for thread in self.threads):
            raise ResearchError("App Server streams did not close")
        if code or self.overflow.is_set() or self.observed_error:
            raise ResearchError(f"App Server capture did not close cleanly (exit {code})")
        return code


class ResearchSession:
    def __init__(self, wire, log, tools, skill):
        self.wire, self.log, self.tools, self.skill = wire, log, tools, skill
        self.pool = ThreadPoolExecutor(max_workers=1)
        self.tool_future = None
        self.tool_info = None
        self.pending_tools = deque()
        self.calls = set()
        self.started_tools = set()
        self.completed_tools = set()
        self.responses = {}
        self.turn_status = None
        self.accepted_steers = set()
        self.acknowledged_steers = set()
        self.seen_controls = set()
        self.current_tool = None
        self.next_heartbeat = 0
        self.next_report = 0
        self.reported_seq = -1
        self.report_error = None
        self.usage = None
        self.observed_model = None
        self.deltas = {}

    def task(self):
        with workspace(self.tools.root) as w:
            return w.store.get("task", self.tools.tid)

    def event(self, msg):
        if msg is None:
            return
        method, p = msg.get("method"), msg.get("params", {})
        if not method:
            if "id" not in msg:
                raise ResearchError("App Server response is missing an id")
            self.responses[msg["id"]] = msg
            return
        if "id" in msg:
            if method != "item/tool/call":
                self.wire.send({"id": msg["id"], "error": {"code": -32601, "message": "No unmanaged permission or external tool is approved"}})
                self.log.emit("failure", reason="unsupported server request", method=method)
                return
            self.start_tool(msg["id"], p)
            return
        if p.get("threadId") and self.log.thread_id and p["threadId"] != self.log.thread_id:
            raise ResearchError("App Server emitted an event for a foreign thread")
        if method == "turn/started":
            self.log.turn_id = p["turn"]["id"]
            self.turn_status = None
            self.log.emit("turn_started")
        elif method == "turn/completed":
            turn = p["turn"]
            if turn["id"] != self.log.turn_id:
                raise ResearchError("App Server completed an unexpected turn")
            self.turn_status = turn["status"]
            self.log.emit("turn_completed", status=self.turn_status, error=turn.get("error"))
        elif method in {"item/started", "item/completed"}:
            item = p["item"]
            kind = item["type"]
            if kind == "dynamicToolCall":
                target = self.started_tools if method == "item/started" else self.completed_tools
                target.add(item["id"])
            elif kind in {"agentMessage", "userMessage", "reasoning", "plan", "contextCompaction"}:
                if kind == "agentMessage" and method == "item/completed":
                    self.log.emit("message", text=item.get("text", "")[:2000], partial=False)
                    self.deltas.pop(item["id"], None)
            else:
                raise ResearchError(f"unmanaged App Server item {kind}; use the phase-checked ai4math_task tool")
        elif method == "item/agentMessage/delta":
            item = p["itemId"]
            text = self.deltas.get(item, "") + p["delta"]
            if len(text) >= 240 or "\n" in text:
                self.log.emit("message", text=text[:1200], partial=True)
                self.deltas[item] = ""
            else:
                self.deltas[item] = text
        elif method == "thread/tokenUsage/updated":
            self.usage = p.get("tokenUsage")
        elif method in {"warning", "error", "configWarning"}:
            self.log.emit("warning", message=str(p.get("message") or p.get("error") or method)[:600])

    def rpc(self, method, params, timeout=None):
        rid = self.wire.request(method, params)
        end = None if timeout is None else time.monotonic() + timeout
        while rid not in self.responses:
            if end is not None and time.monotonic() >= end:
                raise ResearchError(f"App Server did not respond to {method}")
            self.event(self.wire.poll())
            self.finish_tool()
        reply = self.responses.pop(rid)
        if "error" in reply:
            raise ResearchError(f"{method}: {reply['error']}")
        return reply.get("result", {})

    def start_tool(self, rid, p):
        if (p.get("threadId") != self.log.thread_id or p.get("turnId") != self.log.turn_id
                or p.get("tool") != TOOL_NAME or not isinstance(p.get("callId"), str)
                or p.get("callId") in self.calls or self.turn_status is not None):
            raise ResearchError("unbound, duplicate, or inactive worker tool request")
        if len(self.pending_tools) >= 64:
            raise ResearchError("too many pending worker tool requests")
        arguments = p.get("arguments")
        if not isinstance(arguments, dict) or not isinstance(arguments.get("payload"), dict) or not isinstance(arguments.get("action"), str):
            raise ResearchError("worker tool arguments do not match the advertised schema")
        action = arguments["action"]
        self.calls.add(p["callId"])
        cap = self.log.directory / "tools" / f"{len(self.calls):05d}"
        cap.mkdir(parents=True, exist_ok=False)
        _write_new(cap / "request.json", json.dumps({"at": now(), "turn_id": self.log.turn_id, **p}, ensure_ascii=False).encode("utf-8"))
        self.pending_tools.append((rid, action, cap, p["callId"], arguments["payload"]))
        self._run_next_tool()

    def _run_next_tool(self):
        if self.tool_future is not None or self.turn_status is not None or not self.pending_tools:
            return
        rid, action, cap, call_id, payload = self.pending_tools.popleft()
        self.current_tool = action
        self.log.emit("tool_started", action=action, call_id=call_id)
        self.tool_info = (rid, action, cap, call_id)
        if action == "handoff" and self.accepted_steers - self.acknowledged_steers:
            def need_ack():
                raise ResearchError("acknowledge outstanding coordinator corrections with progress before handoff")
            self.tool_future = self.pool.submit(need_ack)
        else:
            self.tool_future = self.pool.submit(self.tools.execute, action, payload, cap)

    def finish_tool(self):
        if self.tool_future is None or not self.tool_future.done():
            return
        rid, action, cap, call_id = self.tool_info
        try:
            result = self.tool_future.result()
            ok = True
        except (ResearchError, OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
            result, ok = {"error": str(exc)}, False
        self.tool_future = None
        self.current_tool = None
        text = json.dumps(result, ensure_ascii=False)
        if len(text.encode("utf-8")) > VIEW_BYTES:
            if self.tools.task["output_limit_bytes"] is None:
                _write_new(cap / "result.full.json", json.dumps({"success": ok, "at": now(), "result": result}, ensure_ascii=False).encode("utf-8"))
            result, ok = {"error": "tool projection exceeded 24 KiB; use a narrower query or smaller input page"}, False
            text = json.dumps(result, ensure_ascii=False)
        if action != "run":
            self.tools.tool_bytes += len(text.encode("utf-8"))
        _write_new(cap / "result.json", json.dumps({"success": ok, "at": now(), "result": result}, ensure_ascii=False).encode("utf-8"))
        self.wire.send({"id": rid, "result": {"success": ok, "contentItems": [{"type": "inputText", "text": text}]}})
        self.log.emit("tool_completed", action=action, success=ok, call_id=call_id)
        if ok and action == "progress":
            ack = result.get("ack")
            self.log.emit("progress", **result)
            if ack in self.accepted_steers:
                self.acknowledged_steers.add(ack)
                self.log.emit("steer_ack", request_id=ack, summary=result["summary"])
        if ok and action == "literature_ready":
            self.log.emit("literature_ready", **result)
        if ok and action == "request_literature":
            self.log.emit("literature_requested", **result)
        if ok and action == "probe_review":
            self.log.emit("probe_review", **result)
        if ok and action == "handoff":
            self.log.emit("handoff_prepared")
        if self.tools.task["output_limit_bytes"] is not None and self.tools.tool_bytes > self.tools.task["output_limit_bytes"]:
            raise ResearchError("worker tool output limit exceeded")
        self._run_next_tool()

    def controls(self):
        requests = self.log.directory / "requests"
        if not requests.is_dir():
            return
        for path in sorted(requests.glob("*.json")):
            if path.stem in self.seen_controls:
                continue
            self.seen_controls.add(path.stem)
            data = read_json(path)
            rid = data["id"]
            task = self.task()
            if (rid != path.stem or data["contract_sha256"] != task["contract_artifact"]
                    or data["task"] != task["id"] or data["actor"] != task["dispatched_by"]):
                raise ResearchError("invalid live control binding")
            self.log.emit("control_received", request_id=rid, action=data["action"], event=data.get("event"))
            try:
                if self.turn_status is not None or not self.log.turn_id:
                    raise ResearchError("no active turn; read the latest queue state")
                if data["action"] == "steer":
                    if data["turn_id"] != self.log.turn_id or getattr(self.tools, "handoff", None):
                        raise ResearchError("stale turn; read the latest queue state")
                    result = self.rpc("turn/steer", {"threadId": self.log.thread_id, "expectedTurnId": self.log.turn_id,
                        "input": [{"type": "text", "text": data["text"] + f"\nAcknowledge this correction in your next ai4math_task progress call with ack={rid}. Keep the frozen task scope."}]})
                    if result.get("turnId") != self.log.turn_id:
                        raise ResearchError("steer response did not bind the expected turn")
                    self.accepted_steers.add(rid)
                    self.log.emit("steer_accepted", request_id=rid, text=data["text"][:1000], event=data["event"])
                elif data["action"] == "interrupt":
                    self.rpc("turn/interrupt", {"threadId": self.log.thread_id, "turnId": self.log.turn_id}, timeout=3)
                    self.log.emit("interrupt_accepted", request_id=rid)
                else:
                    raise ResearchError("unknown control action")
                reply = {"accepted": True}
            except ResearchError as exc:
                self.log.emit("control_rejected", request_id=rid, reason=str(exc))
                reply = {"accepted": False, "reason": str(exc)}
            atomic_json(self.log.directory / "responses" / f"{rid}.json", {"id": rid, **reply})

    def pulse(self):
        tick = time.monotonic()
        if tick >= self.next_heartbeat:
            proc = self.tools.current_process
            self.log.heartbeat(alive=self.wire.proc.poll() is None, app_server_pid=self.wire.proc.pid,
                current_tool=self.current_tool, tool_pid=proc.pid if proc else None, turn_status=self.turn_status,
                model_stream_bytes=self.wire.captured, tool_bytes=self.tools.tool_bytes,
                source_bytes=self.tools.source_bytes, usage=self.usage,
                near_output_limit=(self.tools.task["output_limit_bytes"] is not None
                    and max(self.wire.captured, self.tools.tool_bytes) >= 0.8 * self.tools.task["output_limit_bytes"]),
                report_error=self.report_error)
            self.next_heartbeat = tick + 5
        if tick >= self.next_report and self.reported_seq != self.log.seq:
            rendered = refresh_report(self.tools.root)
            self.report_error = rendered.get("report_error") if isinstance(rendered, dict) else None
            if not self.report_error:
                self.reported_seq = self.log.seq
            self.next_report = tick + 2

    def start(self):
        self.rpc("initialize", {"clientInfo": {"name": "ai4math", "version": __version__},
            "capabilities": {"experimentalApi": True, "optOutNotificationMethods": [
                "item/reasoning/textDelta", "item/reasoning/summaryTextDelta", "item/reasoning/summaryPartAdded"]}})
        self.wire.send({"method": "initialized"})
        tool = TOOL
        task = getattr(self.tools, "task", {})
        if research_return.enabled(task) or getattr(self.tools, "local_literature", False):
            tool = deepcopy(TOOL)
            actions = tool["inputSchema"]["properties"]["action"]["enum"]
            if research_return.enabled(task):
                actions.append("probe_review")
            if getattr(self.tools, "local_literature", False):
                actions[:] = [action for action in actions if action not in {
                    "fetch", "request_literature", "literature_updates"}]
        params = {"cwd": str(self.tools.directory), "sandbox": "read-only", "approvalPolicy": "never",
                  "serviceName": "ai4math", "dynamicTools": [tool], "ephemeral": False}
        settings = getattr(self.tools, "model_settings", None)
        if settings:
            params.update(model=settings["model"], modelProvider=settings["provider"], allowProviderModelFallback=False,
                          config={"model_reasoning_effort": settings["reasoning_effort"]})
        result = self.rpc("thread/start", params)
        self.observed_model = {k: result.get(k) for k in ("model", "modelProvider", "reasoningEffort")}
        if settings and self.observed_model != {"model": settings["model"], "modelProvider": settings["provider"],
                                                 "reasoningEffort": settings["reasoning_effort"]}:
            raise ResearchError("App Server returned different model settings")
        self.log.thread_id = result["thread"]["id"]
        self.log.emit("thread_started")

    def turn(self, instruction):
        self.turn_status = None
        self.log.turn_id = None
        params = {"threadId": self.log.thread_id, "input": [
            {"type": "text", "text": "$ai4math-research " + instruction},
            {"type": "skill", "name": "ai4math-research", "path": str(self.skill)}]}
        settings = getattr(self.tools, "model_settings", None)
        if settings:
            params.update(model=settings["model"], effort=settings["reasoning_effort"])
        result = self.rpc("turn/start", params)
        turn = result["turn"]["id"]
        if self.log.turn_id not in (None, turn):
            raise ResearchError("App Server start response differs from the event")
        self.log.turn_id = turn
        while self.turn_status is None:
            self.event(self.wire.poll())
            self.finish_tool()
            self.controls()
            self.pulse()
        if self.turn_status != "completed" or self.tool_future is not None or self.pending_tools:
            raise ResearchError("worker turn did not complete with closed tool capture")
        self.check_capture()

    def check_capture(self):
        if self.started_tools != self.completed_tools or self.calls != self.completed_tools:
            raise ResearchError("App Server tool events do not match the recorded dynamic tool calls")

    def drain_closed(self):
        if self.turn_status != "completed":
            raise ResearchError("final worker turn has no successful completion")
        while not self.wire.messages.empty():
            msg = self.wire.messages.get_nowait()
            if "method" in msg and "id" in msg:
                raise ResearchError("unexpected server request after final turn completion")
            if msg.get("method") in {"turn/started", "turn/completed"}:
                raise ResearchError("unexpected turn lifecycle after final completion")
            self.event(msg)
        self.check_capture()
        if self.accepted_steers - self.acknowledged_steers:
            raise ResearchError("final handoff has an unacknowledged coordinator correction")

    def wait_release(self):
        while True:
            task = self.task()
            self.event(self.wire.poll(timeout=0.25))
            self.controls()
            self.pulse()
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


def refresh_report(root):
    from .codex_dashboard import refresh
    try:
        return str(refresh(Path(root)))
    except (OSError, ResearchError, ValueError, sqlite3.Error) as exc:
        return {"report_error": str(exc)}


def worker(root, tid, executable, library):
    with workspace(root) as w:
        task = w.store.get("task", tid)
    tools = WorkerTools(root, task, library)
    prep = read_json(tools.directory / "codex-preflight.json")
    if (prep["runtime_sha256"] != runtime_hashes() or prep["codex_sha256"] != file_hash(executable)
            or prep["python_sha256"] != file_hash(sys.executable) or prep["input_sha256"] != task["contract_artifact"]):
        raise ResearchError("owned runtime or contract changed after preflight")
    log = EventLog(tools.live, tid)
    argv = [executable, "app-server", "--listen", "stdio://"]
    _write_new(log.directory / "launch.json", json.dumps({"argv": argv, "at": now(), "python": sys.version,
        "codex_sha256": file_hash(executable), "contract_sha256": task["contract_artifact"], "runtime_sha256": prep["runtime_sha256"]}, ensure_ascii=False).encode("utf-8"))
    wire = AppServer(argv, tools.directory, log.directory, task["output_limit_bytes"])
    session = ResearchSession(wire, log, tools, tools.directory / ".agents/skills/ai4math-research/SKILL.md")
    try:
        session.start()
        if tools.local_literature:
            acquisition = ("Use only the selected local library. Record missing originals and the specific claims or hypotheses "
                           "that remain conditional. Submit the receipt with what you can check.")
        else:
            acquisition = ("Use request_literature for missing references and continue with available evidence. The independent literature session owns network acquisition. Submit the receipt without waiting for that session."
                           if tools.async_literature else "Use fetch only for a needed original absent from the archive.")
        planning = (" In exploration_plan, identify the load-bearing step in a proof or counterexample and explain "
                    "how the first probe could discriminate between approaches to that step."
                    if research_return.enabled(task) else "")
        session.turn("Use the ai4math_task tool only. Start with action contract. Read all frozen inputs and independently search the library (search kind scan) for route-specific gaps. Read selected archived originals using reuse_passage with passage_id. "
                     + acquisition + planning + " Submit literature_ready and finish the turn. Begin the probe after coordinator release.")
        session.wait_release()
        if research_return.enabled(task):
            deduction = (" Spend research effort on deriving or falsifying the next necessary step. Use experiments where "
                         "their possible outcomes change the argument, and follow the contract's source checks. "
                         "Use saved tool outputs and known hashes for handoff without rereading unchanged files."
                         if research_return.deduction_first(task) else "")
            session.turn("The coordinator released your exact literature receipt. Use the ai4math_task tool only. "
                         "Aim to decide the locked target. Prefer a first probe that discriminates between proof routes "
                         "or can produce a decisive witness. Report its prediction before running it. "
                         "Use the frozen research_continuation schema and respond "
                         "to steering. After the probe, call probe_review and work on its remaining obligation before "
                         "handoff. Use contract only if you need the compact schema again. A run command starts in return/; "
                         "use filenames relative to that directory." + deduction)
        else:
            session.turn("The coordinator released your exact literature receipt. Use the ai4math_task tool only. Start with action contract. Report your first probe before running it, respond to steering, and return an auditable finite or unknown result with handoff.")
        if not tools.handoff:
            raise ResearchError("exploration ended without a complete output handoff")
        exit_code = wire.close()
        session.drain_closed()
        capture = {"task": tid, "contract_sha256": task["contract_artifact"], "thread_id": log.thread_id,
                   "final_turn_id": log.turn_id, "app_server_exit_code": exit_code, "model_stream_bytes": wire.captured,
                   "tool_bytes": tools.tool_bytes, "source_bytes": tools.source_bytes,
                   "tool_calls": len(session.calls), "usage": session.usage,
                   "usage_scope": "app_server_thread", "provider_request_receipts": False,
                   "configured_model": tools.model_settings, "observed_model": session.observed_model,
                   "streams": {name: file_hash(log.directory / name) for name in (
                       "app-server.stdout.raw", "app-server.stderr.raw", "app-server.requests.jsonl")},
                   "tool_records": {path.relative_to(log.directory).as_posix(): file_hash(path)
                                    for path in sorted((log.directory / "tools").glob("*/*.json"))}}
        _write_new(log.directory / "capture-receipt.json", json.dumps(capture, ensure_ascii=False).encode("utf-8"))
        log.emit("capture_closed", app_server_exit_code=exit_code, model_stream_bytes=wire.captured,
                 tool_bytes=tools.tool_bytes, source_bytes=tools.source_bytes, tool_calls=len(session.calls),
                 receipt_sha256=file_hash(log.directory / "capture-receipt.json"))
        with workspace(root, read_only=False) as w:
            actor = task["dispatched_by"]
            report = w.apply("task.preflight_seal_batch", tools.handoff, actor)
            _write_new(log.directory / "seal-preflight.json", json.dumps(report, ensure_ascii=False).encode("utf-8"))
            if not report["passed"]:
                raise ResearchError("seal preflight failed: " + json.dumps(report["errors"], ensure_ascii=False))
            w.apply("task.begin_seal", {"task": tid}, actor)
            sealed = w.apply("task.submit_seal_batch", tools.handoff, actor)
        _write_new(log.directory / "seal-result.json", json.dumps(sealed, ensure_ascii=False).encode("utf-8"))
        log.emit("sealed", receipt=sealed.get("receipt", {}).get("artifact_id"))
        refresh_report(root)
        return 0
    except Exception as exc:
        log.emit("failure", reason=str(exc), model_stream_bytes=wire.captured,
                 tool_bytes=tools.tool_bytes, source_bytes=tools.source_bytes)
        refresh_report(root)
        raise
    finally:
        if wire.proc.poll() is None:
            try:
                wire.close()
            except (OSError, ResearchError, subprocess.SubprocessError):
                pass
        session.pool.shutdown(wait=False, cancel_futures=True)


def run_managed(w, tid, actor, executable, library):
    from .codex_skills import skill_manifest
    from .execution import export_task
    from .external_supervisor import supervise_external
    task = w.store.get("task", tid)
    profile = task.get("codex_workflow")
    if not profile or task["status"] != "pending":
        raise ResearchError("codex-run requires a pending Codex-managed task")
    check_worker_profile(profile)
    if executable is None:
        executable = shutil.which("codex")
    if executable is None:
        raise ResearchError("Codex executable unavailable; set --codex-exe")
    binary = Path(executable).resolve(strict=True)
    library = Path(library).resolve(strict=True)
    if str(library) not in profile["source_roots"]:
        raise ResearchError("the independent academic library must be in frozen source_roots")
    from .research import allowed_source
    allowed_source(w, library)
    manifest = read_json(w.store.verify_blob(profile["skill_manifest_artifact"]))
    if manifest != skill_manifest(check_installed=True):
        raise ResearchError("installed and repository skills must match the task's frozen manifest")
    codex_version = subprocess.run([str(binary), "--version"], check=True, capture_output=True, timeout=15, text=True).stdout.strip()
    app_help = subprocess.run([str(binary), "app-server", "--help"], check=True, capture_output=True, timeout=15, text=True).stdout
    if "--listen" not in app_help or "stdio://" not in app_help:
        raise ResearchError("this Codex version does not expose the required App Server stdio interface")
    folder = Path(export_task(w, tid)["directory"])
    skill_folder = folder / ".agents/skills/ai4math-research"
    repository = Path(__file__).resolve().parent.parent
    source = repository / "skills/ai4math-research"
    if not skill_folder.exists():
        shutil.copytree(source, skill_folder)
    actual = {path.relative_to(skill_folder).as_posix(): file_hash(path) for path in skill_folder.rglob("*") if path.is_file()}
    if actual != manifest["skills"]["ai4math-research"]:
        raise ResearchError("task-local research skill copy differs from its manifest")
    prep = folder / "codex-preflight.json"
    expected = {"codex_version": codex_version, "codex_sha256": file_hash(binary), "library": str(library),
        "skill_manifest_artifact": profile["skill_manifest_artifact"], "input_sha256": file_hash(folder / "input.json"),
        "python": sys.version, "python_sha256": file_hash(sys.executable), "runtime_sha256": runtime_hashes()}
    if not prep.exists():
        _write_new(prep, json.dumps({**expected, "checked_at": now()}, ensure_ascii=False).encode("utf-8"))
    prepared = read_json(prep)
    if any(prepared.get(k) != v for k, v in expected.items()):
        raise ResearchError("prepared worker runtime or input differs")
    boot = ("import os,sys,traceback\n"
            "sys.path.insert(0,sys.argv[1])\n"
            "from ai4math.codex_runner import worker\n"
            "try:\n code=worker(*sys.argv[2:])\n"
            "except Exception:\n traceback.print_exc(); code=1\n"
            "sys.stderr.flush(); sys.stdout.flush(); os._exit(code)\n")
    argv = [sys.executable, "-X", "utf8", "-B", "-c", boot, str(repository), str(w.store.root), tid, str(binary), str(library)]
    try:
        return supervise_external(w, tid, argv, actor, managed_codex=True)
    finally:
        refresh_report(w.store.root)

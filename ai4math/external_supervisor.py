"""Owned-process supervision. Windows Job Objects contain descendants.

The bootstrap blocks before executing worker code, so containment is installed
before dispatch. Closing the Windows job kills its tree even if this supervisor
crashes. This is a trusted-local execution adapter, not an OS security sandbox.
"""
import json
import os
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from .store import ResearchError, now
from .external_deadline import expire, fail


def _codex_stop_mailbox(path, task):
    """Trusted-local stop request, independent of SQLite and worker timestamps."""
    try:
        if path.stat().st_size > 65536:
            return None
        row = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if (not isinstance(row, dict) or row.get("id") != path.stem or row.get("action") != "interrupt"
            or row.get("task") != task["id"] or row.get("contract_sha256") != task["contract_artifact"]
            or row.get("actor") != task["dispatched_by"] or not isinstance(row.get("reason"), str)
            or not row["reason"].strip()):
        return None
    return {"request_id": row["id"], "reason": row["reason"], "actor": row["actor"]}


class WindowsJob:
    def __init__(self):
        import ctypes
        from ctypes import wintypes as wt
        self.ctypes = ctypes
        self.api = ctypes.WinDLL("kernel32", use_last_error=True)
        class Basic(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong), ("PerJobUserTimeLimit", ctypes.c_longlong),
                        ("LimitFlags", wt.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wt.DWORD),
                        ("Affinity", ctypes.c_size_t), ("PriorityClass", wt.DWORD), ("SchedulingClass", wt.DWORD)]
        class IO(ctypes.Structure):
            _fields_ = [(name, ctypes.c_ulonglong) for name in
                        ("ReadOperationCount", "WriteOperationCount", "OtherOperationCount", "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]
        class Extended(ctypes.Structure):
            _fields_ = [("BasicLimitInformation", Basic), ("IoInfo", IO),
                        ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                        ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]
        self.api.CreateJobObjectW.argtypes = [ctypes.c_void_p, wt.LPCWSTR]
        self.api.CreateJobObjectW.restype = wt.HANDLE
        self.api.SetInformationJobObject.argtypes = [wt.HANDLE, ctypes.c_int, ctypes.c_void_p, wt.DWORD]
        self.api.AssignProcessToJobObject.argtypes = [wt.HANDLE, wt.HANDLE]
        self.api.TerminateJobObject.argtypes = [wt.HANDLE, wt.UINT]
        self.api.QueryInformationJobObject.argtypes = [wt.HANDLE, ctypes.c_int, ctypes.c_void_p, wt.DWORD, ctypes.c_void_p]
        self.api.CloseHandle.argtypes = [wt.HANDLE]
        self.handle = self.api.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        info = Extended()
        info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self.api.SetInformationJobObject(self.handle, 9, ctypes.byref(info), ctypes.sizeof(info)):
            self.close()
            raise ctypes.WinError(ctypes.get_last_error())

    def assign(self, process):
        if not self.api.AssignProcessToJobObject(self.handle, int(process._handle)):
            raise self.ctypes.WinError(self.ctypes.get_last_error())

    def stop(self):
        if not self.api.TerminateJobObject(self.handle, 124):
            raise self.ctypes.WinError(self.ctypes.get_last_error())
        class Accounting(self.ctypes.Structure):
            _fields_ = [(name, self.ctypes.c_longlong) for name in
                        ("TotalUserTime", "TotalKernelTime", "ThisPeriodTotalUserTime", "ThisPeriodTotalKernelTime")] + [
                        (name, self.ctypes.c_ulong) for name in
                        ("TotalPageFaultCount", "TotalProcesses", "ActiveProcesses", "TotalTerminatedProcesses")]
        deadline = time.monotonic() + 10
        while True:
            info = Accounting()
            if not self.api.QueryInformationJobObject(self.handle, 1, self.ctypes.byref(info), self.ctypes.sizeof(info), None):
                raise self.ctypes.WinError(self.ctypes.get_last_error())
            if info.ActiveProcesses == 0:
                break
            if time.monotonic() >= deadline:
                raise ResearchError("Windows Job still has active processes after termination")
            time.sleep(0.02)

    def close(self):
        if self.handle:
            self.api.CloseHandle(self.handle)
            self.handle = None


class OwnedProcess:
    def __init__(self, argv, cwd):
        self.stop_lock = threading.Lock()
        self.stopped = False
        self.job = WindowsJob() if os.name == "nt" else None
        # The wrapper never executes user code before receiving the release byte.
        bootstrap = ("import subprocess,sys,json; argv=json.loads(sys.argv[1]); "
                     "token=sys.stdin.buffer.read(1); "
                     "sys.exit(125) if token != b'1' else sys.exit(subprocess.call(argv))")
        options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {"start_new_session": True}
        self.process = None
        try:
            self.process = subprocess.Popen([sys.executable, "-c", bootstrap, json.dumps(argv)], cwd=cwd,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **options)
            if self.job:
                self.job.assign(self.process)
        except BaseException:
            self.stop()
            raise

    def release(self):
        self.process.stdin.write(b"1")
        self.process.stdin.flush()
        self.process.stdin.close()

    def stop(self):
        with self.stop_lock:
            if self.stopped:
                return
            self._stop()
            self.stopped = True

    def _stop(self):
        process = self.process
        try:
            if self.job and self.job.handle:
                self.job.stop()
            elif process and os.name != "nt":
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        finally:
            if self.job:
                self.job.close()
            if process:
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=10)


def supervise_external(w, task_id, argv, actor="human", poll_seconds=0.05, *, managed_codex=False):
    """Start the clock, contain and dispatch argv, then enforce every phase.

argv is explicit coordinator input, never a shell string. Worker processes may
use the CLI to submit receipts. Exit without registered seal completion fails.
"""
    task = w.store.get("task", task_id)
    if task.get("external_deadline_policy", {}).get("supervision") != "process":
        raise ResearchError("supervise-external requires a new process-supervised task")
    if not isinstance(argv, list) or not argv or any(not isinstance(a, str) or not a for a in argv):
        raise ResearchError("supervisor needs a nonempty worker argv")
    from .execution import _start_task
    dispatch_anchor = time.monotonic()
    exported, task, _ = _start_task(w, task_id, actor, external=True, supervised=True, managed_codex=managed_codex)
    process = None
    threads = []
    exceeded = threading.Event()
    capture_failed = threading.Event()
    count = [0]
    lock = threading.Lock()

    def capture(stream, name):
        try:
            with w.store.inside("tasks/" + task_id + "/" + name).open("xb") as output:
                while True:
                    block = os.read(stream.fileno(), 4096)
                    if not block:
                        break
                    with lock:
                        remaining = (len(block) if task["output_limit_bytes"] is None
                                     else max(0, task["output_limit_bytes"] - count[0]))
                        output.write(block[:remaining])
                        count[0] += len(block)
                        if task["output_limit_bytes"] is not None and count[0] > task["output_limit_bytes"]:
                            exceeded.set()
        except BaseException:
            capture_failed.set()
        finally:
            stream.close()

    # Monotonic elapsed time adds protection against local wall-clock rollback.
    seen_phase = task["active_phase"]
    monitored = task["external_deadline_policy"].get("version") == 2
    if task.get("external_deadline_at") is None:
        total_monotonic_deadline = float("inf")
    else:
        total_monotonic_deadline = min(dispatch_anchor + task["timeout_seconds"],
            time.monotonic() + max(0, (datetime.fromisoformat(task["external_deadline_at"])
                                      - datetime.fromisoformat(now())).total_seconds()))
    monotonic_deadline = total_monotonic_deadline
    if not monitored:
        monotonic_deadline = min(total_monotonic_deadline,
            time.monotonic() + max(0, (datetime.fromisoformat(task["phase_timing"][seen_phase]["deadline_at"])
                                      - datetime.fromisoformat(now())).total_seconds()),
            dispatch_anchor + task["external_deadline_policy"]["phase_budgets"][seen_phase])
    stop_error = None
    watchdog_done = threading.Event()
    watchdog_expired = threading.Event()
    mailbox_due = threading.Event()
    mailbox_stop = []
    seen_mailbox = set()
    deadline_box = [monotonic_deadline]
    owned_box = [None]
    watchdog_errors = []

    def watchdog():
        # No SQLite calls: a worker holding the write lock cannot delay killing.
        while not watchdog_done.wait(0.02):
            if managed_codex and not mailbox_stop:
                try:
                    requests = list((Path(exported["directory"]) / "live/requests").glob("*.json"))
                except OSError:
                    requests = []  # A mailbox read failure must not disable the deadline watchdog.
                for path in requests:
                    if path.name in seen_mailbox:
                        continue
                    seen_mailbox.add(path.name)
                    request = _codex_stop_mailbox(path, task)
                    if request:
                        mailbox_stop.append({**request, "observed_at": now(),
                                             "monotonic_due": time.monotonic() + 3})
                        break
            if mailbox_stop and time.monotonic() >= mailbox_stop[0]["monotonic_due"]:
                mailbox_due.set()
            timed_out = time.monotonic() >= deadline_box[0]
            if timed_out or exceeded.is_set() or capture_failed.is_set() or mailbox_due.is_set():
                if timed_out:
                    watchdog_expired.set()
                if owned_box[0] is not None:
                    try:
                        owned_box[0].stop()
                        if mailbox_due.is_set():
                            receipt = {k: v for k, v in mailbox_stop[0].items() if k != "monotonic_due"}
                            receipt.update(task=task_id, contract_sha256=task["contract_artifact"],
                                stopped_at=now(), grace_seconds=3, local_tree_stopped=True,
                                pid=owned_box[0].process.pid, trust_basis="native owner containment; no remote-generation assertion")
                            path = Path(exported["directory"]) / "live/native-stop.json"
                            with path.open("x", encoding="utf-8") as stream:
                                json.dump(receipt, stream)
                    except BaseException as exc:
                        watchdog_errors.append(str(exc))
                    return

    watcher = threading.Thread(target=watchdog, daemon=True)
    watcher.start()
    try:
        process = OwnedProcess(argv, exported["directory"])
        owned_box[0] = process
        with w.store.transaction("task.supervisor_started", actor):
            w.store.put("external_supervisor", task_id, {"id": task_id, "pid": process.process.pid,
                "argv": argv, "started_at": now(), "containment": "windows_job" if os.name == "nt" else "posix_process_group"}, new=True)
        for stream, name in ((process.process.stdout, "supervisor.stdout.log"), (process.process.stderr, "supervisor.stderr.log")):
            thread = threading.Thread(target=capture, args=(stream, name), daemon=True)
            thread.start()
            threads.append(thread)
        # Deadline may have elapsed while containment was being established.
        if not expire(w, task_id) and not watchdog_expired.is_set() and time.monotonic() < monotonic_deadline:
            process.release()
        while True:
            expire(w, task_id)
            current = w.store.get("task", task_id)
            if current["status"] != "running" or current.get("external_stage") == "sealed":
                break
            phase = current["active_phase"]
            if phase != seen_phase and not monitored:
                seen_phase = phase
                monotonic_deadline = time.monotonic() + max(0, (datetime.fromisoformat(
                    current["phase_timing"][phase]["deadline_at"]) - datetime.fromisoformat(now())).total_seconds())
                monotonic_deadline = min(monotonic_deadline, total_monotonic_deadline,
                    time.monotonic() + current["external_deadline_policy"]["phase_budgets"][phase])
                deadline_box[0] = monotonic_deadline
            reason = None
            failure_status = "failed"
            if watchdog_expired.is_set() or time.monotonic() >= monotonic_deadline:
                reason = "monotonic " + ("total" if monitored else "phase") + " deadline exceeded: " + phase
                failure_status = "unknown_timeout"
            elif exceeded.is_set():
                reason = "external worker output limit exceeded"
            elif capture_failed.is_set():
                reason = "external worker output capture failed"
            elif mailbox_due.is_set():
                reason = "coordinator requested Codex stop: " + mailbox_stop[0]["reason"]
                failure_status = "interrupted"
            elif process.process.poll() is not None:
                reason = "external worker exited before registered seal completion"
            if reason:
                # A v2 task may have no total timeout. Its failed owner can
                # leave a child holding SQLite or an inherited pipe: stop the
                # contained tree before attempting any terminal DB write.
                if monitored:
                    process.stop()
                with w.store.transaction("task.supervisor_failed", actor):
                    fail(w, w.store.get("task", task_id), reason, failure_status)
                break
            time.sleep(poll_seconds)
    except BaseException as exc:
        if monitored and process:
            try:
                process.stop()
            except BaseException as stop_exc:
                stop_error = str(stop_exc)
        with w.store.transaction("task.supervisor_failed", actor):
            fail(w, w.store.get("task", task_id), "supervisor error: " + str(exc), "failed")
        raise
    finally:
        watchdog_done.set()
        if process:
            try:
                process.stop()
            except BaseException as exc:
                stop_error = str(exc)
            for thread in threads:
                drain_timeout = None if stop_error is None and task["output_limit_bytes"] is None else 2
                thread.join(timeout=drain_timeout)
            if exceeded.is_set() or capture_failed.is_set() or any(thread.is_alive() for thread in threads):
                with w.store.transaction("task.supervisor_failed", actor):
                    fail(w, w.store.get("task", task_id), "external output limit or capture failure", "failed")
            if stop_error is None:
                w.apply("task.confirm_stop", {"task": task_id, "confirmed": True,
                    "evidence": "Owned process containment terminated; direct process wait returned, pid=" + str(process.process.pid)}, actor)
        watcher.join(timeout=10)
        if watchdog_errors:
            stop_error = "; ".join(watchdog_errors)
    if stop_error:
        raise ResearchError("physical stop unconfirmed: " + stop_error)
    return w.store.get("task", task_id)

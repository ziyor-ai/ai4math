"""Bounded local subprocess jobs, exported contracts and explicit recovery.

Commands are trusted local programs, not an OS sandbox. A zero exit code is
execution evidence only; validation requires a separately bound result.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import threading
import time
from pathlib import Path

from .store import ResearchError, encode, file_hash, now
from .request_identity import PROFILE, POLICY, create_environment, result_binding, verify_identity
from . import literature, research_return, strategy


def _dispatch_context(workbench, task):
    store = workbench.store
    decision = strategy.require_dispatch(workbench, task["route"], task["role"])
    from .exploration import check_task
    check_task(workbench, task)
    route = store.get("route", task["route"])
    workbench._available(route)
    if task.get("method"):
        method = store.get("method", task["method"])
        if method["status"] != "trial" or method["latest_review"] != task["method_review"]:
            raise ResearchError("method review changed; create a new task")
    if task.get("node_snapshot") and task["node_snapshot"]["proof_dependencies"] != store.get("node", task["node"])["proof_dependencies"]:
        raise ResearchError("task proof plan changed; create a new task")
    return route, decision


def _task_prompt(task, node, contract):
    parts = [
        f"# Task {task['id']}\n\n{node['statement']}\n\nDomain: {node['domain']}\n"
        f"Scope: {node['quantifier']}\n\nStatement SHA-256: `{node['statement_digest']}`\n\n"
        "Read input.json and the immutable input artifacts. Preserve the exact statement and domain. "
        "Write your argument, counterexample or experiment with assumptions and limitations. "
        "Review is separate from proof. A finite check does not prove a universal statement. "
        "Validation jobs must independently check the inputs and write result.json using the contract. "
        "Do not import the producer's solver or target adapter into a validator.\n"
    ]
    if task["output_limit_bytes"] is None:
        parts.append("\nThis contract has no task output byte cutoff. Retain complete raw streams and artifacts; "
                     "a shortened or paged tool preview is not an output-limit failure. "
                     "All source-acquisition quotas, route quotas, scope and stop controls remain in force.\n")
    if task["role"] == "validate" and (task.get("agent_guidance") or task.get("exploration_context")):
        parts.append("\n独立候选评估：按 input.json 的 result_contract 核验冻结结果，不导入生产程序；评分只适用于声明的有限范围。\n")
    elif task.get("agent_guidance") or task.get("exploration_context"):
        parts.append("\n" + task.get("agent_guidance", ""))
        if task.get("method_cards"):
            parts.append("\n## 方法的学术机制卡\n\n" + json.dumps(task["method_cards"], ensure_ascii=False, indent=2) + "\n")
        if task.get("exploration_context"):
            parts.append("\n## 本轮探索任务\n\n" + json.dumps(task["exploration_context"], ensure_ascii=False, indent=2) + "\n")
    if task.get("literature"):
        parts.append(
            "\n## 外部任务的文献关口\n\n"
            "先核对并阅读 input.json 的全部冻结输入，包括主会话专题检索、来源与统一计划。"
            "随后用自己的查询独立检索相关原文，记录段落、适用条件、检索局限和对探索计划的影响。"
            "先用 artifact.import 冻结独立检索记录，再用与主会话不同的 actor 执行 "
            "apply task.literature_ready --file <回执 JSON>。所需字段见 input.json 的 literature。"
            "收到 external_stage=exploration 后才开始实际探索和实验。"
            "没有合适原文时保留实际查询、检索记录和局限。关口不接纳数学证据。\n")
        if task.get("codex_workflow", {}).get("literature_acquisition") == "local_only":
            parts.append("本合同仅检索选定本地文献库。缺原文时记录出处、未核实的假设和适用限制，"
                         "不要派发补料请求。可在明确条件的情况下继续独立推导。\n")
        elif task["literature"].get("acquisition_mode") == "async":
            parts.append("本合同采用异步文献获取。独立调研仅扫描本地知识库并核对已归档段落。"
                         "新增检索、原文下载和建档由独立文献会话处理。用托管工具 request_literature 或 "
                         "knowledge request 提交具体缺口，并把请求 ID 随回执交回。"
                         "关口可按当前材料评估和放行，不等待请求回填。仅在核对原文和假设后引用新结论。\n")
        version = task["literature"].get("worker_receipt_version", 1)
        if version >= 2:
            parts.append("用提交回执的同一 actor，在收到派工后首次导入该任务专用的检索记录。"
                         "在 exploration_plan 中说明独立调研如何补充、修正或维持主会话的计划，"
                         "并给出首个可证伪检验。")
            if version == 2:
                parts.append("完成时至少提交一件在回执通过后首次导入的探索产物。\n")
            else:
                parts.append("本次只回传调研，并把 worker_receipt_sha256 交给主会话。"
                             "等待主会话用 task.release_exploration 核对并放行后再开始探索与实验。"
                             "所有完成产物须在放行后首次导入。\n")
        if "research_continuation" in contract:
            parts.append("\n## 首检复盘与交回\n\n" + contract["research_continuation"]["instruction"] + "\n")
        elif "external_return_contract" in contract:
            parts.append("\n交回时按 input.json 的 external_return_contract 写 return/return.json。"
                         "直接使用其中 bindings 的键和值；输入摘要字段名为 input_artifact_ids。\n")
    if task.get("external_deadline_policy", {}).get("version") == 2:
        if task.get("codex_workflow"):
            parts.append("\n## Managed Codex workflow\n\n"
                         "Only the owned Codex runtime may dispatch this task. Follow the frozen skill manifest "
                         "and source scope in codex_workflow. ")
        else:
            parts.append("\n## Monitored external workflow\n\n")
        parts.append("Stages are monitored with no stage deadline. "
                     "timeout_seconds is the optional total hard limit; null means no automatic time cutoff. "
                     "Waiting for coordinator review never authorizes exploration. Hold blocks new tools and "
                     "phase transitions but does not pause an optional total clock. Stop on explicit stop, "
                     "terminal status, output failure or total deadline. Seal exact new outputs after release; "
                     "begin_seal and all freshness/binding checks remain required. No budget reset or human attestation.\n")
    elif task.get("external_deadline_policy"):
        parts.append("\n## Server-controlled phase deadlines\n\n"
                     "Read external_deadline_policy in input.json. The literature clock starts before worker dispatch. "
                     "Coordinator waiting has its own bounded budget. Exploration uses the first_check budget. "
                     "Before it expires call task.begin_seal; sealing has its own deadline. "
                     "Freeze the seal manifest binding the contract, original inputs, candidate and each output SHA-256, "
                     "then call task.submit_seal with artifact_ids and seal_manifest_artifact before the seal deadline. "
                     "Submission stops worker execution; coordinator acceptance occurs afterward without spending worker time. "
                     "Worker timestamps cannot extend deadlines. Late artifacts are archive-only; stop on any timeout. "
                     "Host mode requires the coordinator's host interrupt and confirmation; only process supervision "
                     "provides an OS process-tree kill.\n")
    return "".join(parts)


def export_task(workbench, task_id):
    store = workbench.store
    with store.snapshot():
        task = store.get("task", task_id)
        from .checkpoint import inherited
        if inherited(store, task_id):
            raise ResearchError("inherited checkpoint tasks are archival; prepare a new continuation")
        dispatch_decision = None
        if task["status"] == "pending":
            _, dispatch_decision = _dispatch_context(workbench, task)
            literature.check_export(workbench, task, dispatch_decision)
        else:
            from .exploration import check_task
            check_task(workbench, task)
    node = task.get("node_snapshot", store.get("node", task["node"]))
    folder = store.inside(Path("tasks") / task_id)
    folder.mkdir(parents=True, exist_ok=True)
    contract = {"schema_version": 2, "task_id": task_id, "role": task["role"], "node": node,
                "research_policy": store.all("research_policy"),
                "method": store.get("method", task["method"]) if task.get("method") else None,
                "input_artifacts": [{"id": a, "path": str(store.verify_blob(a))} for a in task["inputs"]],
                "timeout_seconds": task["timeout_seconds"], "output_limit_bytes": task["output_limit_bytes"],
                "result_contract": {"task_id": task_id, "statement_digest": node["statement_digest"],
                    "input_artifacts": sorted(task["inputs"]), "method": "<declared certificate method>",
                    "scope": "finite|universal", "evidence_level": "proved|disproved", "passed": True,
                    "checks": ["<independently checked certificate>"], "limitations": ["<trust and scope limits>"]}}
    if task.get("agent_guidance"):
        contract["agent_guidance"] = task["agent_guidance"]
    if task.get("external_deadline_policy"):
        contract["external_deadline_policy"] = task["external_deadline_policy"]
        contract["seal_contract"] = {"operation": "task.begin_seal", "submit_operation": "task.submit_seal", "completion_field": "seal_manifest_artifact",
            "required_fields": ["task_id", "contract_sha256", "statement_digest", "input_artifact_ids", "candidate_digest", "artifacts"],
            "artifact_row": {"artifact_id": "<frozen artifact id>", "sha256": "<same SHA-256>"},
            "candidate_digest_without_candidate": None}
    if task.get("codex_workflow"):
        contract["codex_workflow"] = task["codex_workflow"]
    if task.get("prepared_under_strategy"):
        contract["prepared_under_strategy"] = task["prepared_under_strategy"]
    if task.get("literature"):
        contract["literature"] = {**task["literature"], "initial_stage": "literature",
                                  "advance_operation": "task.literature_ready",
                                  "worker_receipt_fields": ["task", "contract_sha256", "read_input_artifacts",
                                                            "queries", "search_artifact", "sources", "limitations",
                                                            "exploration_plan"],
                                  "source_fields": ["url", "locator", "finding", "artifact_id"]}
        if task["literature"].get("worker_receipt_version", 1) >= 3:
            contract["literature"].update(coordinator_review_stage=literature.REVIEW_STAGE,
                release_operation="task.release_exploration",
                release_fields=["task", "worker_receipt_sha256", "assessment"])
    if task.get("method_cards"):
        contract["method_cards"] = task["method_cards"]
    if task.get("exploration_context"):
        context = task["exploration_context"]
        contract["exploration"] = context
        candidate = context["candidate"]
        contract["result_contract"] = {"candidate": candidate["id"], "candidate_digest": candidate["candidate_digest"],
            "input_artifacts": sorted(task["inputs"]), "scope": "finite", "evidence_level": "unknown",
            "limitations": ["<scope and trust limits>"], "accepts_mathematical_claim": False}
        if task["role"] == "validate":
            protocol = context.get("evaluation_protocol")
            metrics = protocol["metrics"] if protocol else {"quality": {}}
            contract["result_contract"].update(valid="<boolean from independent check>",
                                                metrics=dict.fromkeys(metrics, "<finite number>"),
                                                checks=["<actual checks performed>"])
            if protocol:
                contract["result_contract"].update(task_id=task_id, evaluation_protocol_sha256=protocol["sha256"])
                if candidate.get("transfer"):
                    contract["counterexample_receipt"] = {"include_if_verified": True, "field": "counterexample",
                        "hypothesis": candidate["hypothesis"], "required_fields": ["hypothesis", "case_id", "witness",
                            "search_domain", "ordering", "smaller_cases_checked", "minimality_checked"]}
                if context.get("counterexample"):
                    contract["result_contract"]["rechecked_cases"] = [context["counterexample"]["case_id"]]
    if task.get("literature", {}).get("external_return_version") == 1:
        bindings = {"task_id": task_id, "statement_digest": node["statement_digest"],
                    "input_artifact_ids": sorted(task["inputs"])}
        if task.get("exploration_context"):
            bindings["candidate_digest"] = contract["exploration"]["candidate"]["candidate_digest"]
        contract["external_return_contract"] = {
            "path": "return/return.json", "bindings": bindings,
            "required_fields": [*bindings, "artifacts", "summary", "scope", "suggested_evidence_level",
                                "open_obligations", "next_step"],
        }
        if research_return.enabled(task):
            contract["research_continuation"] = research_return.contract(deductive=research_return.deduction_first(task))
    validator = str(store.verify_blob(task["validator_artifact"])) if task["validator_artifact"] else ""
    fresh = task.get("execution_profile") == PROFILE
    program = str(store.verify_blob(task["program_artifact"])) if fresh and task.get("program_artifact") else ""
    argv = [str(folder / "input.json") if a == "{input}" else validator if a == "{validator}"
            else program if a == "{program}" else str(folder) if a == "{workdir}" else a for a in task["argv"]]
    if fresh:
        identity = verify_identity(store, task)
        argv[1:1] = POLICY["python_flags"]
        contract.update(request_identity=identity, execution_policy=POLICY, study_isolation_ready=False)
        contract["result_contract"].update(result_binding(task))
    contract["argv"] = argv
    encoded = json.dumps(contract, ensure_ascii=False, indent=2) + "\n"
    destination = store.inside(Path("tasks") / task_id / "input.json")
    if destination.exists() and destination.read_text(encoding="utf-8") != encoded:
        raise ResearchError("existing task contract differs; task ids cannot be reused")
    if not destination.exists():
        with destination.open("x", encoding="utf-8") as f:
            f.write(encoded)
    prompt = store.inside(Path("tasks") / task_id / "PROMPT.md")
    prompt_text = _task_prompt(task, node, contract)
    if not prompt.exists():
        with prompt.open("x", encoding="utf-8") as stream:
            stream.write(prompt_text)
    elif task["status"] == "pending" and task.get("literature"):
        _check_handoff_bytes(prompt, prompt_text)
    return {"directory": str(folder), "contract": contract, "prompt": str(prompt), "strategy_decision": dispatch_decision}


def _check_handoff_bytes(path, contents):
    expected = contents.encode("utf-8")
    if path.read_bytes() not in (expected, expected.replace(b"\n", b"\r\n")):
        raise ResearchError("external handoff differs from the generated task package")


def _stop(process):
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10, check=False)
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    if process.poll() is None:
        process.kill()
    process.wait(timeout=10)


def _start_task(workbench, task_id, actor, external=False, supervised=False, managed_codex=False):
    store = workbench.store
    proposed = store.get("task", task_id)
    if proposed.get("codex_workflow") and not (external and supervised and managed_codex is True):
        raise ResearchError("managed Codex tasks require the owned Codex runtime; generic dispatch is forbidden")
    exported = export_task(workbench, task_id)
    folder, argv = Path(exported["directory"]), exported["contract"]["argv"]
    result_path = store.inside(Path("tasks") / task_id / "result.json")
    with store.transaction("task.start_external" if external else "task.start", actor):
        integrity = store.audit()
        if not integrity["passed"]:
            raise ResearchError("cannot run in a workspace with failed integrity audit")
        task = store.get("task", task_id)
        if task["status"] != "pending":
            raise ResearchError("task is not pending; interrupted jobs need a new task id")
        if task.get("codex_workflow"):
            if not (external and supervised and managed_codex is True):
                raise ResearchError("managed Codex tasks require the owned Codex runtime")
            if store.exists("codex_dispatch", "main") and store.get("codex_dispatch", "main")["held"]:
                raise ResearchError("new managed Codex dispatch is held")
            if actor == task["codex_workflow"]["worker_actor"]:
                raise ResearchError("Codex dispatch coordinator must differ from worker actor")
        if external and (task["role"] not in strategy.RESEARCH_ROLES or task.get("execution_profile") == PROFILE):
            raise ResearchError("external dispatch supports trusted-local explore and produce tasks only")
        route, decision = _dispatch_context(workbench, task)
        literature.check_dispatch(workbench, task, decision, external, actor)
        if external and task.get("external_deadline_policy", {}).get("supervision") == "process" and not supervised:
            raise ResearchError("process-supervised external tasks must use supervise-external")
        if any(t["status"] == "running" and t["route"] == route["id"] for t in store.all("task")):
            raise ResearchError("route already has a running task")
        for a in task["inputs"]:
            store.verify_blob(a)
        if task["validator_artifact"]:
            store.verify_blob(task["validator_artifact"])
        # A stale result from a hand-edited task directory must never be accepted.
        if result_path.exists():
            raise ResearchError("task directory already contains result.json; use a fresh task id")
        environment = None
        if task.get("execution_profile") == PROFILE:
            environment, receipt = create_environment(store, task, folder)
            environment_path = store.inside(Path("tasks") / task_id / "environment.json")
            with environment_path.open("x", encoding="utf-8") as stream:
                stream.write(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n")
            task["environment_artifact"] = workbench._artifact(
                {"path": str(environment_path), "purpose": f"task {task_id}: isolated-local environment receipt"}, actor)["id"]
            task["executed_request_identity_sha256"] = task["request_identity"]["sha256"]
        route["runs"] += 1
        if route["runs"] >= route["max_runs"]:
            route.update(status="parked", status_reason="run budget exhausted")
        task.update(status="running", started_at=now(), execution_mode="external" if external else "local")
        if external:
            task["dispatched_by"] = actor
            task["external_stage"] = "literature"
            from .external_deadline import enter
            enter(task, "literature")
        else:
            task.update(executed_argv=argv, executed_by=actor)
        if decision:
            task["dispatched_under_strategy"] = decision
        task["contract_artifact"] = workbench._artifact(
            {"path": str(folder / "input.json"), "purpose": f"task {task_id}: input.json"}, actor)["id"]
        task["prompt_artifact"] = workbench._artifact(
            {"path": str(folder / "PROMPT.md"), "purpose": f"task {task_id}: PROMPT.md"}, actor)["id"]
        if external:
            contract = exported["contract"]
            expected = (("contract_artifact", json.dumps(contract, ensure_ascii=False, indent=2) + "\n"),
                        ("prompt_artifact", _task_prompt(task, contract["node"], contract)))
            for field, contents in expected:
                _check_handoff_bytes(store.verify_blob(task[field]), contents)
        store.put("route", route["id"], route)
        store.put("task", task_id, task)
    return exported, task, environment


def start_external(workbench, task_id, actor="human"):
    exported, task, _ = _start_task(workbench, task_id, actor, external=True)
    return {**exported, "strategy_decision": task.get("dispatched_under_strategy"), "status": task["status"],
            "external_stage": task["external_stage"], "contract_sha256": task["contract_artifact"],
            "phase_timing": task.get("phase_timing"), "active_phase": task.get("active_phase")}


def run_task(workbench, task_id, actor="human"):
    store = workbench.store
    exported, task, environment = _start_task(workbench, task_id, actor)
    folder, argv = Path(exported["directory"]), exported["contract"]["argv"]
    output_paths = [store.inside(Path("tasks") / task_id / name) for name in ("stdout.log", "stderr.log")]

    started = time.monotonic()
    process, status, error = None, "failed", None
    consumed, exceeded = [0], threading.Event()
    capture_errors, capture_failed = [], threading.Event()
    lock = threading.Lock()
    threads = []

    def capture(stream, path):
        try:
            with path.open("wb") as out:
                while True:
                    block = os.read(stream.fileno(), 4096)
                    if not block:
                        break
                    with lock:
                        remaining = (len(block) if task["output_limit_bytes"] is None
                                     else max(0, task["output_limit_bytes"] - consumed[0]))
                        out.write(block[:remaining])
                        consumed[0] += len(block[:remaining])
                        if len(block) > remaining:
                            exceeded.set()
        except OSError as exc:
            with lock:
                capture_errors.append(f"{path.name}: {exc}")
            capture_failed.set()
        finally:
            stream.close()

    try:
        kwargs = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}
        process = subprocess.Popen(argv, cwd=folder, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, shell=False, env=environment, **kwargs)
        with store.transaction("task.pid", actor):
            current = store.get("task", task_id)
            current["pid"] = process.pid
            store.put("task", task_id, current)
        for stream, path in zip((process.stdout, process.stderr), output_paths):
            thread = threading.Thread(target=capture, args=(stream, path), daemon=True)
            thread.start()
            threads.append(thread)
        while True:
            if capture_failed.is_set():
                status = "failed"
                _stop(process)
                break
            if exceeded.is_set():
                status = "output_limit"
                _stop(process)
                break
            if task["timeout_seconds"] is not None and time.monotonic() - started >= task["timeout_seconds"]:
                status = "unknown_timeout"
                _stop(process)
                break
            if process.poll() is not None and all(not t.is_alive() for t in threads):
                status = "completed" if process.returncode == 0 else "failed"
                break
            time.sleep(0.02)
        for thread in threads:
            thread.join(timeout=2)
        if exceeded.is_set() and status == "completed":
            status = "output_limit"
        if capture_errors:
            status, error = "failed", "; ".join(capture_errors)
    except (OSError, subprocess.SubprocessError) as exc:
        error = str(exc)
        if process is not None and process.poll() is None:
            _stop(process)
    except BaseException:
        if process is not None:
            _stop(process)
        # Leave running record for explicit recovery; do not silently retry.
        raise
    with store.transaction("task.finish", actor):
        current = store.get("task", task_id)
        if current["status"] != "running":
            raise ResearchError("task was recovered during execution; outputs need manual review")
        current.update(status=status, finished_at=now(), elapsed_seconds=round(time.monotonic() - started, 4),
                       exit_code=process.returncode if process else None, execution_error=error,
                       captured_bytes=consumed[0], output_artifacts=[])
        if task.get("execution_profile") == PROFILE or task.get("exploration_context", {}).get("evaluation_protocol"):
            current["contract_unchanged"] = (folder / "input.json").is_file() and file_hash(folder / "input.json") == current["contract_artifact"]
            if not current["contract_unchanged"]:
                current.update(status="failed", execution_error="task modified its frozen request contract")
                status = "failed"
        for path in output_paths + [folder / "input.json", folder / "PROMPT.md"]:
            if path.is_file():
                a = workbench._artifact({"path": str(path), "purpose": f"task {task_id}: {path.name}"}, actor)
                current["output_artifacts"].append(a["id"])
        result_path = store.inside(Path("tasks") / task_id / "result.json")
        if status == "completed" and result_path.is_file():
            if task["output_limit_bytes"] is not None and result_path.stat().st_size > task["output_limit_bytes"]:
                current["status"] = "output_limit"
            else:
                current["result_artifact"] = workbench._artifact(
                    {"path": str(result_path), "purpose": f"task {task_id}: validation result"}, actor)["id"]
        store.put("task", task_id, current)
        strategy.finish_round(workbench, current)
    from .research_summary import refresh_after_transition
    refresh_after_transition(workbench)
    return current

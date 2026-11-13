import json
import sys

import pytest

from ai4math.cli import main
from ai4math.execution import export_task, run_task, start_external
from ai4math.store import ResearchError, Store
from ai4math.strategy import brief
from ai4math.workbench import Workbench


def node(w, key, deps=()):
    return w.apply("node.add", {"id": key, "statement": f"Test obligation {key}", "domain": "fixture",
                               "quantifier": "universal", "proof_dependencies": list(deps)})


def route(w, key, target, max_runs=6):
    w.apply("route.add", {"id": key, "node": target, "rationale": "Compare proof methods", "next_question": "What remains?",
        "stop_condition": "Stop at the run limit", "max_runs": max_runs, "stagnation_limit": 2})
    w.apply("route.status", {"id": key, "status": "active", "reason": "Activate bounded fixture"})


def task(w, key, route_id="a", code="pass", role="explore"):
    return w.apply("task.add", {"id": key, "route": route_id, "role": role, "argv": [sys.executable, "-c", code],
                                 "timeout_seconds": 240, "output_limit_bytes": 1000,
                                 "phase_budgets": dict.fromkeys(("literature", "coordinator_review", "first_check", "seal"), 60),
                                 "external_supervision": "host"})


def seal(w, task_id, artifacts):
    from ai4math.external_deadline import bindings
    w.apply("task.begin_seal", {"task": task_id})
    path = w.store.inside(task_id + "-seal.json")
    path.write_text(json.dumps({**bindings(w.store.get("task", task_id)),
        "artifacts": [{"artifact_id": key, "sha256": key} for key in artifacts]}), encoding="utf-8")
    manifest = w.apply("artifact.import", {"path": str(path), "purpose": "seal manifest"})["id"]
    w.apply("task.submit_seal", {"task": task_id, "artifact_ids": artifacts, "seal_manifest_artifact": manifest})
    w.apply("task.confirm_stop", {"task": task_id, "confirmed": True, "evidence": "fixture has no live worker"})
    return manifest


def decide(w, key, choice, route_id=None, **extra):
    return w.apply("strategy.decide", {"id": key, "brief_sha256": brief(w)["brief_sha256"], "decision": choice,
        "next_route": route_id, "reason": "Review the global dependency graph and remaining budgets",
        "next_question": "Check the selected proof gap", **extra})


def literature_artifact(w, name, actor="human"):
    path = w.store.inside(name + ".md")
    path.write_text(name, encoding="utf-8")
    return w.apply("artifact.import", {"path": str(path), "purpose": name}, actor)["id"]


def coordinator_literature(w, decision, *routes, sources=None):
    for key in routes:
        w.apply("literature.search", {"decision": decision, "route": key, "queries": [f"{key} proof gap"],
            "search_artifact": literature_artifact(w, f"{decision}-{key}-search"), "sources": sources or [],
            "limitations": "No suitable original found in this fixture."})
    return w.apply("literature.plan", {"decision": decision, "assessment": "Compare the branch-specific findings",
        "plan_artifact": literature_artifact(w, f"{decision}-plan"), "routes": {key: f"Probe {key}" for key in routes}})


def worker_receipt(w, task_id, dispatch, actor="worker"):
    return {"task": task_id, "contract_sha256": dispatch["contract_sha256"],
            "read_input_artifacts": [item["id"] for item in dispatch["contract"]["input_artifacts"]],
            "queries": [f"Independent search for {task_id}"],
            "search_artifact": literature_artifact(w, f"{task_id}-independent-search", actor),
            "sources": [], "limitations": "No relevant theorem found in the searched scope.",
            "exploration_plan": "Derive a first probe without theorem transfer."}


@pytest.fixture
def workspace(tmp_path):
    w = Workbench.create(tmp_path / "workspace", "review", "Global review fixture")
    try:
        for key in ("a", "b", "c", "alternative"):
            node(w, key)
        node(w, "goal", ["a", "b", "c"])
        w.apply("goal.set", {"node": "goal"})
        for key in ("a", "b", "c"):
            route(w, key, key)
        yield w
    finally:
        w.store.close()


def test_three_rounds_block_create_export_and_start_across_reopen(workspace):
    w = workspace
    task(w, "prepared", "b")
    export_task(w, "prepared")
    task(w, "procedural-review", role="review")
    run_task(w, "procedural-review")
    assert brief(w)["total_rounds"] == 0
    for i in range(3):
        key = f"round-{i}"
        task(w, key, code="raise SystemExit(7)" if i == 0 else "pass")
        run_task(w, key)
    fresh = Workbench(w.store.root)
    try:
        info = brief(fresh)
        assert info["due"] and info["rounds_since_review"] == 3
        assert fresh.board()["next_actions"] == []
        for operation in (export_task, run_task, start_external):
            with pytest.raises(ResearchError, match="review is due"):
                operation(fresh, "prepared")
        with pytest.raises(ResearchError, match="review is due"):
            task(fresh, "new")
        recorded = decide(fresh, "review-1", "switch", "b")
        assert recorded["brief"]["brief_sha256"] == info["brief_sha256"]
        assert brief(fresh)["rounds_since_review"] == 0
        with pytest.raises(ResearchError, match="outside the reviewed focus"):
            task(fresh, "off-focus", "a")
        outcome = run_task(fresh, "prepared")
        assert outcome["dispatched_under_strategy"] == "review-1"
        assert w.store.get("route", "a")["runs"] == 4
    finally:
        fresh.store.close()


def test_deferred_switch_closes_review_but_requires_focus_before_dispatch(workspace):
    w = workspace
    task(w, "prepared", "b")
    for i in range(3):
        run_task(w, task(w, f"round-{i}")["id"])

    before = brief(w)
    with pytest.raises(ResearchError, match="switch must select"):
        decide(w, "missing-focus", "switch")
    with pytest.raises(ResearchError, match="defer_dispatch requires"):
        decide(w, "invalid-defer", "switch", "b", defer_dispatch=True)
    record = decide(w, "wait-for-budget", "switch", defer_dispatch=True)
    after = brief(w)
    assert record["dispatch_deferred"] and after["dispatch_deferred"]
    assert not after["due"] and not after["focus_routes"]
    assert after["routes"] == before["routes"] and after["pools"] == before["pools"]
    assert w.board()["next_actions"] == []
    for operation in (export_task, run_task, start_external):
        with pytest.raises(ResearchError, match="research dispatch is deferred"):
            operation(w, "prepared")
    with pytest.raises(ResearchError, match="research dispatch is deferred"):
        task(w, "new")

    decide(w, "resume", "switch", "b")
    assert "dispatch_deferred" not in brief(w)
    assert run_task(w, "prepared")["status"] == "completed"
    assert w.audit()["passed"]


@pytest.mark.parametrize("local", [False, True])
def test_cli_init_defaults_to_literature_gates_even_for_a_single_route(tmp_path, local):
    root = tmp_path / "workspace"
    flags = ["--local-research"] if local else []
    assert main(["--workspace", str(root), "init", "--project-id", "test", "--title", "Fixture", *flags]) == 0
    w = Workbench(root)
    try:
        node(w, "a")
        w.apply("goal.set", {"node": "a"})
        route(w, "a", "a")
        if not local:
            with pytest.raises(ResearchError, match="register a strategy decision"):
                task(w, "before-decision")
        decide(w, "first-direction", "switch", "a")
        if local:
            task(w, "local-script")
            assert run_task(w, "local-script")["status"] == "completed"
            return

        assert w.board()["next_actions"] == []
        assert brief(w)["literature"]["pending_search_routes"] == ["a"]
        with pytest.raises(ResearchError, match="freeze the coordinator literature plan"):
            task(w, "before-plan")
        coordinator_literature(w, "first-direction", "a")
        assert [action["route"] for action in w.board()["next_actions"]] == ["a"]
        task(w, "external")
        with pytest.raises(ResearchError, match="uses start-external"):
            run_task(w, "external")
        dispatched = start_external(w, "external")
        assert dispatched["external_stage"] == "literature"
        receipt = worker_receipt(w, "external", dispatched)
        ready = w.apply("task.literature_ready", receipt, "worker")
        assert ready["external_stage"] == "awaiting_coordinator_review"
        assert main(["--workspace", str(root), "apply", "task.release_exploration", "--json",
                     json.dumps({"task": "external", "worker_receipt_sha256": ready["worker_receipt_sha256"],
                                 "assessment": "Checked the search and first probe"})]) == 0
        output = literature_artifact(w, "research-output", "worker")
        finished = w.apply("task.finish_external", {"task": "external", "status": "completed",
            "summary": "Independently reviewed fixture output", "artifact_ids": [output],
            "seal_manifest_artifact": seal(w, "external", [output])})
        assert finished["evidence_level"] == "unknown"
        decide(w, "next-direction", "continue", "a")
        with pytest.raises(ResearchError, match="freeze the coordinator literature plan"):
            task(w, "before-next-plan")
        assert w.audit()["passed"]
    finally:
        w.store.close()


def test_parallel_external_rounds_share_gate_and_validate_focus(workspace):
    w = workspace
    task(w, "pending-before-review")
    route(w, "another-a", "a")
    with pytest.raises(ResearchError, match="distinct ready"):
        decide(w, "duplicate-target", "switch", "a", parallel_routes=["another-a"])
    decide(w, "parallel", "switch", "a", parallel_routes=["b", "c"])
    assert brief(w)["literature"]["pending_search_routes"] == ["a", "b", "c"]
    with pytest.raises(ResearchError, match="freeze the coordinator literature plan"):
        task(w, "missing-plan", "b", role="produce")
    for operation in (export_task, run_task, start_external):
        with pytest.raises(ResearchError, match="current coordinator literature plan"):
            operation(w, "pending-before-review")
    assert w.store.get("route", "a")["runs"] == 0
    coordinator_literature(w, "parallel", "a", "b", "c")
    task(w, "prepared-next", "a")
    with pytest.raises(ResearchError, match="uses start-external"):
        run_task(w, "prepared-next")
    dispatches = {}
    for key in ("a", "b", "c"):
        task(w, f"external-{key}", key)
        dispatches[key] = start_external(w, f"external-{key}")
        assert dispatches[key]["strategy_decision"] == "parallel"
        assert dispatches[key]["external_stage"] == "literature"
    with pytest.raises(ResearchError, match="settle or recover"):
        decide(w, "too-soon", "continue", "a")
    pre_receipt = literature_artifact(w, "premature-output")
    for key in ("a", "b", "c"):
        payload = {"task": f"external-{key}", "status": "completed", "summary": "A proof attempt",
                   "artifact_ids": [pre_receipt]}
        with pytest.raises(ResearchError, match="without the worker literature receipt"):
            w.apply("task.finish_external", payload)
        receipt = worker_receipt(w, f"external-{key}", dispatches[key], f"worker-{key}")
        if key == "b":
            used = w.store.get("worker_literature", "external-a")["search_artifact"]
            with pytest.raises(ResearchError, match="already been used"):
                w.apply("task.literature_ready", {**receipt, "search_artifact": used}, "worker-a")
        ready = w.apply("task.literature_ready", receipt, actor=f"worker-{key}")
        assert ready["external_stage"] == "awaiting_coordinator_review"
        with pytest.raises(ResearchError, match="registered by the coordinator"):
            w.apply("task.finish_external", payload, actor=f"worker-{key}")
        with pytest.raises(ResearchError, match="before coordinator release"):
            w.apply("task.finish_external", payload)
        release = {"task": f"external-{key}", "worker_receipt_sha256": ready["worker_receipt_sha256"],
                   "assessment": "Reviewed the worker's sources, assumptions and first probe"}
        with pytest.raises(ResearchError, match="only the dispatching coordinator"):
            w.apply("task.release_exploration", release, actor=f"worker-{key}")
        with pytest.raises(ResearchError, match="exact worker literature receipt"):
            w.apply("task.release_exploration", {**release, "worker_receipt_sha256": "0" * 64})
        approved = w.apply("task.release_exploration", release)
        assert approved["external_stage"] == "exploration"
        assert approved["release"]["contract_sha256"] == dispatches[key]["contract_sha256"]
        with pytest.raises(ResearchError, match="awaiting coordinator literature review"):
            w.apply("task.release_exploration", release)
        output = literature_artifact(w, f"external-{key}-attempt")
        with pytest.raises(ResearchError, match="first imported after coordinator release"):
            w.apply("task.finish_external", {**payload, "artifact_ids": [pre_receipt, output]})
        payload["artifact_ids"] = [output]
        payload["seal_manifest_artifact"] = seal(w, f"external-{key}", [output])
        out = w.apply("task.finish_external", payload)
        assert out["evidence_level"] == "unknown" and not out.get("result_artifact")
        with pytest.raises(ResearchError, match="running external"):
            w.apply("task.finish_external", payload)
    assert brief(w)["due"] and brief(w)["total_rounds"] == 3
    with pytest.raises(ResearchError, match="review is due"):
        start_external(w, "prepared-next")
    decide(w, "single-follow-up", "continue", "a")
    assert brief(w)["literature"]["pending_search_routes"] == ["a"]
    with pytest.raises(ResearchError, match="freeze the coordinator literature plan"):
        task(w, "missing-follow-up-plan")
    assert w.audit()["passed"] and w.store.all("claim") == []


def test_route_exhaustion_triggers_early_and_replan_is_atomic(workspace):
    w = workspace
    route(w, "short", "a", max_runs=1)
    decide(w, "short-review", "switch", "short")
    coordinator_literature(w, "short-review", "short")
    task(w, "last-slot", "short")
    start_external(w, "last-slot")
    seen = brief(w)
    assert not seen["due"] and seen["rounds_since_review"] == 0
    assert w.store.get("route", "short")["status"] == "parked"
    with pytest.raises(ResearchError, match="settle or recover"):
        decide(w, "wait-for-running", "switch", "b")
    w.apply("task.recover", {"task": "last-slot", "reason": "External attempt interrupted", "process_confirmed_stopped": True})
    assert brief(w)["total_rounds"] == 1
    assert brief(w)["due"] and brief(w)["triggers"][0]["kind"] == "route_run_limit"
    with pytest.raises(ResearchError, match="brief changed"):
        decide(w, "stale", "switch", "b", brief_sha256=seen["brief_sha256"])
    original = w.store.get("node", "goal")
    with pytest.raises(ResearchError, match="strategy.decide with replan"):
        w.apply("node.plan", {"node": "goal", "proof_dependencies": ["alternative"], "reason": "Fixture"})
    with pytest.raises(ResearchError, match="open goal dependency"):
        decide(w, "rollback", "replan", "a", plan={"node": "goal", "proof_dependencies": ["alternative"]})
    assert w.store.get("node", "goal") == original and brief(w)["due"]
    recorded = decide(w, "replan", "replan", plan={"node": "goal", "proof_dependencies": ["alternative"]})
    assert recorded["plan"]["after"]["statement_digest"] == original["statement_digest"]
    assert brief(w)["open_obligations"] == ["alternative"] and not brief(w)["due"]
    assert w.store.get("route", "short")["runs"] == 1


def test_external_literature_blocks_stale_plans_and_requires_independent_frozen_inputs(workspace):
    w = workspace
    task(w, "legacy-pending")
    with pytest.raises(ResearchError, match="coordinator literature plan"):
        start_external(w, "legacy-pending")
    assert w.store.get("route", "a")["runs"] == 0
    w.apply("literature.enable", {})
    with pytest.raises(ResearchError, match="current coordinator literature plan"):
        run_task(w, "legacy-pending")
    decide(w, "direction", "switch", "a", parallel_routes=["b"])
    assert brief(w)["literature"]["pending_search_routes"] == ["a", "b"]
    with pytest.raises(ResearchError, match="freeze the coordinator literature plan"):
        task(w, "too-early")
    plan_artifact = literature_artifact(w, "unfinished-plan")
    with pytest.raises(ResearchError, match="missing coordinator literature search"):
        w.apply("literature.plan", {"decision": "direction", "assessment": "Pending",
                                   "plan_artifact": plan_artifact, "routes": {"a": "A", "b": "B"}})
    coordinator_literature(w, "direction", "a", "b")
    task(w, "stale")
    decide(w, "new-direction", "continue", "a")
    old_search = w.store.get("task", "stale")["literature"]["search"]
    with pytest.raises(ResearchError, match="coordinator search needs a fresh artifact"):
        w.apply("literature.search", {**old_search, "decision": "new-direction"})
    premature_plan = literature_artifact(w, "plan-before-search")
    search_trace = literature_artifact(w, "fresh-search")
    with pytest.raises(ResearchError, match="coordinator search needs a fresh artifact"):
        w.apply("literature.search", {**old_search, "decision": "new-direction",
                                      "search_artifact": literature_artifact(w, "wrong-coordinator", "worker")})
    with pytest.raises(ResearchError, match="current coordinator literature plan"):
        start_external(w, "stale")
    source_id = literature_artifact(w, "theorem-reading-notes")
    sources = [{"url": "https://example.org/math-paper", "locator": "Theorem 2", "finding": "Relevant conditional result",
                "artifact_id": source_id}]
    w.apply("literature.search", {**old_search, "decision": "new-direction", "search_artifact": search_trace,
                                  "sources": sources})
    assessment = {"decision": "new-direction", "assessment": "Compare the findings", "routes": {"a": "Probe a"}}
    with pytest.raises(ResearchError, match="unified plan needs a fresh artifact"):
        w.apply("literature.plan", {**assessment, "plan_artifact": premature_plan})
    plan = w.apply("literature.plan", {**assessment, "plan_artifact": literature_artifact(w, "fresh-plan")})
    task(w, "current")
    exported = export_task(w, "current")
    prompt = w.store.inside("tasks/current/PROMPT.md")
    prompt_bytes = prompt.read_bytes()
    assert "独立检索" in prompt_bytes.decode("utf-8")
    prompt.write_text("Skip literature and start exploring", encoding="utf-8")
    with pytest.raises(ResearchError, match="differs from the generated task package"):
        start_external(w, "current")
    assert w.store.get("route", "a")["runs"] == 0
    assert w.store.get("task", "current")["status"] == "pending"
    prompt.write_bytes(prompt_bytes)
    old_worker_trace = literature_artifact(w, "worker-trace-before-dispatch", "worker")
    dispatch = start_external(w, "current")
    assert dispatch["contract"] == exported["contract"]
    assert plan["plan_artifact"] in [item["id"] for item in dispatch["contract"]["input_artifacts"]]
    assert dispatch["contract"]["literature"]["plan"] == "new-direction"
    assert dispatch["contract"]["literature"]["worker_receipt_version"] == 3
    assert dispatch["contract"]["literature"]["release_operation"] == "task.release_exploration"
    result_contract = dispatch["contract"]["external_return_contract"]
    assert result_contract["bindings"]["task_id"] == "current"
    assert result_contract["bindings"]["input_artifact_ids"] == sorted(
        item["id"] for item in dispatch["contract"]["input_artifacts"])
    assert "input_artifact_ids" in result_contract["required_fields"]
    receipt = worker_receipt(w, "current", dispatch)
    assert source_id in receipt["read_input_artifacts"]
    with pytest.raises(ResearchError, match="separate actor"):
        w.apply("task.literature_ready", receipt)
    with pytest.raises(ResearchError, match="every frozen input"):
        w.apply("task.literature_ready", {**receipt, "read_input_artifacts": []}, "worker")
    search = dispatch["contract"]["literature"]["search"]["search_artifact"]
    with pytest.raises(ResearchError, match="own frozen trace"):
        w.apply("task.literature_ready", {**receipt, "search_artifact": search}, "worker")
    with pytest.raises(ResearchError, match="worker literature search needs a fresh artifact"):
        w.apply("task.literature_ready", {**receipt, "search_artifact": old_worker_trace}, "worker")
    wrong_actor_trace = literature_artifact(w, "worker-trace-imported-by-coordinator")
    with pytest.raises(ResearchError, match="worker literature search needs a fresh artifact"):
        w.apply("task.literature_ready", {**receipt, "search_artifact": wrong_actor_trace}, "worker")
    prompt.write_text("altered prompt", encoding="utf-8")
    with pytest.raises(ResearchError, match="handoff changed"):
        w.apply("task.literature_ready", receipt, "worker")
    prompt.write_bytes(prompt_bytes)
    assert w.store.get("task", "current")["external_stage"] == "literature"
    ready = w.apply("task.literature_ready", receipt, "worker")
    with pytest.raises(ResearchError, match="literature stage"):
        w.apply("task.literature_ready", receipt, "worker")
    reopened = Workbench(w.store.root)
    try:
        running = brief(reopened)["running_tasks"][0]
        assert running["external_stage"] == "awaiting_coordinator_review"
        assert running["worker_receipt_sha256"] == ready["worker_receipt_sha256"]
    finally:
        reopened.store.close()
    release = {"task": "current", "worker_receipt_sha256": ready["worker_receipt_sha256"],
               "assessment": "Reviewed the scope of Theorem 2 and the first probe"}
    prompt.write_text("altered after worker receipt", encoding="utf-8")
    with pytest.raises(ResearchError, match="handoff changed"):
        w.apply("task.release_exploration", release)
    assert not w.store.exists("exploration_release", "current")
    prompt.write_bytes(prompt_bytes)
    w.apply("task.release_exploration", release)
    assert brief(w)["running_tasks"][0]["external_stage"] == "exploration"
    assert w.audit()["passed"]


@pytest.mark.parametrize("version", [None, 2])
def test_preexisting_frozen_worker_contract_can_still_finish(workspace, monkeypatch, version):
    from ai4math import literature

    w = workspace
    decide(w, "legacy-contract", "switch", "a")
    coordinator_literature(w, "legacy-contract", "a")
    task_fields = literature.task_fields

    def legacy_task_fields(w, record, decision):
        task_fields(w, record, decision)
        record["literature"].pop("deadline_contract_version")
        if version is None:
            record["literature"].pop("worker_receipt_version")
        else:
            record["literature"]["worker_receipt_version"] = version

    with monkeypatch.context() as fixture:
        fixture.setattr(literature, "task_fields", legacy_task_fields)
        task(w, "already-frozen")
    previous_trace = literature_artifact(w, "legacy-search")
    dispatch = start_external(w, "already-frozen")
    assert dispatch["contract"]["literature"].get("worker_receipt_version") == version
    assert "release_operation" not in dispatch["contract"]["literature"]
    receipt = worker_receipt(w, "already-frozen", dispatch)
    if version is None:
        receipt["search_artifact"] = previous_trace
    ready = w.apply("task.literature_ready", receipt, "worker")
    assert ready["external_stage"] == "exploration"
    output = previous_trace if version is None else literature_artifact(w, "legacy-output")
    result = w.apply("task.finish_external", {"task": "already-frozen", "status": "completed",
                     "summary": "Archived proof attempt", "artifact_ids": [output]})
    assert result["status"] == "completed" and result["evidence_level"] == "unknown"


def test_stagnation_triggers_and_releases_only_by_decision(workspace):
    w = workspace
    for _ in range(2):
        w.apply("route.checkpoint", {"route": "a", "finding": "Gap persists", "next_question": "Compare another approach"})
    info = brief(w)
    assert info["triggers"][0]["kind"] == "route_stagnation_limit"
    with pytest.raises(ResearchError, match="route is not active"):
        decide(w, "cannot-continue", "continue", "a")
    decide(w, "switch", "switch", "b")
    assert w.store.get("route", "a")["stagnant"] == 2 and not brief(w)["due"]


def test_legacy_enable_is_explicit_and_one_time(tmp_path):
    root = tmp_path / "legacy"
    store = Store(root, create=True)
    with store.transaction("workspace.init", "fixture"):
        store.put("project", "main", {"id": "legacy", "title": "Legacy project", "schema_version": 2, "goal": None}, new=True)
    store.close()
    w = Workbench(root)
    try:
        assert brief(w) == {"enabled": False, "due": False}
        previous = w.audit()["events"]
        w.apply("strategy.enable", {"round_interval": 4})
        assert brief(w)["round_interval"] == 4 and w.audit()["events"] == previous + 1
        with pytest.raises(ResearchError, match="already enabled"):
            w.apply("strategy.enable", {})
    finally:
        w.store.close()

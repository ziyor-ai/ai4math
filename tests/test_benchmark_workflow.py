"""Offline regression tests for the opt-in benchmark paths."""
import json
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from ai4math import study
from ai4math.checkpoint import export_checkpoint, restore_checkpoint
from ai4math.codex_runner import ResearchSession, run_managed
from ai4math.codex_worker_tools import WorkerTools
from ai4math.evaluation import EvaluationError, EvaluationLedger, READY
from ai4math.execution import run_task
from ai4math.request_identity import PROFILE
from ai4math.store import ResearchError, Store, digest, file_hash
from ai4math.workbench import Workbench


def model_ledger(tmp_path, cases, name="ledger"):
    evidence = tmp_path / "synthetic-readiness.txt"
    if not evidence.exists():
        evidence.write_text("Synthetic unit test only", encoding="utf-8")
    model = {"provider": "fixture", "model": "m", "revision": "m-2026-11-12",
             "reasoning_effort": "high", "settings_sha256": "f" * 64}
    plan = {"schema_version": 2, "comparison_scope": "inference_only", "arms": ["B", "C"],
            "tasks": {k: digest(v) for k, v in cases.items()}, "per_task_token_cap": 100,
            "development_token_cap": 1, "max_development_calls": 1, "max_solve_calls_per_task": 3,
            "models": {"research": model, "review": model}, "initial_methods_sha256": "b" * 64,
            "evolved_methods_sha256": "c" * 64, "readiness": dict.fromkeys(READY, True),
            "readiness_evidence": dict.fromkeys(READY, file_hash(evidence))}
    return EvaluationLedger.create(tmp_path / name, plan, readiness_files=dict.fromkeys(READY, evidence))


def reserve(ledger, key, wire, arm="B"):
    ledger.reserve({"id": key, "arm": arm, "phase": "solve", "task": "case",
                    "task_sha256": ledger.plan["tasks"]["case"], "methods_sha256": ("b" if arm == "B" else "c") * 64,
                    "role": "research", "model": ledger.plan["models"]["research"], "request_sha256": wire,
                    "input_counter": "provider_exact", "input_tokens": 15, "max_output_tokens": 10,
                    "enforced_output_cap": 10})


def test_bound_usage_requires_exact_receipt_and_blocks_duplicate_after_reopen(tmp_path):
    ledger = model_ledger(tmp_path, {"case": "fixture"})
    reserve(ledger, "one", "1" * 64)
    model = ledger.plan["models"]["research"]
    usage = {"inputTokens": 15, "outputTokens": 5, "totalTokens": 20}
    raw = {"id": "response", "model": model["revision"], "reasoning_effort": "high", "usage": usage, "status": "completed"}
    receipt = {"request_sha256": "1" * 64, "model": model, "raw_response": raw}
    with pytest.raises(EvaluationError, match="bound response"):
        ledger.settle("one", usage)
    with pytest.raises(EvaluationError, match="raw response reasoning effort differs"):
        ledger.settle("one", usage, receipt={**receipt, "raw_response": {**raw, "reasoning_effort": "low"}})
    first = ledger.settle("one", usage, receipt=receipt)
    assert first["charge"] == 20 and first["receipt"]["response_id"] == raw["id"]
    ledger = EvaluationLedger(ledger.root)
    reserve(ledger, "two", "2" * 64, "C")
    with pytest.raises(EvaluationError, match="binding differs"):
        ledger.settle("two", usage, receipt=receipt)
    with pytest.raises(EvaluationError, match="already settled"):
        ledger.settle("two", usage, receipt={**receipt, "request_sha256": "2" * 64})
    ledger.settle("two", None, "timeout")
    audit = ledger.audit()
    assert audit["arms"]["C"]["charged_or_reserved_tokens"] == "25"
    assert audit["arms"]["B"]["total_native_tokens"] == 20
    (ledger.root / "responses" / (first["receipt"]["response_sha256"] + ".json")).write_text("{}")
    with pytest.raises(EvaluationError, match="response bytes changed"):
        ledger.audit()


def test_checkpoint_replays_only_prefix_and_uses_remaining_route_and_original_ledger(tmp_path):
    origin = Workbench.create(tmp_path / "origin", "test", "test")
    ledger = model_ledger(tmp_path, {"case": "fixture"})
    reserve(ledger, "one", "1" * 64)
    ledger.settle("one", None, "timeout")
    try:
        origin.apply("node.add", {"id": "goal", "statement": "Synthetic", "domain": "n=1", "quantifier": "finite"})
        origin.apply("goal.set", {"node": "goal"})
        origin.apply("route.add", {"id": "r", "node": "goal", "rationale": "fixture", "next_question": "fixture",
                                   "stop_condition": "fixture", "max_runs": 2, "stagnation_limit": 3})
        origin.apply("route.status", {"id": "r", "status": "active", "reason": "fixture"})
        program = origin.store.root / "program.py"
        program.write_text("print('fixture')", encoding="utf-8")
        artifact = origin.apply("artifact.import", {"path": str(program), "purpose": "fixture"})["id"]
        spec = {"id": "first", "route": "r", "role": "produce", "argv": [sys.executable, "{program}", "{input}"],
                "inputs": [], "program_artifact": artifact, "execution_profile": PROFILE,
                "timeout_seconds": 3, "output_limit_bytes": 5000,
                "request_context": {"trial_id": "trial", "arm": "B", "request_id": "first"}}
        origin.apply("task.add", spec)
        assert run_task(origin, "first")["status"] == "completed"
        pending = {**spec, "id": "next", "request_context": {**spec["request_context"], "request_id": "next"}}
        origin.apply("task.add", pending)
        at = origin.audit()["events"]
        withheld = origin.store.root / "after.txt"
        withheld.write_text("later evidence", encoding="utf-8")
        future = origin.apply("artifact.import", {"path": str(withheld), "purpose": "after cutoff"})["id"]
        bundle = export_checkpoint(origin, tmp_path / "bundle", at, ledgers={"model": ledger})
        with pytest.raises(ResearchError, match="original ledgers"):
            restore_checkpoint(tmp_path / "bundle", tmp_path / "no-ledger", bundle["manifest_sha256"], actor="fixture")
        dst = tmp_path / "restored"
        result = restore_checkpoint(tmp_path / "bundle", dst, bundle["manifest_sha256"],
                                    actor="fixture", ledgers={"model": ledger})
        assert result["parent_tail"] == bundle["event_tail"] and result["audit"]["passed"]
        restored = Workbench(dst)
        try:
            assert not restored.store.exists("artifact", future)
            assert restored.store.get("route", "r")["runs"] == 1
            with pytest.raises(ResearchError, match="archival"):
                run_task(restored, "next")
            with pytest.raises(ResearchError, match="excluded"):
                restored.apply("artifact.import", {"path": str(withheld), "purpose": "forbidden"})
            new = {**pending, "id": "next-resumed", "resume_from": "next",
                   "request_context": {**pending["request_context"], "request_id": "next-resumed"}}
            restored.apply("task.add", new)
            assert run_task(restored, new["id"])["status"] == "completed"
            assert restored.store.get("route", "r")["runs"] == 2
        finally:
            restored.store.close()
    finally:
        origin.store.close()


def test_study_keeps_failure_rows_and_binds_blind_citation_checks(tmp_path):
    original = tmp_path / "original.pdf"
    original.write_bytes(b"%PDF-synthetic fixture")
    passage = tmp_path / "passage.txt"
    passage.write_text("Assume the space is complete.", encoding="utf-8")
    plan_files = {name: tmp_path / f"{name}.json" for name in ("inputs", "dataset", "B-config", "C-config")}
    for name, path in plan_files.items():
        path.write_text(json.dumps({"fixture": name}), encoding="utf-8")
    cases = {"case": {"statement": "Fixture statement", "domain": "complete spaces", "sources": ["source"],
                      "input_manifest_sha256": file_hash(plan_files["inputs"]), "checker_sha256": None, "checkpoint_sha256": None}}
    ledger = model_ledger(tmp_path, cases)
    plan = {"schema": "ai4math-study-v1", "id": "paired", "dataset": {"name": "fixture", "version": "1", "sha256": file_hash(plan_files["dataset"])},
            "arms": {arm: {"name": arm + "-private-runner", "config_sha256": file_hash(plan_files[f"{arm}-config"])} for arm in ("B", "C")},
            "cases": cases, "checkpoints_seconds": [10], "scoring_version": "rubric-1",
            "model_plan_sha256": ledger.plan_hash, "resource_plan_sha256": None,
            "sources": {"source": {"version": "fixture", "url": "https://example.org/fixture",
                                   "original_sha256": file_hash(original),
                                   "passages": {"p": {"page": 2, "locator": "lemma", "text_sha256": file_hash(passage),
                                                      "assumptions": {"complete": "The space is complete."}}}}}}
    root = tmp_path / "study"
    files = (original, passage, *plan_files.values())
    initial = study.create(root, plan, {file_hash(p): str(p) for p in files}, ledger, None, "coordinator")
    assert initial["scheduled"] == 2 and initial["recorded"] == 0
    store = Store(root)
    try:
        out = tmp_path / "blind.zip"
        with pytest.raises(ResearchError, match="every scheduled outcome"):
            study.export_blind(store, out, "custodian")
        draft = tmp_path / "draft.md"
        draft.write_text("An incomplete argument.", encoding="utf-8")
        row = {"case": "case", "arm": "B", "checkpoint_seconds": 10, "status": "timeout", "elapsed_seconds": 10,
               "stop_confirmed": True, "draft": str(draft), "reason": "private setup details",
               "citations": [{"id": "cite", "source": "source", "passage": "p", "claim": "a claim",
                              "use_point": "line 3", "assumptions": {}}]}
        with pytest.raises(ResearchError, match="physical stop receipt"):
            study.record_outcome(store, row, ledger, None, "coordinator")
        stop = tmp_path / "synthetic-stop.json"
        stop.write_text(json.dumps({"stop_confirmed": True, "task": "fixture"}), encoding="utf-8")
        sealed = study.record_outcome(store, {**row, "stop_receipt": str(stop)}, ledger, None, "coordinator")
        assert sealed["stop_receipt"] == file_hash(stop)
        study.record_outcome(store, {**row, "arm": "C", "status": "not_run", "elapsed_seconds": 0, "stop_confirmed": None,
                                     "draft": None, "citations": []}, ledger, None, "coordinator")
        assert study.export_blind(store, out, "custodian")["packets"] == 2
        with zipfile.ZipFile(out) as archive:
            packets = [json.loads(archive.read(name)) for name in archive.namelist() if name.endswith(".json")]
            assert {p["status"] for p in packets} == {"timeout", "not_run"}
            assert all("arm" not in p and "reason" not in p and "usage" not in p for p in packets)
            assert archive.read("sources/" + file_hash(passage)) == passage.read_bytes()
            assert not any(file_hash(path) in name for path in plan_files.values() for name in archive.namelist())
        packet = next(p for p in packets if p["status"] == "timeout")
        assert packet["citations"]["cite"]["missing_assumptions"] == ["complete"]
        review = {"review_id": packet["review_id"], "packet_sha256": packet["packet_sha256"], "reviewer_kind": "human",
                  "correctness": "unknown", "scope": "fail", "attribution": "fail", "progress": 0,
                  "review_seconds": 3, "notes": "The premise is missing.",
                  "citation_checks": {"cite": {"passage": "pass", "assumptions": "pass", "use_point": "unknown", "notes": "fixture"}}}
        with pytest.raises(ResearchError, match="missing assumption"):
            study.record_review(store, review, "reviewer")
        review["citation_checks"]["cite"]["assumptions"] = "fail"
        study.record_review(store, review, "reviewer")
        assert study.audit(store)["rows"][0]["evidence_level"] == "unknown"
    finally:
        store.close()


def test_legacy_isolated_contract_cannot_launch_as_trusted_local(tmp_path):
    task = {"status": "pending", "codex_workflow": {"tool_sandbox_artifact": "1" * 64}}
    workspace = SimpleNamespace(store=SimpleNamespace(get=lambda kind, key: task))
    with pytest.raises(ResearchError, match="retired tool sandbox"):
        run_managed(workspace, "task", "coordinator", tmp_path / "codex.exe", tmp_path)
    with pytest.raises(ResearchError, match="retired tool sandbox"):
        WorkerTools(tmp_path, task, tmp_path)


def test_codex_refuses_model_drift_before_turn(tmp_path):
    sent = []
    tools = SimpleNamespace(directory=tmp_path, model_settings={"provider": "fixture", "model": "m",
                                                                 "reasoning_effort": "high"})
    wire = SimpleNamespace(send=sent.append)
    log = SimpleNamespace(thread_id=None, emit=lambda *args, **kwargs: None)
    session = ResearchSession(wire, log, tools, tmp_path / "skill")

    def rpc(method, params):
        sent.append((method, params))
        return {"thread": {"id": "thread"}, "model": "wrong", "modelProvider": "fixture", "reasoningEffort": "high"}

    session.rpc = rpc
    try:
        with pytest.raises(ResearchError, match="different model settings"):
            session.start()
        assert sent[-1][1]["allowProviderModelFallback"] is False
        assert sent[-1][1]["model"] == "m"
    finally:
        session.pool.shutdown()

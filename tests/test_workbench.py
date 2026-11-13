"""Policy counterexamples and a complete independently checked finite workflow."""
import io
import json
import sqlite3
import sys
from concurrent.futures import ThreadPoolExecutor

import pytest

from ai4math.cli import main
from ai4math.execution import export_task, run_task
from ai4math.report import markdown
from ai4math.store import ResearchError, file_hash
from ai4math.workbench import Workbench
from examples.run_finite_demo import run as finite_demo


@pytest.fixture
def wb(tmp_path):
    instance = Workbench.create(tmp_path / "workspace", "test", "Test research")
    yield instance
    instance.store.close()


def node(wb, key="goal", quantifier="universal", **extra):
    return wb.apply("node.add", {"id": key, "statement": f"Exact mathematical statement {key}",
                    "domain": "all positive integers" if quantifier == "universal" else "n=2",
                    "quantifier": quantifier, **extra})


def route(wb, max_runs=3, stagnation_limit=2):
    node(wb)
    wb.apply("goal.set", {"node": "goal"})
    wb.apply("route.add", {"id": "route", "node": "goal", "rationale": "A distinct construction idea",
             "next_question": "Can it be lifted uniformly?", "stop_condition": "No progress in two checkpoints",
             "max_runs": max_runs, "stagnation_limit": stagnation_limit})
    wb.apply("route.status", {"id": "route", "status": "active", "reason": "Bounded test"})


def artifact(wb, tmp_path, text="argument"):
    path = tmp_path / "argument.txt"
    path.write_text(text, encoding="utf-8")
    return wb.apply("artifact.import", {"path": str(path), "purpose": "test argument"})["id"]


def claim(wb, key="claim", target="goal", level="unknown", scope="universal", artifacts=None, dependencies=None):
    n = wb.store.get("node", target)
    if level == "finite_support" and not artifacts:
        fixture = wb.store.inside("finite-support-fixture.txt")
        fixture.write_text("Synthetic finite instance n=2; no universal conclusion.", encoding="utf-8")
        artifacts = [wb.apply("artifact.import", {"path": str(fixture), "purpose": "finite support test fixture"})["id"]]
    return wb.apply("claim.add", {"id": key, "node": target, "statement_digest": n["statement_digest"],
        "evidence_level": level, "scope": scope, "summary": "Bound exact statement", "artifact_ids": artifacts or [],
        "evidence_domain": "synthetic n=2 fixture",
        "dependencies": dependencies or []}, "author")


def review(wb, key="review", target="claim", **extra):
    return wb.apply("review.add", {"id": key, "claim": target, "reviewer": "reviewer", "reviewer_kind": "model",
        "auditor": "auditor", "coverage_complete": True, "issues": [], "verdict": "ready_for_human", **extra}, "auditor")


def accept_human(wb, key, target, art, dependencies=None):
    n = wb.store.get("node", target)
    claim(wb, key, target, "proved", n["quantifier"], [art], dependencies)
    wb.apply("validation.add", {"id": f"v-{key}", "claim": key, "method": "human_proof", "trust_basis": "Synthetic test attestation only",
        "attestation": "I checked the exact statement, domain, dependencies and attached argument", "artifact_ids": [art]}, "validator")
    review(wb, f"r-{key}", key)
    return wb.apply("claim.accept", {"claim": key, "review": f"r-{key}", "validation": f"v-{key}"}, "custodian")


def task(wb, key="task", code="print('done')", **extra):
    return wb.apply("task.add", {"id": key, "route": "route", "role": "explore", "argv": [sys.executable, "-c", code],
                    "timeout_seconds": 3, "output_limit_bytes": 10000, **extra}, "worker")


def test_finite_demo_has_independent_certificate_and_ledger(tmp_path):
    result = finite_demo(tmp_path / "demo")
    assert result["audit"]["passed"] and result["delivery"]["passed"]
    w = Workbench(tmp_path / "demo")
    try:
        assert w.node_status("p2-exact") == "proved"
        assert w.store.get("task", "validate")["actor"] != w.store.get("claim", "p2-minimum")["author"]
        assert "finite_certificate" in markdown(w)
        assert w.store.get("route", "witness-and-bound")["runs"] == 2
        assert len(w.store.all("import")) >= len(w.store.all("artifact"))
    finally:
        w.store.close()


def test_goal_and_statement_are_locked(wb):
    original = node(wb)
    wb.apply("goal.set", {"node": "goal"})
    with pytest.raises(ResearchError, match="locked"):
        wb.apply("goal.set", {"node": "goal"})
    with pytest.raises(ResearchError, match="already exists"):
        node(wb, statement="weakened statement")
    assert wb.store.get("node", "goal")["statement_digest"] == original["statement_digest"]


def test_dependency_cycles_roll_back_entire_event(wb):
    node(wb, "lemma")
    node(wb, proof_dependencies=["lemma"])
    old = wb.audit()["tail_hash"]
    with pytest.raises(ResearchError, match="cycle"):
        wb.apply("node.plan", {"node": "lemma", "proof_dependencies": ["goal"], "reason": "bad cycle"})
    assert wb.audit()["tail_hash"] == old
    assert wb.store.get("node", "lemma")["proof_dependencies"] == []


def test_routes_only_target_reachable_nodes(wb):
    route(wb)
    node(wb, "irrelevant")
    template = wb.store.get("route", "route")
    with pytest.raises(ResearchError, match="cone"):
        wb.apply("route.add", {**template, "id": "unrelated", "node": "irrelevant"})


def test_raw_bytes_survive_source_change_and_import_is_idempotent(wb, tmp_path):
    a = artifact(wb, tmp_path, "original bytes")
    path = tmp_path / "argument.txt"
    path.write_text("new bytes", encoding="utf-8")
    assert wb.store.verify_blob(a).read_text(encoding="utf-8") == "original bytes"
    b = wb.apply("artifact.import", {"path": str(path), "purpose": "test argument"})["id"]
    assert a != b
    events = wb.audit()["events"]
    wb.apply("artifact.import", {"path": str(path), "purpose": "test argument"})
    assert wb.audit()["events"] == events


def test_corrupt_frozen_artifact_blocks_acceptance(wb, tmp_path):
    node(wb)
    a = artifact(wb, tmp_path)
    claim(wb, artifacts=[a])
    review(wb)
    wb.store.blob(a).write_bytes(b"tampered")
    assert not wb.audit()["passed"]
    with pytest.raises(ResearchError, match="integrity"):
        wb.apply("claim.accept", {"claim": "claim", "review": "review"}, "custodian")


@pytest.mark.parametrize("identifier", ["../escape", "/absolute", "bad/slash", "", ".."]) 
def test_unsafe_identifiers_are_rejected(wb, identifier):
    with pytest.raises(ResearchError):
        node(wb, identifier)


def test_no_workspace_escape(wb):
    with pytest.raises(ResearchError, match="escape"):
        wb.store.inside("../outside")


def test_finite_evidence_cannot_close_universal_goal(wb):
    node(wb)
    with pytest.raises(ResearchError, match="finite evidence"):
        claim(wb, level="proved", scope="finite")
    claim(wb, level="finite_support", scope="finite")
    review(wb)
    wb.apply("claim.accept", {"claim": "claim", "review": "review"}, "custodian")
    assert wb.node_status("goal") == "open"


def test_stale_statement_digest_is_rejected(wb):
    node(wb)
    with pytest.raises(ResearchError, match="different statement"):
        wb.apply("claim.add", {"id": "c", "node": "goal", "statement_digest": "0" * 64,
                              "scope": "universal", "evidence_level": "unknown", "summary": "bad"})


def test_model_review_cannot_substitute_for_validation(wb):
    node(wb)
    claim(wb, level="proved")
    review(wb)
    with pytest.raises(ResearchError, match="validation"):
        wb.apply("claim.accept", {"claim": "claim", "review": "review"}, "custodian")
    with pytest.raises(ResearchError, match="model opinions"):
        wb.apply("validation.add", {"id": "v", "claim": "claim", "method": "model_review", "trust_basis": "LLM agrees"}, "other")


@pytest.mark.parametrize("extra", [
    {"coverage_complete": False},
    {"reviewer": "author"},
    {"auditor": "reviewer"},
    {"issues": [{"description": "gap", "audit": "unresolved", "severity": "blocking"}]},
    {"issues": [{"description": "gap", "audit": "confirmed", "severity": "blocking"}]},
])
def test_incomplete_or_self_review_is_not_ready(wb, extra):
    node(wb)
    claim(wb)
    with pytest.raises(ResearchError):
        review(wb, **extra)


def test_rejected_feedback_is_audited_and_nonblocking(wb):
    node(wb)
    claim(wb)
    review(wb, issues=[{"description": "false positive", "audit": "rejected", "severity": "blocking"}])
    wb.apply("claim.accept", {"claim": "claim", "review": "review"}, "custodian")
    assert wb.store.get("claim", "claim")["evidence_level"] == "unknown"


def test_latest_adverse_review_cannot_be_hidden(wb):
    node(wb)
    claim(wb)
    review(wb)
    review(wb, "later", verdict="reject")
    with pytest.raises(ResearchError, match="latest review"):
        wb.apply("claim.accept", {"claim": "claim", "review": "review"}, "custodian")


def test_revocation_cascades_to_dependents_and_delivery(wb, tmp_path):
    node(wb, "lemma")
    node(wb, proof_dependencies=["lemma"])
    wb.apply("goal.set", {"node": "goal"})
    art = artifact(wb, tmp_path)
    accept_human(wb, "lemma-proof", "lemma", art)
    accept_human(wb, "goal-proof", "goal", art, ["lemma-proof"])
    wb.apply("ledger.add", {"id": "theorem", "node": "goal", "claim": "goal-proof", "use": "theorem", "text": "Bound exact goal"})
    assert wb.delivery_check()["passed"]
    result = wb.apply("claim.revoke", {"claim": "lemma-proof", "reason": "counterexample to premise"})
    assert result["revoked"] == ["goal-proof", "lemma-proof"]
    assert wb.node_status("goal") == "open" and not wb.delivery_check()["passed"]
    assert "BLOCKED" in markdown(wb)


def test_replanned_obligation_requires_new_claim(wb):
    node(wb, "lemma")
    node(wb)
    claim(wb)
    review(wb)
    wb.apply("node.plan", {"node": "goal", "proof_dependencies": ["lemma"], "reason": "found missing premise"})
    with pytest.raises(ResearchError, match="plan changed"):
        wb.apply("claim.accept", {"claim": "claim", "review": "review"}, "custodian")


def test_claim_cannot_omit_planned_proof_dependency(wb, tmp_path):
    node(wb, "lemma")
    node(wb, proof_dependencies=["lemma"])
    a = artifact(wb, tmp_path)
    with pytest.raises(ResearchError, match="bind each"):
        accept_human(wb, "goal-proof", "goal", a)
    assert wb.store.get("claim", "goal-proof")["status"] == "proposed"


def test_manuscript_cannot_call_observation_a_theorem(wb):
    node(wb)
    claim(wb, level="finite_support", scope="finite")
    review(wb)
    wb.apply("claim.accept", {"claim": "claim", "review": "review"}, "custodian")
    with pytest.raises(ResearchError, match="wording exceeds"):
        wb.apply("ledger.add", {"id": "paper", "node": "goal", "claim": "claim", "text": "all cases", "use": "theorem"})


def test_stagnation_parks_route_without_spawning_new_phase(wb):
    route(wb)
    for _ in range(2):
        wb.apply("route.checkpoint", {"route": "route", "finding": "finite table extended", "next_question": "uniform lift?"})
    assert wb.store.get("route", "route")["status"] == "parked"
    assert wb.board()["next_actions"] == []
    with pytest.raises(ResearchError, match="budget exhausted"):
        wb.apply("route.status", {"id": "route", "status": "active", "reason": "just continue"})


def test_finite_support_has_no_progress_credit(wb):
    route(wb)
    claim(wb, level="finite_support", scope="finite")
    review(wb)
    wb.apply("claim.accept", {"claim": "claim", "review": "review"}, "custodian")
    with pytest.raises(ResearchError, match="progress credit"):
        wb.apply("route.checkpoint", {"route": "route", "finding": "more cases", "next_question": "next?", "claims": ["claim"]})


def test_route_run_budget_is_enforced(wb):
    route(wb, max_runs=1)
    task(wb)
    assert run_task(wb, "task")["status"] == "completed"
    assert wb.store.get("route", "route")["status"] == "parked"
    with pytest.raises(ResearchError):
        task(wb, "second")


def test_timeout_stays_unknown_and_cannot_be_rerun(wb):
    route(wb)
    task(wb, code="import time; time.sleep(30)", timeout_seconds=1)
    result = run_task(wb, "task")
    assert result["status"] == "unknown_timeout"
    assert wb.store.all("claim") == []
    with pytest.raises(ResearchError, match="not pending"):
        run_task(wb, "task")


def test_output_limit_is_bounded_not_success(wb):
    route(wb)
    task(wb, code="import sys; sys.stdout.write('x'*1000000); sys.stdout.flush()", output_limit_bytes=1000)
    result = run_task(wb, "task")
    assert result["status"] == "output_limit" and result["captured_bytes"] <= 1000
    assert wb.store.inside("tasks/task/stdout.log").stat().st_size <= 1000


def test_failed_exit_and_missing_executable_remain_failed(wb):
    route(wb)
    task(wb, code="raise SystemExit(7)")
    assert run_task(wb, "task")["status"] == "failed"
    task(wb, "missing", argv=["ai4math-this-executable-does-not-exist"])
    assert run_task(wb, "missing")["status"] == "failed"


def test_export_is_nonexecuting_and_changed_contract_rejected(wb):
    route(wb)
    task(wb)
    exported = export_task(wb, "task")
    assert wb.store.get("task", "task")["status"] == "pending"
    assert wb.store.get("route", "route")["runs"] == 0
    wb.store.inside("tasks/task/input.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ResearchError, match="contract differs"):
        run_task(wb, "task")


def test_stale_result_cannot_be_accepted(wb):
    route(wb)
    task(wb)
    export_task(wb, "task")
    wb.store.inside("tasks/task/result.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ResearchError, match="already contains"):
        run_task(wb, "task")


def test_validator_must_be_frozen_python_program(wb, tmp_path):
    route(wb)
    checker = artifact(wb, tmp_path, "print('checker')")
    with pytest.raises(ResearchError, match="validation argv"):
        task(wb, role="validate", validator_artifact=checker,
             argv=[sys.executable, "-c", "print('pretend validation')", "{validator}"])


def test_completed_command_without_result_is_not_a_proof(wb, tmp_path):
    route(wb)
    witness = artifact(wb, tmp_path, "witness")
    checker = artifact(wb, tmp_path, "print('passed')")
    claim(wb, level="proved", artifacts=[witness])
    task(wb, role="validate", argv=[sys.executable, "{validator}", "{input}"], validator_artifact=checker, inputs=[witness])
    run_task(wb, "task", "worker")
    with pytest.raises(ResearchError, match="did not produce"):
        wb.apply("validation.add", {"id": "v", "claim": "claim", "task": "task", "method": "formal_kernel", "trust_basis": "explicit checker"}, "validator")


def test_recovery_is_explicit_and_does_not_retry(wb):
    route(wb)
    task(wb)
    with wb.store.transaction("test.simulated_crash", "fixture"):
        t = wb.store.get("task", "task")
        t["status"] = "running"
        wb.store.put("task", "task", t)
    with pytest.raises(ResearchError, match="confirmation"):
        wb.apply("task.recover", {"task": "task", "reason": "crash", "process_confirmed_stopped": False})
    recovered = wb.apply("task.recover", {"task": "task", "reason": "confirmed process absent", "process_confirmed_stopped": True})
    assert recovered["status"] == "interrupted"
    with pytest.raises(ResearchError, match="not pending"):
        run_task(wb, "task")


def test_event_table_is_append_only_and_projection_tamper_is_detected(wb):
    node(wb)
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        wb.store.db.execute("DELETE FROM events")
    wb.store.db.execute("UPDATE records SET body='{}' WHERE kind='node'")
    assert not wb.audit()["passed"]
    with pytest.raises(ResearchError, match="integrity"):
        node(wb, "other")


def test_concurrent_writers_keep_one_event_chain(wb):
    root = wb.store.root
    def write(index):
        local = Workbench(root)
        try:
            node(local, f"lemma-{index}")
        finally:
            local.store.close()
    with ThreadPoolExecutor(max_workers=3) as executor:
        list(executor.map(write, range(6)))
    assert wb.audit()["passed"] and wb.audit()["events"] == 7


def test_cli_delivery_and_anchor_return_failure(wb, capsys):
    root = str(wb.store.root)
    assert main(["--workspace", root, "delivery-check"]) == 2
    assert main(["--workspace", root, "audit", "--anchor", "0" * 64]) == 2
    assert main(["--workspace", root, "report", "--output", "objects/overwrite"]) == 2
    assert main(["legacy", "init", "--config", "unused.json"]) == 2


def test_cli_prints_successful_unicode_receipt_on_legacy_stream(wb, monkeypatch):
    buffer = io.BytesIO()
    stream = io.TextIOWrapper(buffer, encoding="cp1252", errors="strict")
    monkeypatch.setattr(sys, "stdout", stream)
    node = {"id": "unicode", "statement": "核对原文 ∇", "domain": "fixture", "quantifier": "universal"}
    assert main(["--workspace", str(wb.store.root), "apply", "node.add", "--json", json.dumps(node)]) == 0
    stream.flush()
    assert json.loads(buffer.getvalue())["statement"] == wb.store.get("node", "unicode")["statement"] == node["statement"]


def test_init_does_not_overwrite_existing_workspace(wb):
    old = wb.audit()["tail_hash"]
    with pytest.raises(ResearchError, match="already exists"):
        Workbench.create(wb.store.root, "replacement", "bad replacement")
    assert wb.audit()["tail_hash"] == old

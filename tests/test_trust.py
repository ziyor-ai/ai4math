"""Batch 8.0 slice 6: declared receipts stay declared; a policy makes one trusted."""
import json
import sys

import pytest

from ai4math.store import ResearchError, now
from ai4math.workbench import Workbench


@pytest.fixture
def wb(tmp_path):
    w = Workbench.create(tmp_path / "ws", "trust", "Trust policy")
    w.apply("node.add", {"id": "goal", "statement": "Exact goal", "domain": "all n",
                         "quantifier": "universal"})
    w.apply("goal.set", {"node": "goal"})
    w.apply("strategy.configure", {"correspondence_after": 1000})
    w.apply("route.add", {"id": "route", "node": "goal", "rationale": "proof",
                          "next_question": "q", "stop_condition": "s",
                          "max_runs": 50, "stagnation_limit": 50})
    w.apply("route.status", {"id": "route", "status": "active", "reason": "test"})
    yield w
    w.store.close()


def artifact(w, name, text):
    path = w.store.root / name
    path.write_text(text, encoding="utf-8")
    return w.apply("artifact.import", {"path": str(path), "purpose": name})["id"]


def policy_for(w, checker, **overrides):
    payload = {"id": "POLICY-1", "checker_artifact": checker,
               "allowed_kernel_versions": ["4.34.1"],
               "allowed_library_revisions": ["d13f23b7"],
               "allowed_axioms": ["propext"],
               "forbidden_axioms": ["sorryAx"],
               "goal_type_match": "exact_statement_digest",
               "scope": "formal_kernel_only"}
    payload.update(overrides)
    return w.apply("checker.policy.add", payload, "admin")


def build(w, name, result_overrides=None):
    """Create a proved claim plus a completed validate task with a matching result."""
    arg = artifact(w, name + "-arg.txt", "argument for " + name)
    checker = artifact(w, name + "-checker.py", "print('ok')\n")
    node = w.store.get("node", "goal")
    claim_id = name + "-claim"
    w.apply("claim.add", {"id": claim_id, "node": "goal", "statement_digest": node["statement_digest"],
                          "evidence_level": "proved", "scope": "universal", "summary": "proof",
                          "artifact_ids": [arg]}, "author")
    task_id = name + "-task"
    w.apply("task.add", {"id": task_id, "route": "route", "role": "validate",
                         "argv": [sys.executable, "{validator}", "{input}"],
                         "validator_artifact": checker, "inputs": [arg]}, "runner")
    task = w.store.get("task", task_id)
    result = {"task_id": task_id, "statement_digest": node["statement_digest"],
              "method": "formal_kernel", "scope": "universal", "evidence_level": "proved",
              "passed": True, "input_artifacts": sorted(task["inputs"]),
              "checks": ["replayed the proof"], "limitations": ["pinned environment"],
              "lean_version": "Lean (version 4.34.1, commit 5045d005)", "mathlib_revision": "d13f23b7"}
    result.update(result_overrides or {})
    result_art = artifact(w, name + "-result.json", json.dumps(result))
    with w.store.transaction("fixture.complete_task", "test"):
        row = w.store.get("task", task_id)
        row.update(status="completed", result_artifact=result_art, executed_by="runner", finished_at=now())
        w.store.put("task", task_id, row)
    return claim_id, task_id, checker


def validate(w, claim_id, task_id, name, **overrides):
    payload = {"id": name + "-val", "claim": claim_id, "method": "formal_kernel",
               "trust_basis": "pinned kernel replay", "task": task_id}
    payload.update(overrides)
    return w.apply("validation.add", payload, "registrar")


def accept(w, claim_id, validation_id):
    with w.store.transaction("fixture.review", "test"):
        w.store.put("review", claim_id + "-rev",
                    {"id": claim_id + "-rev", "claim": claim_id, "verdict": "ready_for_human",
                     "reviewer": "rev", "auditor": "aud", "review_order": 1,
                     "created_at": "2030-01-01T00:00:00+00:00", "issues": [],
                     "coverage_complete": True}, new=True)
    return w.apply("claim.accept", {"claim": claim_id, "review": claim_id + "-rev",
                                    "validation": validation_id}, "custodian")


def test_a_policy_needs_a_frozen_checker(wb):
    with pytest.raises(ResearchError, match="missing artifact"):
        wb.apply("checker.policy.add", {"id": "P", "checker_artifact": "0" * 64}, "admin")


def test_the_policy_is_unique(wb):
    checker = artifact(wb, "c.py", "print('ok')\n")
    policy_for(wb, checker)
    with pytest.raises(ResearchError, match="already exists"):
        policy_for(wb, checker)


def test_without_a_policy_a_receipt_stays_declared_and_still_accepts(wb):
    claim_id, task_id, _ = build(wb, "plain")
    record = validate(wb, claim_id, task_id, "plain")
    assert record["trust"] == "declared_external_checker"
    assert record["trust_class"] == "declared"
    assert record["verification_round"] == "initial"
    assert "no checker policy" in record["trust_reasons"]
    assert accept(wb, claim_id, record["id"])["status"] == "accepted"


def test_a_policy_authorised_receipt_becomes_trusted(wb):
    claim_id, task_id, checker = build(wb, "ok")
    policy_for(wb, checker)
    record = validate(wb, claim_id, task_id, "ok")
    assert record["trust_class"] == "trusted"
    assert record["trust"] == "trusted"
    assert record["environment_digest"]
    assert record["exact_goal_type_digest"]
    assert accept(wb, claim_id, record["id"])["status"] == "accepted"


def test_a_different_checker_is_only_declared(wb):
    claim_id, task_id, _ = build(wb, "other")
    # Content addressing: the policy checker must differ in bytes, not just in name.
    policy_for(wb, artifact(wb, "policy-checker.py", "print('different checker')\n"))
    record = validate(wb, claim_id, task_id, "other")
    assert record["trust_class"] == "declared"
    with pytest.raises(ResearchError, match="requires a trusted validation"):
        accept(wb, claim_id, record["id"])


def test_a_version_outside_the_policy_is_only_declared(wb):
    claim_id, task_id, checker = build(wb, "ver", {"lean_version": "Lean (version 4.99.0)"})
    policy_for(wb, checker)
    record = validate(wb, claim_id, task_id, "ver")
    assert record["trust_class"] == "declared"
    assert any("lean_version" in reason for reason in record["trust_reasons"])


def test_a_forbidden_axiom_is_only_declared(wb):
    claim_id, task_id, checker = build(wb, "ax", {"axioms": ["propext", "sorryAx"]})
    policy_for(wb, checker)
    record = validate(wb, claim_id, task_id, "ax")
    assert record["trust_class"] == "declared"
    assert any("axiom" in reason for reason in record["trust_reasons"])


def test_a_second_trusted_validation_is_a_recheck(wb):
    claim_id, task_id, checker = build(wb, "re")
    policy_for(wb, checker)
    first = validate(wb, claim_id, task_id, "re")
    second = validate(wb, claim_id, task_id, "re2")
    assert first["verification_round"] == "initial"
    assert second["verification_round"] == "recheck"


def test_a_caller_cannot_supply_the_trust_fields(wb):
    claim_id, task_id, _ = build(wb, "spoof")
    with pytest.raises(ResearchError, match="must not supply"):
        validate(wb, claim_id, task_id, "spoof", trust_class="trusted")

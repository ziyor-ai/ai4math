"""Every validation is referenced from its claim (v3.4 §6.3b)."""
import json
import sys

import pytest

from ai4math import verification
from ai4math.store import now
from ai4math.workbench import Workbench


@pytest.fixture
def wb(tmp_path):
    w = Workbench.create(tmp_path / "ws", "vrefs", "Verification refs")
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


def build(w, name):
    arg = artifact(w, name + "-arg.txt", "argument")
    checker = artifact(w, name + "-checker.py", "print('ok')\n")
    node = w.store.get("node", "goal")
    claim_id = name + "-claim"
    w.apply("claim.add", {"id": claim_id, "node": "goal",
                          "statement_digest": node["statement_digest"],
                          "evidence_level": "proved", "scope": "universal",
                          "summary": "proof", "artifact_ids": [arg]}, "author")
    task_id = name + "-task"
    w.apply("task.add", {"id": task_id, "route": "route", "role": "validate",
                         "argv": [sys.executable, "{validator}", "{input}"],
                         "validator_artifact": checker, "inputs": [arg]}, "runner")
    task = w.store.get("task", task_id)
    result = {"task_id": task_id, "statement_digest": node["statement_digest"],
              "method": "formal_kernel", "scope": "universal", "evidence_level": "proved",
              "passed": True, "input_artifacts": sorted(task["inputs"]),
              "checks": ["replayed"], "limitations": ["pinned"],
              "lean_version": "Lean (version 4.34.1)", "mathlib_revision": "d13f23b7"}
    result_art = artifact(w, name + "-result.json", json.dumps(result))
    with w.store.transaction("fixture.complete", "test"):
        row = w.store.get("task", task_id)
        row.update(status="completed", result_artifact=result_art, executed_by="runner",
                   finished_at=now())
        w.store.put("task", task_id, row)
    return claim_id, task_id, checker


def validate(w, claim_id, task_id, name):
    return w.apply("validation.add", {"id": name + "-val", "claim": claim_id,
                                      "method": "formal_kernel",
                                      "trust_basis": "pinned replay", "task": task_id},
                   "registrar")


def accept(w, claim_id, validation_id):
    with w.store.transaction("fixture.review", "test"):
        w.store.put("review", claim_id + "-rev",
                    {"id": claim_id + "-rev", "claim": claim_id, "verdict": "ready_for_human",
                     "reviewer": "rev", "reviewer_kind": "model", "auditor": "aud",
                     "review_order": 1, "created_at": "2030-01-01T00:00:00+00:00",
                     "issues": [], "coverage_complete": True}, new=True)
    return w.apply("claim.accept", {"claim": claim_id, "review": claim_id + "-rev",
                                    "validation": validation_id}, "custodian")


def test_a_claim_starts_with_no_refs(wb):
    claim_id, _, _ = build(wb, "c1")
    claim = wb.store.get("claim", claim_id)
    assert claim.get("verification_refs") in (None, [])
    assert verification.verification_refs(wb, claim) == []


def test_each_validation_is_appended_to_the_claim(wb):
    claim_id, task_id, _ = build(wb, "c1")
    first = validate(wb, claim_id, task_id, "first")
    assert wb.store.get("claim", claim_id)["verification_refs"] == [first["id"]]
    second = validate(wb, claim_id, task_id, "second")
    assert wb.store.get("claim", claim_id)["verification_refs"] == sorted([first["id"], second["id"]])


def test_acceptance_records_the_certifying_validation(wb):
    claim_id, task_id, _ = build(wb, "c1")
    record = validate(wb, claim_id, task_id, "v")
    accepted = accept(wb, claim_id, record["id"])
    assert accepted["accepted_validation"] == record["id"]
    assert record["id"] in accepted["verification_refs"]


def test_a_trusted_validation_is_marked_on_the_claim(wb):
    claim_id, task_id, checker = build(wb, "c1")
    wb.apply("checker.policy.add", {"id": "P", "checker_artifact": checker}, "admin")
    record = validate(wb, claim_id, task_id, "v")
    assert record["trust_class"] == "trusted"
    accepted = accept(wb, claim_id, record["id"])
    assert accepted["trusted_validation"] == record["id"]


def test_provenance_lists_the_refs(wb):
    claim_id, task_id, _ = build(wb, "c1")
    record = validate(wb, claim_id, task_id, "v")
    accept(wb, claim_id, record["id"])
    page = verification.provenance(wb, claim_id)
    assert page["verification_refs"] == [record["id"]]
    assert page["accepted_validation"] == record["id"]


def test_legacy_claims_derive_their_refs(wb):
    claim_id, task_id, _ = build(wb, "c1")
    validate(wb, claim_id, task_id, "v")
    legacy = dict(wb.store.get("claim", claim_id))
    legacy.pop("verification_refs")
    assert verification.verification_refs(wb, legacy) == ["v-val"]

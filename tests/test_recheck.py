"""Executed rechecks: a fresh replay newer than the toolchain change (v3.4 §4.6d)."""
import json
import sys

import pytest

from ai4math import verification
from ai4math.store import ResearchError, now
from ai4math.workbench import Workbench


@pytest.fixture
def wb(tmp_path):
    w = Workbench.create(tmp_path / "ws", "recheck", "Recheck")
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


def accepted(w, claim_id, dependencies=None, environment=None):
    arg = artifact(w, claim_id + "-arg.txt", "argument")
    digest = w.store.get("node", "goal")["statement_digest"]
    w.apply("claim.add", {"id": claim_id, "node": "goal", "statement_digest": digest,
                          "evidence_level": "proved", "scope": "universal", "summary": "proof",
                          "artifact_ids": [arg], "dependencies": dependencies or []}, "author")
    validation = {"id": claim_id + "-val", "claim": claim_id, "method": "formal_kernel",
                  "passed": True, "artifact_ids": [arg], "trust": "declared_external_checker",
                  "trust_basis": "fixture", "validator": "val", "trust_class": "declared"}
    if environment is not None:
        validation["environment"] = environment
    with w.store.transaction("fixture.accept_inputs", "test"):
        w.store.put("validation", claim_id + "-val", validation, new=True)
        w.store.put("review", claim_id + "-rev",
                    {"id": claim_id + "-rev", "claim": claim_id, "verdict": "ready_for_human",
                     "reviewer": "rev", "reviewer_kind": "model", "auditor": "aud",
                     "review_order": 1, "created_at": "2030-01-01T00:00:00+00:00",
                     "issues": [], "coverage_complete": True}, new=True)
    return w.apply("claim.accept", {"claim": claim_id, "review": claim_id + "-rev",
                                    "validation": claim_id + "-val"}, "custodian")


def fresh_validation(w, claim_id, name="recheck", lean_version="Lean (version 4.34.1)"):
    arg = w.store.get("claim", claim_id)["artifact_ids"][0]
    checker = artifact(w, name + "-checker.py", "print('ok')\n")
    w.apply("task.add", {"id": name + "-task", "route": "route", "role": "validate",
                         "argv": [sys.executable, "{validator}", "{input}"],
                         "validator_artifact": checker, "inputs": [arg],
                         "recheck_claim": claim_id}, "runner")
    node = w.store.get("node", "goal")
    result = {"task_id": name + "-task", "statement_digest": node["statement_digest"],
              "method": "formal_kernel", "scope": "universal", "evidence_level": "proved",
              "passed": True, "input_artifacts": [arg], "checks": ["replayed"],
              "limitations": ["pinned environment"],
              "lean_version": lean_version, "mathlib_revision": "d13f23b7"}
    result_art = artifact(w, name + "-result.json", json.dumps(result))
    with w.store.transaction("fixture.complete", "test"):
        row = w.store.get("task", name + "-task")
        row.update(status="completed", result_artifact=result_art, executed_by="runner",
                   finished_at=now())
        w.store.put("task", name + "-task", row)
    return w.apply("validation.add", {"id": name + "-val", "claim": claim_id,
                                      "method": "formal_kernel",
                                      "trust_basis": "fresh kernel replay",
                                      "task": name + "-task"}, "registrar")


def test_a_revoked_dependency_blocks_a_claim_without_erasing_it(wb):
    accepted(wb, "c1")
    accepted(wb, "c2", dependencies=["c1"])
    result = wb.apply("claim.revoke", {"claim": "c1", "reason": "bad receipt"}, "custodian")
    assert result["revoked"] == ["c1"]
    assert result["affected"] == ["c2"]
    child = wb.store.get("claim", "c2")
    assert child["status"] == "accepted"
    assert verification.recheck_required(wb, child) is True
    assert verification.effective(wb, child) == "blocked"


def test_a_blocked_dependency_cannot_be_used_for_a_new_claim(wb):
    accepted(wb, "c1")
    accepted(wb, "c2", dependencies=["c1"])
    wb.apply("claim.revoke", {"claim": "c1", "reason": "bad receipt"}, "custodian")
    digest = wb.store.get("node", "goal")["statement_digest"]
    with pytest.raises(ResearchError, match="blocked by a revocation"):
        wb.apply("claim.add", {"id": "c3", "node": "goal", "statement_digest": digest,
                               "evidence_level": "proved", "scope": "universal",
                               "summary": "s", "dependencies": ["c2"]}, "author")


def test_a_plain_validation_on_an_accepted_claim_is_refused(wb):
    accepted(wb, "standalone")
    with pytest.raises(ResearchError, match="needs a recheck"):
        wb.apply("validation.add", {"id": "late-val", "claim": "standalone",
                                    "method": "formal_kernel", "trust_basis": "x"}, "registrar")


def test_a_toolchain_change_is_a_recheck_not_an_invalidation(wb):
    accepted(wb, "c1", environment={"lean_version": "Lean (version 4.34.1)",
                                    "mathlib_revision": "d13f23b7"})
    checker = artifact(wb, "policy-checker.py", "print('ok')\n")
    wb.apply("checker.policy.add", {"id": "P", "checker_artifact": checker,
                                    "allowed_kernel_versions": ["4.99"],
                                    "allowed_library_revisions": ["d13f23b7"],
                                    "require_trusted": False}, "admin")
    claim = wb.store.get("claim", "c1")
    assert verification.toolchain_recheck_required(wb, claim) is True
    assert verification.effective(wb, claim) == "unverified"


def test_a_fresh_replay_closes_the_toolchain_recheck(wb):
    accepted(wb, "c1", environment={"lean_version": "Lean (version 4.34.1)",
                                    "mathlib_revision": "d13f23b7"})
    checker = artifact(wb, "policy-checker.py", "print('ok')\n")
    wb.apply("checker.policy.add", {"id": "P", "checker_artifact": checker,
                                    "allowed_kernel_versions": ["4.99"],
                                    "allowed_library_revisions": ["d13f23b7"],
                                    "require_trusted": False}, "admin")
    record = fresh_validation(wb, "c1", lean_version="Lean (version 4.99.0)")
    assert record["verification_round"] == "recheck"
    payload = wb.apply("verification.recheck", {"claim": "c1", "validation": record["id"]},
                       "coordinator")
    assert payload["status"] == "passed"
    claim = wb.store.get("claim", "c1")
    assert verification.toolchain_recheck_required(wb, claim) is False


def test_the_old_validation_is_not_a_recheck(wb):
    accepted(wb, "c1", environment={"lean_version": "Lean (version 4.34.1)",
                                    "mathlib_revision": "d13f23b7"})
    checker = artifact(wb, "policy-checker.py", "print('ok')\n")
    wb.apply("checker.policy.add", {"id": "P", "checker_artifact": checker,
                                    "allowed_kernel_versions": ["4.99"],
                                    "allowed_library_revisions": ["d13f23b7"],
                                    "require_trusted": False}, "admin")
    with pytest.raises(ResearchError, match="recheck round"):
        wb.apply("verification.recheck", {"claim": "c1", "validation": "c1-val"}, "coordinator")


def test_nothing_to_recheck_is_refused(wb):
    accepted(wb, "standalone")
    with pytest.raises(ResearchError, match="nothing to recheck"):
        wb.apply("verification.recheck", {"claim": "standalone",
                                          "validation": "standalone-val"}, "coordinator")


def test_a_revocation_recheck_is_recorded_but_does_not_clear_the_block(wb):
    accepted(wb, "c1")
    accepted(wb, "c2", dependencies=["c1"])
    wb.apply("claim.revoke", {"claim": "c1", "reason": "bad receipt"}, "custodian")
    record = fresh_validation(wb, "c2")
    payload = wb.apply("verification.recheck", {"claim": "c2", "validation": record["id"]},
                       "coordinator")
    assert payload["status"] == "passed"
    assert payload["triggers"] == ["c1"]
    assert "independent claim" in payload["note"]
    claim = wb.store.get("claim", "c2")
    assert verification.recheck_required(wb, claim) is True
    assert verification.effective(wb, claim) == "blocked"


def test_provenance_shows_the_recheck(wb):
    accepted(wb, "c1", environment={"lean_version": "Lean (version 4.34.1)",
                                    "mathlib_revision": "d13f23b7"})
    checker = artifact(wb, "policy-checker.py", "print('ok')\n")
    wb.apply("checker.policy.add", {"id": "P", "checker_artifact": checker,
                                    "allowed_kernel_versions": ["4.99"],
                                    "allowed_library_revisions": ["d13f23b7"],
                                    "require_trusted": False}, "admin")
    record = fresh_validation(wb, "c1", lean_version="Lean (version 4.99.0)")
    wb.apply("verification.recheck", {"claim": "c1", "validation": record["id"]},
             "coordinator")
    page = verification.provenance(wb, "c1")
    assert page["recheck"]["status"] == "passed"
    assert page["recheck"]["validation"] == record["id"]

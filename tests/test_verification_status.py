"""Batch 8.2: historical vs effective verification, recheck and revocation propagation."""
import json

import pytest

from ai4math import verification
from ai4math.health import snapshot
from ai4math.store import ResearchError
from ai4math.workbench import Workbench


@pytest.fixture
def wb(tmp_path):
    w = Workbench.create(tmp_path / "ws", "v82", "Verification status")
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


def accepted(w, claim_id, dependencies=None, node="goal"):
    digest = w.store.get("node", node)["statement_digest"]
    w.apply("claim.add", {"id": claim_id, "node": node, "statement_digest": digest,
                          "evidence_level": "proved", "scope": "universal", "summary": "proof",
                          "dependencies": dependencies or []}, "author")
    with w.store.transaction("fixture.accept_inputs", "test"):
        w.store.put("validation", claim_id + "-val",
                    {"id": claim_id + "-val", "claim": claim_id, "method": "formal_kernel",
                     "passed": True, "artifact_ids": [], "trust": "declared_external_checker",
                     "trust_basis": "fixture", "validator": "val",
                     "trust_class": "declared"}, new=True)
        w.store.put("review", claim_id + "-rev",
                    {"id": claim_id + "-rev", "claim": claim_id, "verdict": "ready_for_human",
                     "reviewer": "rev", "reviewer_kind": "model", "auditor": "aud",
                     "review_order": 1, "created_at": "2030-01-01T00:00:00+00:00",
                     "issues": [], "coverage_complete": True}, new=True)
    return w.apply("claim.accept", {"claim": claim_id, "review": claim_id + "-rev",
                                    "validation": claim_id + "-val"}, "custodian")


def test_a_declared_receipt_is_never_effective(wb):
    claim = accepted(wb, "c1")
    assert verification.effective(wb, claim) == "unverified"
    summary = verification.summary(wb)
    assert summary["effective"]["unverified"] == 1
    assert summary["legacy_unaudited"] == 1


def test_a_policy_trusted_validation_is_effective(wb):
    claim = accepted(wb, "c1")
    checker = artifact(wb, "checker.py", "print('ok')\n")
    wb.apply("checker.policy.add", {"id": "P", "checker_artifact": checker}, "admin")
    with wb.store.transaction("fixture.trust", "test"):
        validation = wb.store.get("validation", "c1-val")
        validation.update(trust_class="trusted", trust="trusted", checker_artifact=checker,
                          environment={"lean_version": "Lean 4.34.1", "mathlib_revision": "d13f23b7"})
        wb.store.put("validation", "c1-val", validation)
    assert verification.current_policy_trust(wb, wb.store.get("validation", "c1-val")) == "trusted"
    assert verification.effective(wb, claim) == "valid"


def test_a_new_toolchain_only_requires_a_recheck(wb):
    claim = accepted(wb, "c1")
    checker = artifact(wb, "checker.py", "print('ok')\n")
    wb.apply("checker.policy.add", {"id": "P", "checker_artifact": checker,
                                    "allowed_kernel_versions": ["4.99"], "require_trusted": False},
             "admin")
    with wb.store.transaction("fixture.env", "test"):
        validation = wb.store.get("validation", "c1-val")
        validation.update(environment={"lean_version": "Lean 4.34.1",
                                       "mathlib_revision": "d13f23b7"})
        wb.store.put("validation", "c1-val", validation)
    validation = wb.store.get("validation", "c1-val")
    assert verification.current_toolchain_recheck(wb, validation) == "recheck_required"
    # A toolchain change alone never invalidates the mathematics.
    assert verification.effective(wb, claim) != "invalid"


def test_a_revoked_dependency_blocks_its_downstream(wb):
    accepted(wb, "c1")
    child = accepted(wb, "c2", dependencies=["c1"])
    wb.apply("claim.revoke", {"claim": "c1", "reason": "the receipt was wrong"}, "custodian")
    assert verification.effective(wb, wb.store.get("claim", "c1")) == "invalid"
    assert verification.recheck_required(wb, child) is True
    assert verification.effective(wb, child) == "blocked"


def test_a_revocation_does_not_touch_an_unrelated_claim(wb):
    accepted(wb, "c1")
    unrelated = accepted(wb, "standalone")
    wb.apply("claim.revoke", {"claim": "c1", "reason": "wrong"}, "custodian")
    assert verification.recheck_required(wb, unrelated) is False
    assert verification.effective(wb, unrelated) == "unverified"


def test_provenance_lists_the_chain(wb):
    accepted(wb, "c1")
    page = verification.provenance(wb, "c1")
    assert page["claim"] == "c1"
    assert page["validations"][0]["policy_trust"] == "untrusted"
    assert page["effective"] == "unverified"
    assert page["historical"][0]["method"] == "formal_kernel"
    json.dumps(page)


def test_health_reports_the_effective_split(wb):
    accepted(wb, "c1")
    state = snapshot(wb)
    assert state["verification"]["accepted_claims"] == 1
    assert state["verification"]["effective"]["unverified"] == 1
    assert state["verification"]["recheck_required"] == 0

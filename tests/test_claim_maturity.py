"""Claim maturity: candidate at add time, verified only at acceptance (v3.5)."""
import pytest

from ai4math import maturity
from ai4math.store import ResearchError
from ai4math.workbench import Workbench


@pytest.fixture
def wb(tmp_path):
    w = Workbench.create(tmp_path / "ws", "maturity", "Maturity")
    w.apply("node.add", {"id": "goal", "statement": "Exact goal", "domain": "all n",
                         "quantifier": "universal"})
    w.apply("goal.set", {"node": "goal"})
    w.apply("strategy.configure", {"correspondence_after": 1000})
    w.apply("route.add", {"id": "route", "node": "goal", "rationale": "proof",
                          "next_question": "q", "stop_condition": "s",
                          "max_runs": 10, "stagnation_limit": 50})
    w.apply("route.status", {"id": "route", "status": "active", "reason": "test"})
    yield w
    w.store.close()


def claim(w, cid, **extra):
    payload = {"id": cid, "node": "goal",
               "statement_digest": w.store.get("node", "goal")["statement_digest"],
               "evidence_level": "proved", "scope": "universal", "summary": "s",
               "proof_route": "direct"}
    payload.update(extra)
    return w.apply("claim.add", payload, "author")


def accept(w, cid):
    with w.store.transaction("fixture", "test"):
        w.store.put("validation", cid + "-val",
                    {"id": cid + "-val", "claim": cid, "method": "formal_kernel",
                     "passed": True, "artifact_ids": [], "trust": "declared_external_checker",
                     "trust_basis": "fixture", "validator": "val"}, new=True)
        w.store.put("review", cid + "-rev",
                    {"id": cid + "-rev", "claim": cid, "verdict": "ready_for_human",
                     "reviewer": "rev", "reviewer_kind": "model", "auditor": "aud",
                     "review_order": 1, "created_at": "2030-01-01T00:00:00+00:00",
                     "issues": [], "coverage_complete": True}, new=True)
    return w.apply("claim.accept", {"claim": cid, "review": cid + "-rev",
                                    "validation": cid + "-val"}, "custodian")


def test_a_new_claim_is_a_proof_candidate(wb):
    row = claim(wb, "c1")
    assert row["maturity"] == "proof_candidate"
    assert row["contribution"] == "exploratory"
    assert row["proof_sketch"] == []


def test_acceptance_promotes_to_verified(wb):
    claim(wb, "c1")
    row = accept(wb, "c1")
    assert row["maturity"] == "verified"


def test_a_caller_cannot_set_maturity(wb):
    with pytest.raises(ResearchError, match="state machine writes claim maturity"):
        claim(wb, "c1", maturity="verified")


def test_contribution_is_recorded(wb):
    row = claim(wb, "c1", contribution="load_bearing")
    assert row["contribution"] == "load_bearing"
    assert maturity.contribution(row) == "load_bearing"
    with pytest.raises(ResearchError, match="contribution"):
        claim(wb, "c2", contribution="important")


def test_a_proof_sketch_must_be_frozen(wb):
    with pytest.raises(ResearchError, match="missing artifact"):
        claim(wb, "c1", proof_sketch=["0" * 64])
    path = wb.store.root / "sketch.md"
    path.write_text("sketch", encoding="utf-8")
    art = wb.apply("artifact.import", {"path": str(path), "purpose": "sketch"}, "author")["id"]
    row = claim(wb, "c2", proof_sketch=[art])
    assert row["proof_sketch"] == [art]


def test_legacy_claims_derive_their_maturity(wb):
    claim(wb, "c1")
    accept(wb, "c1")
    legacy = dict(wb.store.get("claim", "c1"))
    legacy.pop("maturity")
    validations = wb.store.all("validation")
    assert maturity.of_claim(legacy, validations) == "verified"
    pending = {"id": "x", "status": "proposed", "evidence_level": "proved"}
    assert maturity.of_claim(pending, validations) == "proof_candidate"


def test_the_maturity_view_counts_both_axes(wb):
    claim(wb, "c1", contribution="load_bearing")
    claim(wb, "c2")
    accept(wb, "c1")
    page = maturity.view(wb)
    assert page["claims"] == 2
    assert page["by_maturity"]["verified"] == 1
    assert page["by_maturity"]["proof_candidate"] == 1
    assert page["contributions"]["load_bearing"] == 1

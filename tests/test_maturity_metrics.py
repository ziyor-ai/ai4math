"""Maturity metrics, health exposure, budget profile and the drift signal (v3.5)."""
import pytest

from ai4math import metrics
from ai4math.health import panel, snapshot
from ai4math.workbench import Workbench


@pytest.fixture
def wb(tmp_path):
    w = Workbench.create(tmp_path / "ws", "maturity-metrics", "Maturity metrics")
    w.apply("node.add", {"id": "goal", "statement": "Exact goal", "domain": "all n",
                         "quantifier": "universal"})
    w.apply("goal.set", {"node": "goal"})
    w.apply("strategy.configure", {"correspondence_after": 1000})
    w.apply("route.add", {"id": "route", "node": "goal", "rationale": "proof",
                          "next_question": "q", "stop_condition": "s",
                          "max_runs": 20, "stagnation_limit": 50})
    w.apply("route.status", {"id": "route", "status": "active", "reason": "test"})
    path = w.store.root / "note.md"
    path.write_text("note", encoding="utf-8")
    w.apply("artifact.import", {"path": str(path), "purpose": "note"}, "author")
    yield w
    w.store.close()


def finding(w, fid):
    payload = {"id": fid, "node": "goal", "kind": "obstacle_identified",
               "statement": "a transfer lemma is missing", "scope": "fixed g",
               "evidence_kind": "theoretical_argument",
               "evidence_refs": [next(a["id"] for a in w.store.all("artifact"))],
               "relation": {"target": "goal", "type": "load_bearing"},
               "next_action": {"kind": "prove_transfer", "question": "is H bounded?"}}
    return w.apply("finding.add", payload, "author")


def claim(w):
    digest = w.store.get("node", "goal")["statement_digest"]
    w.apply("claim.add", {"id": "c1", "node": "goal", "statement_digest": digest,
                          "evidence_level": "proved", "scope": "universal", "summary": "s",
                          "proof_route": "direct", "contribution": "load_bearing"}, "author")
    with w.store.transaction("fixture", "test"):
        w.store.put("validation", "c1-val",
                    {"id": "c1-val", "claim": "c1", "method": "formal_kernel", "passed": True,
                     "artifact_ids": [], "trust": "declared_external_checker",
                     "trust_basis": "fixture", "validator": "val"}, new=True)
        w.store.put("review", "c1-rev",
                    {"id": "c1-rev", "claim": "c1", "verdict": "ready_for_human",
                     "reviewer": "rev", "reviewer_kind": "model", "auditor": "aud",
                     "review_order": 1, "created_at": "2030-01-01T00:00:00+00:00",
                     "issues": [], "coverage_complete": True}, new=True)
    w.apply("claim.accept", {"claim": "c1", "review": "c1-rev", "validation": "c1-val"},
            "custodian")


def test_the_profile_reports_the_lean_share(wb):
    assert metrics.profile(wb)["lean_budget_share"] == 0.3
    wb.apply("policy.configure", {"lean_budget_share": 0.25}, "admin")
    assert metrics.profile(wb)["lean_budget_share"] == 0.25


def test_the_lean_share_must_be_a_probability(wb):
    import pytest as _pytest
    from ai4math.store import ResearchError
    with _pytest.raises(ResearchError, match="lean_budget_share"):
        wb.apply("policy.configure", {"lean_budget_share": 1.5}, "admin")


def test_health_separates_findings_candidates_and_verified(wb):
    finding(wb, "F-1")
    claim(wb)
    state = snapshot(wb)
    assert state["maturity"]["claims"]["by_maturity"]["verified"] == 1
    assert state["maturity"]["findings"]["total"] == 1
    assert state["maturity"]["load_bearing_unverified"] == 1
    text = panel(state)
    assert "maturity: verified 1" in text


def test_five_load_bearing_findings_without_verification_raise_the_signal(wb):
    for index in range(5):
        finding(wb, f"F-{index}")
    state = snapshot(wb)
    assert state["maturity"]["unverified_findings_accumulating"] is True
    assert any(signal["code"] == "unverified_findings_accumulating" for signal in state["signals"])


def test_a_later_verified_claim_clears_the_drift_signal(wb):
    for index in range(5):
        finding(wb, f"F-{index}")
    claim(wb)
    state = snapshot(wb)
    assert state["maturity"]["unverified_findings_accumulating"] is False


def test_the_brief_and_map_carry_maturity(wb):
    finding(wb, "F-1")
    brief = __import__("ai4math.strategy", fromlist=["brief"]).brief(wb)
    assert brief["maturity"]["findings"]["total"] == 1
    page = __import__("ai4math.strategy", fromlist=["map_view"]).map_view(wb)
    assert page["maturity"]["claims"]["claims"] == 0

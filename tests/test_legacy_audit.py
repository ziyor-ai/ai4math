"""Legacy audits: append-only records of what a receipt audit found (v3.4 §16)."""
import pytest

from ai4math import legacy_audit, verification
from ai4math.store import ResearchError
from ai4math.workbench import Workbench


@pytest.fixture
def wb(tmp_path):
    w = Workbench.create(tmp_path / "ws", "legacy", "Legacy audit")
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


def accepted(w, claim_id="c1"):
    digest = w.store.get("node", "goal")["statement_digest"]
    w.apply("claim.add", {"id": claim_id, "node": "goal", "statement_digest": digest,
                          "evidence_level": "proved", "scope": "universal",
                          "summary": "proof"}, "author")
    with w.store.transaction("fixture.accept_inputs", "test"):
        w.store.put("validation", claim_id + "-val",
                    {"id": claim_id + "-val", "claim": claim_id, "method": "formal_kernel",
                     "passed": True, "artifact_ids": [], "trust": "declared_external_checker",
                     "trust_basis": "fixture", "validator": "val", "trust_class": "declared"},
                    new=True)
        w.store.put("review", claim_id + "-rev",
                    {"id": claim_id + "-rev", "claim": claim_id, "verdict": "ready_for_human",
                     "reviewer": "rev", "reviewer_kind": "model", "auditor": "aud",
                     "review_order": 1, "created_at": "2030-01-01T00:00:00+00:00",
                     "issues": [], "coverage_complete": True}, new=True)
    return w.apply("claim.accept", {"claim": claim_id, "review": claim_id + "-rev",
                                    "validation": claim_id + "-val"}, "custodian")


def audit(w, ident="AUD-1", **overrides):
    payload = {"id": ident, "claim": "c1", "audit_kind": "receipt_targets",
               "status": "unchecked",
               "findings": ["the receipt lists no checked targets"]}
    payload.update(overrides)
    return w.apply("legacy.audit", payload, "auditor")


def test_an_audit_is_recorded_without_touching_the_claim(wb):
    accepted(wb, "c1")
    row = audit(wb)
    claim = wb.store.get("claim", "c1")
    assert row["status"] == "unchecked"
    assert row["findings"] == ["the receipt lists no checked targets"]
    assert claim["status"] == "accepted"
    assert claim["evidence_level"] == "proved"


def test_an_audit_needs_an_accepted_claim(wb):
    digest = wb.store.get("node", "goal")["statement_digest"]
    wb.apply("claim.add", {"id": "draft", "node": "goal", "statement_digest": digest,
                           "evidence_level": "proved", "scope": "universal",
                           "summary": "s"}, "author")
    with pytest.raises(ResearchError, match="accepted claim"):
        audit(wb, claim="draft")


def test_audit_kind_and_status_are_closed_sets(wb):
    accepted(wb, "c1")
    with pytest.raises(ResearchError, match="audit_kind"):
        audit(wb, audit_kind="vibes")
    with pytest.raises(ResearchError, match="audit status"):
        audit(wb, status="maybe")


def test_an_audit_needs_findings(wb):
    accepted(wb, "c1")
    with pytest.raises(ResearchError, match="nonempty finding"):
        audit(wb, findings=[])


def test_audit_evidence_must_be_registered(wb):
    accepted(wb, "c1")
    with pytest.raises(ResearchError, match="not a registered object"):
        audit(wb, evidence_refs=["made-up"])


def test_audits_are_append_only(wb):
    accepted(wb, "c1")
    audit(wb, ident="AUD-1")
    with pytest.raises(ResearchError, match="already exists"):
        audit(wb, ident="AUD-1")
    audit(wb, ident="AUD-2", status="audited", findings=["targets match the claim"])
    assert len(legacy_audit.for_claim(wb, "c1")) == 2


def test_the_view_and_summary_count_audits(wb):
    accepted(wb, "c1")
    accepted(wb, "c2")
    audit(wb, ident="AUD-1")
    audit(wb, ident="AUD-2", claim="c2", status="audited", findings=["ok"])
    page = legacy_audit.view(wb)
    assert page["total"] == 2
    assert page["by_status"]["unchecked"] == 1
    assert page["by_status"]["audited"] == 1
    summary = verification.summary(wb)
    assert summary["legacy_audit"]["total"] == 2


def test_provenance_includes_the_audit(wb):
    accepted(wb, "c1")
    audit(wb, ident="AUD-1")
    page = verification.provenance(wb, "c1")
    assert [row["id"] for row in page["legacy_audit"]] == ["AUD-1"]

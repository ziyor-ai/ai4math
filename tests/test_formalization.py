"""Formalization requests: event-driven, budgeted, never a global gate (v3.5)."""
import pytest

from ai4math import formalization
from ai4math.store import ResearchError
from ai4math.workbench import Workbench


@pytest.fixture
def wb(tmp_path):
    w = Workbench.create(tmp_path / "ws", "formal", "Formalization")
    w.apply("node.add", {"id": "goal", "statement": "Exact goal", "domain": "all n",
                         "quantifier": "universal"})
    w.apply("goal.set", {"node": "goal"})
    w.apply("strategy.configure", {"correspondence_after": 1000})
    w.apply("route.add", {"id": "route", "node": "goal", "rationale": "proof",
                          "next_question": "q", "stop_condition": "s",
                          "max_runs": 10, "stagnation_limit": 50})
    w.apply("route.status", {"id": "route", "status": "active", "reason": "test"})
    path = w.store.root / "sketch.md"
    path.write_text("sketch", encoding="utf-8")
    w.store.put if False else None
    w.apply("artifact.import", {"path": str(path), "purpose": "sketch"}, "author")
    yield w
    w.store.close()


def claim(w, cid="c1", **extra):
    payload = {"id": cid, "node": "goal",
               "statement_digest": w.store.get("node", "goal")["statement_digest"],
               "evidence_level": "proved", "scope": "universal", "summary": "s",
               "proof_route": "direct"}
    payload.update(extra)
    return w.apply("claim.add", payload, "author")


def test_a_proof_sketch_opens_a_request(wb):
    art = next(a["id"] for a in wb.store.all("artifact"))
    claim(wb, "c1", proof_sketch=[art])
    rows = wb.store.all("formalization_request")
    assert len(rows) == 1
    assert rows[0]["claim"] == "c1"
    assert rows[0]["trigger"] == "proof_sketch_ready"
    assert rows[0]["status"] == "pending"


def test_a_claim_without_a_sketch_opens_nothing(wb):
    claim(wb, "c1")
    assert wb.store.all("formalization_request") == []


def test_requests_are_idempotent_while_pending(wb):
    claim(wb, "c1")
    first = wb.apply("formalization.request", {"claim": "c1", "reason": "ready"}, "author")
    second = wb.apply("formalization.request", {"claim": "c1", "reason": "again"}, "author")
    assert first["id"] == second["id"]


def test_the_budget_is_finite(wb):
    wb.apply("policy.configure", {"formalization_budget": {"attempts": 1}}, "admin")
    claim(wb, "c1")
    first = wb.apply("formalization.request", {"claim": "c1", "reason": "one try"}, "author")
    wb.apply("formalization.skip", {"request": first["id"], "reason": "not now"}, "author")
    with pytest.raises(ResearchError, match="budget is exhausted"):
        wb.apply("formalization.request", {"claim": "c1", "reason": "again"}, "author")


def test_resolve_requires_a_passing_validation(wb):
    claim(wb, "c1")
    row = wb.apply("formalization.request", {"claim": "c1", "reason": "ready"}, "author")
    with pytest.raises(ResearchError, match="no passing validation"):
        wb.apply("formalization.resolve", {"request": row["id"]}, "coordinator")
    with wb.store.transaction("fixture", "test"):
        wb.store.put("validation", "c1-val",
                     {"id": "c1-val", "claim": "c1", "method": "formal_kernel", "passed": True,
                      "artifact_ids": [], "trust": "declared_external_checker",
                      "trust_basis": "fixture", "validator": "val"}, new=True)
    done = wb.apply("formalization.resolve", {"request": row["id"]}, "coordinator")
    assert done["status"] == "consumed"
    assert done["resolution"]["kind"] == "validated"
    assert done["resolution"]["validation_ref"] == "c1-val"


def test_skip_needs_a_reason_and_frozen_evidence(wb):
    claim(wb, "c1")
    row = wb.apply("formalization.request", {"claim": "c1", "reason": "ready"}, "author")
    with pytest.raises(ResearchError, match="needs a reason"):
        wb.apply("formalization.skip", {"request": row["id"], "reason": ""}, "author")
    with pytest.raises(ResearchError, match="not a frozen, registered object"):
        wb.apply("formalization.skip", {"request": row["id"], "reason": "blocked",
                                        "evidence_ref": "made-up"}, "author")
    art = next(a["id"] for a in wb.store.all("artifact"))
    done = wb.apply("formalization.skip", {"request": row["id"], "reason": "library gap",
                                           "evidence_ref": art}, "author")
    assert done["resolution"]["kind"] == "skipped"


def test_revoking_the_claim_makes_the_request_stale(wb):
    claim(wb, "c1")
    row = wb.apply("formalization.request", {"claim": "c1", "reason": "ready"}, "author")
    wb.apply("claim.revoke", {"claim": "c1", "reason": "wrong statement"}, "custodian")
    assert wb.store.get("formalization_request", row["id"])["status"] == "stale"


def test_requests_do_not_block_dispatch(wb):
    claim(wb, "c1")
    wb.apply("formalization.request", {"claim": "c1", "reason": "ready"}, "author")
    wb.apply("task.add", {"id": "t1", "route": "route", "role": "explore",
                          "argv": ["python", "-c", "print(1)"],
                          "timeout_seconds": 30, "output_limit_bytes": 1000}, "runner")


def test_the_view_counts_pending_and_validated(wb):
    claim(wb, "c1")
    row = wb.apply("formalization.request", {"claim": "c1", "reason": "ready"}, "author")
    assert formalization.view(wb)["pending"] == [row["id"]]

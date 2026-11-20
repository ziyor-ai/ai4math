"""§8.1/§8.2 readings: closure and effort, exposed through health (P2)."""
import pytest

from ai4math import metrics
from ai4math.health import snapshot
from ai4math.workbench import Workbench


@pytest.fixture
def wb(tmp_path):
    w = Workbench.create(tmp_path / "ws", "closure", "Closure metrics")
    w.apply("node.add", {"id": "child", "statement": "Child", "domain": "all n",
                         "quantifier": "universal"})
    w.apply("node.add", {"id": "goal", "statement": "Goal", "domain": "all n",
                         "quantifier": "universal"})
    w.apply("node.plan", {"node": "goal", "proof_dependencies": ["child"], "reason": "plan"})
    w.apply("goal.set", {"node": "goal"})
    w.apply("strategy.configure", {"correspondence_after": 1000})
    w.apply("route.add", {"id": "r-goal", "node": "goal", "rationale": "p",
                          "next_question": "q", "stop_condition": "s",
                          "max_runs": 10, "stagnation_limit": 20})
    w.apply("route.add", {"id": "r-child", "node": "child", "rationale": "p",
                          "next_question": "q", "stop_condition": "s",
                          "max_runs": 10, "stagnation_limit": 20})
    yield w
    w.store.close()


def accept(w, claim_id, node="goal", proof_route="composition", **extra):
    digest = w.store.get("node", node)["statement_digest"]
    payload = {"id": claim_id, "node": node, "statement_digest": digest,
               "evidence_level": "proved", "scope": "universal", "summary": "s",
               "proof_route": proof_route}
    payload.update(extra)
    w.apply("claim.add", payload, "author")
    with w.store.transaction("fixture.accept", "test"):
        w.store.put("validation", claim_id + "-val",
                    {"id": claim_id + "-val", "claim": claim_id, "method": "formal_kernel",
                     "passed": True, "artifact_ids": [], "trust": "declared_external_checker",
                     "trust_basis": "fixture", "validator": "val", "trust_class": "declared",
                     "exact_goal_type_digest": "d" * 32}, new=True)
        w.store.put("review", claim_id + "-rev",
                    {"id": claim_id + "-rev", "claim": claim_id, "verdict": "ready_for_human",
                     "reviewer": "rev", "reviewer_kind": "model", "auditor": "aud",
                     "review_order": 1, "created_at": "2030-01-01T00:00:00+00:00",
                     "issues": [], "coverage_complete": True}, new=True)
    return w.apply("claim.accept", {"claim": claim_id, "review": claim_id + "-rev",
                                    "validation": claim_id + "-val"}, "custodian")


def test_closure_counts_direct_and_composition_apart(wb):
    accept(wb, "c-child", node="child", proof_route="direct")
    accept(wb, "c-goal", node="goal", proof_route="composition", dependencies=["c-child"])
    reading = metrics.closure(wb)
    assert reading["parents"] == 1
    assert reading["declared_kernel_pass_with_targets"] == 2
    assert reading["declared_kernel_pass_without_targets"] == 0
    assert reading["direct_formal_parent_proved_structured"] == 0
    assert reading["new_composition_accept_without_verified_entailment"] == 1
    assert reading["merge_ready_structured"] == 0


def test_a_verified_composition_makes_the_parent_merge_ready(wb):
    accept(wb, "c-child", node="child", proof_route="direct")
    accept(wb, "c-goal", node="goal", proof_route="composition", dependencies=["c-child"])
    with wb.store.transaction("fixture.inference", "test"):
        wb.store.put("inference", "INF-1",
                     {"id": "INF-1", "parent": "goal", "rule": "composition",
                      "branch_proofs": {"child": "c-child"},
                      "verification": {"status": "verified"}}, new=True)
    reading = metrics.closure(wb)
    assert reading["merge_attempted_structured"] == 1
    assert reading["merge_succeeded_structured"] == 1
    assert reading["new_composition_accept_without_verified_entailment"] == 0


def test_effort_reads_depth_and_root_side_funding(wb):
    wb.apply("route.status", {"id": "r-child", "status": "active", "reason": "test"})
    wb.apply("task.add", {"id": "t1", "route": "r-child", "role": "explore",
                          "argv": ["python", "-c", "print(1)"],
                          "timeout_seconds": 30, "output_limit_bytes": 1000}, "runner")
    accept(wb, "c-child", node="child", proof_route="direct")
    accept(wb, "c-goal", node="goal", proof_route="composition", dependencies=["c-child"])
    reading = metrics.effort(wb)
    assert reading["effort_by_depth"] == {"0": 1, "1": 1}
    assert reading["debt_on_cone"] == 0
    assert reading["root_side_funding"] > 0
    assert reading["effective_unverified"] == 2


def test_health_carries_closure_and_effort(wb):
    accept(wb, "c-child", node="child", proof_route="direct")
    state = snapshot(wb)
    assert state["closure"]["parents"] == 1
    assert "effort_by_depth" in state["effort"]

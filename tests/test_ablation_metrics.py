"""Batch 8.4: lifecycle proxies, run profile and ablation switches."""
import sys

import pytest

from ai4math import ablation, metrics
from ai4math.health import snapshot
from ai4math.store import ResearchError
from ai4math.workbench import Workbench


@pytest.fixture
def wb(tmp_path):
    w = Workbench.create(tmp_path / "ws", "v84", "Metrics")
    w.apply("node.add", {"id": "goal", "statement": "Exact goal", "domain": "all n",
                         "quantifier": "universal"})
    w.apply("goal.set", {"node": "goal"})
    w.apply("strategy.configure", {"correspondence_after": 1000, "critique_interval": 8})
    w.apply("route.add", {"id": "route", "node": "goal", "rationale": "proof",
                          "next_question": "q", "stop_condition": "s",
                          "max_runs": 50, "stagnation_limit": 50})
    w.apply("route.status", {"id": "route", "status": "active", "reason": "test"})
    yield w
    w.store.close()


def accepted(w, cid, node="goal"):
    digest = w.store.get("node", node)["statement_digest"]
    w.apply("claim.add", {"id": cid, "node": node, "statement_digest": digest,
                          "evidence_level": "proved", "scope": "universal", "summary": "s"},
            "author")
    with w.store.transaction("fixture.accept", "test"):
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


def test_lifecycle_rates_are_observable_proxies(wb):
    accepted(wb, "c1")
    accepted(wb, "c2")
    wb.apply("claim.revoke", {"claim": "c2", "reason": "the receipt was wrong"}, "custodian")
    reading = metrics.lifecycle(wb)
    assert reading["ever_accepted"] == 2
    assert reading["revoked"] == 1
    assert reading["later_overturned_rate"] == 0.5
    assert reading["later_upheld_rate"] == 0.5


def test_the_profile_labels_the_run(wb):
    page = metrics.profile(wb)
    assert page["workspace_policy_version"] == "v2c0"
    assert page["critique_interval"] == 8
    assert page["ablation"] == {"disable": []}


def test_an_unknown_ablation_module_is_refused(wb):
    with pytest.raises(ResearchError, match="unknown ablation module"):
        wb.apply("ablation.configure", {"disable": ["magic"]}, "admin")


def test_an_ablation_turns_off_the_inference_gate(wb):
    for child in ("b1", "b2"):
        wb.apply("node.add", {"id": child, "statement": "Branch " + child, "domain": "all n",
                              "quantifier": "universal"})
    wb.apply("node.plan", {"node": "goal", "proof_dependencies": ["b1", "b2"], "reason": "split"})
    accepted(wb, "c1", node="b1")
    accepted(wb, "c2", node="b2")
    wb.apply("ablation.configure", {"disable": ["inference"]}, "admin")
    assert "inference" in ablation.state(wb)["disable"]
    # With the gate ablated the acceptance must not demand a recorded inference.
    digest = wb.store.get("node", "goal")["statement_digest"]
    wb.apply("claim.add", {"id": "cg", "node": "goal", "statement_digest": digest,
                           "evidence_level": "proved", "scope": "universal", "summary": "s",
                           "dependencies": ["c1", "c2"]}, "author")
    with wb.store.transaction("fixture.accept", "test"):
        wb.store.put("validation", "cg-val",
                    {"id": "cg-val", "claim": "cg", "method": "formal_kernel",
                     "passed": True, "artifact_ids": [], "trust": "declared_external_checker",
                     "trust_basis": "fixture", "validator": "val"}, new=True)
        wb.store.put("review", "cg-rev",
                    {"id": "cg-rev", "claim": "cg", "verdict": "ready_for_human",
                     "reviewer": "rev", "reviewer_kind": "model", "auditor": "aud",
                     "review_order": 1, "created_at": "2030-01-01T00:00:00+00:00",
                     "issues": [], "coverage_complete": True}, new=True)
    row = wb.apply("claim.accept", {"claim": "cg", "review": "cg-rev", "validation": "cg-val"},
                   "custodian")
    assert row["status"] == "accepted"


def test_an_ablation_turns_off_the_intent_gate(wb):
    wb.apply("policy.migrate", {"to": "v2c1"}, "admin")
    assert wb.apply("ablation.configure", {"disable": ["intent"]}, "admin")
    wb.apply("task.add", {"id": "t1", "route": "route", "role": "explore",
                          "argv": [sys.executable, "-c", "print(1)"],
                          "timeout_seconds": 30, "output_limit_bytes": 10000}, "runner")


def test_an_ablation_turns_off_generation_blocking(wb):
    wb.apply("generation.request", {"id": "REQ-1", "route": "route", "parent": "goal",
                                    "trigger_event_id": "x", "trigger_reason": "no_progress_drought"},
             "coordinator")
    wb.apply("ablation.configure", {"disable": ["generation"]}, "admin")
    wb.apply("task.add", {"id": "t1", "route": "route", "role": "explore",
                          "argv": [sys.executable, "-c", "print(1)"],
                          "timeout_seconds": 30, "output_limit_bytes": 10000}, "runner")
    from ai4math.execution import run_task
    assert run_task(wb, "t1")["status"] == "completed"


def test_health_carries_the_profile_and_lifecycle(wb):
    accepted(wb, "c1")
    state = snapshot(wb)
    assert state["profile"]["critique_interval"] == 8
    assert state["lifecycle"]["ever_accepted"] == 1
    assert state["lifecycle"]["later_overturned_rate"] == 0.0

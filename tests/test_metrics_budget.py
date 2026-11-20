"""Batch 8.0 slice 9: object revisions, budgets, discoveries and entailment metrics."""
import json
import sys

import pytest

from ai4math import discovery, metrics, scope
from ai4math.health import snapshot
from ai4math.store import ResearchError
from ai4math.workbench import Workbench


@pytest.fixture
def wb(tmp_path):
    w = Workbench.create(tmp_path / "ws", "slice9", "Slice nine")
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


def accepted_claim(w, node, claim_id, dependencies=None):
    digest = w.store.get("node", node)["statement_digest"]
    w.apply("claim.add", {"id": claim_id, "node": node, "statement_digest": digest,
                          "evidence_level": "proved", "scope": "universal", "summary": "proof",
                          "dependencies": dependencies or []}, "author")
    with w.store.transaction("fixture.accept_inputs", "test"):
        w.store.put("validation", claim_id + "-val",
                    {"id": claim_id + "-val", "claim": claim_id, "method": "formal_kernel",
                     "passed": True, "artifact_ids": [], "trust": "declared_external_checker",
                     "trust_basis": "fixture", "validator": "val"}, new=True)
        w.store.put("review", claim_id + "-rev",
                    {"id": claim_id + "-rev", "claim": claim_id, "verdict": "ready_for_human",
                     "reviewer": "rev", "auditor": "aud", "review_order": 1,
                     "created_at": "2030-01-01T00:00:00+00:00", "issues": [],
                     "coverage_complete": True}, new=True)
    return w.apply("claim.accept", {"claim": claim_id, "review": claim_id + "-rev",
                                    "validation": claim_id + "-val"}, "custodian")


def test_revisions_advance_with_each_write(wb):
    first = wb.store.revision("node", "goal")
    assert first > 0
    wb.apply("node.add", {"id": "solo", "statement": "Solo", "domain": "all n",
                          "quantifier": "universal"})
    solo = wb.store.revision("node", "solo")
    assert solo > first
    wb.apply("node.plan", {"node": "goal", "proof_dependencies": ["solo"], "reason": "plan"})
    assert wb.store.revision("node", "goal") > first
    assert wb.store.revision("node", "absent") == 0


def test_accepted_claims_record_the_event_they_followed(wb):
    claim = accepted_claim(wb, "goal", "c1")
    assert isinstance(claim["accepted_after_seq"], int)
    assert claim["accepted_after_seq"] > 0


def test_entailment_metrics_separate_metadata_from_verified_structure(wb):
    for child in ("b1", "b2"):
        wb.apply("node.add", {"id": child, "statement": "Branch " + child, "domain": "all n",
                              "quantifier": "universal"})
    wb.apply("node.plan", {"node": "goal", "proof_dependencies": ["b1", "b2"], "reason": "split"})
    accepted_claim(wb, "b1", "c1")
    accepted_claim(wb, "b2", "c2")
    accepted_claim(wb, "goal", "cg", dependencies=["c1", "c2"])
    reading = metrics.entailment(wb)
    assert reading["parents"] == 1
    assert reading["children_closed_recorded"] == 1
    assert reading["merge_ready_structured"] == 0
    assert reading["accepted_without_structured_entailment"] == 1
    assert reading["accepted_without_structured_entailment_nodes"] == ["goal"]


def test_a_verified_inference_makes_the_parent_merge_ready(wb):
    for child in ("b1", "b2"):
        wb.apply("node.add", {"id": child, "statement": "Branch " + child, "domain": "all n",
                              "quantifier": "universal"})
    wb.apply("node.plan", {"node": "goal", "proof_dependencies": ["b1", "b2"], "reason": "split"})
    accepted_claim(wb, "b1", "c1")
    accepted_claim(wb, "b2", "c2")
    certificate = artifact(wb, "cert.json", "{}")
    wb.apply("inference.add", {"id": "INF-1", "rule": "case_split", "parent": "goal",
                               "required_branches": ["b1", "b2"],
                               "branch_proofs": {"b1": "c1", "b2": "c2"},
                               "verification": {"basis": "formal_kernel",
                                                "certificate_ref": certificate,
                                                "environment_digest": "env"}}, "author")
    reading = metrics.entailment(wb)
    assert reading["inferences_verified"] == 1
    assert reading["merge_ready_structured"] == 1
    assert reading["merge_succeeded_structured"] == 0      # no parent claim yet
    accepted_claim(wb, "goal", "cg", dependencies=["c1", "c2"])
    assert metrics.entailment(wb)["merge_succeeded_structured"] == 1


def test_discovery_requires_a_registered_artifact_and_kind(wb):
    with pytest.raises(ResearchError, match="discovery_kind"):
        wb.apply("discovery.record", {"id": "D1", "node": "goal",
                                      "discovery_kind": "counterexample_candidate",
                                      "artifact_ref": "0" * 64, "finding_kind": "counterexample",
                                      "evidence_location": {"file": "x"}}, "author")


def test_discovery_records_an_artifact_location(wb):
    art = artifact(wb, "note.txt", "a new invariant")
    row = wb.apply("discovery.record", {"id": "D1", "node": "goal",
                                        "discovery_kind": "new_structure_found",
                                        "artifact_ref": art, "finding_kind": "structure",
                                        "evidence_location": {"file": "note.txt", "offset": 3}},
                   "author")
    assert row["artifact_ref"] == art
    assert discovery.count(wb) == 1
    assert snapshot(wb)["totals"]["discoveries"] == 1


def test_exploration_rounds_are_counted_and_capped(wb):
    wb.apply("node.add", {"id": "h2", "statement": "Candidate", "domain": "all n",
                          "quantifier": "universal"})
    wb.apply("research.register", {"node": "h2", "rationale": "candidate"})
    wb.apply("policy.configure", {"exploration_budget": {"rounds": 1}}, "admin")
    route = wb.apply("route.add", {"id": "x1", "node": "h2", "kind": "exploration",
                                   "rationale": "bridge", "next_question": "q",
                                   "stop_condition": "s", "max_runs": 3, "stagnation_limit": 2})
    wb.apply("route.status", {"id": "x1", "status": "active", "reason": "test"})
    wb.apply("round.open", {"id": "r1", "route": "x1"})
    wb.apply("task.add", {"id": "t1", "route": "x1", "role": "explore", "round": "r1",
                          "argv": [sys.executable, "-c", "print(1)"],
                          "timeout_seconds": 30, "output_limit_bytes": 10000}, "runner")
    from ai4math.execution import run_task
    assert run_task(wb, "t1")["status"] == "completed"
    wb.apply("round.assess", {"round": "r1", "finding": "probe done", "next_question": "next",
                              "task_assessments": [{"task_id": "t1",
                                                    "execution_status": "completed",
                                                    "scientific_outcome": "inconclusive"}]}, "coordinator")
    wb.apply("round.settle", {"round": "r1"}, "coordinator")
    view = scope.exploration(wb)
    assert view["rounds_settled"] == 1
    assert view["rounds_limit"] == 1
    with pytest.raises(ResearchError, match="round budget is exhausted"):
        wb.apply("route.add", {"id": "x2", "node": "h2", "kind": "exploration",
                               "rationale": "second", "next_question": "q",
                               "stop_condition": "s", "max_runs": 3, "stagnation_limit": 2})

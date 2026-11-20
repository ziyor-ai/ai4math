"""Batch 8.1: approaches, hypotheses, obstacles and research intents."""
import sys

import pytest

from ai4math import approach as approach_module
from ai4math.store import ResearchError
from ai4math.workbench import Workbench


@pytest.fixture
def wb(tmp_path):
    w = Workbench.create(tmp_path / "ws", "81", "Batch 8.1 objects")
    w.apply("node.add", {"id": "goal", "statement": "Exact goal", "domain": "all n",
                         "quantifier": "universal"})
    w.apply("goal.set", {"node": "goal"})
    w.apply("strategy.configure", {"correspondence_after": 1000})
    w.apply("node.add", {"id": "h2", "statement": "Candidate representation", "domain": "all n",
                         "quantifier": "universal"})
    w.apply("research.register", {"node": "h2", "rationale": "candidate"})
    w.apply("route.add", {"id": "route", "node": "goal", "rationale": "proof",
                          "next_question": "q", "stop_condition": "s",
                          "max_runs": 50, "stagnation_limit": 50})
    w.apply("route.status", {"id": "route", "status": "active", "reason": "test"})
    yield w
    w.store.close()


def obstacle(w, oid="O-1", kind="unproved_dependency"):
    return w.apply("obstacle.add", {"id": oid, "node": "h2", "description": "a blocker",
                                    "kind": kind}, "author")


def hypothesis(w, hid="H-1", **extra):
    payload = {"id": hid, "statement": "H: a weak bound holds", "scope": "fixed g, fixed n"}
    payload.update(extra)
    return w.apply("research_hypothesis.add", payload, "author")


def candidate(w, sid="S-1", **extra):
    payload = {"id": sid, "node": "h2", "approach": "build the bridge directly",
               "family": "geometric-supply",
               "bridge_to_goal": {"mode": "entailment", "statement": "H2 closes the gap"}}
    payload.update(extra)
    return w.apply("approach.add", payload, "author")


def approve(w, sid="S-1", verdict="selected"):
    return w.apply("approach.review", {"id": sid, "verdict": verdict, "reason": "worth one probe"},
                   "reviewer")


def intent(w, iid="I-1", sid="S-1", **extra):
    payload = {"id": iid, "approach": sid, "kind": "exploration",
               "question": "does the bridge hold?",
               "connection_to_goal": "if H2 holds then goal is reachable",
               "relation": "research_relevance", "decisive": True,
               "predicted": [{"observe": "counterexample", "decision": "refute_hypothesis"},
                             {"observe": "no counterexample", "decision": "record_bounded_negative_search"}],
               "stop_condition": "two rounds without a witness"}
    payload.update(extra)
    return w.apply("research_intent.add", payload, "coordinator")


def test_an_obstacle_is_verified_only_with_frozen_evidence(wb):
    obstacle(wb, "O-1", "mathematical_refutation")
    with pytest.raises(ResearchError, match="nonempty evidence"):
        wb.apply("obstacle.verify", {"id": "O-1", "evidence": ""}, "verifier")
    path = wb.store.root / "e.txt"
    path.write_text("witness", encoding="utf-8")
    artifact = wb.apply("artifact.import", {"path": str(path), "purpose": "witness"})["id"]
    row = wb.apply("obstacle.verify", {"id": "O-1", "evidence": artifact}, "verifier")
    assert row["verification_status"] == "verified"


def test_only_a_verified_refutation_can_be_marked_refuted(wb):
    obstacle(wb, "O-1", "unproved_dependency")
    path = wb.store.root / "e.txt"
    path.write_text("witness", encoding="utf-8")
    artifact = wb.apply("artifact.import", {"path": str(path), "purpose": "witness"})["id"]
    wb.apply("obstacle.verify", {"id": "O-1", "evidence": artifact}, "verifier")
    with pytest.raises(ResearchError, match="verified mathematical refutation"):
        wb.apply("obstacle.status", {"id": "O-1", "status": "refuted"}, "author")


def test_a_hypothesis_refuted_needs_a_verified_refutation_or_a_disproof(wb):
    hypothesis(wb)
    obstacle(wb, "O-1", "unproved_dependency")
    with pytest.raises(ResearchError, match="verified mathematical_refutation"):
        wb.apply("hypothesis.refute", {"id": "H-1", "obstacle": "O-1"}, "author")


def test_a_verified_refutation_refutes_the_hypothesis(wb):
    hypothesis(wb)
    obstacle(wb, "O-1", "mathematical_refutation")
    path = wb.store.root / "e.txt"
    path.write_text("witness", encoding="utf-8")
    artifact = wb.apply("artifact.import", {"path": str(path), "purpose": "witness"})["id"]
    wb.apply("obstacle.verify", {"id": "O-1", "evidence": artifact}, "verifier")
    row = wb.apply("hypothesis.refute", {"id": "H-1", "obstacle": "O-1"}, "author")
    assert row["status"] == "refuted"
    assert row["refuting"] == ["O-1"]


def test_an_approach_starts_as_a_candidate_and_cannot_be_dispatched(wb):
    candidate(wb)
    with pytest.raises(ResearchError, match="exploring approach"):
        wb.apply("route.add", {"id": "x1", "node": "h2", "kind": "exploration",
                               "rationale": "bridge", "next_question": "q",
                               "stop_condition": "s", "max_runs": 3, "stagnation_limit": 2})


def test_review_then_activation_unlocks_an_exploration_route(wb):
    row = candidate(wb)
    assert row["status"] == "candidate"
    assert approve(wb)["status"] == "selected"
    assert wb.apply("approach.activate", {"id": "S-1"}, "coordinator")["status"] == "exploring"
    route = wb.apply("route.add", {"id": "x1", "node": "h2", "kind": "exploration",
                                   "rationale": "bridge", "next_question": "q",
                                   "stop_condition": "s", "max_runs": 3, "stagnation_limit": 2})
    assert route["kind"] == "exploration"


def test_a_rejected_approach_must_say_what_it_differs_from(wb):
    candidate(wb)
    with pytest.raises(ResearchError, match="differs"):
        approve(wb, verdict="rejected")


def test_retiring_needs_a_reopen_condition(wb):
    candidate(wb)
    approve(wb)
    wb.apply("approach.activate", {"id": "S-1"}, "coordinator")
    with pytest.raises(ResearchError, match="reopen_condition"):
        wb.apply("approach.status", {"id": "S-1", "status": "retired"}, "coordinator")
    row = wb.apply("approach.status", {"id": "S-1", "status": "retired",
                                       "reopen_condition": "if a second source is measured"}, "coordinator")
    assert row["status"] == "retired"


def test_an_approach_is_refuted_only_through_a_refuted_hypothesis(wb):
    hypothesis(wb)
    candidate(wb, hypotheses=["H-1"])
    approve(wb)
    wb.apply("approach.activate", {"id": "S-1"}, "coordinator")
    with pytest.raises(ResearchError, match="refuted hypothesis"):
        wb.apply("approach.status", {"id": "S-1", "status": "refuted"}, "coordinator")


def test_a_retired_approach_leaves_the_active_set(wb):
    candidate(wb)
    approve(wb)
    wb.apply("approach.activate", {"id": "S-1"}, "coordinator")
    wb.apply("approach.status", {"id": "S-1", "status": "retired",
                                 "reopen_condition": "new source"}, "coordinator")
    assert approach_module.view(wb)["exploring"] == []


def test_a_task_intent_must_be_decisive_with_two_predictions(wb):
    candidate(wb)
    approve(wb)
    wb.apply("approach.activate", {"id": "S-1"}, "coordinator")
    with pytest.raises(ResearchError, match="two different predicted decisions"):
        intent(wb, predicted=[{"observe": "counterexample", "decision": "refute_hypothesis"}])


def test_connection_to_goal_must_name_a_registered_node(wb):
    candidate(wb)
    approve(wb)
    wb.apply("approach.activate", {"id": "S-1"}, "coordinator")
    with pytest.raises(ResearchError, match="registered node id"):
        intent(wb, connection_to_goal="this helps somehow")


def test_an_intent_is_consumed_by_exactly_one_task(wb):
    candidate(wb)
    approve(wb)
    wb.apply("approach.activate", {"id": "S-1"}, "coordinator")
    intent(wb)
    wb.apply("task.add", {"id": "t1", "route": "route", "role": "explore", "intent": "I-1",
                          "argv": [sys.executable, "-c", "print(1)"],
                          "timeout_seconds": 30, "output_limit_bytes": 10000}, "runner")
    with pytest.raises(ResearchError, match="already used"):
        wb.apply("task.add", {"id": "t2", "route": "route", "role": "explore", "intent": "I-1",
                              "argv": [sys.executable, "-c", "print(1)"],
                              "timeout_seconds": 30, "output_limit_bytes": 10000}, "runner")


def test_a_policy_workspace_demands_an_intent(wb):
    wb.apply("checker.policy.add", {"id": "P",
                                    "checker_artifact": wb.apply(
                                        "artifact.import",
                                        {"path": _file(wb, "c.py", "print(1)\n"),
                                         "purpose": "checker"})["id"]}, "admin")
    wb.apply("policy.migrate", {"to": "v2c1"}, "admin")
    with pytest.raises(ResearchError, match="requires a research_intent"):
        wb.apply("task.add", {"id": "t1", "route": "route", "role": "explore",
                              "argv": [sys.executable, "-c", "print(1)"],
                              "timeout_seconds": 30, "output_limit_bytes": 10000}, "runner")


def test_a_task_records_the_read_set_and_flags_a_changed_basis(wb, monkeypatch):
    hypothesis(wb)
    candidate(wb, hypotheses=["H-1"])
    approve(wb)
    wb.apply("approach.activate", {"id": "S-1"}, "coordinator")
    intent(wb, hypothesis="H-1")
    task = wb.apply("task.add", {"id": "t1", "route": "route", "role": "explore", "intent": "I-1",
                                 "argv": [sys.executable, "-c", "print(1)"],
                                 "timeout_seconds": 30, "output_limit_bytes": 10000}, "runner")
    assert task["basis_read_set"]["approach"]["id"] == "S-1"
    wb.apply("hypothesis.support", {"id": "H-1", "claim": _accepted_claim(wb)}, "author")
    assert wb._stale_basis(wb.store.get("task", "t1")) == ["hypothesis:H-1"]


def _file(w, name, text):
    path = w.store.root / name
    path.write_text(text, encoding="utf-8")
    return str(path)


def _accepted_claim(w):
    digest = w.store.get("node", "goal")["statement_digest"]
    w.apply("claim.add", {"id": "c-support", "node": "goal", "statement_digest": digest,
                          "evidence_level": "proved", "scope": "universal", "summary": "s"},
            "author")
    with w.store.transaction("fixture.accept", "test"):
        w.store.put("validation", "c-support-val",
                    {"id": "c-support-val", "claim": "c-support", "method": "formal_kernel",
                     "passed": True, "artifact_ids": [], "trust": "declared_external_checker",
                     "trust_basis": "fixture", "validator": "val"}, new=True)
        w.store.put("review", "c-support-rev",
                    {"id": "c-support-rev", "claim": "c-support", "verdict": "ready_for_human",
                     "reviewer": "rev", "reviewer_kind": "model", "auditor": "aud",
                     "review_order": 1,
                     "created_at": "2030-01-01T00:00:00+00:00", "issues": [],
                     "coverage_complete": True}, new=True)
    w.apply("claim.accept", {"claim": "c-support", "review": "c-support-rev",
                             "validation": "c-support-val"}, "custodian")
    return "c-support"

"""Batch 8.0 slice 4: one node classifier for proof obligations and exploration."""
import sys

import pytest

from ai4math import scope
from ai4math.execution import run_task
from ai4math.health import snapshot
from ai4math.store import ResearchError
from ai4math.workbench import Workbench
from test_strategy import decide


@pytest.fixture
def wb(tmp_path):
    w = Workbench.create(tmp_path / "ws", "scope", "Node scope")
    w.apply("node.add", {"id": "goal", "statement": "Exact goal", "domain": "all n",
                         "quantifier": "universal"})
    w.apply("goal.set", {"node": "goal"})
    w.apply("strategy.configure", {"correspondence_after": 1000})
    w.apply("node.add", {"id": "lemma", "statement": "Exact lemma", "domain": "all n",
                         "quantifier": "universal"})
    w.apply("node.plan", {"node": "goal", "proof_dependencies": ["lemma"],
                          "reason": "the lemma is the only planned obligation"})
    yield w
    w.store.close()


def add_node(w, node_id):
    return w.apply("node.add", {"id": node_id, "statement": "Candidate " + node_id,
                                "domain": "all n", "quantifier": "universal"})


def register(w, node_id, rationale="a speculative bridge"):
    return w.apply("research.register", {"node": node_id, "rationale": rationale})


def test_scope_separates_proof_from_exploration(wb):
    add_node(wb, "h2")
    assert scope.node_scope(wb, "goal") == "proof"
    assert scope.node_scope(wb, "lemma") == "proof"
    assert scope.node_scope(wb, "h2") == "none"
    register(wb, "h2")
    assert scope.node_scope(wb, "h2") == "exploration"


def test_a_proof_obligation_cannot_be_registered_as_exploration(wb):
    with pytest.raises(ResearchError, match="already a proof obligation"):
        register(wb, "lemma")


def test_registration_is_explicit_and_unique(wb):
    add_node(wb, "h2")
    register(wb, "h2")
    with pytest.raises(ResearchError, match="already registered"):
        register(wb, "h2")


def test_registration_has_a_bounded_cap(wb):
    for index in range(scope.DEFAULT_REGISTRATION_CAP):
        add_node(wb, f"c{index}")
        register(wb, f"c{index}")
    add_node(wb, "overflow")
    with pytest.raises(ResearchError, match="budget is exhausted"):
        register(wb, "overflow")


def test_exploration_route_requires_a_registered_candidate(wb):
    add_node(wb, "h2")
    with pytest.raises(ResearchError, match="registered research candidate"):
        wb.apply("route.add", {"id": "x1", "node": "h2", "kind": "exploration",
                               "rationale": "bridge", "next_question": "q",
                               "stop_condition": "s", "max_runs": 3, "stagnation_limit": 2})


def test_a_proof_route_cannot_target_a_research_candidate(wb):
    add_node(wb, "h2")
    register(wb, "h2")
    with pytest.raises(ResearchError, match="goal dependency cone"):
        wb.apply("route.add", {"id": "x1", "node": "h2", "rationale": "bridge",
                               "next_question": "q", "stop_condition": "s",
                               "max_runs": 3, "stagnation_limit": 2})


def test_exploration_route_runs_and_settles_without_promoting_an_unverified_bridge(wb):
    add_node(wb, "h2")
    register(wb, "h2")
    route = wb.apply("route.add", {"id": "x1", "node": "h2", "kind": "exploration",
                                   "rationale": "bridge", "next_question": "q",
                                   "stop_condition": "s", "max_runs": 3, "stagnation_limit": 2})
    assert route["kind"] == "exploration"
    wb.apply("route.status", {"id": "x1", "status": "active", "reason": "test"})
    assert not wb.apply("task.preflight", {"route": "x1", "role": "explore"})["ready"]
    decide(wb, "explore-x1", "switch", "x1")
    assert wb.apply("task.preflight", {"route": "x1", "role": "explore"})["ready"]
    wb.apply("round.open", {"id": "r1", "route": "x1"})
    task = wb.apply("task.add", {"id": "t1", "route": "x1", "role": "explore", "round": "r1",
                                 "argv": [sys.executable, "-c", "print(1)"],
                                 "timeout_seconds": 30, "output_limit_bytes": 10000})
    assert task["node"] == "h2"
    finished = run_task(wb, "t1")
    assert finished["status"] == "completed"
    wb.apply("finding.add", {"id": "bridge-candidate", "node": "h2", "round": "r1", "task": "t1",
        "statement": "The minimal fixture returns 1", "scope": "The single fixture invocation",
        "kind": "new_structure", "evidence_kind": "finite_experiment",
        "evidence_refs": [finished["output_artifacts"][0]],
        "relation": {"target": "goal", "type": "speculative_bridge"},
        "next_action": {"kind": "check_bridge", "question": "Does the fixture result apply to the locked domain?"}})
    wb.apply("round.assess", {"round": "r1", "finding": "Bounded fixture probe only",
        "next_question": "Check the bridge", "findings": ["bridge-candidate"],
        "task_assessments": [{"task_id": "t1", "execution_status": "completed",
                              "scientific_outcome": "supporting_evidence"}]})
    assert wb.apply("round.settle", {"round": "r1"})["state"] == "settled"
    plan = wb.apply("node.plan", {"node": "goal", "proof_dependencies": ["lemma", "h2"],
                                  "reason": "Investigate the candidate bridge"})
    assert plan["proof_dependencies"] == ["lemma"]
    assert plan["proposed_dependencies"] == ["h2"]
    proof_debt = scope.debt(wb)
    assert proof_debt["nodes"] == ["lemma"]
    assert "h2" not in proof_debt["nodes"]
    assert scope.exploration(wb)["rounds_settled"] == 1
    assert wb.node_status("goal") == "open"
    assert wb.evidence_status("goal") == "unknown"
    assert wb.audit()["passed"]


def test_health_reports_proof_and_exploration_separately(wb):
    add_node(wb, "h2")
    register(wb, "h2")
    state = snapshot(wb)
    assert state["totals"]["open_obligations"] == 1          # lemma only
    assert state["totals"]["exploration_registered"] == 1
    assert state["totals"]["exploration_open"] == 1
    assert state["exploration"]["nodes"] == ["h2"]


def test_a_default_route_is_a_proof_route(wb):
    route = wb.apply("route.add", {"id": "p1", "node": "lemma", "rationale": "proof",
                                   "next_question": "q", "stop_condition": "s",
                                   "max_runs": 3, "stagnation_limit": 2})
    assert route["kind"] == "proof"

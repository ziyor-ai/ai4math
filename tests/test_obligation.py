"""Obligation accounting: stagnation survives route renaming, outcomes are explicit."""
import sys

import pytest

from ai4math import obligation, strategy
from ai4math.execution import run_task
from ai4math.store import ResearchError
from ai4math.workbench import Workbench


@pytest.fixture
def wb(tmp_path):
    instance = Workbench.create(tmp_path / "workspace", "test", "Obligation accounting")
    yield instance
    instance.store.close()


def goal(wb):
    wb.apply("node.add", {"id": "goal", "statement": "Exact goal", "domain": "all n",
                          "quantifier": "universal"})
    wb.apply("goal.set", {"node": "goal"})
    # this file tests obligation accounting, not the correspondence gate
    wb.apply("strategy.configure", {"correspondence_after": 1000})


def add_route(wb, key="route", max_runs=50, stagnation_limit=50):
    wb.apply("route.add", {"id": key, "node": "goal", "rationale": "a distinct construction",
                           "next_question": "does it lift?", "stop_condition": "no progress",
                           "max_runs": max_runs, "stagnation_limit": stagnation_limit})
    wb.apply("route.status", {"id": key, "status": "active", "reason": "bounded test"})


def checkpoint(wb, key="route", **extra):
    payload = {"route": key, "finding": "one more local estimate", "next_question": "next?",
               "outcome": "no-progress"}
    payload.update(extra)
    return wb.apply("route.checkpoint", payload)


def explore_task(wb, key="task"):
    return wb.apply("task.add", {"id": key, "route": "route", "role": "explore",
                                 "argv": [sys.executable, "-c", "print('done')"],
                                 "timeout_seconds": 5, "output_limit_bytes": 1000})


def test_stagnation_is_counted_per_obligation_not_per_route(wb):
    goal(wb)
    add_route(wb)
    for _ in range(obligation.DEFAULT_STAGNATION_LIMIT):
        checkpoint(wb)
    wb.apply("task.add", {"id": "t1", "route": "route", "role": "explore",
                          "argv": [sys.executable, "-c", "print(1)"],
                          "timeout_seconds": 5, "output_limit_bytes": 1000})
    with pytest.raises(ResearchError, match="stagnation limit"):
        run_task(wb, "t1")


def test_a_renamed_route_cannot_reset_the_clock(wb):
    goal(wb)
    add_route(wb)
    for _ in range(obligation.DEFAULT_STAGNATION_LIMIT):
        checkpoint(wb)
    add_route(wb, "route2")
    fresh = wb.store.get("route", "route2")
    assert fresh["continuation_of"] == "route"
    assert fresh["obligation_rounds"] == obligation.DEFAULT_STAGNATION_LIMIT
    # the gate sits on dispatch: the contract can be prepared, but it cannot run
    wb.apply("task.add", {"id": "t3", "route": "route2", "role": "explore",
                          "argv": [sys.executable, "-c", "print(1)"],
                          "timeout_seconds": 5, "output_limit_bytes": 1000})
    with pytest.raises(ResearchError, match="stagnation limit"):
        run_task(wb, "t3")


def test_a_reviewed_switch_releases_the_obligation(wb):
    goal(wb)
    add_route(wb)
    for _ in range(obligation.DEFAULT_STAGNATION_LIMIT):
        checkpoint(wb)
    add_route(wb, "route2")
    reviewed = strategy.brief(wb)
    wb.apply("strategy.decide", {"id": "d1", "brief_sha256": reviewed["brief_sha256"],
                                 "decision": "switch", "next_route": "route2",
                                 "reason": "the renamed route is the same mechanism",
                                 "next_question": "does the new mechanism survive a long block?"})
    assert wb.store.get("obligation", "goal")["stagnant"] == 0
    wb.apply("task.add", {"id": "t4", "route": "route2", "role": "explore",
                          "argv": [sys.executable, "-c", "print('ok')"],
                          "timeout_seconds": 5, "output_limit_bytes": 1000})
    assert run_task(wb, "t4")["status"] == "completed"


def test_round_outcome_is_explicit(wb):
    goal(wb)
    add_route(wb)
    with pytest.raises(ResearchError, match="claim outcome needs"):
        checkpoint(wb, outcome="claim")
    with pytest.raises(ResearchError, match="discharged needs"):
        checkpoint(wb, outcome="discharged")
    with pytest.raises(ResearchError, match="one of"):
        checkpoint(wb, outcome="reviewed")
    assert wb.store.get("obligation", "goal")["rounds"] == 0


def test_new_leaf_needs_a_debt_justification_or_a_supersession(wb):
    goal(wb)
    for index in range(obligation.DEBT_SOFT_LIMIT):
        wb.apply("node.add", {"id": f"n{index}", "statement": f"Exact lemma {index}",
                              "domain": "all n", "quantifier": "universal"})
    # Batch 8.0: proof debt counts only open nodes inside the goal's proof cone.
    wb.apply("node.plan", {"node": "goal", "reason": "the eight open leaves are the plan",
                           "proof_dependencies": [f"n{index}" for index in range(obligation.DEBT_SOFT_LIMIT)]})
    with pytest.raises(ResearchError, match="net obligation debt"):
        wb.apply("node.add", {"id": "n8", "statement": "Exact lemma 8", "domain": "all n",
                              "quantifier": "universal"})
    wb.apply("node.add", {"id": "n8", "statement": "Exact lemma 8", "domain": "all n",
                          "quantifier": "universal", "supersedes": ["n0"]})
    assert wb.store.get("node", "n0")["superseded_by"] == "n8"
    wb.apply("node.add", {"id": "n9", "statement": "Exact lemma 9", "domain": "all n",
                          "quantifier": "universal", "debt_justification": "closes a gap the reviewer named"})

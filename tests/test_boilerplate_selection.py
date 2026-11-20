"""Batch 8.1: boilerplate guard, selection layer and the strategy map."""
import json
import sys

import pytest

from ai4math import boilerplate, strategy
from ai4math.store import ResearchError
from ai4math.workbench import Workbench


@pytest.fixture
def wb(tmp_path):
    w = Workbench.create(tmp_path / "ws", "bp", "Boilerplate guard")
    w.apply("node.add", {"id": "goal", "statement": "Exact goal", "domain": "all n",
                         "quantifier": "universal"})
    w.apply("goal.set", {"node": "goal"})
    w.apply("strategy.configure", {"correspondence_after": 1000})
    w.apply("node.add", {"id": "h2", "statement": "Candidate", "domain": "all n",
                         "quantifier": "universal"})
    w.apply("research.register", {"node": "h2", "rationale": "candidate"})
    yield w
    w.store.close()


def design(**overrides):
    payload = {"mechanism": "evaluate the residual with a weighted Sobolev cutoff at scale k",
               "proposed_tool": "interval arithmetic for the cutoff constant",
               "falsifier": "the cutoff constant is unbounded as k grows",
               "representation": "weighted Sobolev norm of the residual",
               "bridge": "a uniform constant bounds the residual by the source norm",
               "plan": "compute the constant for k = 4, 8, 16 and compare the growth",
               "family": "harmonic-analysis",
               "assumptions": ["fixed g", "fixed n"]}
    payload.update(overrides)
    return payload


def test_the_old_boilerplate_is_rejected_in_every_field():
    for text in boilerplate.FROZEN:
        payload = design(mechanism=text)
        with pytest.raises(ResearchError, match="boilerplate"):
            boilerplate.check_design(payload)


def test_a_renamed_copy_of_one_field_is_rejected():
    payload = design(bridge="the cutoff constant is unbounded as k grows")
    with pytest.raises(ResearchError, match="reuses the same text"):
        boilerplate.check_design(payload)


def test_a_real_design_passes_and_the_registry_is_stable():
    boilerplate.check_design(design())
    first = boilerplate.registry()
    assert first["count"] == len(boilerplate.FROZEN)
    assert first["sha256"] == boilerplate.registry()["sha256"]


@pytest.mark.dsh_legacy
def test_open_round_without_a_design_is_refused(wb):
    from ai4math.round import open_round

    with pytest.raises(ResearchError, match="explicit design"):
        open_round(wb, "r1", "h2", "lib", "does the cutoff stay bounded?", card="card-1")


@pytest.mark.dsh_legacy
def test_open_round_with_boilerplate_is_refused(wb):
    from ai4math.round import open_round

    with pytest.raises(ResearchError, match="boilerplate"):
        open_round(wb, "r1", "h2", "lib", "q", card="card-1",
                   design=design(mechanism=boilerplate.FROZEN[0]))


@pytest.mark.dsh_legacy
def test_a_batch_needs_two_families(wb):
    from ai4math.round import open_batch

    specs = [{"name": "r1", "node": "h2", "question": "q1", "family": "harmonic-analysis",
              "design": design()},
             {"name": "r2", "node": "h2", "question": "q2", "family": "harmonic-analysis",
              "design": design()}]
    with pytest.raises(ResearchError, match="structurally distinct families"):
        open_batch(wb, specs, "lib", card="card-1")


@pytest.mark.dsh_legacy
def test_a_batch_rejects_boilerplate_before_any_work(wb):
    from ai4math.round import open_batch

    specs = [{"name": "r1", "node": "h2", "question": "q1", "family": "analysis",
              "design": design()},
             {"name": "r2", "node": "h2", "question": "q2", "family": "geometry",
              "design": design(bridge=boilerplate.FROZEN[3])}]
    with pytest.raises(ResearchError, match="boilerplate"):
        open_batch(wb, specs, "lib", card="card-1")


def _approach(w, sid="S-1"):
    w.apply("approach.add", {"id": sid, "node": "h2", "approach": "weighted cutoff",
                             "family": "harmonic-analysis",
                             "bridge_to_goal": {"mode": "sufficient_condition"}}, "author")
    w.apply("approach.review", {"id": sid, "verdict": "selected", "reason": "worth one probe"},
            "reviewer")
    return w.apply("approach.activate", {"id": sid}, "coordinator")


def test_narrow_records_the_old_scope(wb):
    _approach(wb)
    row = wb.apply("approach.narrow", {"id": "S-1", "reason": "only the k<=8 window is relevant",
                                       "new_scope": "fixed g, n, k <= 8"}, "coordinator")
    assert row["scope"] == "fixed g, n, k <= 8"
    assert row["narrowed"][0]["old_scope"] is None


def test_select_records_the_mapped_decision_and_never_fakes_it(wb):
    _approach(wb)
    row = wb.apply("strategy.select", {"id": "SEL-1", "action": "narrow", "approach": "S-1",
                                       "reason": "keep only the small window",
                                       "new_scope": "k <= 8"}, "coordinator")
    assert row["mapped_decision"] == "replan"
    assert row["events"] == ["approach.narrow"]
    assert wb.store.get("approach", "S-1")["scope"] == "k <= 8"


def test_retire_and_pause_differ(wb):
    _approach(wb)
    wb.apply("strategy.select", {"id": "SEL-P", "action": "pause", "approach": "S-1",
                                 "reason": "waiting for a source"}, "coordinator")
    assert wb.store.get("approach", "S-1")["status"] == "paused"
    wb.apply("strategy.select", {"id": "SEL-R", "action": "retire", "approach": "S-1",
                                 "reason": "the mechanism cannot reach the goal",
                                 "reopen_condition": "a new source supplies the missing bound"},
             "coordinator")
    row = wb.store.get("approach", "S-1")
    assert row["status"] == "retired"
    assert row["reopen_condition"].startswith("a new source")
    mapping = wb.apply("strategy.select", {"id": "SEL-S", "action": "switch",
                                           "reason": "spend the next round elsewhere",
                                           "routes": ["r-2"]}, "coordinator")
    assert mapping["mapped_decision"] == "switch"


def test_pause_blocks_dispatch_and_reactivation_resumes_it(wb):
    _approach(wb)
    wb.apply("route.add", {"id": "x1", "node": "h2", "kind": "exploration",
                           "rationale": "bridge", "next_question": "q", "stop_condition": "s",
                           "max_runs": 3, "stagnation_limit": 2})
    wb.apply("route.status", {"id": "x1", "status": "active", "reason": "test"})
    wb.apply("approach.status", {"id": "S-1", "status": "paused"}, "coordinator")
    with pytest.raises(ResearchError, match="approach is not active"):
        wb.apply("task.add", {"id": "t1", "route": "x1", "role": "explore",
                              "argv": [sys.executable, "-c", "print(1)"],
                              "timeout_seconds": 30, "output_limit_bytes": 10000}, "runner")
    wb.apply("approach.activate", {"id": "S-1"}, "coordinator")
    wb.apply("task.add", {"id": "t2", "route": "x1", "role": "explore",
                          "argv": [sys.executable, "-c", "print(1)"],
                          "timeout_seconds": 30, "output_limit_bytes": 10000}, "runner")


def test_the_strategy_map_shows_the_research_state(wb):
    _approach(wb)
    wb.apply("research_hypothesis.add", {"id": "H-1", "statement": "a bound holds",
                                         "scope": "fixed g"}, "author")
    wb.apply("obstacle.add", {"id": "O-1", "node": "h2", "description": "no bound yet",
                              "kind": "unproved_dependency"}, "author")
    page = strategy.map_view(wb)
    assert page["approaches"]["by_status"]["exploring"] == 1
    assert page["hypotheses"]["by_status"]["open"] == 1
    assert page["obstacles"]["by_kind"]["unproved_dependency"] == 1
    assert page["routes"]["total"] == 0
    json.dumps(page)

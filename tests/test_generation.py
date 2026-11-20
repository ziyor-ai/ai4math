"""Batch 8.0 slice 8: local, consumable generation obligations."""
import sys

import pytest

from ai4math import generation
from ai4math.execution import run_task
from ai4math.store import ResearchError, now
from ai4math.workbench import Workbench


@pytest.fixture
def wb(tmp_path):
    w = Workbench.create(tmp_path / "ws", "generation", "Generation obligations")
    w.apply("node.add", {"id": "goal", "statement": "Exact goal", "domain": "all n",
                         "quantifier": "universal"})
    w.apply("goal.set", {"node": "goal"})
    w.apply("strategy.configure", {"correspondence_after": 1000, "critique_interval": 1})
    w.apply("route.add", {"id": "route", "node": "goal", "rationale": "one direction",
                          "next_question": "q", "stop_condition": "s",
                          "max_runs": 50, "stagnation_limit": 50})
    w.apply("route.status", {"id": "route", "status": "active", "reason": "test"})
    yield w
    w.store.close()


def add_task(w, tid, route="route", role="explore", intent=None):
    payload = {"id": tid, "route": route, "role": role,
               "argv": [sys.executable, "-c", "print(1)"],
               "timeout_seconds": 30, "output_limit_bytes": 10000}
    if intent is not None:
        payload["intent_kind"] = intent
    return w.apply("task.add", payload, "runner")


def request(w, rid="REQ-1", **overrides):
    payload = {"id": rid, "route": "route", "parent": "goal",
               "trigger_event_id": "goal:stagnant:3", "trigger_reason": "no_progress_drought"}
    payload.update(overrides)
    return w.store and w.apply("generation.request", payload, "coordinator")


def test_a_request_is_idempotent_on_its_trigger_triple(wb):
    first = request(wb)
    second = request(wb, rid="REQ-2")
    assert second["id"] == first["id"]
    assert len(wb.store.all("generation_request")) == 1


def test_a_pending_request_blocks_only_ordinary_exploration(wb):
    request(wb)
    add_task(wb, "t1")
    with pytest.raises(ResearchError, match="pending generation obligation"):
        run_task(wb, "t1")
    # A verification task is allowed.
    add_task(wb, "t2", intent="verification")
    assert run_task(wb, "t2")["status"] == "completed"
    # An entailment-analysis task is allowed.
    add_task(wb, "t3", intent="entailment_analysis")
    assert run_task(wb, "t3")["status"] == "completed"


def test_other_routes_are_not_blocked(wb):
    wb.apply("node.add", {"id": "other", "statement": "Other obligation", "domain": "all n",
                          "quantifier": "universal"})
    wb.apply("node.plan", {"node": "goal", "proof_dependencies": ["other"], "reason": "plan"})
    wb.apply("route.add", {"id": "route2", "node": "other", "rationale": "second",
                           "next_question": "q", "stop_condition": "s",
                           "max_runs": 50, "stagnation_limit": 50})
    wb.apply("route.status", {"id": "route2", "status": "active", "reason": "test"})
    request(wb)
    add_task(wb, "t9", route="route2")
    assert run_task(wb, "t9")["status"] == "completed"


def test_generate_consumes_the_request_with_candidates(wb):
    request(wb)
    row = wb.apply("strategy.generate", {"request": "REQ-1", "candidates": [
        {"id": "S-02", "family": "reductio", "mechanism": "assume no coverage and build a counterexample"}]},
        "coordinator")
    assert row["status"] == "consumed"
    assert row["resolution"]["kind"] == "generated"
    assert generation.pending(wb) == []


def test_an_empty_generation_needs_a_reason_and_a_next_probe(wb):
    request(wb)
    with pytest.raises(ResearchError, match="reason_no_candidate"):
        wb.apply("strategy.generate", {"request": "REQ-1", "candidates": []}, "coordinator")
    row = wb.apply("strategy.generate", {"request": "REQ-1", "candidates": [],
                                         "reason_no_candidate": "no distinct mechanism found",
                                         "next_probe": "read the frozen source again"},
                   "coordinator")
    assert row["resolution"]["kind"] == "generated"
    assert wb.store.get("strategy_generation", row["resolution"]["event_ref"])["reason_no_candidate"]


def test_skip_needs_registered_evidence(wb):
    request(wb)
    with pytest.raises(ResearchError, match="not a frozen, registered object"):
        wb.apply("generation.skip", {"request": "REQ-1", "reason": "busy",
                                     "evidence_ref": "made-up"}, "coordinator")


def test_skip_with_a_real_artifact_closes_the_request(wb):
    request(wb)
    path = wb.store.root / "note.txt"
    path.write_text("we already have this", encoding="utf-8")
    artifact = wb.apply("artifact.import", {"path": str(path), "purpose": "note"})["id"]
    row = wb.apply("generation.skip", {"request": "REQ-1", "reason": "already covered",
                                       "evidence_ref": artifact}, "coordinator")
    assert row["resolution"]["kind"] == "justified_skip"
    assert row["resolution"]["evidence_ref"] == artifact


def test_resolve_refuses_while_the_trigger_is_active(wb):
    request(wb, trigger_reason="missing_entailment")
    with pytest.raises(ResearchError, match="trigger is still active"):
        wb.apply("generation.resolve", {"request": "REQ-1"}, "coordinator")


def test_resolve_succeeds_once_the_entailment_is_verified(wb):
    request(wb, trigger_reason="missing_entailment")
    with wb.store.transaction("fixture.inference", "test"):
        wb.store.put("inference", "INF-9",
                     {"id": "INF-9", "rule": "composition", "parent": "goal",
                      "verification": {"status": "verified"}}, new=True)
    row = wb.apply("generation.resolve", {"request": "REQ-1"}, "coordinator")
    assert row["resolution"]["kind"] == "trigger_resolved"


def test_a_closed_request_cannot_be_closed_twice(wb):
    request(wb)
    wb.apply("strategy.generate", {"request": "REQ-1", "candidates": [] ,
                                   "reason_no_candidate": "none", "next_probe": "read"}, "coordinator")
    with pytest.raises(ResearchError, match="already closed"):
        wb.apply("generation.resolve", {"request": "REQ-1"}, "coordinator")


def test_a_no_progress_drought_creates_one_request(wb):
    for index in range(2):
        wb.apply("route.checkpoint", {"route": "route", "finding": "nothing new " + str(index),
                                      "next_question": "next?", "outcome": "no-progress"})
    pending = generation.pending(wb, route="route")
    assert len(pending) == 1
    assert pending[0]["trigger_reason"] == "no_progress_drought"


def test_a_blocked_route_resumes_after_generation(wb):
    request(wb)
    add_task(wb, "t1")
    with pytest.raises(ResearchError, match="pending generation obligation"):
        run_task(wb, "t1")
    wb.apply("strategy.generate", {"request": "REQ-1", "candidates": [],
                                   "reason_no_candidate": "none", "next_probe": "probe"},
             "coordinator")
    add_task(wb, "t2")
    assert run_task(wb, "t2")["status"] == "completed"

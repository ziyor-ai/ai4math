"""A task-less attempt round and the guidance messages (audit F12/F13)."""
import pytest

from ai4math.store import ResearchError
from ai4math.workbench import Workbench


@pytest.fixture
def wb(tmp_path):
    w = Workbench.create(tmp_path / "ws", "attempt", "Attempt rounds")
    w.apply("node.add", {"id": "goal", "statement": "Exact goal", "domain": "all n",
                         "quantifier": "universal"})
    w.apply("goal.set", {"node": "goal"})
    w.apply("strategy.configure", {"correspondence_after": 1000})
    w.apply("route.add", {"id": "route", "node": "goal", "rationale": "proof",
                          "next_question": "q", "stop_condition": "s",
                          "max_runs": 10, "stagnation_limit": 50})
    w.apply("route.status", {"id": "route", "status": "active", "reason": "test"})
    yield w
    w.store.close()


def test_a_task_less_attempt_becomes_a_settleable_round(wb):
    wb.apply("round.open", {"id": "r1", "route": "route"}, "coordinator")
    checkpoint = wb.apply("round.record_attempt", {
        "round": "r1", "finding": "pairing sketched, no Lean proof",
        "next_question": "can the pairing be formalised?",
        "outcome": "no-progress", "reason": "dispatch gate refused a local research task"},
        "coordinator")
    assert checkpoint["attempt"] is True
    assert checkpoint["task_assessments"] == []
    settled = wb.apply("round.settle", {"round": "r1"}, "coordinator")
    assert settled["state"] == "settled"
    assert wb.store.get("obligation", "goal")["rounds"] == 1


def test_an_attempt_cannot_carry_claims(wb):
    wb.apply("round.open", {"id": "r1", "route": "route"}, "coordinator")
    with pytest.raises(ResearchError, match="cannot carry claims"):
        wb.apply("round.record_attempt", {"round": "r1", "finding": "f", "next_question": "q",
                                          "claims": ["c1"]}, "coordinator")


def test_a_round_with_tasks_uses_assess(wb):
    wb.apply("task.add", {"id": "t1", "route": "route", "role": "explore",
                          "argv": ["python", "-c", "print(1)"],
                          "timeout_seconds": 30, "output_limit_bytes": 1000}, "runner")
    wb.apply("round.open", {"id": "r1", "route": "route", "tasks": ["t1"]}, "coordinator")
    with pytest.raises(ResearchError, match="dispatched tasks"):
        wb.apply("round.record_attempt", {"round": "r1", "finding": "f", "next_question": "q"},
                 "coordinator")

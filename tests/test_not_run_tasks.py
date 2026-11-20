"""A never-started task can be closed by the round assessment (audit F14).

A dispatch refusal leaves the task pending so the operator can fix the blocker and
retry. If the round is closed anyway, the assessment marks it abandoned with
execution_status=not_run instead of leaving it pending forever.
"""
import sys

import pytest

from ai4math.store import ResearchError
from ai4math.workbench import Workbench


@pytest.fixture
def wb(tmp_path):
    w = Workbench.create(tmp_path / "ws", "notrun", "Not-run tasks")
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


def add_task(w, tid):
    return w.apply("task.add", {"id": tid, "route": "route", "role": "explore",
                                "argv": [sys.executable, "-c", "print(1)"],
                                "timeout_seconds": 30, "output_limit_bytes": 1000}, "runner")


def test_a_never_started_task_can_be_assessed_and_is_closed(wb):
    wb.apply("round.open", {"id": "r1", "route": "route"}, "coordinator")
    wb.apply("task.add", {"id": "t1", "route": "route", "role": "explore", "round": "r1",
                          "argv": [sys.executable, "-c", "print(1)"],
                          "timeout_seconds": 30, "output_limit_bytes": 1000}, "runner")
    checkpoint = wb.apply("round.assess", {
        "round": "r1", "finding": "dispatch was refused; nothing ran",
        "next_question": "fix the blocker and open a new round",
        "task_assessments": [{"task_id": "t1", "execution_status": "not_run",
                              "scientific_outcome": "not_assessed"}]}, "coordinator")
    assert checkpoint["outcome"] == "no-progress"
    task = wb.store.get("task", "t1")
    assert task["status"] == "abandoned"
    assert "never started" in task["abandon_reason"]


def test_not_run_is_refused_for_a_started_task(wb):
    add_task(wb, "t1")
    from ai4math.execution import run_task
    run_task(wb, "t1")
    wb.apply("round.open", {"id": "r1", "route": "route", "tasks": ["t1"]}, "coordinator")
    with pytest.raises(ResearchError, match="unfinished tasks|never started"):
        wb.apply("round.assess", {"round": "r1", "finding": "f", "next_question": "q",
                                  "task_assessments": [{"task_id": "t1",
                                                        "execution_status": "not_run",
                                                        "scientific_outcome": "not_assessed"}]},
                 "coordinator")

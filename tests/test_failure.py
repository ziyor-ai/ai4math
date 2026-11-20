"""Repeated failures on one route stop the loop instead of burning rounds."""
import sys

import pytest

from ai4math.execution import run_task
from ai4math.health import snapshot
from ai4math.store import ResearchError
from ai4math.workbench import Workbench


@pytest.fixture
def wb(tmp_path):
    instance = Workbench.create(tmp_path / "workspace", "test", "Failure ledger")
    yield instance
    instance.store.close()


def goal_and_route(wb, **strategy):
    wb.apply("node.add", {"id": "goal", "statement": "Exact goal", "domain": "all n",
                          "quantifier": "universal"})
    wb.apply("goal.set", {"node": "goal"})
    if strategy:
        wb.apply("strategy.configure", strategy)
    wb.apply("route.add", {"id": "route", "node": "goal", "rationale": "one mechanism",
                           "next_question": "does it close?", "stop_condition": "no progress",
                           "max_runs": 20, "stagnation_limit": 20})
    wb.apply("route.status", {"id": "route", "status": "active", "reason": "test"})


def failing(wb, key):
    wb.apply("task.add", {"id": key, "route": "route", "role": "explore",
                          "argv": [sys.executable, "-c", "import sys; sys.exit(3)"],
                          "timeout_seconds": 10, "output_limit_bytes": 1000})
    return run_task(wb, key)


def passing(wb, key):
    wb.apply("task.add", {"id": key, "route": "route", "role": "explore",
                          "argv": [sys.executable, "-c", "print('ok')"],
                          "timeout_seconds": 10, "output_limit_bytes": 1000})
    return run_task(wb, key)


def test_two_failures_park_the_route_and_halt(wb):
    goal_and_route(wb)
    assert failing(wb, "f1")["status"] == "failed"
    assert wb.store.get("route", "route")["status"] == "active"
    assert failing(wb, "f2")["status"] == "failed"
    assert wb.store.get("route", "route")["status"] == "parked"
    state = wb.store.get("strategy_state", "main")
    assert "failed 2 times" in state["halt"]["reason"]
    with pytest.raises(ResearchError, match="not active"):
        wb.apply("task.add", {"id": "f3", "route": "route", "role": "explore",
                              "argv": [sys.executable, "-c", "print(1)"],
                              "timeout_seconds": 5, "output_limit_bytes": 1000})


def test_a_success_clears_the_streak(wb):
    goal_and_route(wb)
    failing(wb, "f1")
    passing(wb, "ok1")
    assert wb.store.get("failure_state", "route")["streak"] == 0
    failing(wb, "f2")
    assert "halt" not in wb.store.get("strategy_state", "main")


def test_retry_limit_is_configurable(wb):
    goal_and_route(wb, retry_limit=3)
    failing(wb, "f1")
    failing(wb, "f2")
    assert wb.store.get("route", "route")["status"] == "active"
    failing(wb, "f3")
    assert wb.store.get("route", "route")["status"] == "parked"


def test_panel_reports_failures(wb):
    goal_and_route(wb)
    failing(wb, "f1")
    state = snapshot(wb)
    assert state["totals"]["failures"] == 1
    assert state["failures"]["streaks"] == {"route": 1}
    assert any(signal["code"] == "failure_streak" for signal in state["signals"])

"""Scheduling is a ranked suggestion, never an automatic dispatch (P2, §4.13)."""
import pytest

from ai4math.strategy import schedule
from ai4math.workbench import Workbench


@pytest.fixture
def wb(tmp_path):
    w = Workbench.create(tmp_path / "ws", "schedule", "Schedule")
    w.apply("node.add", {"id": "a", "statement": "A", "domain": "all n",
                         "quantifier": "universal"})
    w.apply("node.add", {"id": "b", "statement": "B", "domain": "all n",
                         "quantifier": "universal"})
    w.apply("node.add", {"id": "goal", "statement": "Goal", "domain": "all n",
                         "quantifier": "universal"})
    w.apply("node.plan", {"node": "goal", "proof_dependencies": ["a", "b"], "reason": "plan"})
    w.apply("goal.set", {"node": "goal"})
    w.apply("strategy.configure", {"correspondence_after": 1000})
    yield w
    w.store.close()


def test_ready_obligations_come_first(wb):
    page = schedule(wb)
    nodes = [item["node"] for item in page["items"]]
    assert nodes[0] in {"a", "b"}
    assert all(item["ready"] for item in page["items"])


def test_active_routes_are_listed_not_auto_created(wb):
    wb.apply("route.add", {"id": "r-a", "node": "a", "rationale": "p", "next_question": "q",
                           "stop_condition": "s", "max_runs": 5, "stagnation_limit": 5})
    wb.apply("route.status", {"id": "r-a", "status": "active", "reason": "test"})
    page = schedule(wb)
    first = next(item for item in page["items"] if item["node"] == "a")
    assert first["active_routes"] == ["r-a"]
    assert page["note"].startswith("scheduling suggestion")


def test_a_goal_without_open_obligations_schedules_nothing(wb):
    wb.apply("node.plan", {"node": "goal", "proof_dependencies": [], "reason": "direct"})
    assert schedule(wb)["items"] == []

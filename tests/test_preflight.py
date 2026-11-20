"""task.preflight lists every dispatch blocker at once (audit F12)."""
import pytest

from ai4math.store import ResearchError
from ai4math.workbench import Workbench


@pytest.fixture
def wb(tmp_path):
    w = Workbench.create(tmp_path / "ws", "preflight", "Preflight")
    w.apply("node.add", {"id": "goal", "statement": "Exact goal", "domain": "all n",
                         "quantifier": "universal"})
    w.apply("goal.set", {"node": "goal"})
    w.apply("strategy.configure", {"correspondence_after": 1000})
    w.apply("route.add", {"id": "route", "node": "goal", "rationale": "proof",
                          "next_question": "q", "stop_condition": "s",
                          "max_runs": 5, "stagnation_limit": 5})
    w.apply("route.status", {"id": "route", "status": "active", "reason": "test"})
    yield w
    w.store.close()


def test_an_undecided_route_reports_the_whole_chain(wb):
    page = wb.apply("task.preflight", {"route": "route", "role": "explore"}, "coordinator")
    gates = {item["gate"] for item in page["blockers"]}
    assert page["ready"] is False
    assert "literature" in gates
    assert len(page["sequence"]) == 3


def test_a_parked_route_is_reported(wb):
    wb.apply("route.status", {"id": "route", "status": "parked", "reason": "test"}, "coordinator")
    page = wb.apply("task.preflight", {"route": "route", "role": "validate"}, "coordinator")
    assert any(item["gate"] == "route" for item in page["blockers"])


def test_a_pending_generation_request_is_reported(wb):
    wb.apply("generation.request", {"id": "REQ-1", "route": "route", "parent": "goal",
                                    "trigger_event_id": "x",
                                    "trigger_reason": "no_progress_drought"}, "coordinator")
    page = wb.apply("task.preflight", {"route": "route", "role": "explore"}, "coordinator")
    assert any(item["gate"] == "generation" for item in page["blockers"])
    validate = wb.apply("task.preflight", {"route": "route", "role": "validate"}, "coordinator")
    assert not any(item["gate"] == "generation" for item in validate["blockers"])


def test_a_missing_route_is_refused(wb):
    with pytest.raises(ResearchError, match="missing route"):
        wb.apply("task.preflight", {"route": "nope", "role": "explore"}, "coordinator")


def test_task_add_attaches_the_preflight_list(wb):
    wb.apply("route.status", {"id": "route", "status": "parked", "reason": "test"}, "coordinator")
    with pytest.raises(ResearchError) as error:
        wb.apply("task.add", {"id": "t1", "route": "route", "role": "explore",
                              "argv": ["python", "-c", "print(1)"],
                              "timeout_seconds": 30, "output_limit_bytes": 1000}, "runner")
    message = str(error.value)
    assert "preflight:" in message
    assert "parked" in message

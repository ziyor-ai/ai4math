"""Local research execution is an explicit, off-by-default opt-in (P2 remainder)."""
import sys

import pytest

from ai4math.store import ResearchError
from ai4math.workbench import Workbench


@pytest.fixture
def wb(tmp_path):
    w = Workbench.create(tmp_path / "ws", "local", "Local research")
    w.apply("node.add", {"id": "goal", "statement": "Exact goal", "domain": "all n",
                         "quantifier": "universal"})
    w.apply("goal.set", {"node": "goal"})
    w.apply("strategy.configure", {"correspondence_after": 1000})
    w.apply("route.add", {"id": "route", "node": "goal", "rationale": "probe",
                          "next_question": "q", "stop_condition": "s",
                          "max_runs": 10, "stagnation_limit": 20})
    w.apply("route.status", {"id": "route", "status": "active", "reason": "test"})
    yield w
    w.store.close()


def explore(w, tid="t1"):
    return w.apply("task.add", {"id": tid, "route": "route", "role": "explore",
                                "argv": [sys.executable, "-c", "print('probe')"],
                                "timeout_seconds": 60, "output_limit_bytes": 10000}, "runner")


def test_local_research_is_off_by_default(wb):
    explore(wb)
    wb.apply("literature.enable", {}, "coordinator")
    from ai4math.execution import run_task
    with pytest.raises(ResearchError, match="literature"):
        run_task(wb, "t1", "runner")


def test_the_opt_in_must_be_a_boolean(wb):
    with pytest.raises(ResearchError, match="local_research must be a boolean"):
        wb.apply("policy.configure", {"local_research": "yes"}, "admin")


def test_the_opt_in_allows_a_local_explore_task(wb):
    wb.apply("literature.enable", {}, "coordinator")
    wb.apply("policy.configure", {"local_research": True}, "admin")
    explore(wb)
    from ai4math.execution import run_task
    task = run_task(wb, "t1", "runner")
    assert task["status"] == "completed"
    assert (wb.store.root / "tasks" / "t1" / "stdout.log").read_text(encoding="utf-8").strip() == "probe"


def test_a_local_research_task_cannot_accept_a_claim(wb):
    wb.apply("policy.configure", {"local_research": True}, "admin")
    explore(wb)
    from ai4math.execution import run_task
    run_task(wb, "t1", "runner")
    digest = wb.store.get("node", "goal")["statement_digest"]
    wb.apply("claim.add", {"id": "c1", "node": "goal", "statement_digest": digest,
                           "evidence_level": "proved", "scope": "universal",
                           "summary": "s"}, "author")
    with pytest.raises(ResearchError, match="missing review"):
        wb.apply("claim.accept", {"claim": "c1", "review": "missing"}, "custodian")

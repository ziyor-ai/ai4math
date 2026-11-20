"""Guard coverage for the external dispatch entry points (P2 remainder).

The positive paths need a real Codex runtime and a frozen skill manifest, so these
tests pin the deterministic refusals: a plain task can never be dispatched as a
subagent, and only the dispatching worker or coordinator can begin a seal.
"""
import pytest

from ai4math.store import ResearchError
from ai4math.workbench import Workbench


@pytest.fixture
def wb(tmp_path):
    w = Workbench.create(tmp_path / "ws", "guards", "Dispatch guards")
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


def task(w, tid="t1"):
    w.apply("task.add", {"id": tid, "route": "route", "role": "explore",
                         "argv": ["python", "-c", "print(1)"],
                         "timeout_seconds": 30, "output_limit_bytes": 1000}, "runner")
    return w.store.get("task", tid)


def test_prepare_subagent_refuses_a_plain_task(wb):
    from ai4math.codex_runner import prepare_subagent
    task(wb)
    with pytest.raises(ResearchError, match="Codex-managed task"):
        prepare_subagent(wb, "t1", "runner", wb.store.root)


def test_prepare_subagent_refuses_a_missing_task(wb):
    from ai4math.codex_runner import prepare_subagent
    with pytest.raises(ResearchError, match="missing task"):
        prepare_subagent(wb, "nope", "runner", wb.store.root)


def test_begin_seal_refuses_a_task_without_an_external_policy(wb):
    from ai4math.external_deadline import begin_seal
    task(wb)
    with pytest.raises(ResearchError, match="running managed exploration task"):
        begin_seal(wb, {"task": "t1"}, "coordinator")


def test_only_the_worker_or_coordinator_may_begin_a_seal(wb):
    from ai4math.external_deadline import begin_seal
    task(wb)
    with wb.store.transaction("fixture.external", "test"):
        row = wb.store.get("task", "t1")
        row.update(status="running", external_stage="exploration",
                   external_deadline_policy={"seconds": 60},
                   dispatched_by="coordinator", deadline_at="2030-01-01T00:00:00+00:00")
        wb.store.put("task", "t1", row)
        wb.store.put("worker_literature", "t1",
                     {"id": "t1", "actor": "worker", "created_at": "2030-01-01T00:00:00+00:00"},
                     new=True)
    with pytest.raises(ResearchError, match="only the worker or coordinator"):
        begin_seal(wb, {"task": "t1"}, "intruder")

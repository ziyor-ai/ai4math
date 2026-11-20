"""Batch 8.0 slice 2: round lifecycle, one checkpoint per round, settlement once."""
from concurrent.futures import ThreadPoolExecutor
import sys
from threading import Barrier

import pytest

from ai4math import handoff, settlement, store as store_module
from ai4math.execution import run_task, start_external
from ai4math.store import Failpoint, ResearchError, file_hash
from ai4math.workbench import Workbench
from test_strategy import coordinator_literature, decide, worker_receipt


@pytest.fixture
def wb(tmp_path):
    w = Workbench.create(tmp_path / "ws", "rounds", "Round lifecycle")
    w.apply("node.add", {"id": "goal", "statement": "Exact goal", "domain": "all n",
                         "quantifier": "universal"})
    w.apply("goal.set", {"node": "goal"})
    w.apply("strategy.configure", {"correspondence_after": 1000})
    w.apply("route.add", {"id": "route", "node": "goal", "rationale": "one direction",
                          "next_question": "does it hold?", "stop_condition": "no progress",
                          "max_runs": 50, "stagnation_limit": 50})
    w.apply("route.status", {"id": "route", "status": "active", "reason": "fixture"})
    w.apply("round.open", {"id": "r1", "route": "route"})
    yield w
    w.store.close()
    store_module.disarm()


def add_task(w, tid="t1"):
    return w.apply("task.add", {"id": tid, "route": "route", "role": "explore", "round": "r1",
                                "argv": [sys.executable, "-c", "print(1)"],
                                "timeout_seconds": 30, "output_limit_bytes": 10000})


def complete(w, tid="t1"):
    task = run_task(w, tid)
    assert task["status"] == "completed"
    return task


def assessment(science="inconclusive", execution="completed", refs=None):
    item = {"task_id": "t1", "execution_status": execution, "scientific_outcome": science}
    if refs is not None:
        item["evidence_refs"] = refs
    return item


def assess(w, task_assessments=None, **extra):
    payload = {"round": "r1", "finding": "one bounded probe returned 1",
               "next_question": "what next?", "task_assessments": task_assessments or [assessment()]}
    payload.update(extra)
    return w.apply("round.assess", payload)


def managed_completion(w, tid="managed"):
    sealed = w.store.get("external_seal", tid)
    return {"task": tid, "status": "completed", "summary": "Received the frozen offline probe",
            "artifact_ids": sealed["output_artifacts"], "seal_manifest_artifact": sealed["artifact_id"]}


def finish_managed_round(w, round_id="r1", tid="managed"):
    completion = managed_completion(w, tid)
    result = w.apply("task.finish_external", completion)
    assert (result["round"], result["status"], result["stop_state"], result["evidence_level"]) == (
        round_id, "completed", "confirmed", "unknown")
    returned = handoff.read(w.store, tid)
    assert returned["schema"] == handoff.SCHEMA
    assert returned["artifacts"] == completion["artifact_ids"]
    assert (returned["status"], returned["evidence_level"]) == ("completed", "unknown")
    sequence = w.store.last_seq()
    with pytest.raises(ResearchError, match="running external task"):
        w.apply("task.finish_external", completion)
    assert w.store.last_seq() == sequence
    checkpoint = w.apply("round.assess", {"round": round_id, "finding": "Probe ran in the fixed fixture domain",
        "next_question": "Check the remaining obligation independently",
        "task_assessments": [{"task_id": tid, "execution_status": "completed", "scientific_outcome": "inconclusive"}]})
    assert checkpoint["id"] == f"cp-{round_id}"
    w.apply("round.settle", {"round": round_id})
    assert settlement.state(w, round_id) == "settled"
    assert w.store.get("obligation", result["node"])["rounds"] == 1
    assert w.node_status(result["node"]) == "open"
    assert w.audit()["passed"]


def test_a_task_is_attached_to_its_round(wb):
    task = add_task(wb)
    assert task["round"] == "r1"
    assert settlement.row(wb, "r1")["tasks"] == ["t1"]
    assert settlement.state(wb, "r1") == "running"


def test_round_open_needs_a_real_route(wb):
    with pytest.raises(ResearchError, match="missing route"):
        wb.apply("round.open", {"id": "r2", "route": "nope"})


def test_assess_refuses_while_a_task_is_unfinished(wb):
    add_task(wb)
    with pytest.raises(ResearchError, match="unfinished tasks"):
        assess(wb)


def test_assess_needs_exactly_one_assessment_per_task(wb):
    add_task(wb)
    complete(wb)
    with pytest.raises(ResearchError, match="outside this round"):
        assess(wb, [{"task_id": "other", "execution_status": "completed",
                     "scientific_outcome": "inconclusive"}])


def test_proved_needs_an_accepted_claim(wb):
    add_task(wb)
    complete(wb)
    with pytest.raises(ResearchError, match="needs an accepted claim"):
        assess(wb, [assessment(science="proved")])


def test_an_unfinished_task_cannot_be_recorded_as_proved(wb):
    wb.apply("task.add", {"id": "t1", "route": "route", "role": "explore", "round": "r1",
                         "argv": [sys.executable, "-c", "raise SystemExit(1)"],
                         "timeout_seconds": 30})
    assert run_task(wb, "t1")["status"] == "failed"
    with pytest.raises(ResearchError, match="must be not_assessed"):
        assess(wb, [assessment(science="proved", execution="failed", refs=["c"])])


def test_the_checkpoint_is_written_exactly_once(wb):
    add_task(wb)
    complete(wb)
    checkpoint = assess(wb)
    assert checkpoint["id"] == "cp-r1"
    assert settlement.state(wb, "r1") == "assessed"
    with pytest.raises(ResearchError, match="already assessed"):
        assess(wb)


def test_settlement_applies_the_bookkeeping_once(wb):
    add_task(wb)
    complete(wb)
    assess(wb)
    before = len(wb.store.get("route", "route")["checkpoints"])
    first = wb.apply("round.settle", {"round": "r1"})
    after = len(wb.store.get("route", "route")["checkpoints"])
    assert after == before + 1
    second = wb.apply("round.settle", {"round": "r1"})
    assert second["id"] == first["id"]
    assert len(wb.store.get("route", "route")["checkpoints"]) == after
    assert wb.store.get("obligation", "goal")["rounds"] == 1
    assert settlement.state(wb, "r1") == "settled"


def test_settlement_before_the_checkpoint_is_refused(wb):
    add_task(wb)
    complete(wb)
    with pytest.raises(ResearchError, match="not assessed"):
        wb.apply("round.settle", {"round": "r1"})


def test_a_crash_before_the_checkpoint_commit_leaves_nothing(wb):
    add_task(wb)
    complete(wb)
    store_module.arm("before_commit")
    with pytest.raises(Failpoint):
        assess(wb)
    assert not wb.store.exists("round_checkpoint", "cp-r1")
    # The whole transaction rolled back, including the derived "ready_to_assess" state.
    assert settlement.state(wb, "r1") == "running"


def test_a_crash_after_the_checkpoint_commit_is_recoverable(wb):
    add_task(wb)
    complete(wb)
    store_module.arm("after_commit")
    with pytest.raises(Failpoint):
        assess(wb)
    assert wb.store.exists("round_checkpoint", "cp-r1")
    assert settlement.state(wb, "r1") == "assessed"
    assert not wb.store.exists("round_settled", "r1")
    settled = wb.apply("round.recover", {"round": "r1", "mode": "settle"})
    assert settled["id"] == "r1"
    assert settlement.state(wb, "r1") == "settled"
    assert wb.audit()["passed"]


def test_settlement_retry_after_lost_commit_response_does_not_count_twice(wb):
    add_task(wb)
    complete(wb)
    assess(wb)
    store_module.arm("after_commit")
    with pytest.raises(Failpoint):
        wb.apply("round.settle", {"round": "r1"})
    sequence = wb.store.last_seq()
    assert wb.apply("round.recover", {"round": "r1", "mode": "settle"})["id"] == "r1"
    assert wb.store.last_seq() == sequence
    assert wb.store.get("obligation", "goal")["rounds"] == 1
    assert len(wb.store.get("route", "route")["checkpoints"]) == 1
    assert wb.audit()["passed"]


def test_concurrent_settlement_counts_the_checkpoint_once(wb):
    add_task(wb)
    complete(wb)
    assess(wb)
    barrier = Barrier(2, timeout=10)

    def settle_round():
        reader = Workbench(wb.store.root)
        try:
            barrier.wait()
            return reader.apply("round.settle", {"round": "r1"})
        finally:
            reader.store.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        callers = [pool.submit(settle_round) for _ in range(2)]
        assert [caller.result(timeout=35)["id"] for caller in callers] == ["r1", "r1"]
    assert wb.store.get("obligation", "goal")["rounds"] == 1
    assert len(wb.store.get("route", "route")["checkpoints"]) == 1
    assert len(wb.store.all("round_settled")) == 1
    assert wb.audit()["passed"]


def test_an_unassessed_round_can_be_aborted_but_not_settled(wb):
    add_task(wb)
    aborted = wb.apply("round.recover", {"round": "r1", "mode": "abort", "reason": "worker never started"})
    assert aborted["state"] == "aborted"
    with pytest.raises(ResearchError, match="not assessed"):
        wb.apply("round.settle", {"round": "r1"})
    with pytest.raises(ResearchError, match="no longer accepts tasks"):
        add_task(wb, "t2")


@pytest.mark.parametrize("aborted", [False, True], ids=["settle", "aborted"])
def test_round_bound_managed_task_finishes_before_assessment(wb, aborted):
    decide(wb, "direction", "switch", "route")
    coordinator_literature(wb, "direction", "route")
    wb.apply("task.add", {"id": "t1", "route": "route", "role": "explore", "round": "r1",
                          "argv": [sys.executable, "-c", "pass"], "external_supervision": "host"})
    dispatch = start_external(wb, "t1")
    ready = wb.apply("task.literature_ready", worker_receipt(wb, "t1", dispatch), "worker")
    wb.apply("task.release_exploration", {"task": "t1", "worker_receipt_sha256": ready["worker_receipt_sha256"],
                                           "assessment": "Independent search reviewed"})
    wb.apply("task.begin_seal", {"task": "t1"}, "worker")
    output = wb.store.inside("tasks/t1/return/probe.txt")
    output.parent.mkdir(exist_ok=True)
    output.write_text("One bounded offline probe", encoding="utf-8")
    sealed = wb.apply("task.submit_seal_batch", {"task": "t1", "contract_sha256": dispatch["contract_sha256"],
                       "artifacts": [{"path": output.name, "sha256": file_hash(output)}]}, "worker")["receipt"]
    completion = {"task": "t1", "status": "completed", "summary": "Received the frozen probe",
                  "artifact_ids": sealed["output_artifacts"], "seal_manifest_artifact": sealed["artifact_id"]}

    with pytest.raises(ResearchError, match="confirmed physical stop"):
        wb.apply("task.finish_external", completion)
    wb.apply("task.confirm_stop", {"task": "t1", "confirmed": True, "evidence": "fixture owns no worker process"})
    with pytest.raises(ResearchError, match="unfinished tasks"):
        assess(wb)
    with pytest.raises(ResearchError, match="frozen submitted seal output set"):
        wb.apply("task.finish_external", {**completion, "seal_manifest_artifact": file_hash(output)})

    if aborted:
        wb.apply("round.recover", {"round": "r1", "mode": "abort", "reason": "round intentionally closed"})
    task = wb.apply("task.finish_external", completion)
    assert (task["round"], task["status"], task["evidence_level"]) == ("r1", "completed", "unknown")
    assert not wb.store.exists("round_checkpoint", "cp-r1")

    if aborted:
        with pytest.raises(ResearchError, match="round is aborted"):
            assess(wb)
        assert settlement.state(wb, "r1") == "aborted"
        assert wb.store.get("route", "route")["checkpoints"] == []
    else:
        with pytest.raises(ResearchError, match="needs an accepted claim"):
            assess(wb, [assessment(science="proved")])
        assert assess(wb)["id"] == "cp-r1"
        wb.apply("round.settle", {"round": "r1"})
        wb.apply("round.settle", {"round": "r1"})
        assert settlement.state(wb, "r1") == "settled"
        assert wb.store.get("obligation", "goal")["rounds"] == 1

    assert wb.audit()["passed"]


def test_a_task_outside_a_round_keeps_the_old_behaviour(wb):
    task = wb.apply("task.add", {"id": "t9", "route": "route", "role": "explore",
                                 "argv": [sys.executable, "-c", "print(1)"],
                                 "timeout_seconds": 30, "output_limit_bytes": 10000})
    assert "round" not in task
    assert settlement.row(wb, "r1")["tasks"] == []

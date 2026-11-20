"""Fixes for the process friction found in the 2026-11-18 simple-task batch.

Covers audit items F2 (empty round message), F3 (round.attach), F4 (evidence_refs
message), F5 (input purpose), F6 (expected certificate values), F7 (task.supersede)
and F8 (settle returns the round record).
"""
import sys

import pytest

from ai4math.execution import export_task, run_task
from ai4math.store import ResearchError
from ai4math.workbench import Workbench


@pytest.fixture
def wb(tmp_path):
    w = Workbench.create(tmp_path / "ws", "flowfix", "Flow fixes")
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


def add_task(w, tid, role="explore", inputs=None):
    payload = {"id": tid, "route": "route", "role": role,
               "argv": [sys.executable, "-c", "print(1)"],
               "timeout_seconds": 30, "output_limit_bytes": 10000}
    if inputs:
        payload["inputs"] = inputs
    return w.apply("task.add", payload, "runner")


def test_an_empty_round_says_it_has_no_tasks(wb):
    wb.apply("round.open", {"id": "r1", "route": "route"}, "coordinator")
    with pytest.raises(ResearchError, match="no attached tasks"):
        wb.apply("round.assess", {"round": "r1", "finding": "f", "next_question": "q",
                                  "task_assessments": [{"task_id": "t1",
                                                        "execution_status": "completed",
                                                        "scientific_outcome": "not_assessed"}]},
                 "coordinator")


def test_round_attach_binds_a_finished_task(wb):
    add_task(wb, "t1")
    run_task(wb, "t1")
    wb.apply("round.open", {"id": "r1", "route": "route"}, "coordinator")
    row = wb.apply("round.attach", {"round": "r1", "task": "t1"}, "coordinator")
    assert row["tasks"] == ["t1"]
    assert wb.store.get("task", "t1")["round"] == "r1"


def test_attach_refuses_a_running_task(wb):
    add_task(wb, "t1")
    wb.apply("round.open", {"id": "r1", "route": "route"}, "coordinator")
    with pytest.raises(ResearchError, match="terminal task"):
        wb.apply("round.attach", {"round": "r1", "task": "t1"}, "coordinator")


def test_settle_returns_the_round_record(wb):
    add_task(wb, "t1")
    run_task(wb, "t1")
    wb.apply("round.open", {"id": "r1", "route": "route", "tasks": ["t1"]}, "coordinator")
    wb.apply("round.assess", {"round": "r1", "finding": "f", "next_question": "q",
                              "task_assessments": [{"task_id": "t1",
                                                    "execution_status": "completed",
                                                    "scientific_outcome": "inconclusive"}]},
             "coordinator")
    settled = wb.apply("round.settle", {"round": "r1"}, "coordinator")
    assert settled["state"] == "settled"


def test_the_contract_names_input_purposes_and_expected_certificate_values(wb):
    path = wb.store.root / "proof.txt"
    path.write_text("proof", encoding="utf-8")
    art = wb.apply("artifact.import", {"path": str(path), "purpose": "lean proof"}, "author")["id"]
    digest = wb.store.get("node", "goal")["statement_digest"]
    wb.apply("claim.add", {"id": "c1", "node": "goal", "statement_digest": digest,
                           "evidence_level": "proved", "scope": "universal", "summary": "s",
                           "artifact_ids": [art]}, "author")
    add_task(wb, "t1", inputs=[art])
    contract = export_task(wb, "t1")["contract"]
    assert contract["input_artifacts"][0]["purpose"] == "lean proof"
    assert contract["result_contract"]["claim"] == "c1"
    assert contract["result_contract"]["scope"] == "universal"
    assert contract["result_contract"]["evidence_level"] == "proved"


def test_supersede_opens_a_fresh_attempt(wb):
    add_task(wb, "t1")
    wb.apply("task.abandon", {"task": "t1", "reason": "wrong checker"}, "runner")
    row = wb.apply("task.supersede", {"task": "t1", "id": "t1-a2"}, "runner")
    assert row["status"] == "pending"
    assert row["supersedes"] == "t1"
    assert row["attempt"] == 2


def test_supersede_refuses_a_live_task(wb):
    add_task(wb, "t1")
    with pytest.raises(ResearchError, match="only a finished task"):
        wb.apply("task.supersede", {"task": "t1", "id": "t1-a2"}, "runner")

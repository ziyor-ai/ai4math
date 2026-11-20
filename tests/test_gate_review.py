"""The long-cycle review gate stops the loop instead of letting it spin."""
import json
import sys

import pytest

from ai4math.cli import main
from ai4math.review_gate import review
from ai4math.store import ResearchError
from ai4math.workbench import Workbench


@pytest.fixture
def wb(tmp_path):
    instance = Workbench.create(tmp_path / "workspace", "test", "Review gate")
    yield instance
    instance.store.close()


def goal_and_route(wb):
    wb.apply("node.add", {"id": "goal", "statement": "Exact goal", "domain": "all n",
                          "quantifier": "universal"})
    wb.apply("goal.set", {"node": "goal"})
    # Keep the independent stagnation gate outside this long-cycle fixture.
    wb.apply("strategy.configure", {"obligation_stagnation_limit": 200})
    wb.apply("route.add", {"id": "route", "node": "goal", "rationale": "one mechanism",
                           "next_question": "does it close?", "stop_condition": "no progress",
                           "max_runs": 200, "stagnation_limit": 200})
    wb.apply("route.status", {"id": "route", "status": "active", "reason": "test"})


def receipt(wb):
    path = wb.store.root / "receipt.txt"
    path.write_text("failed probe: the uniform bound fails at scale two", encoding="utf-8")
    return wb.apply("artifact.import", {"path": str(path), "purpose": "failed probe output"})["id"]


def checkpoint(wb, outcome="falsified", receipt_id=None):
    payload = {"route": "route", "finding": "local estimate", "next_question": "next?", "outcome": outcome}
    if outcome == "falsified":
        payload["artifacts"] = [receipt_id or receipt(wb)]
    return wb.apply("route.checkpoint", payload)


def task(wb, key="task"):
    wb.apply("task.add", {"id": key, "route": "route", "role": "explore",
                          "argv": [sys.executable, "-c", "print(1)"],
                          "timeout_seconds": 5, "output_limit_bytes": 1000})
    from ai4math.execution import run_task
    return run_task(wb, key)


def test_gate_is_due_after_the_configured_rounds(wb):
    goal_and_route(wb)
    for _ in range(20):
        checkpoint(wb)
    from ai4math.review_gate import brief
    assert brief(wb)["due"] is True
    with pytest.raises(ResearchError, match="review gate is due"):
        task(wb)


def test_gate_review_clears_the_gate_and_records_metrics(wb):
    goal_and_route(wb)
    for _ in range(20):
        checkpoint(wb)
    record = review(wb, {"decision": "continue", "reason": "one more block",
                         "next_question": "does the block close?"}, "coordinator")
    assert record["rounds"] == 20
    assert record["metrics"]["obligation_rounds"] == 20
    assert wb.store.get("strategy_state", "main")["gate_rounds"] == 20
    wb.apply("task.add", {"id": "t2", "route": "route", "role": "explore",
                          "argv": [sys.executable, "-c", "print(2)"],
                          "timeout_seconds": 5, "output_limit_bytes": 1000})


def test_a_stagnant_obligation_trips_the_halt(wb):
    wb.store  # fixture
    wb.apply("node.add", {"id": "goal", "statement": "Exact goal", "domain": "all n",
                          "quantifier": "universal"})
    wb.apply("goal.set", {"node": "goal"})
    wb.apply("route.add", {"id": "route", "node": "goal", "rationale": "one mechanism",
                           "next_question": "close?", "stop_condition": "no progress",
                           "max_runs": 200, "stagnation_limit": 200})
    wb.apply("route.status", {"id": "route", "status": "active", "reason": "test"})
    for _ in range(12):
        checkpoint(wb, "no-progress")
    state = wb.store.get("strategy_state", "main")
    assert state["halt"]["reason"].startswith("obligation goal")
    with pytest.raises(ResearchError, match="halted"):
        task(wb)
    review(wb, {"decision": "switch", "reason": "new mechanism", "next_question": "does it hold?"}, "coordinator")
    assert "halt" not in wb.store.get("strategy_state", "main")


def test_pause_decision_keeps_the_halt(wb):
    goal_and_route(wb)
    for _ in range(20):
        checkpoint(wb)
    review(wb, {"decision": "pause", "reason": "hand to a human", "next_question": "resume when reviewed"}, "coordinator")
    with pytest.raises(ResearchError, match="halted"):
        task(wb, "t3")


def test_cli_review_gate(wb, tmp_path, capsys):
    goal_and_route(wb)
    for _ in range(20):
        checkpoint(wb)
    payload = tmp_path / "gate.json"
    payload.write_text(json.dumps({"decision": "continue", "reason": "proceed",
                                   "next_question": "next block?",
                                   "attestation": "I read the panel and the gap; continue one block."}), encoding="utf-8")
    capsys.readouterr()
    assert main(["--workspace", str(wb.store.root), "review-gate", "--file", str(payload)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["decision"] == "continue"


def test_falsified_needs_a_receipt(wb):
    goal_and_route(wb)
    with pytest.raises(ResearchError, match="failed probe"):
        wb.apply("route.checkpoint", {"route": "route", "finding": "failed estimate",
                                      "next_question": "next?", "outcome": "falsified"})


def test_gate_review_needs_an_independent_actor(wb):
    from ai4math.execution import run_task
    goal_and_route(wb)
    wb.apply("task.add", {"id": "work", "route": "route", "role": "explore",
                          "argv": [sys.executable, "-c", "print(1)"],
                          "timeout_seconds": 5, "output_limit_bytes": 1000}, "coordinator")
    run_task(wb, "work", "coordinator")
    for _ in range(20):
        checkpoint(wb)
    with pytest.raises(ResearchError, match="other than the last dispatcher"):
        review(wb, {"decision": "continue", "reason": "keep going",
                    "next_question": "next?"}, "coordinator")
    record = review(wb, {"decision": "continue", "reason": "reviewed independently",
                        "next_question": "next?"}, "independent-reviewer")
    assert record["decision"] == "continue"


def test_human_gate_review_needs_an_attestation(wb):
    goal_and_route(wb)
    for _ in range(20):
        checkpoint(wb)
    with pytest.raises(ResearchError, match="attestation"):
        review(wb, {"decision": "continue", "reason": "ok", "next_question": "next?"}, "human")
    review(wb, {"decision": "continue", "reason": "ok", "next_question": "next?",
                "attestation": "I read the panel and the open gap myself."}, "human")


def test_mechanism_depth_can_be_required(tmp_path):
    instance = Workbench.create(tmp_path / "workspace", "test", "Depth guard")
    try:
        instance.apply("strategy.configure", {"min_route_rounds": 3})
    except ResearchError:
        pass
    try:
        instance.apply("node.add", {"id": "goal", "statement": "Exact goal", "domain": "all n",
                                    "quantifier": "universal"})
        instance.apply("goal.set", {"node": "goal"})
        instance.apply("route.add", {"id": "first", "node": "goal", "rationale": "mechanism one",
                                     "next_question": "q", "stop_condition": "s",
                                     "max_runs": 50, "stagnation_limit": 50})
        instance.apply("route.status", {"id": "first", "status": "active", "reason": "test"})
        instance.apply("route.checkpoint", {"route": "first", "finding": "one round",
                                            "next_question": "q2", "outcome": "no-progress"})
        with pytest.raises(ResearchError, match="mechanism depth"):
            instance.apply("route.add", {"id": "second", "node": "goal", "rationale": "mechanism two",
                                         "next_question": "q", "stop_condition": "s",
                                         "max_runs": 50, "stagnation_limit": 50})
    finally:
        instance.store.close()

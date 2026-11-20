"""Uniform handoff records and the workspace survey."""
import json
import sys

import pytest

from ai4math.cli import main
from ai4math.handoff import read, write
from ai4math.store import ResearchError
from ai4math.workbench import Workbench
from ai4math.workspaces import survey


@pytest.fixture
def wb(tmp_path):
    instance = Workbench.create(tmp_path / "workspace", "test", "Handoff")
    yield instance
    instance.store.close()


def goal_and_route(wb):
    wb.apply("node.add", {"id": "goal", "statement": "Exact goal", "domain": "all n",
                          "quantifier": "universal"})
    wb.apply("goal.set", {"node": "goal"})
    wb.apply("route.add", {"id": "route", "node": "goal", "rationale": "one mechanism",
                           "next_question": "does it close?", "stop_condition": "no progress",
                           "max_runs": 20, "stagnation_limit": 20})
    wb.apply("route.status", {"id": "route", "status": "active", "reason": "test"})


def test_completed_task_writes_a_uniform_record(wb, tmp_path):
    from ai4math.execution import run_task
    goal_and_route(wb)
    wb.apply("task.add", {"id": "work", "route": "route", "role": "explore",
                          "argv": [sys.executable, "-c", "print(1)"],
                          "timeout_seconds": 10, "output_limit_bytes": 1000})
    run_task(wb, "work")
    record = read(wb.store, "work")
    assert record["schema"] == "handoff/v1"
    assert record["status"] == "completed"
    assert record["role"] == "explore"
    assert (wb.store.inside("tasks") / "work" / "handoff.json").is_file()


def test_failed_task_also_gets_a_record(wb):
    from ai4math.execution import run_task
    goal_and_route(wb)
    wb.apply("task.add", {"id": "bad", "route": "route", "role": "explore",
                          "argv": [sys.executable, "-c", "import sys; sys.exit(4)"],
                          "timeout_seconds": 10, "output_limit_bytes": 1000})
    run_task(wb, "bad")
    record = read(wb.store, "bad")
    assert record["status"] == "failed"


def test_legacy_layouts_are_normalised(wb):
    goal_and_route(wb)
    wb.apply("task.add", {"id": "legacy", "route": "route", "role": "produce",
                          "argv": [sys.executable, "-c", "print(1)"]})
    folder = wb.store.inside("tasks") / "legacy"
    (folder / "return").mkdir(parents=True, exist_ok=True)
    (folder / "return" / "return.json").write_text(json.dumps({
        "summary": "done", "suggested_evidence_level": "finite_support",
        "artifacts": ["b.txt", "a.txt"]}), encoding="utf-8")
    record = read(wb.store, "legacy")
    assert record["schema"] == "return/v1"
    assert record["artifacts"] == ["a.txt", "b.txt"]


def test_result_only_layout(wb):
    goal_and_route(wb)
    wb.apply("task.add", {"id": "checked", "route": "route", "role": "review",
                          "argv": [sys.executable, "-c", "print(1)"]})
    folder = wb.store.inside("tasks") / "checked"
    (folder).mkdir(parents=True, exist_ok=True)
    (folder / "result.json").write_text(json.dumps({"status": "passed", "summary": "ok"}), encoding="utf-8")
    record = read(wb.store, "checked")
    assert record["schema"] == "result/v1"
    assert record["status"] == "passed"


def test_missing_record_is_an_error(wb):
    goal_and_route(wb)
    wb.apply("task.add", {"id": "empty", "route": "route", "role": "produce",
                          "argv": [sys.executable, "-c", "print(1)"]})
    with pytest.raises(ResearchError, match="no handoff record"):
        read(wb.store, "empty")


def test_cli_handoff(wb, capsys):
    from ai4math.execution import run_task
    goal_and_route(wb)
    wb.apply("task.add", {"id": "printed", "route": "route", "role": "explore",
                          "argv": [sys.executable, "-c", "print(1)"],
                          "timeout_seconds": 10, "output_limit_bytes": 1000})
    run_task(wb, "printed")
    capsys.readouterr()
    assert main(["--workspace", str(wb.store.root), "handoff", "--task", "printed"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema"] == "handoff/v1"


def test_survey_flags_a_split_goal(tmp_path):
    root = tmp_path / "workspaces"
    root.mkdir()
    for name in ("one", "two", "three"):
        w = Workbench.create(root / name, name, "research " + name)
        statement = "Exact goal A" if name != "three" else "Exact goal B"
        w.apply("node.add", {"id": "goal", "statement": statement, "domain": "all n",
                             "quantifier": "universal"})
        w.apply("goal.set", {"node": "goal"})
        w.store.close()
    state = survey(root)
    assert len(state["workspaces"]) == 3
    assert state["split_goal"] is True
    names = sorted(next(iter(state["duplicate_goals"].values())))
    assert names == ["one", "two"]
    assert main(["--workspace", str(root / "one"), "workspaces", "--root", str(root), "--json"]) == 0

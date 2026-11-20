"""The correspondence gate keeps a drifting model tied to the locked statement."""
import json
import sys

import pytest

from ai4math.cli import main
from ai4math.correspondence import record, view
from ai4math.store import ResearchError
from ai4math.workbench import Workbench


@pytest.fixture
def wb(tmp_path):
    instance = Workbench.create(tmp_path / "workspace", "test", "Correspondence")
    yield instance
    instance.store.close()


def goal_and_route(wb):
    wb.apply("node.add", {"id": "goal", "statement": "Exact goal", "domain": "all n",
                          "quantifier": "universal"})
    wb.apply("goal.set", {"node": "goal"})
    wb.apply("route.add", {"id": "route", "node": "goal", "rationale": "two-wave model",
                           "next_question": "does it close?", "stop_condition": "no progress",
                           "max_runs": 200, "stagnation_limit": 200})
    wb.apply("route.status", {"id": "route", "status": "active", "reason": "test"})


def artifact(wb):
    path = wb.store.root / "mapping.md"
    path.write_text("model claim -> locked statement mapping", encoding="utf-8")
    return wb.apply("artifact.import", {"path": str(path), "purpose": "correspondence mapping"})["id"]


def checkpoint(wb):
    return wb.apply("route.checkpoint", {"route": "route", "finding": "local estimate",
                                         "next_question": "next?", "outcome": "no-progress"})


def mapping(wb, verdict="aligned", kind="model", **extra):
    payload = {"node": "goal", "verdict": verdict,
               "mapping": [{"model_claim": "two-wave pressure", "locked_target": "fixed-g critical count",
                            "status": "mapped" if verdict == "aligned" else "mismatch"}],
               "artifacts": [artifact(wb)], "reviewer_kind": kind}
    payload.update(extra)
    return payload


def test_new_mechanism_needs_a_mapping_after_six_rounds(wb):
    goal_and_route(wb)
    for _ in range(6):
        checkpoint(wb)
    with pytest.raises(ResearchError, match="correspondence gate"):
        wb.apply("route.add", {"id": "route2", "node": "goal", "rationale": "third mechanism",
                               "next_question": "q", "stop_condition": "s",
                               "max_runs": 50, "stagnation_limit": 50})
    record(wb, mapping(wb), "independent-reviewer")
    wb.apply("route.add", {"id": "route2", "node": "goal", "rationale": "third mechanism",
                           "next_question": "q", "stop_condition": "s",
                           "max_runs": 50, "stagnation_limit": 50})
    assert view(wb, "goal")["latest"]["verdict"] == "aligned"


def test_a_mismatch_trips_the_halt(wb):
    from ai4math.execution import run_task
    goal_and_route(wb)
    record(wb, mapping(wb, verdict="mismatch"), "independent-reviewer")
    state = wb.store.get("strategy_state", "main")
    assert "correspondence mismatch" in state["halt"]["reason"]
    wb.apply("task.add", {"id": "t", "route": "route", "role": "explore",
                          "argv": [sys.executable, "-c", "print(1)"],
                          "timeout_seconds": 5, "output_limit_bytes": 1000})
    with pytest.raises(ResearchError, match="halted"):
        run_task(wb, "t")


def test_human_mapping_needs_an_attestation(wb):
    goal_and_route(wb)
    with pytest.raises(ResearchError, match="attestation"):
        record(wb, mapping(wb, kind="human"), "human")
    record(wb, mapping(wb, kind="human", attestation="I checked the mapping against the locked statement."), "human")


def test_mapping_rows_are_validated(wb):
    goal_and_route(wb)
    with pytest.raises(ResearchError, match="nonempty mapping"):
        record(wb, {"node": "goal", "verdict": "aligned", "mapping": [], "artifacts": [artifact(wb)]}, "reviewer")
    with pytest.raises(ResearchError, match="status"):
        record(wb, {"node": "goal", "verdict": "aligned",
                    "mapping": [{"model_claim": "a", "locked_target": "b", "status": "fine"}],
                    "artifacts": [artifact(wb)]}, "reviewer")
    with pytest.raises(ResearchError, match="artifact"):
        record(wb, {"node": "goal", "verdict": "aligned", "mapping": [
            {"model_claim": "a", "locked_target": "b", "status": "mapped"}], "artifacts": []}, "reviewer")


def test_cli_correspondence(wb, tmp_path, capsys):
    goal_and_route(wb)
    payload = tmp_path / "corr.json"
    payload.write_text(json.dumps(mapping(wb)), encoding="utf-8")
    capsys.readouterr()
    assert main(["--workspace", str(wb.store.root), "correspondence", "--file", str(payload)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["verdict"] == "aligned"

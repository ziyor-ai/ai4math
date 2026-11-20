"""The health panel is read-only and reports the failure modes of long runs."""
import json
import sys

import pytest

from ai4math.cli import main
from ai4math.health import panel, snapshot
from ai4math.workbench import Workbench


@pytest.fixture
def wb(tmp_path):
    instance = Workbench.create(tmp_path / "workspace", "test", "Health panel")
    yield instance
    instance.store.close()


def goal_with_route(wb):
    wb.apply("node.add", {"id": "goal", "statement": "Exact goal", "domain": "all n",
                          "quantifier": "universal"})
    wb.apply("goal.set", {"node": "goal"})
    wb.apply("route.add", {"id": "route", "node": "goal", "rationale": "one mechanism",
                           "next_question": "does it close?", "stop_condition": "no progress",
                           "max_runs": 50, "stagnation_limit": 50})
    wb.apply("route.status", {"id": "route", "status": "active", "reason": "test"})


def checkpoint(wb, **extra):
    payload = {"route": "route", "finding": "one more local estimate", "next_question": "next?"}
    payload.update(extra)
    return wb.apply("route.checkpoint", payload)


def test_empty_workspace_has_no_signals(wb):
    wb.apply("node.add", {"id": "goal", "statement": "Exact goal", "domain": "all n",
                          "quantifier": "universal"})
    wb.apply("goal.set", {"node": "goal"})
    state = snapshot(wb)
    assert state["totals"]["obligation_rounds"] == 0
    assert state["signals"] == []
    assert "goal:" in panel(state)


def test_unreviewed_falsification_does_not_reset_the_drought(wb):
    goal_with_route(wb)
    for _ in range(3):
        checkpoint(wb, outcome="no-progress")
    state = snapshot(wb)
    assert state["obligations"][0]["since_claim"] == 3
    assert state["obligations"][0]["rounds"] == 3
    path = wb.store.root / "probe.txt"
    path.write_text("failed probe", encoding="utf-8")
    artifact = wb.apply("artifact.import", {"path": str(path), "purpose": "failed probe"})["id"]
    checkpoint(wb, outcome="falsified", artifacts=[artifact])
    assert snapshot(wb)["obligations"][0]["since_claim"] == 4


def test_long_stagnation_raises_a_stop_signal(wb):
    goal_with_route(wb)
    for _ in range(12):
        checkpoint(wb, outcome="no-progress")
    state = snapshot(wb)
    codes = {signal["code"] for signal in state["signals"]}
    assert "stagnation_limit" in codes


def test_round_records_feed_the_trend_section(wb, tmp_path):
    goal_with_route(wb)
    folder = wb.store.root / "reports" / "rounds"
    folder.mkdir(parents=True, exist_ok=True)
    for index, scope in enumerate(["short", "a much longer scope statement here"]):
        (folder / f"round{index}.json").write_text(json.dumps(
            {"scope": scope, "gaps": "gap", "sources": [{"url": str(i)} for i in range(index + 1)],
             "artifacts": [{"id": str(i)} for i in range(index + 3)]}), encoding="utf-8")
    state = snapshot(wb)
    assert state["records"]["files"] == 2
    assert state["records"]["artifacts"] == 3.5
    assert state["records"]["scope_trend"] == {"first": 5, "second": 34}


def test_cli_health_prints_json(wb, capsys):
    goal_with_route(wb)
    capsys.readouterr()
    assert main(["--workspace", str(wb.store.root), "health", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["totals"]["routes"] == 1
    assert main(["--workspace", str(wb.store.root), "health"]) == 0
    assert "obligation rounds" in capsys.readouterr().out


def test_depth_metrics_expose_mechanism_churn(wb):
    goal_with_route(wb)
    for index in range(10):
        wb.apply("route.add", {"id": f"r{index}", "node": "goal", "rationale": "another mechanism",
                               "next_question": "does it close?", "stop_condition": "no progress",
                               "max_runs": 5, "stagnation_limit": 5})
    state = snapshot(wb)
    assert state["depth"]["routes"] == 11
    assert state["depth"]["median_checkpoints"] == 0
    assert any(signal["code"] == "mechanism_churn" for signal in state["signals"])
    assert any(signal["code"] == "route_proliferation" for signal in state["signals"])

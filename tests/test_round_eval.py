"""The 8.3 checkpoint eval segment: record what changed, with frozen evidence."""
import pytest

from ai4math.store import ResearchError
from ai4math.workbench import Workbench


@pytest.fixture
def wb(tmp_path):
    w = Workbench.create(tmp_path / "ws", "eval", "Eval segment")
    w.apply("node.add", {"id": "goal", "statement": "Exact goal", "domain": "all n",
                         "quantifier": "universal"})
    w.apply("goal.set", {"node": "goal"})
    w.apply("strategy.configure", {"correspondence_after": 1000})
    w.apply("route.add", {"id": "route", "node": "goal", "rationale": "proof",
                          "next_question": "q", "stop_condition": "s",
                          "max_runs": 10, "stagnation_limit": 50})
    w.apply("route.status", {"id": "route", "status": "active", "reason": "test"})
    path = w.store.root / "note.md"
    path.write_text("evidence note", encoding="utf-8")
    w.apply("artifact.import", {"path": str(path), "purpose": "note"}, "author")
    yield w
    w.store.close()


def artifact_id(w):
    return next(a["id"] for a in w.store.all("artifact"))


def attempt(w, ident="r1", **extra):
    payload = {"round": ident, "finding": "f", "next_question": "q"}
    payload.update(extra)
    return w.apply("round.record_attempt", payload, "coordinator")


def test_an_eval_segment_is_stored_on_the_checkpoint(wb):
    wb.apply("round.open", {"id": "r1", "route": "route"}, "coordinator")
    checkpoint = attempt(wb, eval={"obstacle_effect": "weakened"})
    assert checkpoint["eval"] == {"obstacle_effect": "weakened"}


def test_an_unknown_effect_is_refused(wb):
    wb.apply("round.open", {"id": "r1", "route": "route"}, "coordinator")
    with pytest.raises(ResearchError, match="obstacle_effect"):
        attempt(wb, eval={"obstacle_effect": "solved"})


def test_a_resolved_obstacle_needs_frozen_evidence(wb):
    wb.apply("round.open", {"id": "r1", "route": "route"}, "coordinator")
    with pytest.raises(ResearchError, match="needs evidence_refs"):
        attempt(wb, eval={"obstacle_effect": "resolved"})
    with pytest.raises(ResearchError, match="not a registered object"):
        attempt(wb, eval={"obstacle_effect": "resolved", "evidence_refs": ["made-up"]})
    checkpoint = attempt(wb, eval={"obstacle_effect": "resolved",
                                   "evidence_refs": [artifact_id(wb)]})
    assert checkpoint["eval"]["evidence_refs"] == [artifact_id(wb)]


def test_prediction_match_and_critique_are_checked(wb):
    wb.apply("round.open", {"id": "r1", "route": "route"}, "coordinator")
    with pytest.raises(ResearchError, match="prediction_match"):
        attempt(wb, eval={"prediction_match": "maybe"})
    with pytest.raises(ResearchError, match="missing critique"):
        attempt(wb, eval={"critique_ref": "no-such-critique"})
    checkpoint = attempt(wb, eval={"prediction_match": "untested"})
    assert checkpoint["eval"]["prediction_match"] == "untested"


def test_an_empty_eval_is_refused(wb):
    wb.apply("round.open", {"id": "r1", "route": "route"}, "coordinator")
    with pytest.raises(ResearchError, match="nonempty object"):
        attempt(wb, eval={})
    with pytest.raises(ResearchError, match="needs obstacle_effect"):
        attempt(wb, eval={"note": "nothing relevant"})


def test_settlement_carries_the_eval_into_the_route_checkpoint(wb):
    wb.apply("round.open", {"id": "r1", "route": "route"}, "coordinator")
    attempt(wb, eval={"obstacle_effect": "weakened"})
    wb.apply("round.settle", {"round": "r1"}, "coordinator")
    latest = wb.store.get("route", "route")["checkpoints"][-1]
    assert latest["eval"] == {"obstacle_effect": "weakened"}

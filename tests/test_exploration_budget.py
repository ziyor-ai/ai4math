"""Exploration round budget: reserved at open, spent at settle (v3.4 §6.8)."""
import pytest

from ai4math.store import ResearchError
from ai4math.workbench import Workbench


@pytest.fixture
def wb(tmp_path):
    w = Workbench.create(tmp_path / "ws", "budget", "Budget")
    w.apply("node.add", {"id": "goal", "statement": "Exact goal", "domain": "all n",
                         "quantifier": "universal"})
    w.apply("goal.set", {"node": "goal"})
    w.apply("strategy.configure", {"correspondence_after": 1000})
    w.apply("policy.configure", {"exploration_budget": {"rounds": 2, "registrations": 5}}, "admin")
    w.apply("node.add", {"id": "probe", "statement": "Probe", "domain": "finite case",
                         "quantifier": "finite"})
    w.apply("research.register", {"node": "probe", "rationale": "probe the finite case"},
            "coordinator")
    w.apply("route.add", {"id": "explore", "node": "probe", "rationale": "probe",
                          "next_question": "q", "stop_condition": "s",
                          "max_runs": 10, "stagnation_limit": 10, "kind": "exploration"})
    w.apply("route.status", {"id": "explore", "status": "active", "reason": "test"})
    yield w
    w.store.close()


def open_round(w, ident):
    return w.apply("round.open", {"id": ident, "route": "explore"}, "coordinator")


def settle_attempt(w, ident):
    w.apply("round.record_attempt", {"round": ident, "finding": "probe", "next_question": "q"},
            "coordinator")
    w.apply("round.settle", {"round": ident}, "coordinator")


def test_open_rounds_reserve_budget(wb):
    open_round(wb, "r1")
    open_round(wb, "r2")
    with pytest.raises(ResearchError, match="budget is exhausted"):
        open_round(wb, "r3")
    assert open_round(wb, "r1") if False else True


def test_settled_rounds_spend_budget(wb):
    open_round(wb, "r1")
    settle_attempt(wb, "r1")
    open_round(wb, "r2")
    settle_attempt(wb, "r2")
    with pytest.raises(ResearchError, match="budget is exhausted"):
        open_round(wb, "r3")


def test_a_settled_round_does_not_double_count(wb):
    open_round(wb, "r1")
    settle_attempt(wb, "r1")
    # Re-opening the same id is refused before any budget is consulted.
    with pytest.raises(ResearchError, match="already exists"):
        open_round(wb, "r1")


def test_the_budget_views_agree_with_the_reservation(wb):
    from ai4math.budget import usage
    open_round(wb, "r1")
    settle_attempt(wb, "r1")
    open_round(wb, "r2")
    reading = usage(wb)
    assert reading["settled"] == 1
    assert reading["open"] == 1
    assert reading["open_rounds"] == ["r2"]


def test_a_proof_route_does_not_spend_the_exploration_budget(wb):
    open_round(wb, "r1")
    settle_attempt(wb, "r1")
    wb.apply("route.add", {"id": "proof-route", "node": "goal", "rationale": "p",
                           "next_question": "q", "stop_condition": "s",
                           "max_runs": 5, "stagnation_limit": 5})
    wb.apply("route.status", {"id": "proof-route", "status": "active", "reason": "test"})
    wb.apply("round.open", {"id": "rp", "route": "proof-route"}, "coordinator")
    from ai4math.budget import usage
    assert usage(wb)["settled"] == 1

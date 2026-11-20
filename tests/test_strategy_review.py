"""Batch 8.0: a strategy/direction review role that is not an acceptance gate."""
import pytest

from ai4math import identities, review
from ai4math.store import ResearchError
from ai4math.workbench import Workbench


@pytest.fixture
def research(tmp_path):
    w = Workbench.create(tmp_path / "ws", "strategy-review", "Strategy review fixture")
    w.apply("node.add", {"id": "goal", "statement": "Fixed statement", "domain": "n>=1",
                         "quantifier": "universal"})
    w.apply("goal.set", {"node": "goal"})
    w.apply("route.add", {"id": "route", "node": "goal", "rationale": "One direction",
                          "next_question": "Does it hold?", "stop_condition": "No progress",
                          "max_runs": 3, "stagnation_limit": 2})
    w.apply("route.status", {"id": "route", "status": "active", "reason": "fixture"})
    w.apply("task.add", {"id": "task", "route": "route", "role": "explore",
                         "argv": ["python", "-c", "pass"]})
    with w.store.transaction("identity.enroll", "test"):
        identities.enroll(w, "critic", "model")
    yield w
    w.store.close()


def payload(**overrides):
    base = {"verdict": "pass", "basis": "the round answers its recorded intent",
            "unresolved": [],
            "rubric": {"intent": "clear", "connection": "clear", "evidence": "clear",
                       "stop_condition": "clear"}}
    base.update(overrides)
    return base


def rec(w, **overrides):
    """Record inside one transaction, the way the CLI and apply() do."""
    body = payload(**overrides)
    with w.store.transaction("round_review.record", "critic"):
        return review.record(w, "task", review.CRITIQUE_ROLE, body["verdict"], "artifact-sha",
                             "checked the four rubric items", "critic", body)


def test_a_strategy_review_can_be_recorded(research):
    record = rec(research)
    assert record["role"] == "strategy"
    assert record["verdict"] == "pass"


def test_strategy_review_requires_a_basis(research):
    with pytest.raises(ResearchError, match="nonempty basis"):
        rec(research, basis="")


def test_strategy_review_requires_the_full_rubric(research):
    with pytest.raises(ResearchError, match="rubric items"):
        rec(research, rubric={"intent": "clear"})


def test_a_blocked_item_cannot_pass(research):
    with pytest.raises(ResearchError, match="blocked rubric"):
        rec(research, rubric={"intent": "blocked", "connection": "clear",
                              "evidence": "clear", "stop_condition": "clear"})


def test_uncertainty_is_allowed_with_a_named_gap(research):
    record = rec(research, verdict="fail", unresolved=["no frozen artifact for the bound"],
                 rubric={"intent": "clear", "connection": "open",
                         "evidence": "blocked", "stop_condition": "clear"})
    assert record["verdict"] == "fail"
    assert record["role"] == "strategy"


def test_the_strategy_role_is_not_an_acceptance_gate(research):
    rec(research)
    # Only adversarial + verify decide `passed`; a lone strategy review cannot close a round.
    assert review.passed(research, "task") is False
    assert set(review.status(research, "task")) == set(review.ROLES)

"""Bounded scout contracts, scoped review, and offline expansion comparisons."""
import json
import sys

import pytest

from ai4math import identities, review, strategy
from ai4math.checkpoint import export_checkpoint, restore_checkpoint
from ai4math.execution import export_task, run_task
from ai4math.store import ResearchError
from ai4math.workbench import Workbench


LIMITS = {"timeout_seconds": 30, "output_limit_bytes": 4096}
DOMAIN = "Fixture cover cases 1 to 4"
DECISIONS = {"success": "request_verification", "failure": "refute_hypothesis",
             "unknown": "reassess_or_pause"}


def setup(path):
    w = Workbench.create(path, "remainder", "Bounded remainder fixture")
    w.apply("node.add", {"id": "goal", "statement": "Locked target", "domain": "fixture", "quantifier": "universal"})
    w.apply("goal.set", {"node": "goal"})
    w.apply("approach.add", {"id": "approach", "node": "goal", "approach": "Isolate the cover condition",
                             "family": "cover", "bridge_to_goal": {"mode": "entailment", "statement": "Cover first"}}, "worker")
    w.apply("approach.review", {"id": "approach", "verdict": "selected", "reason": "Test the cover"}, "reviewer")
    w.apply("approach.activate", {"id": "approach"})
    w.apply("obstacle.add", {"id": "O", "node": "goal", "kind": "unproved_dependency", "description": "Cover condition"})
    w.apply("route.add", {"id": "route", "node": "goal", "rationale": "Cover", "next_question": "Cover?",
                         "stop_condition": "Probe once per batch", "max_runs": 8, "stagnation_limit": 8})
    w.apply("route.status", {"id": "route", "status": "active", "reason": "Fixture"})
    choose(w, "D1", "switch")
    return w


def choose(w, name, choice="continue"):
    return w.apply("strategy.decide", {"id": name, "brief_sha256": strategy.brief(w)["brief_sha256"],
                   "decision": choice, "next_route": "route", "next_question": "Test coverage",
                   "reason": "Proceed with scoped trial"}, "coordinator")


def intent(w, name, **changes):
    payload = {"id": name, "approach": "approach", "kind": "probe", "question": "Is the cover complete?",
               "connection_to_goal": "The cover condition is needed for goal", "stop_condition": "Finish the fixed sample",
               "predicted": [{"observe": label, "decision": value} for label, value in DECISIONS.items()]}
    return w.apply("research_intent.add", {**payload, **changes}, "coordinator")


def scout_intent(w, name, **changes):
    return intent(w, name, scout={"obstacle": "O", "goal_interface": "Cover implies the bridge",
                                 "fragile_condition": "Every case is accounted for", "input_domain": "Cases 1 and 2",
                                 "verification": "Compare the witness against the fixture",
                                 "decisions": DECISIONS, "budget": LIMITS, **changes})


def task(w, name, key, **changes):
    payload = {"id": name, "route": "route", "role": "explore", "intent": key,
               "argv": [sys.executable, "-c", "import json; from pathlib import Path; Path('result.json').write_text(json.dumps(dict(observed='fixture witness')))"],
               **LIMITS}
    return w.apply("task.add", {**payload, **changes}, "worker")


def settle_scout(w, name, prediction="matched"):
    scout_intent(w, name + "-intent")
    w.apply("round.open", {"id": name + "-round", "route": "route"})
    task(w, name, name + "-intent", round=name + "-round")
    assert run_task(w, name, "worker")["status"] == "completed"
    w.apply("round.assess", {"round": name + "-round", "finding": "Fixture probe result", "next_question": "Check the remainder",
                            "task_assessments": [{"task_id": name, "execution_status": "completed", "scientific_outcome": "inconclusive"}],
                            "eval": {"intent": name + "-intent", "prediction_match": prediction}}, "reviewer")
    w.apply("round.settle", {"round": name + "-round"}, "coordinator")


def opinions(w, scout):
    artifacts = []
    entries = [("adversarial", {"failures": [], "strongest_unchecked_assumption": "Coverage"}),
               ("strategy", {"basis": "Coverage and budget reviewed", "rubric": dict.fromkeys(review.CRITIQUE_ITEMS, "clear")})]
    for role, payload in entries:
        actor = "r-" + role
        path = w.store.root / (role + ".txt")
        approval = {"kind": "domain_scan", "scout_task": scout, "input_domain": DOMAIN, "budget": LIMITS}
        path.write_text(json.dumps(approval) if role == "strategy" else f"Fixture review {scout} {role}", encoding="utf-8")
        artifact = w.apply("artifact.import", {"path": str(path), "purpose": "Synthetic review fixture"}, actor)["id"]
        with w.store.transaction("fixture.review", actor):
            identities.enroll(w, actor, "model")
            review.record(w, scout, role, "pass", artifact, "Fixture review", actor, payload)
        artifacts.append(artifact)
    return artifacts


def test_scout_needs_three_distinct_decisions_and_freezes_limits(tmp_path):
    w = setup(tmp_path / "w")
    try:
        with pytest.raises(ResearchError, match="three distinct"):
            scout_intent(w, "bad", decisions={**DECISIONS, "unknown": DECISIONS["failure"]})
        scout_intent(w, "scout")
        with pytest.raises(ResearchError, match="fit the bounded probe"):
            task(w, "over", "scout", timeout_seconds=None)
        assert w.store.get("research_intent", "scout")["consumed_by"] is None
        task(w, "bounded", "scout")
        contract = export_task(w, "bounded")["contract"]
        assert contract["probe_plan"]["scout"]["decisions"] == DECISIONS
        assert contract["timeout_seconds"] == LIMITS["timeout_seconds"]
    finally:
        w.store.close()


@pytest.mark.parametrize("scenario", ["unrun", "untested", "stale_decision", "missing_review", "unfrozen_review",
                                     "changed_budget", "approved"])
def test_expansion_offline_pair(tmp_path, scenario):
    """Compare admission across equally anchored, independently restored workspaces."""
    source = setup(tmp_path / "source")
    try:
        if scenario == "unrun":
            scout_intent(source, "scout-intent")
            task(source, "scout", "scout-intent")
        else:
            settle_scout(source, "scout", "untested" if scenario == "untested" else "matched")
        inputs = opinions(source, "scout") if scenario not in {"unrun", "missing_review"} else []
        if scenario not in {"unrun", "stale_decision"}:
            choose(source, "D2")
        cutoff = export_checkpoint(source, tmp_path / ("prefix-" + scenario), source.audit()["events"])
    finally:
        source.store.close()
    if scenario == "unfrozen_review":
        inputs = inputs[:1]
    for arm in ("baseline", "candidate"):
        destination = tmp_path / arm
        restored = restore_checkpoint(cutoff["path"], destination, cutoff["manifest_sha256"], actor="offline-replay")
        assert restored["parent_tail"] == cutoff["event_tail"] and restored["audit"]["passed"]
        w = Workbench(destination)
        try:
            changes = {} if arm == "baseline" else {"expansion": {"kind": "domain_scan", "scout_task": "scout",
                       "decision": "D2", "input_domain": DOMAIN,
                       "budget": {**LIMITS, "timeout_seconds": 60} if scenario == "changed_budget" else LIMITS}}
            intent(w, "expand", kind="exploration", **changes)
            if arm == "candidate" and scenario != "approved":
                with pytest.raises(ResearchError, match={"unrun": "completed and settled", "untested": "prediction assessed",
                                   "stale_decision": "new current strategy", "missing_review": "round_review",
                                   "unfrozen_review": "freeze both scout review", "changed_budget": "match the frozen strategy review"}[scenario]):
                    task(w, "expand", "expand", inputs=inputs)
                assert not w.store.exists("task", "expand")
            else:
                task(w, "expand", "expand", inputs=inputs)
                contract = export_task(w, "expand")["contract"]
                if changes:
                    assert contract["probe_plan"]["expansion"]["scout_round"] == "scout-round"
                else:
                    assert "probe_plan" not in contract
            assert w.node_status("goal") == "open"
        finally:
            w.store.close()


def test_two_decision_batches_at_one_obstacle_request_review_once(tmp_path):
    w = setup(tmp_path / "w")
    try:
        settle_scout(w, "s1")
        settle_scout(w, "s2")
        assert not w.store.all("generation_request")
        choose(w, "D2")
        settle_scout(w, "s3")
        request, = w.store.all("generation_request")
        assert (request["obstacle_id"], request["trigger_reason"]) == ("O", "bottleneck_no_decision_delta")
        w.apply("round.settle", {"round": "s3-round"})
        assert len(w.store.all("generation_request")) == 1
        assert w.node_status("goal") == "open"
    finally:
        w.store.close()

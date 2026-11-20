from concurrent.futures import ThreadPoolExecutor
import sys
from threading import Barrier

import pytest

from ai4math import health, obligation, review_gate, store, strategy
from ai4math.execution import run_task
from ai4math.store import Failpoint, ResearchError
from ai4math.workbench import Workbench


def node(w, key):
    return w.apply("node.add", {"id": key, "statement": "Statement for " + key,
                                "domain": "synthetic integers", "quantifier": "universal"})


def route(w, key, target="goal", **kwargs):
    w.apply("route.add", {"id": key, "node": target, "kind": "proof", "rationale": "Test a construction",
                          "next_question": "Can the construction be checked?", "stop_condition": "Bound exhausted",
                          "max_runs": 9, "stagnation_limit": 9, **kwargs})
    w.apply("route.status", {"id": key, "status": "active", "reason": "Synthetic trial"})


def choose(w, key, selection, mode="direct"):
    brief = strategy.brief(w)
    return w.apply("strategy.decide", {"id": key, "brief_sha256": brief["brief_sha256"],
                                      "decision": "continue" if selection == brief["current_route"] else "switch",
                                      "next_route": selection, "reason": "Review the next trial",
                                      "next_question": "Test the exact scope",
                                      "research_steps": {selection: {"mode": mode, "probe": "Check the witness",
                                                                     "falsifier": "Witness violates a hypothesis"}}})


def task(w, key, selection="r9", code="print(1)", approach="approach"):
    intent = key + "-intent"
    w.apply("research_intent.add", {"id": intent, "approach": approach, "question": "Can it lift?",
                                    "connection_to_goal": "The scope is goal", "decisive": False,
                                    "stop_condition": "One probe"}, "coordinator")
    w.apply("round.open", {"id": key + "-round", "route": selection})
    return w.apply("task.add", {"id": key, "role": "explore", "route": selection, "round": key + "-round",
                                "intent": intent, "argv": [sys.executable, "-c", code]})


def settle(w, key, state="completed"):
    w.apply("round.assess", {"round": key + "-round", "finding": "Probe readback",
                             "next_question": "Inspect the missing hypothesis",
                             "task_assessments": [{"task_id": key, "execution_status": state,
                                                   "scientific_outcome": "inconclusive" if state == "completed" else "not_assessed"}]})
    return w.apply("round.settle", {"round": key + "-round"})


@pytest.fixture
def workspace(tmp_path):
    w = Workbench.create(tmp_path / "research", "fixture", "Research lifecycle", review_interval=99)
    node(w, "goal")
    w.apply("goal.set", {"node": "goal"})
    w.apply("strategy.configure", {"obligation_stagnation_limit": 1, "correspondence_after": 999})
    w.apply("approach.add", {"id": "approach", "node": "goal", "approach": "Check a bounded witness",
                             "family": "toy", "bridge_to_goal": {"mode": "entailment", "statement": "One witness"}}, "author")
    w.apply("approach.review", {"id": "approach", "verdict": "selected", "reason": "Test fixture"}, "reviewer")
    w.apply("approach.activate", {"id": "approach"})
    try:
        yield w
    finally:
        store.disarm()
        w.store.close()


def test_legacy_migration_replays_actual_starts_and_preserves_original_events(workspace):
    w = workspace
    route(w, "r9")
    w.apply("task.add", {"id": "earlier", "role": "explore", "route": "r9", "argv": [sys.executable, "-c", "print(1)"]})
    run_task(w, "earlier", "coordinator")
    earlier = w.store.get("task", "earlier")
    events = w.store.export_events()
    w.apply("policy.migrate", {"to": "v2c4"}, "admin")
    assert obligation.view(w, "goal")["runs_by_role"] == {"explore": 1}
    assert w.store.get("task", "earlier") == earlier
    assert w.store.export_events()[:len(events)] == events
    assert w.store.audit()["passed"]


def test_predecessor_and_gate_lease_preserve_running_and_closure_counts(workspace):
    w = workspace
    w.apply("policy.migrate", {"to": "v2c4"}, "admin")
    route(w, "r9")
    route(w, "r99")
    choose(w, "first", "r9")
    task(w, "one")
    assert run_task(w, "one", "coordinator")["status"] == "completed"
    settlement = settle(w, "one")
    assert w.apply("round.settle", {"round": "one-round"})["id"] == settlement["id"]
    route(w, "r100", continuation_of="r9")
    assert w.store.get("route", "r100")["continuation_of"] == "r9"
    assert "continuation_of" not in w.store.get("route", "r99")
    choose(w, "switch", "r100")
    task(w, "two", "r100", "raise SystemExit(1)")
    with pytest.raises(ResearchError, match="halted"):
        run_task(w, "two", "coordinator")
    review_gate.review(w, {"decision": "switch", "reason": "Try one decisive probe", "next_question": "Check witness",
                           "next_action": {"kind": "switch", "node": "goal"},
                           "continuation": {"node": "goal", "max_runs": 1}}, "reviewer")
    assert run_task(w, "two", "coordinator")["status"] == "failed"
    settle(w, "two", "failed")
    row = obligation.view(w, "goal")
    assert (row["runs"], row["rounds"], row["closures"], row["rounds_since_closure"]) == (2, 2, 0, 2)
    assert review_gate.halted(w)
    assert health.snapshot(w)["obligations"][0]["rounds_since_closure"] == 2


def test_replan_invalidates_prepared_research_without_spending_budget(workspace):
    w = workspace
    w.apply("policy.migrate", {"to": "v2c4"}, "admin")
    route(w, "r9")
    with pytest.raises(ResearchError, match="register proof obligations"):
        choose(w, "no-plan", "r9", "obligation")
    choose(w, "first", "r9", "bootstrap")
    task(w, "before")
    node(w, "lemma")
    w.apply("node.plan", {"node": "goal", "proof_dependencies": ["lemma"], "reason": "New required premise"})
    with pytest.raises(ResearchError, match="stale strategy or proof basis"):
        run_task(w, "before", "coordinator")
    assert w.node_status("goal") == "open"
    assert obligation.view(w, "goal")["runs"] == 0


def test_start_transaction_rolls_back_and_concurrent_retry_counts_once(workspace):
    w = workspace
    w.apply("policy.migrate", {"to": "v2c4"}, "admin")
    route(w, "r9")
    choose(w, "first", "r9")
    task(w, "probe")
    store.arm("before_commit")
    with pytest.raises(Failpoint):
        run_task(w, "probe", "coordinator")
    store.disarm()
    assert obligation.view(w, "goal")["runs"] == 0
    barrier = Barrier(2)

    def attempt():
        handle = Workbench(w.store.root)
        try:
            barrier.wait(timeout=10)
            return run_task(handle, "probe", "coordinator")["status"]
        except ResearchError:
            return "refused"
        finally:
            handle.store.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(lambda _: attempt(), range(2))) == ["completed", "refused"]
    assert obligation.view(w, "goal")["runs"] == 1
    assert [row["id"] for row in w.store.all("task_dispatch")] == ["probe"]


@pytest.mark.parametrize("revoke_before_settlement", [False, True])
def test_verified_leaf_closes_once_while_the_composition_is_still_open(workspace, revoke_before_settlement):
    w = workspace
    w.apply("policy.migrate", {"to": "v2c4"}, "admin")
    lemma = node(w, "lemma")
    w.apply("node.plan", {"node": "goal", "proof_dependencies": ["lemma"], "reason": "First prove the leaf"})
    w.apply("approach.add", {"id": "leaf-approach", "node": "lemma", "approach": "Prove the leaf",
                             "family": "toy", "bridge_to_goal": {"mode": "entailment", "statement": "One premise"}}, "author")
    w.apply("approach.review", {"id": "leaf-approach", "verdict": "selected", "reason": "Test fixture"}, "reviewer")
    w.apply("approach.activate", {"id": "leaf-approach"})
    route(w, "r9", "lemma")
    choose(w, "verify-leaf", "r9", "obligation")
    task(w, "probe", approach="leaf-approach")
    result = run_task(w, "probe", "coordinator")
    proof = result["output_artifacts"][0]
    w.apply("claim.add", {"id": "leaf-proof", "node": "lemma", "statement_digest": lemma["statement_digest"],
                          "evidence_level": "proved", "scope": "universal", "proof_route": "direct",
                          "artifact_ids": [proof], "summary": "Synthetic proof fixture"}, "author")
    evidence = {"round": "probe-round", "finding": "Leaf proof", "next_question": "Check implication to goal",
                "claims": ["leaf-proof"], "task_assessments": [{"task_id": "probe", "execution_status": "completed",
                    "scientific_outcome": "proved", "evidence_refs": ["leaf-proof"]}]}
    with pytest.raises(ResearchError, match="must be accepted"):
        w.apply("round.assess", evidence)
    w.apply("validation.add", {"id": "attestation", "claim": "leaf-proof", "method": "human_proof",
                               "trust_basis": "Synthetic human-attestation fixture", "artifact_ids": [proof],
                               "attestation": "I checked the exact statement, domain, dependencies and attached argument"},
            "fixture-attestor")
    w.apply("review.add", {"id": "review", "claim": "leaf-proof", "reviewer": "reviewer", "reviewer_kind": "model",
                           "auditor": "auditor", "coverage_complete": True, "issues": [],
                           "verdict": "ready_for_human"}, "auditor")
    w.apply("claim.accept", {"claim": "leaf-proof", "review": "review", "validation": "attestation"}, "custodian")
    w.apply("round.assess", evidence)
    if revoke_before_settlement:
        w.apply("claim.revoke", {"claim": "leaf-proof", "reason": "Premise withdrawn during review"}, "custodian")
    w.apply("round.settle", {"round": "probe-round"})
    assert w.node_status("lemma") == ("open" if revoke_before_settlement else "proved")
    assert w.node_status("goal") == "open"
    account = obligation.view(w, "lemma")
    if revoke_before_settlement:
        assert w.store.get("round_checkpoint", "cp-probe-round")["claims"] == ["leaf-proof"]
        assert w.store.get("round_settled", "probe-round")["invalidated_claims"] == ["leaf-proof"]
        assert (account["closures"], account["rounds_since_closure"]) == (0, 1)
    else:
        assert (account["closures"], account["rounds_since_closure"]) == (1, 0)

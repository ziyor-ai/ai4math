import json
import sys

import pytest

from ai4math.cli import main
from ai4math.execution import run_task
from ai4math.goal_impact import view
from ai4math.store import ResearchError
from ai4math.strategy import brief
from ai4math.workbench import Workbench


@pytest.fixture
def workspace(tmp_path):
    w = Workbench.create(tmp_path / "workspace", "impact", "Goal impact", external_research=False)
    node(w, "statement-context")
    node(w, "left")
    node(w, "right")
    node(w, "goal", proof_dependencies=["left", "right"], statement_dependencies=["statement-context"])
    w.apply("goal.set", {"node": "goal"})
    yield w
    w.store.close()


def node(w, ident, **fields):
    return w.apply("node.add", {"id": ident, "statement": ident, "domain": "all n", "quantifier": "universal", **fields})


def artifact(w, name):
    path = w.store.root / (name + ".txt")
    path.write_text("Synthetic evidence for " + name, encoding="utf-8")
    return w.apply("artifact.import", {"path": str(path), "purpose": name})["id"]


def accept(w, node_id, claim_id, *, level="proved", method="human_proof", **fields):
    aid = artifact(w, claim_id)
    claim = w.apply("claim.add", {"id": claim_id, "node": node_id,
                                  "statement_digest": w.store.get("node", node_id)["statement_digest"],
                                  "evidence_level": level, "scope": "universal", "summary": "Fixture",
                                  "proof_route": "direct", "artifact_ids": [aid], **fields}, "producer")
    vid = claim_id + "-validation"
    if level in {"proved", "disproved"}:
        if method == "human_proof":
            w.apply("validation.add", {"id": vid, "claim": claim_id, "method": method,
                                       "trust_basis": "attestation fixture", "artifact_ids": [aid],
                                       "attestation": "I checked the exact statement, domain, dependencies and attached argument"}, "validator")
        else:
            policy = w.store.all("checker_policy")
            validation = {"id": vid, "claim": claim_id, "method": method, "passed": True,
                          "trust_class": "declared", "trust": "declared_external_checker", "artifact_ids": [aid]}
            if policy:
                validation.update(trust_class="trusted", trust="trusted", checker_artifact=policy[0]["checker_artifact"],
                                  environment={"lean_version": "fixture", "mathlib_revision": "fixture"})
            with w.store.transaction("fixture.validation", "validator"):
                w.store.put("validation", vid, validation, new=True)
    review = w.apply("review.add", {"id": claim_id + "-review", "claim": claim_id, "reviewer": "reviewer",
                                     "reviewer_kind": "model", "auditor": "auditor", "coverage_complete": True,
                                     "issues": [], "verdict": "ready_for_human"}, "auditor")
    request = {"claim": claim["id"], "review": review["id"]}
    if level in {"proved", "disproved"}:
        request["validation"] = vid
    return w.apply("claim.accept", request, "custodian")


def route(w, node_id):
    ident = "route-" + node_id
    w.apply("route.add", {"id": ident, "node": node_id, "rationale": "fixture", "next_question": "exact scope",
                          "stop_condition": "fixture", "max_runs": 10, "stagnation_limit": 4})
    w.apply("route.status", {"id": ident, "status": "active", "reason": "fixture"})
    return ident


def decide(w, ident, route_id):
    return w.apply("strategy.decide", {"id": ident, "brief_sha256": brief(w)["brief_sha256"],
                                       "decision": "switch", "reason": "fixture", "next_question": "exact scope",
                                       "next_route": route_id}, "coordinator")


def test_exact_closure_does_not_follow_from_finite_support_or_statement_context(workspace):
    w = workspace
    start = w.store.last_seq()
    accept(w, "right", "finite", level="finite_support", scope="finite", evidence_domain="n=1")
    accept(w, "statement-context", "standalone-definition-proof")
    accept(w, "left", "left-proof")
    impact = view(w, from_event=start)
    assert impact["validated_discharged_ids"] == ["left"]
    assert impact["remaining_obligation_ids"] == ["goal", "right"]
    assert impact["before"]["seq"] == start
    assert impact["after"]["seq"] == w.store.last_seq()
    assert {row["id"] for row in impact["obligations"]} == {"goal", "left", "right"}


def test_trusted_refutation_is_a_blocker_not_a_proved_prerequisite(workspace):
    w = workspace
    checker = artifact(w, "checker")
    w.apply("checker.policy.add", {"id": "policy", "checker_artifact": checker})
    start = w.store.last_seq()
    accept(w, "right", "witness", level="disproved", scope="finite", method="counterexample")
    impact = view(w, from_event=start)
    assert impact["refuted_ids"] == ["right"]
    assert impact["validated_discharged_ids"] == []
    assert "right" in impact["remaining_obligation_ids"]


def test_trusted_composition_and_revocation_update_historical_state(workspace):
    w = workspace
    start = w.store.last_seq()
    left = accept(w, "left", "left-proof")
    right = accept(w, "right", "right-proof")
    checker = artifact(w, "checker")
    certificate = artifact(w, "case-certificate")
    w.apply("checker.policy.add", {"id": "policy", "checker_artifact": checker})
    w.apply("inference.add", {"id": "composition", "rule": "case_split", "parent": "goal",
                              "required_branches": ["left", "right"],
                              "branch_proofs": {"left": left["id"], "right": right["id"]},
                              "verification": {"basis": "formal_kernel", "certificate_ref": certificate,
                                               "environment_digest": "fixture", "checker_artifact": checker}})
    accept(w, "goal", "goal-proof", method="formal_kernel", proof_route="composition",
           dependencies=[left["id"], right["id"]])
    impact = view(w, from_event=start)
    assert impact["validated_discharged_ids"] == ["goal", "left", "right"]
    goal = next(row for row in impact["obligations"] if row["id"] == "goal")
    assert goal["evidence"][0]["inference_id"] == "composition"
    w.apply("claim.revoke", {"claim": left["id"], "reason": "fixture revocation"})
    impact = view(w, from_event=impact["after"]["seq"])
    assert impact["reopened_ids"] == ["goal", "left"]
    assert impact["remaining_obligation_ids"] == ["goal", "left"]


def test_counterfactual_does_not_close_parents_or_record_new_claims(workspace):
    w = workspace
    chosen = decide(w, "choose-left", route(w, "left"))
    before = w.store.last_seq()
    impact = view(w)
    assert impact["basis_decision"]["id"] == chosen["id"]
    assert impact["basis_decision"]["event"] == before
    assert impact["before"]["seq"] == before - 1
    assert impact["counterfactual"]["cases"][0]["remaining_in_recorded_plan"] == ["goal", "right"]
    assert impact["remaining_obligation_ids"] == ["goal", "left", "right"]
    assert w.store.last_seq() == before
    assert not w.store.all("claim")


def test_goal_impact_cli_preserves_the_strategy_brief_digest(workspace, capsys):
    w = workspace
    first = brief(w)
    start = w.store.last_seq()
    assert main(["--workspace", str(w.store.root), "strategy-brief", "--goal-impact",
                 "--impact-from-event", str(start)]) == 0
    output = json.loads(capsys.readouterr().out)
    report = output.pop("goal_impact")
    assert output == first
    assert report["before"]["seq"] == start == report["after"]["seq"] == w.store.last_seq()
    assert main(["--workspace", str(w.store.root), "strategy-brief", "--impact-from-event", str(start)]) == 2
    assert "requires --goal-impact" in capsys.readouterr().err


def test_replan_and_settled_rounds_are_anchored_to_the_event_window(workspace):
    w = workspace
    route_id = route(w, "left")
    decision = decide(w, "choose-left", route_id)
    w.apply("round.open", {"id": "r1", "route": route_id})
    w.apply("task.add", {"id": "t1", "route": route_id, "round": "r1", "role": "explore",
                         "argv": [sys.executable, "-c", "print('offline')"]})
    run_task(w, "t1", "runner")
    w.apply("round.assess", {"round": "r1", "finding": "A fixture completed", "next_question": "Compare",
                             "task_assessments": [{"task_id": "t1", "execution_status": "completed",
                                                   "scientific_outcome": "inconclusive"}]})
    w.apply("round.settle", {"round": "r1"})
    node(w, "new-dependency")
    w.apply("node.plan", {"node": "goal", "proof_dependencies": ["left", "new-dependency"], "reason": "fixture"})
    impact = view(w)
    assert impact["before"]["proof_plan_sha256"] != impact["after"]["proof_plan_sha256"]
    assert impact["new_open_obligation_ids"] == ["new-dependency"]
    assert impact["removed_from_plan_ids"] == ["right"]
    assert impact["settled_rounds"][0]["decision_ids"] == [decision["id"]]
    assert impact["settled_rounds"][0]["completed_task_ids"] == ["t1"]
    assert not impact["decision_changes"]


def test_out_of_bounds_event_cutoff_is_refused(workspace):
    with pytest.raises(ResearchError, match="beyond the workspace"):
        view(workspace, from_event=workspace.store.last_seq() + 1)


def test_bottleneck_identity_requires_an_explicit_link_across_nodes(workspace):
    w = workspace
    for ident, key in (("obstacle-left", "left"), ("obstacle-right", "right")):
        w.apply("obstacle.add", {"id": ident, "node": key, "description": "The same label",
                                "kind": "unproved_dependency"})
    for ident, key in (("original", "left"), ("representation", "right")):
        w.apply("approach.add", {"id": ident, "node": key, "approach": ident, "family": "fixture",
                                "core_obstacle": "obstacle-left", "bridge_to_goal": {"mode": "equivalence"}})
    def bottlenecks():
        return {row["id"]: row["approaches"] for row in view(w)["open_bottlenecks"]}

    assert bottlenecks() == {"obstacle-left": [{"id": "original", "node": "left"}], "obstacle-right": []}
    mapping = artifact(w, "explicit-mapping")
    w.apply("correspondence.record", {"id": "exact-map", "node": "left", "verdict": "aligned",
                                      "mapping": [{"model_claim": "right", "locked_target": "obstacle-left",
                                                   "status": "mapped"}], "artifacts": [mapping]}, "reviewer")
    assert bottlenecks()["obstacle-left"] == [{"id": "original", "node": "left"},
                                               {"id": "representation", "node": "right", "correspondence": "exact-map",
                                                "artifact_ids": [mapping]}]

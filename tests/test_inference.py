"""Batch 8.0 slice 7: a composition claim needs a verified derivation, not just proofs."""
import json
import sys

import pytest

from ai4math.store import ResearchError, now
from ai4math.workbench import Workbench


@pytest.fixture
def wb(tmp_path):
    w = Workbench.create(tmp_path / "ws", "inference", "Inference closure")
    w.apply("node.add", {"id": "goal", "statement": "Exact goal", "domain": "all n",
                         "quantifier": "universal"})
    w.apply("goal.set", {"node": "goal"})
    w.apply("strategy.configure", {"correspondence_after": 1000})
    w.apply("route.add", {"id": "route", "node": "goal", "rationale": "proof",
                          "next_question": "q", "stop_condition": "s",
                          "max_runs": 50, "stagnation_limit": 50})
    w.apply("route.status", {"id": "route", "status": "active", "reason": "test"})
    for branch in ("b1", "b2"):
        w.apply("node.add", {"id": branch, "statement": "Branch " + branch, "domain": "all n",
                             "quantifier": "universal"})
    w.apply("node.plan", {"node": "goal", "proof_dependencies": ["b1", "b2"], "reason": "case split"})
    yield w
    w.store.close()


def artifact(w, name, text):
    path = w.store.root / name
    path.write_text(text, encoding="utf-8")
    return w.apply("artifact.import", {"path": str(path), "purpose": name})["id"]


def accepted_claim(w, node, claim_id, dependencies=None, route="composition"):
    digest = w.store.get("node", node)["statement_digest"]
    w.apply("claim.add", {"id": claim_id, "node": node, "statement_digest": digest,
                          "evidence_level": "proved", "scope": "universal", "summary": "proof",
                          "proof_route": route, "dependencies": dependencies or []}, "author")
    with w.store.transaction("fixture.accept_inputs", "test"):
        w.store.put("validation", claim_id + "-val",
                    {"id": claim_id + "-val", "claim": claim_id, "method": "formal_kernel",
                     "passed": True, "artifact_ids": [], "trust": "declared_external_checker",
                     "trust_basis": "fixture", "validator": "val"}, new=True)
        w.store.put("review", claim_id + "-rev",
                    {"id": claim_id + "-rev", "claim": claim_id, "verdict": "ready_for_human",
                     "reviewer": "rev", "auditor": "aud", "review_order": 1,
                     "created_at": "2030-01-01T00:00:00+00:00", "issues": [],
                     "coverage_complete": True}, new=True)
    return w.apply("claim.accept", {"claim": claim_id, "review": claim_id + "-rev",
                                    "validation": claim_id + "-val"}, "custodian")


def inference(w, iid, proofs, **overrides):
    payload = {"id": iid, "rule": "case_split", "parent": "goal",
               "required_branches": ["b1", "b2"], "branch_proofs": proofs,
               "review": {"model_verdict": "plausible"}}
    payload.update(overrides)
    return w.apply("inference.add", payload, "author")


def goal_claim(w, cid="g-claim", dependencies=None):
    digest = w.store.get("node", "goal")["statement_digest"]
    w.apply("claim.add", {"id": cid, "node": "goal", "statement_digest": digest,
                          "evidence_level": "proved", "scope": "universal", "summary": "by cases",
                          "proof_route": "composition", "dependencies": dependencies or []}, "author")
    with w.store.transaction("fixture.goal_inputs", "test"):
        w.store.put("validation", cid + "-val",
                    {"id": cid + "-val", "claim": cid, "method": "formal_kernel",
                     "passed": True, "artifact_ids": [], "trust": "declared_external_checker",
                     "trust_basis": "fixture", "validator": "val"}, new=True)
        w.store.put("review", cid + "-rev",
                    {"id": cid + "-rev", "claim": cid, "verdict": "ready_for_human",
                     "reviewer": "rev", "auditor": "aud", "review_order": 1,
                     "created_at": "2030-01-01T00:00:00+00:00", "issues": [],
                     "coverage_complete": True}, new=True)
    return cid


def accept_goal(w, cid):
    return w.apply("claim.accept", {"claim": cid, "review": cid + "-rev",
                                    "validation": cid + "-val"}, "custodian")


def test_branch_proofs_must_cover_every_branch(wb):
    accepted_claim(wb, "b1", "c1")
    accepted_claim(wb, "b2", "c2")
    with pytest.raises(ResearchError, match="do not cover"):
        inference(wb, "INF-1", {"b1": "c1"})


def test_a_branch_proof_must_be_an_accepted_claim(wb):
    digest = wb.store.get("node", "b1")["statement_digest"]
    wb.apply("claim.add", {"id": "pending", "node": "b1", "statement_digest": digest,
                           "evidence_level": "proved", "scope": "universal", "summary": "s"},
             "author")
    accepted_claim(wb, "b2", "c2")
    with pytest.raises(ResearchError, match="not accepted"):
        inference(wb, "INF-1", {"b1": "pending", "b2": "c2"})


def test_review_alone_is_only_a_candidate_and_cannot_close_the_parent(wb):
    accepted_claim(wb, "b1", "c1")
    accepted_claim(wb, "b2", "c2")
    record = inference(wb, "INF-1", {"b1": "c1", "b2": "c2"})
    assert record["verification"]["status"] == "reviewed_candidate"
    cid = goal_claim(wb, dependencies=["c1", "c2"])
    with pytest.raises(ResearchError, match="unverified entailment: INF-1"):
        accept_goal(wb, cid)


def test_a_certificated_inference_allows_acceptance(wb):
    accepted_claim(wb, "b1", "c1")
    accepted_claim(wb, "b2", "c2")
    certificate = artifact(wb, "cert.json", json.dumps({"checks": ["cases covered"]}))
    record = inference(wb, "INF-1", {"b1": "c1", "b2": "c2"}, verification={
        "basis": "formal_kernel", "certificate_ref": certificate,
        "environment_digest": "lean-4.34.1/mathlib-d13f23b7"})
    assert record["verification"]["status"] == "verified"
    cid = goal_claim(wb, dependencies=["c1", "c2"])
    assert accept_goal(wb, cid)["status"] == "accepted"


def test_a_flaw_verdict_keeps_the_inference_unverified(wb):
    accepted_claim(wb, "b1", "c1")
    accepted_claim(wb, "b2", "c2")
    certificate = artifact(wb, "cert.json", "{}")
    record = inference(wb, "INF-1", {"b1": "c1", "b2": "c2"},
                       review={"model_verdict": "flaw_found"},
                       verification={"basis": "formal_kernel", "certificate_ref": certificate,
                                     "environment_digest": "env"})
    assert record["verification"]["status"] == "unverified"


def test_a_verified_inference_needs_a_certificate(wb):
    accepted_claim(wb, "b1", "c1")
    accepted_claim(wb, "b2", "c2")
    with pytest.raises(ResearchError, match="certificate_ref"):
        inference(wb, "INF-1", {"b1": "c1", "b2": "c2"},
                  verification={"basis": "formal_kernel"})


def test_a_nested_unverified_inference_blocks_the_parent(wb):
    # b1 is itself a composition with an unverified derivation.
    for leaf in ("l1", "l2"):
        wb.apply("node.add", {"id": leaf, "statement": "Leaf " + leaf, "domain": "all n",
                              "quantifier": "universal"})
    wb.apply("node.plan", {"node": "b1", "proof_dependencies": ["l1", "l2"], "reason": "split"})
    accepted_claim(wb, "l1", "cl1")
    accepted_claim(wb, "l2", "cl2")
    wb.apply("inference.add", {"id": "INF-B1", "rule": "case_split", "parent": "b1",
                               "required_branches": ["l1", "l2"],
                               "branch_proofs": {"l1": "cl1", "l2": "cl2"},
                               "review": {"model_verdict": "plausible"}}, "author")
    # b1 itself is proved directly, so its own claim does not need the derivation;
    # the unverified INF-B1 still poisons any parent that composes through b1.
    c1 = accepted_claim(wb, "b1", "c1", route="direct")
    accepted_claim(wb, "b2", "c2")
    certificate = artifact(wb, "cert.json", "{}")
    inference(wb, "INF-1", {"b1": "c1", "b2": "c2"}, verification={
        "basis": "formal_kernel", "certificate_ref": certificate, "environment_digest": "env"})
    cid = goal_claim(wb, dependencies=["c1", "c2"])
    with pytest.raises(ResearchError, match="unverified entailment: INF-B1"):
        accept_goal(wb, cid)


def test_without_any_inference_the_legacy_path_still_accepts(wb):
    accepted_claim(wb, "b1", "c1")
    accepted_claim(wb, "b2", "c2")
    cid = goal_claim(wb, dependencies=["c1", "c2"])
    assert accept_goal(wb, cid)["status"] == "accepted"


def test_a_policy_requires_a_recorded_inference(wb):
    accepted_claim(wb, "b1", "c1")
    accepted_claim(wb, "b2", "c2")
    checker = artifact(wb, "checker.py", "print('ok')\n")
    # Isolate the inference rule from the trusted-validation rule of slice 6.
    wb.apply("checker.policy.add", {"id": "P", "checker_artifact": checker,
                                    "require_trusted": False}, "admin")
    cid = goal_claim(wb, dependencies=["c1", "c2"])
    with pytest.raises(ResearchError, match="needs a recorded inference"):
        accept_goal(wb, cid)


def test_a_verified_bridge_promotes_a_registered_candidate(wb):
    wb.apply("node.add", {"id": "h2", "statement": "Candidate", "domain": "all n",
                          "quantifier": "universal"})
    wb.apply("research.register", {"node": "h2", "rationale": "bridge candidate"})
    certificate = artifact(wb, "bridge.json", "{}")
    record = wb.apply("inference.add", {"id": "BR-1", "rule": "bridge", "parent": "goal",
                                        "dep": "h2",
                                        "verification": {"basis": "formal_kernel",
                                                         "certificate_ref": certificate,
                                                         "environment_digest": "env"}}, "author")
    assert record["verification"]["status"] == "verified"
    planned = wb.apply("node.plan", {"node": "goal", "proof_dependencies": ["h2"],
                                     "reason": "verified bridge"})
    assert planned["proof_dependencies"] == ["h2"]

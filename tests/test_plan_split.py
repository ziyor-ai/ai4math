"""Batch 8.0 slice 5: candidate dependencies stay out of the formal cone; direct proofs."""
import sys

import pytest

from ai4math import scope
from ai4math.store import ResearchError
from ai4math.workbench import Workbench


@pytest.fixture
def wb(tmp_path):
    w = Workbench.create(tmp_path / "ws", "plan", "Plan split")
    w.apply("node.add", {"id": "goal", "statement": "Exact goal", "domain": "all n",
                         "quantifier": "universal"})
    w.apply("goal.set", {"node": "goal"})
    w.apply("strategy.configure", {"correspondence_after": 1000})
    w.apply("node.add", {"id": "h2", "statement": "Candidate representation", "domain": "all n",
                         "quantifier": "universal"})
    w.apply("research.register", {"node": "h2", "rationale": "a speculative bridge"})
    yield w
    w.store.close()


def add_node(w, node_id):
    return w.apply("node.add", {"id": node_id, "statement": "Node " + node_id,
                                "domain": "all n", "quantifier": "universal"})


def test_node_propose_records_a_candidate_without_touching_the_plan(wb):
    add_node(wb, "bridge")
    node = wb.apply("node.propose", {"node": "h2", "proposed_dependencies": ["bridge"],
                                     "reason": "bridge might connect the representation"})
    assert node["proposed_dependencies"] == ["bridge"]
    assert node["proof_dependencies"] == []
    # One explicit hop from a registered candidate is dispatchable exploration,
    # but it never enters the goal's proof cone.
    assert scope.node_scope(wb, "bridge") == "exploration"
    assert "bridge" not in scope.proof_cone(wb)


def test_proposing_between_two_cone_nodes_is_refused(wb):
    add_node(wb, "lemma")
    wb.apply("node.plan", {"node": "goal", "proof_dependencies": ["lemma"], "reason": "plan"})
    with pytest.raises(ResearchError, match="already in the proof cone"):
        wb.apply("node.propose", {"node": "goal", "proposed_dependencies": ["lemma"],
                                  "reason": "redundant"})


def test_planning_a_research_candidate_keeps_it_proposed(wb):
    node = wb.apply("node.plan", {"node": "goal", "proof_dependencies": ["h2"],
                                  "reason": "try the speculative bridge"})
    assert node["proof_dependencies"] == []
    assert node["proposed_dependencies"] == ["h2"]
    assert "proposed_dependencies" in node["plan_notice"]
    assert scope.node_scope(wb, "h2") == "exploration"


def test_planning_a_brand_new_node_still_bootstraps_the_dag(wb):
    add_node(wb, "fresh")
    node = wb.apply("node.plan", {"node": "goal", "proof_dependencies": ["fresh"],
                                  "reason": "top-down decomposition"})
    assert node["proof_dependencies"] == ["fresh"]
    assert scope.node_scope(wb, "fresh") == "proof"


def test_a_verified_bridge_promotes_the_candidate(wb):
    with wb.store.transaction("fixture.inference", "test"):
        wb.store.put("inference", "inf1", {"id": "inf1", "parent": "goal", "dep": "h2",
                                           "rule": "bridge",
                                           "verification": {"status": "verified"}}, new=True)
    node = wb.apply("node.plan", {"node": "goal", "proof_dependencies": ["h2"],
                                  "reason": "the bridge is verified"})
    assert node["proof_dependencies"] == ["h2"]
    assert scope.node_scope(wb, "h2") == "proof"


def test_an_unverified_bridge_does_not_promote(wb):
    with wb.store.transaction("fixture.inference", "test"):
        wb.store.put("inference", "inf1", {"id": "inf1", "parent": "goal", "dep": "h2",
                                           "rule": "bridge",
                                           "verification": {"status": "reviewed_candidate"}},
                     new=True)
    node = wb.apply("node.plan", {"node": "goal", "proof_dependencies": ["h2"],
                                  "reason": "only reviewed"})
    assert node["proof_dependencies"] == []
    assert node["proposed_dependencies"] == ["h2"]


def test_claim_route_must_be_known(wb):
    node = wb.store.get("node", "goal")
    with pytest.raises(ResearchError, match="proof_route"):
        wb.apply("claim.add", {"id": "c1", "node": "goal", "statement_digest": node["statement_digest"],
                               "evidence_level": "proved", "scope": "universal",
                               "summary": "s", "proof_route": "guess"}, "author")


def _accept_fixture(w, claim_id, node_id):
    """Insert the review and validation rows _accept needs, without a task round."""
    with w.store.transaction("fixture.accept_inputs", "test"):
        w.store.put("validation", claim_id + "-val",
                    {"id": claim_id + "-val", "claim": claim_id, "method": "formal_kernel",
                     "passed": True, "artifact_ids": [], "trust": "declared_external_checker",
                     "validator": "val"}, new=True)
        w.store.put("review", claim_id + "-rev",
                    {"id": claim_id + "-rev", "claim": claim_id, "verdict": "ready_for_human",
                     "reviewer": "rev", "auditor": "aud", "review_order": 1,
                     "created_at": "2030-01-01T00:00:00+00:00", "issues": [],
                     "coverage_complete": True}, new=True)


def test_a_direct_claim_is_accepted_without_binding_the_planned_dependencies(wb):
    node = wb.store.get("node", "goal")
    add_node(wb, "lemma")
    wb.apply("node.plan", {"node": "goal", "proof_dependencies": ["lemma"],
                           "reason": "the planned decomposition"})
    wb.apply("claim.add", {"id": "direct", "node": "goal", "statement_digest": node["statement_digest"],
                           "evidence_level": "proved", "scope": "universal", "summary": "full proof",
                           "proof_route": "direct"}, "author")
    _accept_fixture(wb, "direct", "goal")
    accepted = wb.apply("claim.accept", {"claim": "direct", "review": "direct-rev",
                                         "validation": "direct-val"}, "custodian")
    assert accepted["status"] == "accepted"
    assert accepted["proof_route"] == "direct"


def test_a_composition_claim_must_bind_the_planned_dependencies(wb):
    node = wb.store.get("node", "goal")
    add_node(wb, "lemma")
    wb.apply("node.plan", {"node": "goal", "proof_dependencies": ["lemma"],
                           "reason": "the planned decomposition"})
    wb.apply("claim.add", {"id": "comp", "node": "goal", "statement_digest": node["statement_digest"],
                           "evidence_level": "proved", "scope": "universal", "summary": "by lemma",
                           "proof_route": "composition"}, "author")
    _accept_fixture(wb, "comp", "goal")
    with pytest.raises(ResearchError, match="must bind each planned proof dependency"):
        wb.apply("claim.accept", {"claim": "comp", "review": "comp-rev",
                                  "validation": "comp-val"}, "custodian")


def test_a_direct_claim_survives_a_later_plan_change(wb):
    node = wb.store.get("node", "goal")
    add_node(wb, "lemma")
    wb.apply("node.plan", {"node": "goal", "proof_dependencies": ["lemma"], "reason": "plan"})
    wb.apply("claim.add", {"id": "direct2", "node": "goal", "statement_digest": node["statement_digest"],
                           "evidence_level": "proved", "scope": "universal", "summary": "full proof",
                           "proof_route": "direct"}, "author")
    _accept_fixture(wb, "direct2", "goal")
    wb.apply("node.plan", {"node": "goal", "proof_dependencies": [], "reason": "simplified"})
    accepted = wb.apply("claim.accept", {"claim": "direct2", "review": "direct2-rev",
                                         "validation": "direct2-val"}, "custodian")
    assert accepted["status"] == "accepted"

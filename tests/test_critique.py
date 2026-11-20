"""Batch 8.3: blinded critique, obstacle evidence states, optional sketch."""
import sys

import pytest

from ai4math import critique as critique_module
from ai4math.store import ResearchError
from ai4math.workbench import Workbench


@pytest.fixture
def wb(tmp_path):
    w = Workbench.create(tmp_path / "ws", "v83", "Critique")
    w.apply("node.add", {"id": "goal", "statement": "Exact goal", "domain": "all n",
                         "quantifier": "universal"})
    w.apply("goal.set", {"node": "goal"})
    w.apply("strategy.configure", {"correspondence_after": 1000})
    w.apply("node.add", {"id": "h2", "statement": "Candidate", "domain": "all n",
                         "quantifier": "universal"})
    w.apply("research.register", {"node": "h2", "rationale": "candidate"})
    w.apply("approach.add", {"id": "S-1", "node": "h2", "approach": "weighted cutoff",
                             "family": "harmonic-analysis",
                             "bridge_to_goal": {"mode": "sufficient_condition"}}, "author")
    yield w
    w.store.close()


def critique(w, cid="CR-1", **overrides):
    payload = {"id": cid, "approach": "S-1", "verdict": "plausible",
               "bundle": "A weighted cutoff bounds the residual by the source norm.",
               "reason": "the mechanism is specific and falsifiable",
               "reviewer": "reviewer-1", "auditor": "auditor-1", "actor": "coordinator"}
    payload.update(overrides)
    return w.apply("critique.add", payload, "coordinator")


def test_a_blind_bundle_must_not_name_the_candidate(wb):
    with pytest.raises(ResearchError, match="blind bundle names"):
        critique(wb, bundle="Approach S-1 bounds the residual by the source norm.")


def test_the_node_id_is_also_stripped(wb):
    with pytest.raises(ResearchError, match="blind bundle names"):
        critique(wb, bundle="For node h2 a weighted cutoff bounds the residual.")


def test_unknown_reviewed_verdict_is_refused(wb):
    with pytest.raises(ResearchError, match="verdict must be one of"):
        critique(wb, verdict="looks_fine")


def test_i_do_not_know_is_a_first_class_verdict(wb):
    row = critique(wb, verdict="insufficient_evidence",
                   gaps=["no frozen measurement of the cutoff constant"])
    assert row["verdict"] == "insufficient_evidence"
    assert row["gaps"] == ["no frozen measurement of the cutoff constant"]
    assert wb.store.get("approach", "S-1")["status"] == "candidate"


def test_insufficient_evidence_must_name_the_gap(wb):
    with pytest.raises(ResearchError, match="must name the missing evidence"):
        critique(wb, verdict="insufficient_evidence")


def test_requires_verification_must_name_the_evidence(wb):
    with pytest.raises(ResearchError, match="must name the required evidence"):
        critique(wb, verdict="requires_verification")
    row = critique(wb, verdict="requires_verification",
                   required_evidence=["an interval-arithmetic certificate for k=8"])
    assert row["required_evidence"] == ["an interval-arithmetic certificate for k=8"]


def test_a_plausible_critique_promotes_a_candidate(wb):
    critique(wb)
    assert wb.store.get("approach", "S-1")["status"] == "reviewed"


def test_a_duplicate_verdict_rejects_the_candidate(wb):
    critique(wb, verdict="duplicate")
    assert wb.store.get("approach", "S-1")["status"] == "rejected"


def test_unresolved_conflict_forces_an_explicit_doubt_verdict(wb):
    conflicts = [{"item": "bridge to the goal", "positions": ["entails", "only suggests"],
                  "resolved": False}]
    with pytest.raises(ResearchError, match="unresolved conflict"):
        critique(wb, conflicts=conflicts)
    row = critique(wb, verdict="unresolved", conflicts=conflicts)
    assert row["conflicts"][0]["resolved"] is False


def test_up_to_three_distinct_alternatives(wb):
    alternatives = [
        {"mechanism": "spectral projector bound", "differs_from": "weighted cutoff",
         "first_probe": "compute the first eigenvalue gap"},
        {"mechanism": "duhamel formula", "differs_from": "weighted cutoff",
         "first_probe": "bound the integral kernel"},
    ]
    row = critique(wb, alternatives=alternatives)
    assert len(row["alternatives"]) == 2
    with pytest.raises(ResearchError, match="0..3 alternatives"):
        critique(wb, cid="CR-2", alternatives=alternatives * 2)
    with pytest.raises(ResearchError, match="structurally different"):
        critique(wb, cid="CR-3", alternatives=[alternatives[0], dict(alternatives[0])])


def test_obstacle_evidence_is_reported_not_verified(wb):
    path = wb.store.root / "neg.txt"
    path.write_text("the bound fails at k=16", encoding="utf-8")
    artifact = wb.apply("artifact.import", {"path": str(path), "purpose": "negative result"})["id"]
    wb.apply("obstacle.add", {"id": "O-1", "node": "h2", "kind": "mathematical_refutation",
                              "description": "bound fails"}, "author")
    row = wb.apply("obstacle.evidence", {"id": "O-1",
                                         "evidence": {"artifact": artifact}}, "author")
    assert row["verification_status"] == "reported"
    assert row["evidence"]["artifact"] == artifact
    disputed = wb.apply("obstacle.dispute", {"id": "O-1", "reason": "the run used the wrong grid"},
                        "author")
    assert disputed["verification_status"] == "disputed"


def test_a_sketch_must_state_its_gaps(wb):
    with pytest.raises(ResearchError, match="must list its gaps"):
        wb.apply("sketch.add", {"id": "K-1", "approach": "S-1", "content": "outline"}, "author")
    row = wb.apply("sketch.add", {"id": "K-1", "approach": "S-1", "content": "outline",
                                  "gaps": [{"missing": "the cutoff bound",
                                            "why_load_bearing": "the whole estimate rests on it",
                                            "next_step": "prove it for k=8"}]}, "author")
    assert len(row["gaps"]) == 1


def test_a_sketch_reference_must_exist(wb):
    with pytest.raises(ResearchError, match="missing"):
        wb.apply("sketch.add", {"id": "K-1", "approach": "S-1", "content": "outline",
                                "references": [{"kind": "node", "id": "nope"}],
                                "gaps": [{"missing": "x", "why_load_bearing": "y",
                                          "next_step": "z"}]}, "author")


def test_a_sketch_is_not_evidence(wb):
    wb.apply("sketch.add", {"id": "K-1", "approach": "S-1", "content": "outline",
                            "gaps": [{"missing": "x", "why_load_bearing": "y",
                                      "next_step": "z"}]}, "author")
    assert wb.store.all("claim") == []
    assert critique_module.view(wb, "S-1")["total"] == 0

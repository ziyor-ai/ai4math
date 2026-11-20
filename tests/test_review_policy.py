"""Opt-in review policies: on-demand literature and an adversarial reviewer."""
import sys

import pytest

from ai4math.store import ResearchError
from ai4math.workbench import Workbench


@pytest.fixture
def wb(tmp_path):
    instance = Workbench.create(tmp_path / "workspace", "test", "Policies")
    yield instance
    instance.store.close()


def goal(wb):
    wb.apply("node.add", {"id": "goal", "statement": "Exact goal", "domain": "all n",
                          "quantifier": "universal"})
    wb.apply("goal.set", {"node": "goal"})


def artifact(wb, tmp_path, name="argument.txt"):
    path = tmp_path / name
    path.write_text("argument", encoding="utf-8")
    return wb.apply("artifact.import", {"path": str(path), "purpose": "argument"})["id"]


def claim(wb, art):
    node = wb.store.get("node", "goal")
    return wb.apply("claim.add", {"id": "c1", "node": "goal", "statement_digest": node["statement_digest"],
                                  "evidence_level": "proved", "scope": "universal",
                                  "summary": "bound statement", "artifact_ids": [art]}, "author")


def validation(wb, art):
    wb.apply("validation.add", {"id": "v1", "claim": "c1", "method": "human_proof",
                                "trust_basis": "synthetic attestation",
                                "attestation": "I checked the exact statement, domain, dependencies and attached argument",
                                "artifact_ids": [art]}, "validator")


def review(wb, key="r1", stance=None, reviewer="reviewer"):
    payload = {"id": key, "claim": "c1", "reviewer": reviewer, "reviewer_kind": "model",
               "auditor": "auditor", "coverage_complete": True, "issues": [], "verdict": "ready_for_human"}
    if stance:
        payload["stance"] = stance
    return wb.apply("review.add", payload, "auditor")


def test_adversarial_review_is_opt_in(wb, tmp_path):
    art = artifact(wb, tmp_path)
    goal(wb)
    claim(wb, art)
    validation(wb, art)
    review(wb)
    wb.apply("claim.accept", {"claim": "c1", "review": "r1", "validation": "v1"}, "custodian")


def test_adversarial_review_can_be_required(wb, tmp_path):
    art = artifact(wb, tmp_path)
    goal(wb)
    wb.apply("strategy.configure", {"adversarial_review": True})
    claim(wb, art)
    validation(wb, art)
    review(wb)
    with pytest.raises(ResearchError, match="adversarial review required"):
        wb.apply("claim.accept", {"claim": "c1", "review": "r1", "validation": "v1"}, "custodian")
    review(wb, "r2", stance="adversarial", reviewer="opponent")
    wb.apply("claim.accept", {"claim": "c1", "review": "r2", "validation": "v1"}, "custodian")


def test_on_demand_literature_requires_a_stated_need(wb):
    from ai4math.literature import check_dispatch
    goal(wb)
    wb.apply("strategy.configure", {"literature_mode": "on-demand"})
    with pytest.raises(ResearchError, match="literature.need"):
        check_dispatch(wb, {"id": "t", "role": "explore", "literature": {"need": "  "}}, None, False, "coordinator")
    with pytest.raises(ResearchError, match="literature.need"):
        check_dispatch(wb, {"id": "t", "role": "explore", "literature": {}}, None, False, "coordinator")


def test_configure_validates_the_new_settings(wb):
    goal(wb)
    with pytest.raises(ResearchError, match="literature_mode"):
        wb.apply("strategy.configure", {"literature_mode": "sometimes"})
    with pytest.raises(ResearchError, match="adversarial_review"):
        wb.apply("strategy.configure", {"adversarial_review": "yes"})

"""Level 1 findings: explicit content, evidence boundary, never a proof (v3.5)."""
import sys

import pytest

from ai4math import findings
from ai4math.store import ResearchError
from ai4math.workbench import Workbench


@pytest.fixture
def wb(tmp_path):
    w = Workbench.create(tmp_path / "ws", "findings", "Findings")
    w.apply("node.add", {"id": "goal", "statement": "Exact goal", "domain": "all n",
                         "quantifier": "universal"})
    w.apply("goal.set", {"node": "goal"})
    w.apply("strategy.configure", {"correspondence_after": 1000})
    path = w.store.root / "note.md"
    path.write_text("the bridge needs a transfer lemma", encoding="utf-8")
    w.apply("artifact.import", {"path": str(path), "purpose": "note"}, "author")
    yield w
    w.store.close()


def finding(w, fid="F-1", **overrides):
    payload = {"id": fid, "node": "goal", "kind": "obstacle_identified",
               "statement": "the direct upper bound is not reachable; a transfer lemma is missing",
               "scope": "fixed g, fixed n, current locked conditions",
               "evidence_kind": "theoretical_argument",
               "evidence_refs": [next(a["id"] for a in w.store.all("artifact"))],
               "relation": {"target": "goal", "type": "load_bearing"},
               "next_action": {"kind": "prove_transfer", "question": "does H stay bounded on the window?"}}
    payload.update(overrides)
    return w.apply("finding.add", payload, "author")


def test_a_finding_is_recorded_unverified(wb):
    row = finding(wb)
    assert row["verification"] == {"status": "unverified", "basis": None, "ref": None}
    assert findings.checked(row) is True
    assert wb.store.all("claim") == []


def test_a_finding_needs_math_content_and_a_next_action(wb):
    with pytest.raises(ResearchError, match="statement"):
        finding(wb, statement="")
    with pytest.raises(ResearchError, match="next_action"):
        finding(wb, next_action={"kind": "prove"})

def test_a_finding_cannot_claim_verification(wb):
    with pytest.raises(ResearchError, match="keeps findings unverified"):
        finding(wb, verification={"status": "verified"})


def test_the_relation_target_must_exist(wb):
    with pytest.raises(ResearchError, match="missing node"):
        finding(wb, relation={"target": "nope", "type": "load_bearing"})


def test_evidence_must_be_a_frozen_object(wb):
    with pytest.raises(ResearchError, match="not a frozen, registered object"):
        finding(wb, evidence_refs=["made-up"])
    sha = next(a["id"] for a in wb.store.all("artifact"))
    assert finding(wb, fid="F-2", evidence_refs=[sha])["evidence_refs"] == [sha]


def test_a_finding_is_not_a_proof_dependency(wb):
    row = finding(wb)
    digest = wb.store.get("node", "goal")["statement_digest"]
    with pytest.raises(ResearchError, match="not a proof dependency"):
        wb.apply("claim.add", {"id": "c1", "node": "goal", "statement_digest": digest,
                               "evidence_level": "proved", "scope": "universal",
                               "summary": "s", "dependencies": [row["id"]]}, "author")


def test_only_checked_findings_count_as_progress(wb):
    assert findings.checked(finding(wb, fid="F-1")) is True
    speculative = finding(wb, fid="F-2", relation={"target": "goal", "type": "speculative_bridge"})
    assert findings.checked(speculative) is False
    conjecture = finding(wb, fid="F-3", evidence_kind="conjecture")
    assert findings.checked(conjecture) is False


def test_revise_updates_the_next_action_only(wb):
    row = finding(wb)
    revised = wb.apply("finding.revise", {"id": row["id"],
                                          "next_action": {"kind": "refine", "question": "which window?"}},
                       "author")
    assert revised["next_action"]["question"] == "which window?"
    assert revised["statement"] == row["statement"]
    assert revised["verification"]["status"] == "unverified"


def test_a_finding_cannot_be_attached_to_another_nodes_task(wb):
    wb.apply("node.add", {"id": "other", "statement": "Other", "domain": "all n",
                          "quantifier": "universal"})
    wb.apply("node.plan", {"node": "goal", "proof_dependencies": ["other"], "reason": "plan"})
    wb.apply("route.add", {"id": "r-other", "node": "other", "rationale": "x",
                           "next_question": "q", "stop_condition": "s",
                           "max_runs": 2, "stagnation_limit": 2})
    wb.apply("route.status", {"id": "r-other", "status": "active", "reason": "test"})
    wb.apply("task.add", {"id": "t-other", "route": "r-other", "role": "explore",
                          "argv": [sys.executable, "-c", "print(1)"],
                          "timeout_seconds": 30, "output_limit_bytes": 1000}, "runner")
    with pytest.raises(ResearchError, match="must belong to the finding node"):
        finding(wb, task="t-other")


def test_the_view_counts_by_kind_and_evidence(wb):
    finding(wb, fid="F-1")
    finding(wb, fid="F-2", evidence_kind="conjecture",
            relation={"target": "goal", "type": "exploratory"})
    page = findings.view(wb)
    assert page["total"] == 2
    assert page["checked"] == 1
    assert page["by_evidence"]["conjecture"] == 1

"""A round that only produces a finding is a real round (v3.5, level 1)."""
import sys

import pytest

from ai4math.execution import run_task
from ai4math.store import ResearchError
from ai4math.workbench import Workbench
from test_workbench import accept_human, artifact, claim, review


@pytest.fixture
def wb(tmp_path):
    w = Workbench.create(tmp_path / "ws", "roundfind", "Round findings")
    w.apply("node.add", {"id": "goal", "statement": "Exact goal", "domain": "all n",
                         "quantifier": "universal"})
    w.apply("goal.set", {"node": "goal"})
    w.apply("strategy.configure", {"correspondence_after": 1000})
    w.apply("route.add", {"id": "route", "node": "goal", "rationale": "proof",
                          "next_question": "q", "stop_condition": "s",
                          "max_runs": 10, "stagnation_limit": 50})
    w.apply("route.status", {"id": "route", "status": "active", "reason": "test"})
    path = w.store.root / "note.md"
    path.write_text("a transfer lemma is missing", encoding="utf-8")
    w.apply("artifact.import", {"path": str(path), "purpose": "note"}, "author")
    yield w
    w.store.close()


def finding(w, fid="F-1", **overrides):
    payload = {"id": fid, "node": "goal", "kind": "obstacle_identified",
               "statement": "a transfer lemma is missing", "scope": "fixed g, fixed n",
               "evidence_kind": "theoretical_argument",
               "evidence_refs": [next(a["id"] for a in w.store.all("artifact"))],
               "relation": {"target": "goal", "type": "load_bearing"},
               "next_action": {"kind": "prove_transfer", "question": "is H bounded?"}}
    payload.update(overrides)
    return w.apply("finding.add", payload, "author")


def independent_review(w, row, *, scope=None, reviewer="independent", kind="unproved_dependency"):
    w.apply("obstacle.add", {"id": "bottleneck", "node": row["node"], "kind": kind,
                              "scope": scope or row["scope"], "description": "exact probe condition"}, "author")
    evidence = row["evidence_refs"][0]
    w.apply("obstacle.verify", {"id": "bottleneck", "evidence": evidence}, reviewer)
    return {"obstacle": "bottleneck", "obstacle_effect": "reformulated", "evidence_refs": [evidence]}


def test_a_round_with_only_a_finding_settles(wb):
    row = finding(wb)
    wb.apply("round.open", {"id": "r1", "route": "route"}, "coordinator")
    checkpoint = wb.apply("round.record_attempt", {
        "round": "r1", "finding": "located the missing transfer lemma",
        "next_question": "can the transfer lemma be stated?",
        "findings": [row["id"]]}, "coordinator")
    assert checkpoint["outcome"] == "finding"
    assert checkpoint["findings"] == [row["id"]]
    settled = wb.apply("round.settle", {"round": "r1"}, "coordinator")
    assert settled["state"] == "settled"
    assert wb.store.get("round_settled", "r1")["outcome"] == "finding"
    assert wb.store.get("obligation", "goal")["findings"] == [row["id"]]
    assert wb.store.get("obligation", "goal")["stagnant"] == 1
    assert wb.store.get("route", "route")["stagnant"] == 1


def test_a_finding_from_another_node_is_refused(wb):
    wb.apply("node.add", {"id": "other", "statement": "Other", "domain": "all n",
                          "quantifier": "universal"})
    wb.apply("node.plan", {"node": "goal", "proof_dependencies": ["other"], "reason": "plan"})
    other = finding(wb, fid="F-2", node="other")
    wb.apply("round.open", {"id": "r1", "route": "route"}, "coordinator")
    with pytest.raises(ResearchError, match="different node"):
        wb.apply("round.record_attempt", {"round": "r1", "finding": "f",
                                          "next_question": "q", "findings": [other["id"]]},
                 "coordinator")


def test_the_same_finding_cannot_be_credited_twice(wb):
    row = finding(wb)
    wb.apply("round.open", {"id": "r1", "route": "route"}, "coordinator")
    with pytest.raises(ResearchError, match="duplicate finding"):
        wb.apply("round.record_attempt", {"round": "r1", "finding": "f", "next_question": "q",
                                          "findings": [row["id"], row["id"]]}, "coordinator")


def test_a_finding_outcome_needs_an_id(wb):
    wb.apply("round.open", {"id": "r1", "route": "route"}, "coordinator")
    with pytest.raises(ResearchError, match="finding outcome"):
        wb.apply("round.record_attempt", {"round": "r1", "finding": "f", "next_question": "q",
                                          "outcome": "finding"}, "coordinator")


def test_findings_cannot_hide_under_no_progress(wb):
    row = finding(wb)
    with pytest.raises(ResearchError, match="finding or claim outcome"):
        wb.apply("route.checkpoint", {"route": "route", "finding": "f", "next_question": "q",
                                      "outcome": "no-progress", "findings": [row["id"]]},
                 "coordinator")


def test_a_reviewed_finding_resets_stagnation_once_across_routes_and_finding_ids(wb):
    wb.apply("route.checkpoint", {"route": "route", "finding": "nothing", "next_question": "q",
                                  "outcome": "no-progress"}, "coordinator")
    assert wb.store.get("route", "route")["stagnant"] == 1
    row = finding(wb)
    evaluation = independent_review(wb, row)
    wb.apply("round.open", {"id": "r1", "route": "route"}, "coordinator")
    wb.apply("round.record_attempt", {"round": "r1", "finding": "found the gap",
                                      "next_question": "is H bounded?", "findings": [row["id"]],
                                      "eval": evaluation}, "coordinator")
    wb.apply("round.settle", {"round": "r1"}, "coordinator")
    route = wb.store.get("route", "route")
    ledger = wb.store.get("obligation", "goal")
    progress = route["checkpoints"][-1]["progress"]
    assert route["stagnant"] == ledger["stagnant"] == 0
    assert progress == ledger["outcomes"][-1]["progress"] == wb.store.get("round_settled", "r1")["progress"]
    assert progress["decision_changes"][0]["finding_id"] == row["id"]
    assert progress["validated_discharged_ids"] == []

    wb.apply("route.add", {"id": "renamed", "node": "goal", "rationale": "same obligation",
                           "next_question": "q", "stop_condition": "s", "max_runs": 10, "stagnation_limit": 50})
    copied = finding(wb, fid="F-copy", statement="the same gap in different words")
    renamed = wb.apply("route.checkpoint", {"route": "renamed", "finding": "repeat",
                       "next_question": "is H bounded?", "findings": [copied["id"]],
                       "eval": evaluation}, "coordinator")
    assert renamed["stagnant"] == wb.store.get("obligation", "goal")["stagnant"] == 1
    assert renamed["checkpoints"][-1]["findings"] == [copied["id"]]
    assert renamed["checkpoints"][-1]["progress"]["decision_changes"] == []


def test_a_speculative_finding_does_not_reset_stagnation(wb):
    wb.apply("route.checkpoint", {"route": "route", "finding": "nothing", "next_question": "q",
                                  "outcome": "no-progress"}, "coordinator")
    row = finding(wb, relation={"target": "goal", "type": "speculative_bridge"})
    wb.apply("route.checkpoint", {"route": "route", "finding": "maybe a bridge",
                                  "next_question": "q", "findings": [row["id"]]}, "coordinator")
    assert wb.store.get("route", "route")["stagnant"] == 2
    assert wb.store.get("obligation", "goal")["findings"] == [row["id"]]
    assert wb.store.get("obligation", "goal")["stagnant"] == 2


@pytest.mark.parametrize("mismatch", ["target", "scope", "reviewer", "decision"])
def test_unbound_or_unreviewed_findings_do_not_reset_either_ledger(wb, mismatch):
    wb.apply("node.add", {"id": "other", "statement": "Other", "domain": "all n", "quantifier": "universal"})
    row = finding(wb, relation={"target": "other" if mismatch == "target" else "goal", "type": "load_bearing"})
    evaluation = independent_review(wb, row, scope="different n" if mismatch == "scope" else None,
                                    reviewer="author" if mismatch == "reviewer" else "independent")
    route = wb.apply("route.checkpoint", {"route": "route", "finding": "proposed gap", "findings": [row["id"]],
                      "next_question": "q" if mismatch == "decision" else "is H bounded?",
                      "eval": evaluation}, "coordinator")
    assert route["stagnant"] == wb.store.get("obligation", "goal")["stagnant"] == 1
    assert route["checkpoints"][-1]["findings"] == [row["id"]]
    assert route["checkpoints"][-1]["progress"]["decision_changes"] == []


def test_a_reviewed_route_refutation_changes_the_decision_without_closing_the_goal(wb):
    row = finding(wb)
    evaluation = independent_review(wb, row, kind="mathematical_refutation")
    wb.apply("obstacle.status", {"id": "bottleneck", "status": "refuted"}, "independent")
    route = wb.apply("route.checkpoint", {"route": "route", "finding": "one route failed",
                      "next_question": "try a different premise", "outcome": "falsified",
                      "artifacts": row["evidence_refs"], "eval": evaluation}, "coordinator")
    progress = route["checkpoints"][-1]["progress"]
    assert progress["decision_changes"][0]["kind"] == "route_refutation"
    assert progress["validated_discharged_ids"] == [] and wb.node_status("goal") == "open"


def test_only_current_exact_proof_closes_an_obligation_once(wb, tmp_path):
    art = artifact(wb, tmp_path, "Synthetic proof fixture")
    claim(wb, "finite", level="finite_support", scope="finite", artifacts=[art])
    review(wb, "r-finite", "finite")
    wb.apply("claim.accept", {"claim": "finite", "review": "r-finite"}, "custodian")
    with pytest.raises(ResearchError, match="progress credit needs"):
        wb.apply("route.checkpoint", {"route": "route", "finding": "finite sample", "next_question": "q",
                                      "claims": ["finite"]}, "coordinator")
    accept_human(wb, "proof", "goal", art)
    payload = {"route": "route", "finding": "exact proof", "next_question": "archive", "claims": ["proof"]}
    route = wb.apply("route.checkpoint", payload, "coordinator")
    progress = route["checkpoints"][-1]["progress"]
    assert progress["validated_discharged_ids"] == ["goal"] and progress["decision_changes"] == []
    wb.apply("claim.revoke", {"claim": "proof", "reason": "Synthetic revocation"}, "custodian")
    accept_human(wb, "same-proof", "goal", art)
    route = wb.apply("route.checkpoint", {**payload, "claims": ["same-proof"]}, "coordinator")
    assert route["checkpoints"][-1]["progress"]["validated_discharged_ids"] == []


@pytest.mark.parametrize("code, timeout, limit, raw_status, execution_status", [
    ("raise SystemExit(1)", 5, 10000, "failed", "failed"),
    ("import time; time.sleep(5)", 1, 10000, "unknown_timeout", "timed_out"),
    ("print('x' * 10000)", 5, 100, "output_limit", "output_limited"),
])
def test_assessment_cannot_relabel_an_execution_failure(wb, code, timeout, limit, raw_status, execution_status):
    wb.apply("round.open", {"id": "r1", "route": "route"}, "coordinator")
    wb.apply("task.add", {"id": "t1", "route": "route", "role": "explore", "round": "r1",
                          "argv": [sys.executable, "-c", code], "timeout_seconds": timeout,
                          "output_limit_bytes": limit}, "runner")
    assert run_task(wb, "t1", "runner")["status"] == raw_status
    assessment = {"task_id": "t1", "execution_status": "completed", "scientific_outcome": "not_assessed"}
    body = {"round": "r1", "finding": "no completed execution", "next_question": "inspect failure",
            "task_assessments": [assessment]}
    with pytest.raises(ResearchError, match="does not match registered task state"):
        wb.apply("round.assess", body, "coordinator")
    assessment["execution_status"] = execution_status
    wb.apply("round.assess", body, "coordinator")
    wb.apply("round.settle", {"round": "r1"}, "coordinator")
    assert wb.store.get("round_settled", "r1")["progress"]["completed_task_ids"] == []


def test_a_claim_and_a_finding_can_share_a_round(wb):
    row = finding(wb)
    digest = wb.store.get("node", "goal")["statement_digest"]
    wb.apply("claim.add", {"id": "c1", "node": "goal", "statement_digest": digest,
                           "evidence_level": "proved", "scope": "universal", "summary": "s",
                           "proof_route": "direct"}, "author")
    with wb.store.transaction("fixture.accept", "test"):
        wb.store.put("validation", "c1-val",
                     {"id": "c1-val", "claim": "c1", "method": "formal_kernel", "passed": True,
                      "artifact_ids": [], "trust": "declared_external_checker",
                      "trust_basis": "fixture", "validator": "val"}, new=True)
        wb.store.put("review", "c1-rev",
                     {"id": "c1-rev", "claim": "c1", "verdict": "ready_for_human",
                      "reviewer": "rev", "reviewer_kind": "model", "auditor": "aud",
                      "review_order": 1, "created_at": "2030-01-01T00:00:00+00:00",
                      "issues": [], "coverage_complete": True}, new=True)
    wb.apply("claim.accept", {"claim": "c1", "review": "c1-rev", "validation": "c1-val"},
             "custodian")
    wb.apply("route.checkpoint", {"route": "route", "finding": "proof plus a finding",
                                  "next_question": "q", "outcome": "claim", "claims": ["c1"],
                                  "findings": [row["id"]]}, "coordinator")
    obligation = wb.store.get("obligation", "goal")
    assert obligation["claims"] == ["c1"]
    assert obligation["findings"] == [row["id"]]

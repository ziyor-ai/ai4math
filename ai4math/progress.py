"""Qualify progress once, while settling a checkpoint."""

from .findings import checked
from .progress_evidence import claim_evidence
from .store import digest


def _same_text(first, second):
    return isinstance(first, str) and isinstance(second, str) and first.split() == second.split()


def _reviewed_obstacle(w, node, evaluation, actor):
    key = evaluation.get("obstacle")
    if not isinstance(key, str) or not w.store.exists("obstacle", key):
        return None
    row = w.store.get("obstacle", key)
    reviewer = row.get("verified_by")
    scope = row.get("scope")
    evidence = row.get("evidence")
    if (row["node"] != node or row.get("verification_status") != "verified"
            or not reviewer or reviewer in {actor, row.get("author")}
            or not isinstance(scope, str) or not scope.strip()
            or not isinstance(evidence, str) or not w.store.exists("artifact", evidence)):
        return None
    w.store.verify_blob(evidence)
    return row


def _decision_changes(w, route, checkpoint, findings, actor):
    if _same_text(checkpoint["next_question"], route["next_question"]):
        return []
    evaluation = checkpoint.get("eval")
    if (not isinstance(evaluation, dict)
            or evaluation.get("obstacle_effect") not in ("weakened", "reformulated", "resolved")):
        return []
    obstacle = _reviewed_obstacle(w, route["node"], evaluation, actor)
    if obstacle is None:
        return []
    evidence = obstacle["evidence"]
    refs = evaluation.get("evidence_refs")
    if not isinstance(refs, list) or evidence not in refs:
        return []
    base = {"node": route["node"], "obstacle": obstacle["id"], "scope": obstacle["scope"],
            "evidence_refs": [evidence], "reviewer": obstacle["verified_by"],
            "next_question": checkpoint["next_question"]}
    if (checkpoint["outcome"] == "falsified" and obstacle["kind"] == "mathematical_refutation"
            and obstacle["status"] == "refuted" and evidence in checkpoint["artifacts"]):
        return [{**base, "kind": "route_refutation"}]
    return [{**base, "kind": "finding", "finding_id": finding["id"]} for finding in findings
            if checked(finding) and finding["relation"]["target"] == route["node"]
            and obstacle["verified_by"] != finding["author"] and evidence in finding["evidence_refs"]
            and _same_text(finding["scope"], obstacle["scope"])
            and _same_text(finding["next_action"]["question"], checkpoint["next_question"])]


def _new_closure(w, node, checkpoint, claims, previous):
    if checkpoint["outcome"] == "discharged":
        claims = [claim for claim in w.store.all("claim") if claim["node"] == node]
    if not claims:
        return None
    cache = {}
    prior_ids = set(previous.get("progress_closure_claim_ids", []))
    # A second proof of an obligation that is still closed is useful material,
    # but it does not close that obligation again. A newly proved reopened
    # obligation still needs new evidence bytes.
    for key in sorted(prior_ids):
        if claim_evidence(w, w.store.get("claim", key), cache=cache):
            return None
    seen = set(previous.get("progress_credit_keys", []))
    for claim in claims:
        evidence = claim_evidence(w, claim, cache=cache)
        if evidence:
            # A new reviewer, checker version, or receipt for the same proof
            # bytes is not a new proof of the obligation.
            material = claim.get("artifact_ids") or evidence["artifact_ids"]
            key = "closure:" + digest(sorted(set(material)))
            if key not in seen and claim["id"] not in prior_ids:
                return key, evidence
    return None


def qualify(w, route, checkpoint, claims, findings, previous, actor, assessments):
    """Keep actual task completion separate from reviewed decisions and closure."""
    completed = [item["task_id"] for item in assessments if item["execution_status"] == "completed"
                 and w.store.get("task", item["task_id"])["status"] == "completed"]
    seen = set(previous.get("progress_credit_keys", []))
    credits = []
    decisions = []
    for decision in _decision_changes(w, route, checkpoint, findings, actor):
        # A different route, finding id, or description of the same frozen
        # material must not reset the same obligation again.
        key = "decision:" + decision["evidence_refs"][0]
        if key not in seen:
            decisions.append(decision)
            credits.append(key)
            seen.add(key)
    closure = _new_closure(w, route["node"], checkpoint, claims, previous)
    if closure:
        key, evidence = closure
        credits.append(key)
    return {"completed_task_ids": completed, "decision_changes": decisions,
            "validated_discharged_ids": [route["node"]] if closure else [],
            "closure_claim_ids": [closure[1]["claim_id"]] if closure else [],
            "closure_evidence": [closure[1]] if closure else [],
            "credit_keys": credits, "advanced": bool(credits)}

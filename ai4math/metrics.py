"""Derived entailment metrics (Batch 8.0 slice 9, frozen baseline §8.1).

Names follow the frozen table: everything structural carries the ``_structured``
suffix. A direct formal proof does not have to fabricate an inference record, so
``accepted_without_structured_entailment`` is a metadata reading, never a verdict
that the mathematics is wrong.
"""
from .inference import DERIVATION_RULES
from .metrics_closure import closure, effort  # noqa: F401  (public readings, §8.1/§8.2)


def lifecycle(w):
    """Observable proxies, not a false-positive rate (frozen baseline §8)."""
    claims = w.store.all("claim")
    ever = [c for c in claims if c.get("accepted_at")]
    revoked = [c for c in ever if c.get("status") == "revoked"]
    total = len(ever)
    return {"ever_accepted": total,
            "still_accepted": total - len(revoked),
            "revoked": len(revoked),
            "later_overturned_rate": round(len(revoked) / total, 3) if total else 0.0,
            "later_upheld_rate": round((total - len(revoked)) / total, 3) if total else 0.0}


def profile(w):
    """Label every run so an ablation or budget experiment is comparable."""
    from .ablation import state as ablation_state
    from .policy import current
    from .strategy import state as strategy_state
    row = current(w)
    settings = strategy_state(w) or {}
    return {"workspace_policy_version": row.get("workspace_policy_version"),
            "exploration_budget": row.get("exploration_budget") or {},
            "formalization_budget": row.get("formalization_budget") or {},
            "lean_budget_share": row.get("lean_budget_share", 0.3),
            "ablation": ablation_state(w),
            "critique_interval": settings.get("critique_interval"),
            "gate_interval": settings.get("gate_interval"),
            "obligation_stagnation_limit": settings.get("obligation_stagnation_limit")}


def maturity(w):
    """Two axes: claim maturity (correctness) and findings (research value).

    ``unverified_findings_accumulating`` is a drift signal, not a verdict: it fires
    when load-bearing findings pile up and nothing has been verified since the oldest
    of them, which is exactly the failure mode the per-round-Lean rule would cause in
    reverse (exploration that never closes).
    """
    from .findings import checked as finding_checked, view as finding_view
    from .formalization import view as formalization_view
    from .maturity import view as claim_view
    claims = claim_view(w)
    findings = finding_view(w)
    rows = w.store.all("research_finding")
    load_bearing = [row for row in rows
                    if row["relation"]["type"] in ("load_bearing", "supporting")
                    and row["evidence_kind"] != "conjecture"]
    verified = [c for c in w.store.all("claim") if c.get("status") == "accepted"
                and c.get("accepted_after_seq")]
    drift = False
    if len(load_bearing) >= 5:
        oldest = min(load_bearing, key=lambda row: w.store.revision("research_finding", row["id"]))
        since = w.store.revision("research_finding", oldest["id"])
        drift = not any((c.get("accepted_after_seq") or 0) > since for c in verified)
    return {"claims": claims,
            "findings": {**findings, "checked": len([row for row in rows if finding_checked(row)])},
            "load_bearing_unverified": len(load_bearing),
            "formalization": formalization_view(w),
            "unverified_findings_accumulating": drift}


def entailment(w):
    nodes = {n["id"]: n for n in w.store.all("node")}
    claims = [c for c in w.store.all("claim") if c["status"] == "accepted"]
    proved_claims = [c for c in claims if c["evidence_level"] == "proved"]
    inference = w.store.all("inference")
    verified = {r["id"] for r in inference
                if (r.get("verification") or {}).get("status") == "verified"}
    derivations = {}
    for row in inference:
        if row.get("rule") in DERIVATION_RULES:
            derivations.setdefault(row["parent"], []).append(row["id"])
    parents = [node for node in nodes.values() if node["proof_dependencies"]]
    children_closed = [node for node in parents
                       if all(w.node_status(dep) == "proved" for dep in node["proof_dependencies"])]
    merge_ready = [node for node in children_closed
                   if derivations.get(node["id"])
                   and all(key in verified for key in derivations[node["id"]])]
    parent_claims = {c["node"] for c in proved_claims}
    merge_succeeded = [node for node in merge_ready if node["id"] in parent_claims]
    without_meta = [c for c in proved_claims
                    if nodes[c["node"]]["proof_dependencies"] and not derivations.get(c["node"])]
    return {"parents": len(parents),
            "children_closed_recorded": len(children_closed),
            "inferences": len(inference),
            "inferences_verified": len(verified),
            "merge_ready_structured": len(merge_ready),
            "merge_succeeded_structured": len(merge_succeeded),
            "accepted_without_structured_entailment": len(without_meta),
            "nodes_merge_ready": sorted(node["id"] for node in merge_ready),
            "accepted_without_structured_entailment_nodes": sorted({c["node"] for c in without_meta})}

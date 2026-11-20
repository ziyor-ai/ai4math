"""Evidence usable by the progress ledger and its read-only historical view."""

from . import inference, trust, verification
from .store import ResearchError


def _matched_inference(w, claim, plan, dependencies):
    policy = trust.policy(w)
    if policy is None:
        return None
    for row in w.store.all("inference"):
        if row.get("parent") != claim["node"]:
            continue
        record = row.get("verification") or {}
        if (record.get("status") != "verified" or record.get("basis") not in inference.BASES
                or record.get("checker_artifact") != policy["checker_artifact"]
                or not record.get("environment_digest") or not record.get("certificate_ref")):
            continue
        branches = row.get("branch_proofs") or {}
        if row["rule"] == "bridge":
            matched = len(plan) == 1 and row.get("dep") in plan and bool(dependencies.get(row["dep"]))
        else:
            matched = (row["rule"] in inference.DERIVATION_RULES
                       and set(row.get("required_branches") or []) == set(branches) == set(plan)
                       and all(cid in dependencies.get(node, ()) for node, cid in branches.items()))
        if matched:
            try:
                w.store.verify_blob(record["certificate_ref"])
                w.store.verify_blob(record["checker_artifact"])
            except (OSError, ResearchError):
                continue
            return row
    return None


def claim_evidence(w, claim, *, cache=None, _trail=frozenset()):
    """Return auditable current evidence for an exact resolved claim, or None.

    This is a conservative reporting projection. It does not change the
    workspace's acceptance policy or treat legacy acceptance as proof.
    """
    ident = claim["id"]
    if ident in _trail:
        return None
    cache = {} if cache is None else cache
    if ident not in cache:
        cache[ident] = _claim_evidence(w, claim, cache, _trail | {ident})
    return cache[ident]


def _claim_evidence(w, claim, cache, trail):
    level = claim.get("evidence_level")
    if claim.get("status") != "accepted" or level not in {"proved", "disproved"}:
        return None
    node = w.store.get("node", claim["node"])
    if claim.get("statement_digest") != node.get("statement_digest"):
        return None
    scope = claim.get("scope")
    if scope != node.get("quantifier") and not (
            level == "disproved" and node.get("quantifier") == "universal" and scope == "finite"):
        return None
    if verification.effective(w, claim) != "valid":
        return None

    validations = [row for row in w.store.all("validation") if row.get("claim") == claim["id"]
                   and row.get("passed") and (row.get("trust") == "human_attestation" or (
                       verification.current_policy_trust(w, row) == "trusted" and
                       verification.current_toolchain_recheck(w, row) != "recheck_required"))]
    if level == "disproved":
        # A finite witness can refute the exact universal statement only via
        # the original counterexample validation, not via a bounded search.
        validations = [row for row in validations if row.get("method") == "counterexample"]
    if not validations:
        return None

    dependencies = {}
    for ident in claim.get("dependencies") or []:
        dependency = w.store.get("claim", ident)
        if dependency.get("evidence_level") != "proved" or not claim_evidence(w, dependency, cache=cache, _trail=trail):
            return None
        dependencies.setdefault(dependency["node"], set()).add(ident)

    matched = None
    route = claim.get("proof_route", "composition")
    if route not in {"composition", "direct"}:
        return None
    if level == "proved" and route == "composition":
        plan = node.get("proof_dependencies") or []
        if (claim.get("proof_plan") or []) != plan or any(dep not in dependencies for dep in plan):
            return None
        if plan:
            matched = _matched_inference(w, claim, plan, dependencies)
            if matched is None:
                return None

    artifacts = set(claim.get("artifact_ids") or [])
    for row in validations:
        artifacts.update(row.get("artifact_ids") or [])
        if row.get("checker_artifact"):
            artifacts.add(row["checker_artifact"])
    if matched:
        artifacts.add(matched["verification"]["certificate_ref"])
    if not artifacts:
        return None
    try:
        for ident in sorted(artifacts):
            w.store.verify_blob(ident)
    except (OSError, ResearchError):
        return None
    return {"claim_id": claim["id"], "node": node["id"], "statement_digest": claim["statement_digest"],
            "scope": scope, "evidence_level": level, "validation_ids": sorted(v["id"] for v in validations),
            "inference_id": matched["id"] if matched else None, "artifact_ids": sorted(artifacts)}

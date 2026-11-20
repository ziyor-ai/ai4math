"""Verification status: history, current trust, toolchain recheck, effective state.

Batch 8.2 (frozen baseline §4.6c, §4.6d).

Four things are deliberately kept apart:

* ``historical_verification`` — what a checker actually reported, frozen;
* ``current_policy_trust`` — whether the workspace still trusts that checker;
* ``current_toolchain_recheck`` — whether a newer toolchain must be re-run;
* ``effective_verification`` — what formal acceptance may use right now.

A new Lean/Mathlib version alone never invalidates a proof: it can only raise
``recheck_required``. A revocation propagates only to downstream claims accepted
before the revocation event, never by a workspace-wide counter.
"""
from .store import ResearchError, now

TRUST = ("trusted", "suspended", "untrusted")
RECHECK = ("not_required", "recheck_required", "passed", "failed", "blocked")
EFFECTIVE = ("valid", "unverified", "invalid", "blocked")


def historical(w, claim):
    rows = []
    for validation in w.store.all("validation"):
        if validation["claim"] != claim["id"]:
            continue
        rows.append({"validation": validation["id"], "method": validation["method"],
                     "passed": bool(validation.get("passed")),
                     "trust": validation.get("trust"),
                     "trust_class": validation.get("trust_class"),
                     "checker_artifact": validation.get("checker_artifact"),
                     "environment": validation.get("environment"),
                     "environment_digest": validation.get("environment_digest"),
                     "axioms": validation.get("axioms"),
                     "expr_check": validation.get("expr_check"),
                     "checked_type": validation.get("checked_type"),
                     "checked_type_sha256": validation.get("checked_type_sha256"),
                     "verification_round": validation.get("verification_round")})
    return rows


def current_policy_trust(w, validation):
    from .trust import policy
    record = policy(w)
    if record is None:
        return "untrusted"
    if (validation.get("trust_class") == "trusted"
            and validation.get("checker_artifact") == record["checker_artifact"]):
        return "trusted"
    return "suspended"


def _matches(value, allowed):
    if not allowed:
        return True
    if not isinstance(value, str) or not value.strip():
        return False
    low = value.lower()
    return any(item.lower() in low for item in allowed)


def current_toolchain_recheck(w, validation):
    from .trust import policy
    record = policy(w)
    environment = validation.get("environment") or {}
    if record is None:
        return "not_required"
    if not environment:
        return "recheck_required"
    if (_matches(environment.get("lean_version"), record.get("allowed_kernel_versions") or [])
            and _matches(environment.get("mathlib_revision"), record.get("allowed_library_revisions") or [])):
        return "not_required"
    return "recheck_required"


def _transitive_dependencies(w, claim, seen=None):
    seen = seen if seen is not None else set()
    out = []
    for key in claim.get("dependencies") or []:
        if key in seen:
            continue
        seen.add(key)
        dependency = w.store.get("claim", key)
        out.append(dependency)
        out.extend(_transitive_dependencies(w, dependency, seen))
    return out


def verification_refs(w, claim):
    """Every validation bound to this claim; stored refs win, legacy rows derive."""
    stored = claim.get("verification_refs")
    if stored:
        return list(stored)
    return [v["id"] for v in w.store.all("validation") if v.get("claim") == claim["id"]]


def _recheck_covers(w, claim, dependency_revision):
    recheck = claim.get("recheck") or {}
    if recheck.get("status") != "passed" or not recheck.get("validation"):
        return False
    return w.store.revision("validation", recheck["validation"]) > dependency_revision


def toolchain_recheck_required(w, claim):
    """True when a newer Lean/Mathlib must replay this claim's proof.

    This is deliberately separate from a revocation: a toolchain change never
    invalidates the mathematics, it only asks for a fresh replay under the current
    environment (v3.4 §4.6d).
    """
    from .trust import policy
    record = policy(w)
    if record is None:
        return False
    stale = [v for v in w.store.all("validation")
             if v.get("claim") == claim["id"] and v.get("passed")
             and current_toolchain_recheck(w, v) == "recheck_required"]
    if not stale:
        return False
    recheck = claim.get("recheck") or {}
    if recheck.get("status") == "passed" and recheck.get("validation"):
        newest_stale = max(w.store.revision("validation", v["id"]) for v in stale)
        if w.store.revision("validation", recheck["validation"]) > newest_stale:
            return False
    return True


def recheck_required(w, claim):
    """True when a dependency was revoked after this claim was accepted.

    A revoked dependency blocks the claim until an independent re-proof is accepted
    as a *new* claim; a recheck never silently re-authorises the old dependency edge.
    """
    if claim.get("status") != "accepted":
        return False
    accepted_at = claim.get("accepted_after_seq")
    if not isinstance(accepted_at, int) or accepted_at <= 0:
        # A legacy acceptance has no event anchor; never infer staleness from nothing.
        return False
    for dependency in _transitive_dependencies(w, claim):
        if dependency.get("status") != "revoked":
            continue
        revision = int(w.store.revision("claim", dependency["id"]))
        if revision > accepted_at:
            return True
    return False


def effective(w, claim, seen=None):
    """Aggregate state usable for acceptance, over the real dependency closure."""
    seen = seen if seen is not None else set()
    if claim["id"] in seen:
        return "blocked"
    seen = seen | {claim["id"]}
    if claim.get("status") == "revoked":
        return "invalid"
    if recheck_required(w, claim):
        return "blocked"
    validations = [v for v in w.store.all("validation")
                   if v.get("claim") == claim["id"] and v.get("passed")]
    if not validations:
        return "unverified"
    base = "unverified"
    if any(v.get("trust") == "human_attestation" for v in validations):
        base = "valid"
    elif any(current_policy_trust(w, v) == "trusted" for v in validations):
        base = "valid"
    for dependency in _transitive_dependencies(w, claim):
        state = effective(w, dependency, seen)
        if state in {"invalid", "blocked"}:
            return "blocked"
        if state == "unverified":
            base = "unverified"
    return base


def summary(w):
    claims = [c for c in w.store.all("claim") if c.get("status") == "accepted"]
    counts = {name: 0 for name in EFFECTIVE}
    for claim in claims:
        counts[effective(w, claim)] += 1
    from .legacy_audit import view as audit_view
    rechecks = [claim.get("recheck", {}).get("status") for claim in claims
                if claim.get("recheck")]
    return {"accepted_claims": len(claims),
            "effective": counts,
            "recheck_required": len([c for c in claims if recheck_required(w, c)]),
            "toolchain_recheck_required": len([c for c in claims
                                               if toolchain_recheck_required(w, c)]),
            "recheck_status": {status: rechecks.count(status)
                               for status in ("passed", "failed")},
            "legacy_audit": audit_view(w),
            "legacy_unaudited": len([c for c in claims
                                     if all(v.get("trust_class") != "trusted"
                                            for v in w.store.all("validation")
                                            if v.get("claim") == c["id"])])}


def provenance(w, claim_id):
    """The chain a reader needs to audit one acceptance."""
    claim = w.store.get("claim", claim_id)
    node = w.store.get("node", claim["node"])
    rows = []
    for validation in w.store.all("validation"):
        if validation.get("claim") != claim_id:
            continue
        from .trust import policy
        record = policy(w)
        rows.append({"validation": validation["id"], "method": validation["method"],
                     "trust_class": validation.get("trust_class"),
                     "policy_trust": current_policy_trust(w, validation),
                     "toolchain_recheck": current_toolchain_recheck(w, validation),
                     "checker_artifact": validation.get("checker_artifact"),
                     "policy_checker": record["checker_artifact"] if record else None,
                     "task": validation.get("task"), "passed": bool(validation.get("passed"))})
    derivations = []
    for inference in w.store.all("inference"):
        if inference.get("parent") == claim["node"]:
            derivations.append({"inference": inference["id"], "rule": inference["rule"],
                                "verification": (inference.get("verification") or {}).get("status"),
                                "branches": sorted((inference.get("branch_proofs") or {}).keys())})
    from .legacy_audit import for_claim as legacy_for_claim
    return {"claim": claim_id, "node": claim["node"], "node_statement_digest": node["statement_digest"],
            "evidence_level": claim["evidence_level"], "proof_route": claim.get("proof_route", "composition"),
            "accepted_at": claim.get("accepted_at"), "accepted_after_seq": claim.get("accepted_after_seq"),
            "dependencies": list(claim.get("dependencies") or []),
            "validations": rows, "inferences": derivations,
            "verification_refs": verification_refs(w, claim),
            "accepted_validation": claim.get("accepted_validation"),
            "trusted_validation": claim.get("trusted_validation"),
            "recheck": claim.get("recheck"),
            "legacy_audit": legacy_for_claim(w, claim_id),
            "effective": effective(w, claim), "recheck_required": recheck_required(w, claim),
            "historical": historical(w, claim)}


def recheck(w, p, actor):
    """Record a fresh replay of a claim whose proof must be re-verified.

    Two triggers: the toolchain/policy no longer matches the recorded environment, or
    a dependency was revoked and the caller wants the fresh replay on record. A
    revocation itself is not cleared: the claim stays blocked until an independent
    proof is accepted as a new claim.
    """
    claim = w.store.get("claim", p.get("claim"))
    if claim.get("status") != "accepted":
        raise ResearchError("only an accepted claim can be rechecked")
    stale_environment = toolchain_recheck_required(w, claim)
    revocation = recheck_required(w, claim)
    if not stale_environment and not revocation:
        raise ResearchError("nothing to recheck: environment and dependencies are current")
    validation = w.store.get("validation", p.get("validation"))
    if validation.get("claim") != claim["id"]:
        raise ResearchError("recheck validation does not certify this claim")
    if not validation.get("passed"):
        raise ResearchError("recheck validation did not pass")
    if validation.get("verification_round") != "recheck":
        raise ResearchError("recheck validation must be recorded as a recheck round")
    if stale_environment:
        newest_stale = max(w.store.revision("validation", v["id"])
                           for v in w.store.all("validation")
                           if v.get("claim") == claim["id"] and v.get("passed")
                           and current_toolchain_recheck(w, v) == "recheck_required")
        if w.store.revision("validation", validation["id"]) <= newest_stale:
            raise ResearchError("recheck validation is older than the toolchain change; replay again")
    if revocation:
        triggers = sorted({dependency["id"] for dependency in _transitive_dependencies(w, claim)
                           if dependency.get("status") == "revoked"})
        claim["recheck"] = {"status": "passed", "validation": validation["id"], "at": now(),
                            "triggers": triggers,
                            "note": "revocation is not cleared; an independent claim is needed"}
    else:
        claim["recheck"] = {"status": "passed", "validation": validation["id"], "at": now(),
                            "triggers": []}
    w.store.put("claim", claim["id"], claim)
    return claim["recheck"]


HANDLERS = {"verification.recheck": recheck}

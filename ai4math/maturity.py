"""Claim maturity: level 2 (proof candidate) versus level 3 (verified).

Batch 8.5 (v3.5). Level 1 is ``research_finding`` (see findings.py); levels 2 and 3
are the existing ``claim`` object, so there is no second claim system.

The state machine writes ``claim["maturity"]``; readers of legacy claims (written
before this field existed) get a derived value instead of a rewrite:

    accepted + a passing validation        -> verified
    accepted finite_support with artifacts -> verified (its stated level is finite)
    anything else                          -> proof_candidate
"""
LEVELS = ("proof_candidate", "verified")


def of_claim(claim, validations):
    """Derive or read the maturity of one claim; never writes."""
    stored = claim.get("maturity")
    if stored in LEVELS:
        return stored
    if claim.get("status") == "accepted":
        passed = any(v.get("claim") == claim.get("id") and v.get("passed")
                     for v in validations)
        if passed or claim.get("evidence_level") == "finite_support":
            return "verified"
        return "proof_candidate"
    return "proof_candidate"


def verified(claim, validations):
    return of_claim(claim, validations) == "verified"


def contribution(claim):
    """The value axis: why this claim matters, independent of correctness."""
    return claim.get("contribution") or "exploratory"


def view(w):
    validations = w.store.all("validation")
    claims = w.store.all("claim")
    counts = {level: 0 for level in LEVELS}
    contributions = {"load_bearing": 0, "supporting": 0, "exploratory": 0, "none": 0}
    for claim in claims:
        counts[of_claim(claim, validations)] += 1
        if claim.get("status") == "accepted":
            contributions[contribution(claim)] += 1
    accepted = [c for c in claims if c.get("status") == "accepted"]
    return {"claims": len(claims), "accepted": len(accepted),
            "by_maturity": counts,
            "verified_share": round(counts["verified"] / len(claims), 3) if claims else 0.0,
            "contributions": contributions}

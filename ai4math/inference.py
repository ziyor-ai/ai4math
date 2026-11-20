"""Inference records: how a parent is actually derived from its parts.

Batch 8.0 slice 7 (frozen baseline §4.6, §4.6b).

A composition claim needs more than "every child is proved": it needs a recorded
derivation whose branches are covered and whose certificated verification is
``verified``. A model review can raise a record to ``reviewed_candidate`` only.
"""
from .store import ResearchError, now

RULES = ("case_split", "composition", "bridge")
BASES = ("formal_kernel", "independent_checker", "exact_enumeration")
DERIVATION_RULES = ("case_split", "composition")


def row(w, key):
    return w.store.get("inference", key)


def _text(p, *keys):
    for key in keys:
        if not isinstance(p.get(key), str) or not p[key].strip():
            raise ResearchError("nonempty text required: " + key)


def add(w, p, actor):
    if not isinstance(p, dict):
        raise ResearchError("inference.add needs an object payload")
    _text(p, "id", "rule", "parent")
    if p["rule"] not in RULES:
        raise ResearchError("inference rule must be one of " + ", ".join(RULES))
    if w.store.exists("inference", p["id"]):
        raise ResearchError("inference already exists: " + p["id"])
    parent = w.store.get("node", p["parent"])
    record = {"id": p["id"], "rule": p["rule"], "parent": parent["id"],
              "common_context": p.get("common_context") or {},
              "required_branches": list(p.get("required_branches") or []),
              "branch_context": dict(p.get("branch_context") or {}),
              "branch_proofs": dict(p.get("branch_proofs") or {}),
              "dep": p.get("dep"),
              "review": dict(p.get("review") or {}),
              "author": actor, "created_at": now()}
    if p["rule"] == "bridge":
        _text(p, "dep")
        w.store.get("node", p["dep"])
    else:
        branches = record["required_branches"]
        if len(branches) < 2 or len(set(branches)) != len(branches):
            raise ResearchError("a " + p["rule"] + " needs at least two distinct branches")
        for branch in branches:
            w.store.get("node", branch)
        proofs = record["branch_proofs"]
        missing = sorted(set(branches) - set(proofs))
        if missing:
            raise ResearchError("branch proofs do not cover: " + ", ".join(missing))
        extra = sorted(set(proofs) - set(branches))
        if extra:
            raise ResearchError("branch proofs cite unknown branches: " + ", ".join(extra))
        for branch, claim_id in proofs.items():
            if claim_id in (None, ""):
                raise ResearchError("branch " + branch + " has no proof claim yet")
            claim = w.store.get("claim", claim_id)
            if claim["node"] != branch:
                raise ResearchError("branch proof claim is bound to a different node: " + branch)
            if claim["status"] != "accepted":
                raise ResearchError("branch proof claim is not accepted: " + claim_id)
    verification = p.get("verification") or {}
    record["verification"] = _verification(w, record, verification)
    w.store.put("inference", record["id"], record, new=True)
    return record


def _verification(w, record, verification):
    status = "unverified"
    review = record.get("review") or {}
    if review.get("model_verdict") == "plausible":
        status = "reviewed_candidate"
    if review.get("model_verdict") == "flaw_found":
        return {"status": "unverified", "basis": None, "certificate_ref": None,
                "environment_digest": None, "reason": "a review found a flaw"}
    basis = verification.get("basis")
    if basis is None:
        return {"status": status, "basis": None, "certificate_ref": None,
                "environment_digest": None}
    if basis not in BASES:
        raise ResearchError("verification basis must be one of " + ", ".join(BASES))
    certificate = verification.get("certificate_ref")
    if not isinstance(certificate, str) or not certificate.strip():
        raise ResearchError("a verified inference needs a certificate_ref")
    w.store.verify_blob(certificate)
    environment = verification.get("environment_digest")
    if not isinstance(environment, str) or not environment.strip():
        raise ResearchError("a verified inference needs an environment_digest")
    from .trust import policy
    checker = verification.get("checker_artifact")
    recorded = policy(w)
    if recorded is not None and checker != recorded["checker_artifact"]:
        raise ResearchError("inference certificate was not produced by the policy checker")
    for branch in record.get("branch_proofs", {}):
        if record["branch_proofs"][branch] is None:
            raise ResearchError("cannot verify an inference with an unproved branch")
    return {"status": "verified", "basis": basis, "certificate_ref": certificate,
            "environment_digest": environment, "checker_artifact": checker}


def closure(w, claim, seen=None, trail=None):
    """Walk the derivation closure of one claim; return unverified inference ids."""
    seen = seen or set()
    trail = trail or []
    node = w.store.get("node", claim["node"])
    if node["id"] in seen:
        return []
    seen.add(node["id"])
    bad = []
    rows = [r for r in w.store.all("inference")
            if r["parent"] == node["id"] and r["rule"] in DERIVATION_RULES]
    for inference in rows:
        if (inference.get("verification") or {}).get("status") != "verified":
            bad.append(inference["id"])
            continue
        for branch, claim_id in (inference.get("branch_proofs") or {}).items():
            branch_claim = w.store.get("claim", claim_id)
            bad.extend(closure(w, branch_claim, seen, trail + [inference["id"]]))
    return bad


def require_closure(w, claim):
    """Refuse a composition acceptance whose derivation closure is not verified."""
    from .trust import policy
    from .ablation import disabled
    if disabled(w, "inference"):
        return "ablated"
    node = w.store.get("node", claim["node"])
    rows = [r for r in w.store.all("inference")
            if r["parent"] == node["id"] and r["rule"] in DERIVATION_RULES]
    if not rows:
        if policy(w) is not None and node["proof_dependencies"]:
            raise ResearchError("a composition claim on a planned node needs a recorded inference")
        return "absent"
    bad = closure(w, claim)
    if bad:
        raise ResearchError("composition has unverified entailment: " + ", ".join(sorted(set(bad))))
    return "verified"


HANDLERS = {"inference.add": add}

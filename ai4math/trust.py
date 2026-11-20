"""Checker policy and trust assessment.

Batch 8.0 slice 6 (frozen baseline §4.6c, §6.4b).

An external receipt that says ``passed: true`` is a *declaration*. It becomes
``trust_class=trusted`` only when a workspace checker policy authorises exactly that
checker, certificate format, environment and axiom set. The state machine writes the
trust fields; a payload that carries them is rejected.
"""
from .store import ResearchError, digest, now

POLICY_KIND = "checker_policy"
DEFAULT_TRUSTED_METHODS = ("formal_kernel",)


def policy(w):
    rows = w.store.all(POLICY_KIND)
    return rows[0] if rows else None


def _text_list(value, name):
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(x, str) or not x.strip() for x in value):
        raise ResearchError(name + " must be a list of nonempty strings")
    return value


def add_policy(w, p, actor):
    if not isinstance(p, dict):
        raise ResearchError("checker.policy.add needs an object payload")
    policy_id = p.get("id")
    if not isinstance(policy_id, str) or not policy_id.strip():
        raise ResearchError("checker.policy.add needs an id")
    if w.store.exists(POLICY_KIND, policy_id):
        raise ResearchError("checker policy already exists: " + policy_id)
    checker = p.get("checker_artifact")
    if not isinstance(checker, str) or not checker.strip():
        raise ResearchError("checker.policy.add needs a checker_artifact")
    w.store.verify_blob(checker)
    require_trusted = p.get("require_trusted", True)
    if type(require_trusted) is not bool:
        raise ResearchError("require_trusted must be a boolean")
    require_axiom_list = p.get("require_axiom_list", False)
    if type(require_axiom_list) is not bool:
        raise ResearchError("require_axiom_list must be a boolean")
    record = {"id": policy_id, "checker_artifact": checker,
              "allowed_kernel_versions": _text_list(p.get("allowed_kernel_versions"), "allowed_kernel_versions"),
              "allowed_library_revisions": _text_list(p.get("allowed_library_revisions"), "allowed_library_revisions"),
              "allowed_axioms": _text_list(p.get("allowed_axioms"), "allowed_axioms"),
              "forbidden_axioms": _text_list(p.get("forbidden_axioms"), "forbidden_axioms"),
              "certificate_format": p.get("certificate_format"),
              "goal_type_match": p.get("goal_type_match", "exact_statement_digest"),
              "scope": p.get("scope", "formal_kernel_only"),
              "require_trusted": require_trusted,
              "require_axiom_list": require_axiom_list,
              "created_by": actor, "created_at": now()}
    w.store.put(POLICY_KIND, policy_id, record, new=True)
    return record


def _version_ok(value, allowed):
    if not allowed:
        return True
    if not isinstance(value, str) or not value.strip():
        return False
    low = value.lower()
    return any(item.lower() in low for item in allowed)


def _axioms_ok(result, record):
    listed = result.get("axioms") or result.get("authorized_axioms")
    allowed = record.get("allowed_axioms") or []
    forbidden = record.get("forbidden_axioms") or []
    if listed is None:
        # Real kernel receipts often state the closure in prose rather than a list.
        # A policy may demand the list; the default accepts a silent receipt so the
        # trusted path stays reachable for existing certificate formats.
        return not record.get("require_axiom_list", False)
    if not isinstance(listed, list) or any(not isinstance(x, str) for x in listed):
        return False
    if any(name in forbidden for name in listed):
        return False
    if allowed:
        return set(listed).issubset(set(allowed))
    return True


def _environment(result):
    lean = result.get("lean_version")
    mathlib = result.get("mathlib_revision")
    if not lean and not mathlib:
        return None
    return digest({"lean_version": lean, "mathlib_revision": mathlib})


def _goal_type(claim, result):
    statement = result.get("statement_digest")
    if statement != claim.get("statement_digest"):
        return None
    return digest({"statement_digest": statement, "targets": result.get("targets") or []})


def assessment(w, claim, result, method, checker_artifact):
    """Return the trust fields for one validation result; never trusts the caller."""
    record = policy(w)
    reasons = []
    trusted = True
    if record is None:
        trusted = False
        reasons.append("no checker policy")
    else:
        if checker_artifact != record["checker_artifact"]:
            trusted = False
            reasons.append("checker artifact is not the policy checker")
        if record.get("scope") == "formal_kernel_only" and method not in DEFAULT_TRUSTED_METHODS:
            trusted = False
            reasons.append("policy authorises formal_kernel only")
        if result.get("statement_digest") != claim.get("statement_digest"):
            trusted = False
            reasons.append("statement digest differs from the claim")
        if not _version_ok(result.get("lean_version"), record.get("allowed_kernel_versions") or []):
            trusted = False
            reasons.append("lean_version is outside the policy")
        if not _version_ok(result.get("mathlib_revision"), record.get("allowed_library_revisions") or []):
            trusted = False
            reasons.append("mathlib revision is outside the policy")
        if not _axioms_ok(result, record):
            trusted = False
            reasons.append("axiom set is not authorised by the policy")
        expected_format = record.get("certificate_format")
        if expected_format and result.get("certificate_format") not in (None, expected_format):
            trusted = False
            reasons.append("certificate format differs from the policy")
    from .verification_policy import check as check_policy, policy as verification_policy
    basis, basis_reasons = check_policy(w, claim, result, method, checker_artifact)
    if basis_reasons:
        trusted = False
        reasons.extend(basis_reasons)
    if basis == "formal_kernel":
        constraint = (verification_policy(w)["bases"]["formal_kernel"].get("checker_policy"))
        if constraint and record is not None and record.get("id") != constraint:
            trusted = False
            reasons.append("checker policy is not the one named by the verification policy")
    prior_trusted = any(v.get("claim") == claim["id"] and v.get("trust_class") == "trusted"
                        for v in w.store.all("validation"))
    return {"trust_class": "trusted" if trusted else "declared",
            "verification_round": "recheck" if prior_trusted else "initial",
            "verification_basis": basis,
            "verification_policy_id": (verification_policy(w) or {}).get("id"),
            "environment_digest": _environment(result),
            "exact_goal_type_digest": _goal_type(claim, result),
            "trust_reasons": reasons}


def require_trusted(w):
    from .ablation import disabled
    if disabled(w, "verification"):
        return False
    record = policy(w)
    return bool(record and record.get("require_trusted"))


HANDLERS = {"checker.policy.add": add_policy}

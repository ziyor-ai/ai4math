"""Verification policy: which bases may upgrade a claim, and on what evidence.

Batch 8.2 P0 closure (frozen baseline v3.4 §4.6c). ``checker_policy`` answers "is this
checker authorised"; ``verification_policy`` answers "is this *kind* of evidence
authorised to support this kind of conclusion". Both are workspace-level records
written by the state machine, never selected by a payload.

The four bases follow §4.6c:

    formal_kernel       a kernel-checked proof of the exact goal type
    exact_enumeration   a trusted search over the declared complete finite domain
    independent_checker an independent checker whose scope and certificate are authorised
    human_attestation   an explicit human attestation (not a model opinion)

A basis that is not listed here can still produce a ``declared`` receipt; it just never
becomes ``trusted``.
"""
from .store import ResearchError, now

BASES = ("formal_kernel", "exact_enumeration", "independent_checker", "human_attestation")
METHOD_TO_BASIS = {"formal_kernel": "formal_kernel", "human_proof": "human_attestation",
                   "exhaustive_search": "exact_enumeration",
                   "finite_certificate": "exact_enumeration"}
KIND = "verification_policy"


def policy(w):
    rows = w.store.all(KIND)
    return rows[0] if rows else None


def _bool(container, key, default):
    value = container.get(key, default)
    if type(value) is not bool:
        raise ResearchError(key + " must be a boolean")
    return value


def add(w, p, actor):
    if not isinstance(p, dict):
        raise ResearchError("verification.policy.add needs an object payload")
    ident = p.get("id")
    if not isinstance(ident, str) or not ident.strip():
        raise ResearchError("verification.policy.add needs an id")
    if w.store.exists(KIND, ident):
        raise ResearchError("verification policy already exists: " + ident)
    bases = p.get("bases")
    if not isinstance(bases, dict) or not bases:
        raise ResearchError("verification policy needs a nonempty bases object")
    unknown = set(bases) - set(BASES)
    if unknown:
        raise ResearchError("unknown verification basis: " + ", ".join(sorted(unknown)))
    record = {"id": ident, "bases": {}, "created_by": actor, "created_at": now()}
    for name, detail in bases.items():
        if not isinstance(detail, dict):
            raise ResearchError("basis " + name + " needs an object")
        if name == "formal_kernel":
            checker_policy = detail.get("checker_policy")
            if checker_policy is not None and (not isinstance(checker_policy, str)
                                               or not checker_policy.strip()):
                raise ResearchError("formal_kernel.checker_policy must be a policy id")
            record["bases"][name] = {
                "checker_policy": checker_policy,
                "require_goal_type": _bool(detail, "require_goal_type", True),
                "require_environment": _bool(detail, "require_environment", True)}
        elif name == "exact_enumeration":
            record["bases"][name] = {
                "require_exhaustive": _bool(detail, "require_exhaustive", True),
                "require_domain_digest": _bool(detail, "require_domain_digest", True)}
        elif name == "independent_checker":
            checkers = detail.get("checker_artifacts") or []
            if not isinstance(checkers, list) or any(not isinstance(x, str) or not x.strip()
                                                     for x in checkers):
                raise ResearchError("independent_checker.checker_artifacts must be a list of ids")
            record["bases"][name] = {
                "checker_artifacts": checkers,
                "certificate_format": detail.get("certificate_format")}
        else:
            record["bases"][name] = {}
    w.store.put(KIND, ident, record, new=True)
    return record


def basis_for(w, claim, method, checker_artifact, certificate_format=None):
    """Return the authorised basis name for one validation, or ``None``.

    ``method`` wins when it maps to a basis; ``independent_checker`` is the fallback for
    a checker explicitly listed by the policy. A receipt that names no basis is only
    authorised when it is a plain formal kernel receipt and that basis is listed.
    """
    record = policy(w)
    if record is None:
        return None
    basis = METHOD_TO_BASIS.get(method)
    if method in ("formal_kernel", "human_proof") and basis not in record["bases"]:
        return None
    if basis is not None:
        return basis if basis in record["bases"] else None
    # An unmapped method is judged as an independent-checker basis; whether this
    # particular checker is listed is checked separately, with its own reason.
    return "independent_checker" if "independent_checker" in record["bases"] else None


def check(w, claim, result, method, checker_artifact):
    """Return ``(basis, reasons)``; reasons is empty when the basis is authorised."""
    record = policy(w)
    if record is None:
        return None, []
    basis = basis_for(w, claim, method, checker_artifact)
    if basis is None:
        return None, ["verification basis is not authorised by the verification policy"]
    reasons = []
    detail = record["bases"][basis]
    if basis == "formal_kernel":
        if detail.get("require_goal_type") and not result.get("statement_digest"):
            reasons.append("formal kernel receipt has no statement digest")
        if detail.get("require_environment") and not (result.get("lean_version")
                                                      or result.get("mathlib_revision")):
            reasons.append("formal kernel receipt has no environment")
    elif basis == "exact_enumeration":
        if detail.get("require_exhaustive") and result.get("exhaustive") is not True:
            reasons.append("enumeration does not declare an exhaustive search")
        if detail.get("require_domain_digest") and not isinstance(result.get("domain_digest"), str):
            reasons.append("enumeration has no domain digest")
    elif basis == "independent_checker":
        if detail.get("checker_artifacts") and checker_artifact not in detail["checker_artifacts"]:
            reasons.append("independent checker is not listed by the policy")
        expected = detail.get("certificate_format")
        if expected and result.get("certificate_format") != expected:
            reasons.append("certificate format differs from the verification policy")
    return basis, reasons


HANDLERS = {"verification.policy.add": add}

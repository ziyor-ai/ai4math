"""Obstacles: the negative-result record (Batch 8.1, frozen baseline §6.5)."""
from .store import ResearchError, now

KINDS = ("mathematical_refutation", "unproved_dependency", "experimental_limit",
         "method_failure", "resource_limit")
VERIFICATION = ("verified", "reported", "unverified", "disputed")
STATUSES = ("open", "resolved", "refuted")


def add(w, p, actor):
    if not isinstance(p, dict):
        raise ResearchError("obstacle.add needs an object payload")
    for key in ("id", "description", "kind"):
        if not isinstance(p.get(key), str) or not p[key].strip():
            raise ResearchError("obstacle.add needs nonempty " + key)
    if p["kind"] not in KINDS:
        raise ResearchError("obstacle kind must be one of " + ", ".join(KINDS))
    if w.store.exists("obstacle", p["id"]):
        raise ResearchError("obstacle already exists: " + p["id"])
    if p.get("node") is not None:
        w.store.get("node", p["node"])
    if p.get("hypothesis") is not None:
        w.store.get("research_hypothesis", p["hypothesis"])
    if p.get("approach") is not None:
        w.store.get("approach", p["approach"])
    if p.get("claim") is not None:
        w.store.get("claim", p["claim"])
    row = {"id": p["id"], "node": p.get("node"), "description": p["description"],
           "kind": p["kind"], "hypothesis": p.get("hypothesis"),
           "approach": p.get("approach"), "claim": p.get("claim"),
           "scope": p.get("scope"), "verification_status": "reported" if p.get("evidence") else "unverified",
           "status": "open", "evidence": p.get("evidence"), "reopen_condition": p.get("reopen_condition"),
           "author": actor, "created_at": now()}
    if p.get("evidence"):
        _check_evidence(w, p["evidence"])
    w.store.put("obstacle", row["id"], row, new=True)
    return row


def _check_evidence(w, evidence):
    if not isinstance(evidence, dict):
        raise ResearchError("obstacle evidence must be an object with artifact (and optional task)")
    artifact = evidence.get("artifact")
    if not isinstance(artifact, str) or not artifact.strip():
        raise ResearchError("obstacle evidence needs an artifact")
    w.store.verify_blob(artifact)
    if evidence.get("task") is not None:
        w.store.get("task", evidence["task"])


def evidence(w, p, actor):
    """Attach a negative-result artifact: moves unverified -> reported, never -> verified."""
    if not isinstance(p, dict):
        raise ResearchError("obstacle.evidence needs an object payload")
    row = w.store.get("obstacle", p.get("id"))
    reference = p.get("evidence")
    _check_evidence(w, reference)
    row.update(evidence=reference, verification_status="reported", reported_by=actor, reported_at=now())
    w.store.put("obstacle", row["id"], row)
    return row


def dispute(w, p, actor):
    row = w.store.get("obstacle", p.get("id"))
    reason = p.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        raise ResearchError("obstacle.dispute needs a reason")
    row.update(verification_status="disputed", dispute_reason=reason, disputed_by=actor,
               disputed_at=now())
    w.store.put("obstacle", row["id"], row)
    return row


def verified(w):
    return [row for row in w.store.all("obstacle") if row.get("verification_status") == "verified"]


def verify(w, p, actor):
    if not isinstance(p, dict):
        raise ResearchError("obstacle.verify needs an object payload")
    for key in ("id", "evidence"):
        if not isinstance(p.get(key), str) or not p[key].strip():
            raise ResearchError("obstacle.verify needs nonempty " + key)
    row = w.store.get("obstacle", p["id"])
    w.store.verify_blob(p["evidence"])
    row.update(verification_status="verified", evidence=p["evidence"], verified_by=actor,
               verified_at=now())
    w.store.put("obstacle", row["id"], row)
    return row


def status(w, p, actor):
    if not isinstance(p, dict):
        raise ResearchError("obstacle.status needs an object payload")
    row = w.store.get("obstacle", p.get("id"))
    target = p.get("status")
    if target not in STATUSES:
        raise ResearchError("obstacle status must be one of " + ", ".join(STATUSES))
    if target == "refuted":
        if row["kind"] != "mathematical_refutation" or row["verification_status"] != "verified":
            raise ResearchError("only a verified mathematical refutation can be marked refuted")
    row.update(status=target, status_by=actor, status_at=now())
    w.store.put("obstacle", row["id"], row)
    return row


HANDLERS = {"obstacle.add": add, "obstacle.verify": verify, "obstacle.status": status,
            "obstacle.evidence": evidence, "obstacle.dispute": dispute}

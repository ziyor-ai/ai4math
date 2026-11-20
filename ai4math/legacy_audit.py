"""Append-only audit trail for claims accepted before the trust policy existed.

Batch 8.2 P0 closure (frozen baseline v3.4 §0.2, §16). The 10 historical composite
claims keep their original receipts; what was missing is a first-class record saying
what an audit actually found. This module adds that record and nothing else:

    legacy.audit {id, claim, audit_kind, status, findings, evidence_refs?}

Statuses mirror ``verification.RECHECK`` where it applies:

    audited          the receipt was inspected against this claim's target
    unchecked        no audit was possible (missing targets, missing receipt)
    recheck_passed   a fresh replay under the current policy passed
    recheck_failed   a fresh replay failed; the claim keeps its historical receipt

Records are never edited: a second opinion is a new record. The audit never changes a
claim's ``evidence_level`` or ``status``; it only tells a reader whether the receipt
was checked and what was found.
"""
from .store import ResearchError, now

KINDS = ("receipt_targets", "inference_metadata", "replay", "statement_fidelity")
STATUSES = ("audited", "unchecked", "recheck_passed", "recheck_failed")
EXISTING = ("artifact", "claim", "inference", "validation", "task", "round_checkpoint",
            "round_settled", "research_finding")


def add(w, p, actor):
    if not isinstance(p, dict):
        raise ResearchError("legacy.audit needs an object payload")
    ident = p.get("id")
    if not isinstance(ident, str) or not ident.strip():
        raise ResearchError("legacy.audit needs an id")
    if w.store.exists("legacy_audit", ident):
        raise ResearchError("legacy audit already exists: " + ident)
    claim = w.store.get("claim", p.get("claim"))
    if claim.get("status") != "accepted":
        raise ResearchError("legacy.audit audits an accepted claim")
    kind = p.get("audit_kind")
    if kind not in KINDS:
        raise ResearchError("audit_kind must be one of " + ", ".join(KINDS))
    status = p.get("status")
    if status not in STATUSES:
        raise ResearchError("audit status must be one of " + ", ".join(STATUSES))
    findings = p.get("findings")
    if not isinstance(findings, list) or not findings or \
            any(not isinstance(item, str) or not item.strip() for item in findings):
        raise ResearchError("an audit needs at least one nonempty finding")
    refs = list(p.get("evidence_refs") or [])
    for ref in refs:
        if not any(w.store.exists(existing, ref) for existing in EXISTING):
            raise ResearchError("audit evidence_ref is not a registered object: " + str(ref))
    row = {"id": ident, "claim": claim["id"], "node": claim["node"],
           "audit_kind": kind, "status": status, "findings": list(findings),
           "evidence_refs": refs, "note": p.get("note"),
           "audited_by": actor, "audited_at": now()}
    w.store.put("legacy_audit", ident, row, new=True)
    return row


def for_claim(w, claim_id):
    return [row for row in w.store.all("legacy_audit") if row["claim"] == claim_id]


def view(w):
    rows = w.store.all("legacy_audit")
    return {"total": len(rows),
            "by_status": {status: len([row for row in rows if row["status"] == status])
                          for status in STATUSES},
            "by_kind": {kind: len([row for row in rows if row["audit_kind"] == kind])
                        for kind in KINDS},
            "claims": sorted({row["claim"] for row in rows})}


HANDLERS = {"legacy.audit": add}

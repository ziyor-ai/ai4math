"""Formalization requests: the event-driven Lean obligation (Batch 8.5, v3.5).

A claim may exist as a ``proof_candidate`` without any Lean attempt. A request marks
"this candidate is ready for a formalization attempt", carries its own small budget,
and closes in exactly one of three ways: a passing validation (``validated``), a
justified skip with frozen evidence (``skipped``), or budget exhaustion (``exhausted``).

Requests never block dispatch. The strict gate stays where it was: ``claim.accept``
still needs a validation for proved/disproved claims.
"""
from .store import ResearchError, now

TRIGGERS = ("proof_sketch_ready", "accept_attempt", "bridge_proposed", "manual")
STATUSES = ("pending", "consumed", "exhausted", "stale")
EVIDENCE_KINDS = ("artifact", "claim", "inference", "task", "validation", "round_checkpoint",
                  "round_settled", "research_finding", "obstacle")


def _limit(w):
    from .policy import current
    budget = current(w).get("formalization_budget") or {}
    value = budget.get("attempts", 2)
    if type(value) is not int or value < 1:
        raise ResearchError("formalization_budget.attempts must be a positive integer")
    return value


def for_claim(w, claim_id):
    return [row for row in w.store.all("formalization_request") if row["claim"] == claim_id]


def pending(w, claim_id=None):
    return [row for row in w.store.all("formalization_request")
            if row["status"] == "pending" and (claim_id is None or row["claim"] == claim_id)]


def request(w, p, actor):
    """Register (or return) the pending formalization request of one claim."""
    if not isinstance(p, dict):
        raise ResearchError("formalization.request needs an object payload")
    claim_id = p.get("claim")
    if not isinstance(claim_id, str) or not claim_id.strip():
        raise ResearchError("formalization.request needs a claim")
    claim = w.store.get("claim", claim_id)
    if claim["status"] != "proposed":
        raise ResearchError("only a proposed claim can request formalization")
    trigger = p.get("trigger", "manual")
    if trigger not in TRIGGERS:
        raise ResearchError("formalization trigger must be one of " + ", ".join(TRIGGERS))
    reason = p.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        raise ResearchError("formalization.request needs a reason")
    existing = pending(w, claim_id)
    if existing:
        return existing[0]                      # idempotent while one is pending
    used = len(for_claim(w, claim_id))
    limit = _limit(w)
    if used >= limit:
        raise ResearchError("formalization budget is exhausted for " + claim_id +
                            " (" + str(used) + "/" + str(limit) + "); skip or open a new claim")
    row = {"id": p.get("id") or "FR-" + claim_id + "-" + str(used + 1),
           "claim": claim_id, "trigger": trigger, "reason": reason.strip(),
           "status": "pending", "attempt": used + 1, "max_attempts": limit,
           "resolution": {"kind": None, "validation_ref": None, "reason": None},
           "created_by": actor, "created_at": now()}
    if w.store.exists("formalization_request", row["id"]):
        raise ResearchError("formalization request already exists: " + row["id"])
    w.store.put("formalization_request", row["id"], row, new=True)
    return row


def _close(w, row, kind, validation_ref, reason, actor):
    row.update(status="consumed",
               resolution={"kind": kind, "validation_ref": validation_ref,
                           "reason": reason},
               consumed_by=actor, consumed_at=now())
    w.store.put("formalization_request", row["id"], row)
    return row


def resolve(w, p, actor):
    """Close a request only when a passing validation already exists for its claim."""
    if not isinstance(p, dict):
        raise ResearchError("formalization.resolve needs an object payload")
    row = w.store.get("formalization_request", p.get("request"))
    if row["status"] != "pending":
        raise ResearchError("formalization request is already closed: " + row["id"])
    claim = w.store.get("claim", row["claim"])
    if claim["status"] == "revoked":
        raise ResearchError("claim was revoked; the request is stale")
    if claim["status"] not in {"proposed", "accepted"}:
        raise ResearchError("claim is " + claim["status"] + "; nothing to formalize")
    passed = [v for v in w.store.all("validation")
              if v.get("claim") == row["claim"] and v.get("passed")]
    if not passed:
        raise ResearchError("no passing validation for claim " + row["claim"] + " yet")
    return _close(w, row, "validated", passed[-1]["id"], p.get("reason"), actor)


def close_for_claim(w, claim_id, validation_ref, actor):
    """A passing validation or an acceptance closes the claim's pending requests."""
    rows = pending(w, claim_id)
    for row in rows:
        _close(w, row, "validated", validation_ref, "validation accepted", actor)
    return rows


def skip(w, p, actor):
    """Close a request with a stated reason and, optionally, frozen evidence."""
    if not isinstance(p, dict):
        raise ResearchError("formalization.skip needs an object payload")
    row = w.store.get("formalization_request", p.get("request"))
    if row["status"] != "pending":
        raise ResearchError("formalization request is already closed: " + row["id"])
    reason = p.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        raise ResearchError("formalization.skip needs a reason")
    evidence = p.get("evidence_ref")
    if evidence is not None:
        if not any(w.store.exists(kind, evidence) for kind in EVIDENCE_KINDS):
            raise ResearchError("formalization.skip evidence_ref is not a frozen, registered object")
    return _close(w, row, "skipped", None, reason + ("" if evidence is None else " [" + evidence + "]"),
                  actor)


def stale_for_claim(w, claim_id, actor):
    """A revoked claim makes every pending formalization of it stale."""
    rows = pending(w, claim_id)
    for row in rows:
        row.update(status="stale", stale_by=actor, stale_at=now())
        w.store.put("formalization_request", row["id"], row)
    return rows


def view(w):
    rows = w.store.all("formalization_request")
    return {"total": len(rows),
            "pending": sorted(row["id"] for row in rows if row["status"] == "pending"),
            "by_status": {status: len([row for row in rows if row["status"] == status])
                          for status in STATUSES},
            "validated": [row["id"] for row in rows
                          if row["status"] == "consumed"
                          and row["resolution"]["kind"] == "validated"]}


HANDLERS = {"formalization.request": request, "formalization.resolve": resolve,
            "formalization.skip": skip}

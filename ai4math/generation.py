"""Local generation obligations: a scoped, consumable request, never a global gate.

Batch 8.0 slice 8 (frozen baseline §4.12, §6.4c).

A request is bound to one (approach, obstacle, trigger) triple. It blocks only the
ordinary exploration of the affected route: verification, entailment analysis,
settlement and other routes keep running. Three ways close it — a generation, a
justified skip with checkable evidence, or a state-machine check that the trigger
itself disappeared.
"""
from .store import ResearchError, now, digest

STATUSES = ("pending", "consumed", "deferred")
ALLOWED_INTENT = {"entailment_analysis", "verification", "settlement"}


def _text(p, *keys):
    for key in keys:
        if not isinstance(p.get(key), str) or not p[key].strip():
            raise ResearchError("nonempty text required: " + key)


def rows(w):
    return w.store.all("generation_request")


def pending(w, route=None, node=None):
    out = []
    for row in rows(w):
        if row.get("status") != "pending":
            continue
        if route is not None and row.get("route") != route:
            continue
        if node is not None and row.get("parent") != node:
            continue
        out.append(row)
    return out


def request(w, p, actor):
    """Create (or return) the single pending request for one trigger triple."""
    if not isinstance(p, dict):
        raise ResearchError("generation.request needs an object payload")
    _text(p, "id", "trigger_reason")
    if w.store.exists("generation_request", p["id"]):
        raise ResearchError("generation request already exists: " + p["id"])
    route = p.get("route")
    if route is not None:
        w.store.get("route", route)
    parent = p.get("parent")
    if parent is not None:
        w.store.get("node", parent)
    obstacle = p.get("obstacle_id")
    if obstacle is not None:
        w.store.get("obstacle", obstacle)
    key = {"approach": p.get("approach_id"), "obstacle": obstacle,
           "trigger": p.get("trigger_event_id")}
    for row in rows(w):
        if (row.get("approach_id"), row.get("obstacle_id"), row.get("trigger_event_id")) == (
                key["approach"], key["obstacle"], key["trigger"]):
            return row                      # idempotent on the trigger triple
    record = {"id": p["id"], "route": route, "parent": parent,
              "approach_id": p.get("approach_id"), "obstacle_id": obstacle,
              "trigger_event_id": p.get("trigger_event_id"),
              "trigger_reason": p["trigger_reason"], "scope": "affected_route",
              "status": "pending",
              "resolution": {"kind": None, "event_ref": None, "evidence_ref": None},
              "created_by": actor, "created_at": now()}
    w.store.put("generation_request", record["id"], record, new=True)
    return record


def _close(w, row, kind, event_ref, evidence_ref, actor, reason):
    row.update(status="consumed",
               resolution={"kind": kind, "event_ref": event_ref,
                           "evidence_ref": evidence_ref, "reason": reason},
               consumed_by=actor, consumed_at=now())
    w.store.put("generation_request", row["id"], row)
    return row


def generate(w, p, actor):
    """Close a request with 0..3 candidates; an empty result needs a stated reason."""
    _text(p, "request")
    row = w.store.get("generation_request", p["request"])
    if row["status"] != "pending":
        raise ResearchError("generation request is already closed: " + row["id"])
    candidates = p.get("candidates") or []
    if not isinstance(candidates, list) or len(candidates) > 3:
        raise ResearchError("strategy.generate accepts 0..3 candidates")
    for candidate in candidates:
        if not isinstance(candidate, dict) or not isinstance(candidate.get("mechanism"), str) \
                or not candidate["mechanism"].strip():
            raise ResearchError("a candidate needs a mechanism description")
        if candidate.get("family") is None:
            raise ResearchError("a candidate needs a mechanism family")
    if not candidates:
        _text(p, "reason_no_candidate", "next_probe")
    record = {"id": "GEN-" + digest({"request": row["id"], "at": now()})[:12],
              "request": row["id"], "route": row.get("route"), "parent": row.get("parent"),
              "candidates": candidates,
              "reason_no_candidate": p.get("reason_no_candidate"),
              "next_probe": p.get("next_probe"), "author": actor, "created_at": now()}
    w.store.put("strategy_generation", record["id"], record, new=True)
    return _close(w, row, "generated", record["id"], None, actor, p.get("reason_no_candidate"))


def skip(w, p, actor):
    """Close a request with a mechanically checkable reason."""
    _text(p, "request", "reason")
    row = w.store.get("generation_request", p["request"])
    if row["status"] != "pending":
        raise ResearchError("generation request is already closed: " + row["id"])
    evidence = p.get("evidence_ref")
    if not isinstance(evidence, str) or not evidence.strip():
        raise ResearchError("generation.skip needs an evidence_ref")
    kinds = ("artifact", "claim", "inference", "obstacle", "task", "round_checkpoint",
             "round_settled", "validation")
    if not any(w.store.exists(kind, evidence) for kind in kinds):
        raise ResearchError("generation.skip evidence_ref is not a frozen, registered object")
    return _close(w, row, "justified_skip", None, evidence, actor, p["reason"])


def trigger_resolved(w, row):
    """True only when the trigger's own subject is gone or verified."""
    if row.get("trigger_reason") == "missing_entailment" and row.get("parent"):
        return any(r["parent"] == row["parent"] and
                   (r.get("verification") or {}).get("status") == "verified"
                   for r in w.store.all("inference") if r.get("rule") in ("case_split", "composition"))
    if row.get("trigger_reason") in {"missing_entailment", "no_progress_drought"} and row.get("parent"):
        if w.node_status(row["parent"]) in {"proved", "refuted"}:
            return True
    obstacle = row.get("obstacle_id")
    if obstacle:
        try:
            record = w.store.get("obstacle", obstacle)
        except ResearchError:
            record = None
        if record and record.get("verification_status") == "verified" and \
                record.get("status") in {"resolved", "refuted"}:
            return True
    return False


def resolve(w, p, actor):
    _text(p, "request")
    row = w.store.get("generation_request", p["request"])
    if row["status"] != "pending":
        raise ResearchError("generation request is already closed: " + row["id"])
    if not trigger_resolved(w, row):
        raise ResearchError("the trigger is still active; generate, skip with evidence, or close the obstacle")
    return _close(w, row, "trigger_resolved", None, row.get("obstacle_id"), actor,
                  "the state machine confirms the trigger no longer applies")


def check_dispatch(w, task):
    """Refuse ordinary exploration of an affected route while a request is pending."""
    from .ablation import disabled
    if disabled(w, "generation"):
        return
    if task.get("role") == "validate":
        return
    intent = task.get("intent_kind") or "exploration"
    if intent in ALLOWED_INTENT:
        return
    blocked = pending(w, route=task.get("route")) or pending(w, node=task.get("node"))
    if blocked:
        raise ResearchError("route has a pending generation obligation (" + blocked[0]["id"] +
                            "); run strategy.generate, skip with evidence, or resolve it")


def view(w):
    rows_ = rows(w)
    return {"pending": [r["id"] for r in rows_ if r.get("status") == "pending"],
            "consumed": [r["id"] for r in rows_ if r.get("status") == "consumed"],
            "total": len(rows_)}


HANDLERS = {"generation.request": request, "strategy.generate": generate,
            "generation.skip": skip, "generation.resolve": resolve}

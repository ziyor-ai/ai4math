"""Approaches: the strategy card with a real lifecycle (Batch 8.1, §4.12, §6.3).

candidate -> reviewed -> rejected | parked | selected -> exploring
exploring -> paused | retired | refuted

A candidate is not dispatchable; a retired approach leaves the active set and must
say what would reopen it; ``refuted`` requires a refuted hypothesis, never a model
opinion or a budget decision.
"""
from .store import ResearchError, digest, now

STATUSES = ("candidate", "reviewed", "rejected", "parked", "selected",
            "exploring", "paused", "retired", "refuted")
ACTIVE = ("exploring", "paused")
REVIEW_VERDICTS = ("rejected", "parked", "selected")


def add(w, p, actor):
    if not isinstance(p, dict):
        raise ResearchError("approach.add needs an object payload")
    for key in ("id", "node", "approach", "family"):
        if not isinstance(p.get(key), str) or not p[key].strip():
            raise ResearchError("approach.add needs nonempty " + key)
    if w.store.exists("approach", p["id"]):
        raise ResearchError("approach already exists: " + p["id"])
    node = w.store.get("node", p["node"])
    hypotheses = p.get("hypotheses") or []
    if not isinstance(hypotheses, list) or any(not isinstance(x, str) for x in hypotheses):
        raise ResearchError("hypotheses must be a list of hypothesis ids")
    for key in hypotheses:
        w.store.get("research_hypothesis", key)
    obstacle = p.get("core_obstacle")
    if obstacle is not None:
        w.store.get("obstacle", obstacle)
    bridge = p.get("bridge_to_goal") or {}
    if not isinstance(bridge, dict) or bridge.get("mode") not in {
            "entailment", "equivalence", "sufficient_condition", "construction"}:
        raise ResearchError("bridge_to_goal.mode must be entailment, equivalence, "
                            "sufficient_condition or construction")
    budget = p.get("budget") or {}
    if not isinstance(budget, dict):
        raise ResearchError("approach budget must be an object")
    row = {"id": p["id"], "node": node["id"], "approach": p["approach"],
           "family": p["family"], "status": "candidate", "hypotheses": list(hypotheses),
           "core_obstacle": obstacle, "key_insight": p.get("key_insight") or {},
           "required_lemmas": list(p.get("required_lemmas") or []),
           "bridge_to_goal": {**bridge, "audited": bool(bridge.get("audited", False))},
           "budget": {"rounds_soft": int(budget.get("rounds_soft", 6)),
                      "min_rounds_before_switch": int(budget.get("min_rounds_before_switch", 3)),
                      "hard_cap": budget.get("hard_cap")},
           "reopen_condition": None, "author": actor, "created_at": now()}
    w.store.put("approach", row["id"], row, new=True)
    return row


def review(w, p, actor):
    row = w.store.get("approach", p.get("id"))
    verdict = p.get("verdict")
    if verdict not in REVIEW_VERDICTS:
        raise ResearchError("approach review verdict must be " + ", ".join(REVIEW_VERDICTS))
    if row["status"] not in {"candidate", "reviewed"}:
        raise ResearchError("only a candidate can be reviewed (status " + row["status"] + ")")
    reason = p.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        raise ResearchError("an approach review needs a reason")
    if verdict == "rejected":
        differs = p.get("differs_from")
        if not isinstance(differs, str) or not differs.strip():
            raise ResearchError("a rejected approach must say what it differs from")
        row["differs_from"] = differs
    row.update(status=verdict if verdict != "selected" else "selected",
               review_reason=reason, reviewed_by=actor, reviewed_at=now())
    w.store.put("approach", row["id"], row)
    return row


def activate(w, p, actor):
    row = w.store.get("approach", p.get("id"))
    if row["status"] not in {"selected", "paused"}:
        raise ResearchError("only a selected or paused approach can start exploring")
    if w.node_status(row["node"]) != "open":
        raise ResearchError("approach target is no longer open")
    row.update(status="exploring", activated_by=actor, activated_at=now())
    w.store.put("approach", row["id"], row)
    return row


def status(w, p, actor):
    row = w.store.get("approach", p.get("id"))
    target = p.get("status")
    if target not in {"paused", "retired", "refuted"}:
        raise ResearchError("approach.status accepts paused, retired or refuted")
    if target == "paused":
        if row["status"] != "exploring":
            raise ResearchError("only an exploring approach can pause")
    elif target == "retired":
        condition = p.get("reopen_condition")
        if not isinstance(condition, str) or not condition.strip():
            raise ResearchError("a retired approach needs a reopen_condition")
        if row["status"] not in {"exploring", "paused", "selected"}:
            raise ResearchError("cannot retire an approach in status " + row["status"])
        row["reopen_condition"] = condition
    else:
        refuted = [h for h in row.get("hypotheses") or []
                   if w.store.get("research_hypothesis", h)["status"] == "refuted"]
        if not refuted:
            raise ResearchError("an approach is refuted only by a refuted hypothesis")
        row["refuted_by"] = sorted(refuted)
    row.update(status=target, status_by=actor, status_at=now())
    w.store.put("approach", row["id"], row)
    return row


SELECT_ACTIONS = ("continue", "switch", "parallel", "narrow", "pause", "retire", "probe")
MAPPING = {
    "continue": {"events": [], "decision": "continue"},
    "switch": {"events": [], "decision": "switch"},
    "parallel": {"events": [], "decision": "switch"},
    "narrow": {"events": ["approach.narrow"], "decision": "replan"},
    "pause": {"events": ["approach.narrowed_scope=pause"], "decision": "switch"},
    "retire": {"events": ["approach.status=retired"], "decision": None},
    "probe": {"events": ["research_intent.kind=probe"], "decision": None},
}


def narrow(w, p, actor):
    """Narrow an approach without pretending it was a replan of the whole plan."""
    row = w.store.get("approach", p.get("id"))
    reason = p.get("reason")
    new_scope = p.get("new_scope")
    if not isinstance(reason, str) or not reason.strip():
        raise ResearchError("approach.narrow needs a reason")
    if not isinstance(new_scope, str) or not new_scope.strip():
        raise ResearchError("approach.narrow needs the narrowed scope")
    history = list(row.get("narrowed") or [])
    history.append({"reason": reason, "old_scope": row.get("scope"),
                    "new_scope": new_scope, "at": now()})
    row["narrowed"] = history
    row["scope"] = new_scope
    w.store.put("approach", row["id"], row)
    return row


def select(w, p, actor):
    """Selection compatibility layer: state action first, dispatch action unchanged.

    The record states the mapped legacy decision so a caller can follow up with
    ``strategy.decide``; this op never fabricates a decision on the caller's behalf.
    """
    if not isinstance(p, dict):
        raise ResearchError("strategy.select needs an object payload")
    action = p.get("action")
    if action not in SELECT_ACTIONS:
        raise ResearchError("selection action must be one of " + ", ".join(SELECT_ACTIONS))
    reason = p.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        raise ResearchError("a selection needs a reason")
    approach_id = p.get("approach")
    row = None
    if action in {"narrow", "pause", "retire"}:
        if not isinstance(approach_id, str) or not approach_id.strip():
            raise ResearchError("a " + action + " selection needs an approach id")
        row = w.store.get("approach", approach_id)
    if action == "narrow":
        row = narrow(w, {"id": approach_id, "reason": reason, "new_scope": p.get("new_scope")}, actor)
    elif action == "pause":
        if row["status"] != "exploring":
            raise ResearchError("only an exploring approach can pause")
        row.update(status="paused", paused_reason=reason, status_by=actor, status_at=now())
        w.store.put("approach", row["id"], row)
    elif action == "retire":
        condition = p.get("reopen_condition")
        if not isinstance(condition, str) or not condition.strip():
            raise ResearchError("a retire selection needs a reopen_condition")
        if row["status"] not in {"exploring", "paused", "selected"}:
            raise ResearchError("cannot retire an approach in status " + row["status"])
        row.update(status="retired", reopen_condition=condition, retire_reason=reason,
                   status_by=actor, status_at=now())
        w.store.put("approach", row["id"], row)
    elif action == "probe":
        intent_id = p.get("intent")
        if not isinstance(intent_id, str) or not intent_id.strip():
            raise ResearchError("a probe selection needs a research_intent id")
        intent = w.store.get("research_intent", intent_id)
        if intent["kind"] != "probe":
            raise ResearchError("a probe selection needs an intent of kind probe")
    record = {"id": p.get("id") or ("SEL-" + str(digest({"action": action, "at": now()})[:10])),
              "action": action, "approach": approach_id, "reason": reason,
              "mapped_decision": MAPPING[action]["decision"], "events": MAPPING[action]["events"],
              "routes": list(p.get("routes") or []), "intent": p.get("intent"),
              "author": actor, "created_at": now()}
    if w.store.exists("selection", record["id"]):
        raise ResearchError("selection already exists: " + record["id"])
    w.store.put("selection", record["id"], record, new=True)
    return record


def active(w, node):
    rows = [r for r in w.store.all("approach") if r["node"] == node]
    if not rows:
        return None
    return [r for r in rows if r["status"] in ACTIVE or r["status"] == "selected"]


def view(w):
    rows = w.store.all("approach")
    return {"total": len(rows),
            "by_status": {status: len([r for r in rows if r["status"] == status])
                          for status in STATUSES},
            "exploring": sorted(r["id"] for r in rows if r["status"] == "exploring")}


HANDLERS = {"approach.add": add, "approach.review": review,
            "approach.activate": activate, "approach.status": status,
            "approach.narrow": narrow, "strategy.select": select}

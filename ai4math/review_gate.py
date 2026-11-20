"""Long-cycle review gate and stop rule.

The strategy clock reviews every few rounds.  This module adds the slower gate:
after a fixed number of rounds the loop must stop and record a gate review, and
a stalled obligation trips a halt that only a recorded decision can lift.
"""

from .store import ResearchError, digest, now

DEFAULT_GATE_INTERVAL = 20
DECISIONS = ("continue", "switch", "pause", "close")


def _row(workbench):
    from .strategy import state
    return state(workbench)


def interval(workbench):
    row = _row(workbench)
    if row and isinstance(row.get("gate_interval"), int) and row["gate_interval"] >= 1:
        return row["gate_interval"]
    return DEFAULT_GATE_INTERVAL


def _rounds(row):
    return int(row.get("checkpoint_rounds", 0) or 0)


def due(workbench):
    row = _row(workbench)
    if row is None:
        return False
    return _rounds(row) - row.get("gate_rounds", 0) >= interval(workbench)


def halted(workbench):
    row = _row(workbench)
    return bool(row and row.get("halt"))


def trip(workbench, reason):
    if workbench.store.pending is None:
        with workbench.store.transaction("gate.trip", "system"):
            return _trip(workbench, reason)
    return _trip(workbench, reason)


def _trip(workbench, reason):
    row = _row(workbench)
    if row is None or row.get("halt"):
        return row
    row["halt"] = {"reason": reason, "at": now(), "rounds": _rounds(row)}
    workbench.store.put("strategy_state", "main", row)
    return row


def check(workbench, role):
    from .strategy import RESEARCH_ROLES
    from .policy import strict_research
    if role not in RESEARCH_ROLES and not strict_research(workbench):
        return
    row = _row(workbench)
    if row is None:
        return
    halt = row.get("halt")
    if halt:
        raise ResearchError("research is halted: " + str(halt.get("reason")) +
                            "; record review-gate with a decision before dispatching")
    if due(workbench):
        raise ResearchError("review gate is due after " + str(_rounds(row) - row.get("gate_rounds", 0)) +
                            " rounds; record review-gate before dispatching")


def _last_dispatcher(workbench, roles=None):
    tasks = [task for task in workbench.store.all("task")
             if (task.get("dispatched_by") or task.get("executed_by")) and task.get("started_at")
             and (roles is None or task["role"] in roles)]
    if not tasks:
        return None
    tasks.sort(key=lambda task: task.get("started_at") or "")
    task = tasks[-1]
    return task.get("dispatched_by") or task.get("executed_by")


def review(workbench, payload, actor):
    if workbench.store.pending is None:
        with workbench.store.transaction("gate.review", actor):
            return review(workbench, payload, actor)
    for key in ("decision", "reason", "next_question"):
        if not isinstance(payload.get(key), str) or not payload[key].strip():
            raise ResearchError("gate review needs nonempty " + key)
    if payload["decision"] not in DECISIONS:
        raise ResearchError("gate decision must be one of " + ", ".join(DECISIONS))
    from .policy import strict_research
    from .strategy import RESEARCH_ROLES
    strict = strict_research(workbench)
    if actor == "human":
        if not isinstance(payload.get("attestation"), str) or not payload["attestation"].strip():
            raise ResearchError("a human gate review needs a nonempty attestation")
    elif actor == _last_dispatcher(workbench) or (strict and actor == _last_dispatcher(workbench, RESEARCH_ROLES)):
        raise ResearchError("the gate review needs an actor other than the last dispatcher; "
                            "use an independent reviewer or a human attestation")
    row = _row(workbench)
    if row is None:
        raise ResearchError("gate review requires an enabled workspace")
    from .health import snapshot
    state = snapshot(workbench)
    record = {"id": payload.get("id") or digest({"gate": _rounds(row), "decision": payload["decision"],
                                                 "reason": payload["reason"]}),
              "decision": payload["decision"], "reason": payload["reason"],
              "next_question": payload["next_question"], "rounds": _rounds(row),
              "actor": actor, "created_at": now(), "metrics": state["totals"], "signals": state["signals"],
              "evidence_level": "unknown"}
    if strict and payload["decision"] in {"continue", "switch"}:
        from . import obligation, research_flow
        record["next_action"] = research_flow.review_action(workbench, payload)
        continuation = payload.get("continuation")
        if continuation is not None:
            if (not isinstance(continuation, dict) or set(continuation) != {"node", "max_runs"}
                    or continuation["node"] != record["next_action"]["node"]
                    or type(continuation["max_runs"]) is not int
                    or not 1 <= continuation["max_runs"] <= obligation.limit(workbench)):
                raise ResearchError("continuation needs the reviewed node and max_runs within the obligation limit")
            account = obligation.view(workbench, continuation["node"])
            record["continuation"] = {**continuation, "base_runs": obligation.research_runs(account)}
    workbench.store.put("gate_review", record["id"], record, new=True)
    row["gate_rounds"] = _rounds(row)
    row["last_gate_review"] = record["id"]
    if payload["decision"] == "pause" or (strict and payload["decision"] == "close"):
        row["halt"] = {"reason": payload["reason"], "at": now(), "rounds": _rounds(row)}
    else:
        row.pop("halt", None)
    workbench.store.put("strategy_state", "main", row)
    return record


def brief(workbench):
    row = _row(workbench)
    if row is None:
        return {"enabled": False}
    since = _rounds(row) - row.get("gate_rounds", 0)
    previous = None
    if row.get("last_gate_review"):
        try:
            previous = workbench.store.get("gate_review", row["last_gate_review"])
        except ResearchError:
            previous = None
    return {"enabled": True, "due": since >= interval(workbench), "halted": bool(row.get("halt")),
            "halt": row.get("halt"), "interval": interval(workbench), "rounds_since_review": since,
            "rounds_to_gate": max(0, interval(workbench) - since),
            "last_review": None if previous is None else {k: previous[k] for k in (
                "id", "decision", "reason", "rounds", "next_action", "continuation") if k in previous}}

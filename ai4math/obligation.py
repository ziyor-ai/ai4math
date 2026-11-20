"""Per-obligation accounting.

A route is a plan for discharging one obligation.  The counters that decide
whether a line of attack is stuck belong to the obligation, not to the route:
registering a fresh route every round must not reset the stop rule.
"""

from .store import ResearchError, now

DEFAULT_STAGNATION_LIMIT = 12
DEBT_SOFT_LIMIT = 8
OUTCOMES = ("claim", "finding", "discharged", "falsified", "no-progress")


def limit(workbench):
    from .strategy import state as strategy_state
    row = strategy_state(workbench)
    if row and isinstance(row.get("obligation_stagnation_limit"), int):
        return row["obligation_stagnation_limit"]
    return DEFAULT_STAGNATION_LIMIT


def _row(workbench, node, create=False):
    try:
        return workbench.store.get("obligation", node)
    except ResearchError:
        pass
    if not create:
        return None
    row = {"id": node, "node": node, "runs": 0, "rounds": 0, "stagnant": 0, "claims": [],
           "findings": [], "outcomes": [], "last_outcome": None, "last_round_at": None,
           "routes": [], "release": None, "updated_at": now()}
    workbench.store.put("obligation", node, row, new=True)
    return row


def track(workbench, node):
    """Create the obligation row and report the counters a new route inherits."""
    row = _row(workbench, node, create=True)
    return {"rounds": row["rounds"], "runs": row["runs"], "stagnant": row["stagnant"],
            "routes": list(row["routes"]), "claims": len(row["claims"])}


def record_dispatch(workbench, task):
    store = workbench.store
    task_id, node = task["id"], task["node"]
    row = _row(workbench, node, create=True)
    if store.exists("task_dispatch", task_id):
        raise ResearchError("task already has a dispatch receipt: " + task_id)
    dispatch = {key: task[key] for key in ("id", "node", "route", "role", "started_at")}
    store.put("task_dispatch", task_id, dispatch, new=True)
    row["runs"] += 1
    by_role = row.setdefault("runs_by_role", {})
    by_role[task["role"]] = by_role.get(task["role"], 0) + 1
    if task["route"] not in row["routes"]:
        row["routes"].append(task["route"])
    row["updated_at"] = now()
    store.put("obligation", node, row)
    return row


def rebuild_dispatches(workbench):
    """Build the v2c4 projection from the earlier, canonical start events."""
    store = workbench.store
    for row in store.all("obligation"):
        row.update(runs=0, runs_by_role={}, routes=[], decision_changes=0,
                   closures=0, rounds_since_closure=0)
        for entry in row["outcomes"]:
            _count_progress(row, entry.get("progress") or {})
        store.put("obligation", row["node"], row)
    for event in store.export_events():
        if event["operation"] not in {"task.start", "task.start_external"}:
            continue
        for change in event["changes"]:
            task = change["value"]
            if change["kind"] == "task" and task.get("status") == "running":
                record_dispatch(workbench, task)


def _count_progress(row, progress):
    decisions = len(progress.get("decision_changes", []))
    closures = len(progress.get("validated_discharged_ids", []))
    row["decision_changes"] = row.get("decision_changes", 0) + decisions
    row["closures"] = row.get("closures", 0) + closures
    row["rounds_since_closure"] = 0 if closures else row.get("rounds_since_closure", 0) + 1


def research_runs(row):
    roles = row.get("runs_by_role", {})
    return sum(roles.get(role, 0) for role in ("explore", "produce"))


def has_continuation(workbench, row):
    from .strategy import state
    strategy = state(workbench) or {}
    review = strategy.get("last_gate_review")
    continuation = workbench.store.get("gate_review", review).get("continuation") if review else None
    return bool(continuation and continuation["node"] == row["node"] and
                research_runs(row) - continuation["base_runs"] < continuation["max_runs"])


def record_round(workbench, row, route, outcome, claims, findings, progress):
    if outcome not in OUTCOMES:
        raise ResearchError("round outcome must be one of " + ", ".join(OUTCOMES))
    row["rounds"] += 1
    row["last_outcome"] = outcome
    row["outcomes"].append({"outcome": outcome, "route": route["id"], "claims": list(claims),
                            "findings": list(findings), "progress": progress, "at": now()})
    row["findings"] = sorted(set(row.get("findings", []) + findings))
    row["claims"] = sorted(set(row["claims"] + claims))
    row["progress_credit_keys"] = sorted(set(row.get("progress_credit_keys", []) + progress["credit_keys"]))
    row["progress_closure_claim_ids"] = sorted(set(row.get("progress_closure_claim_ids", []) +
                                                   progress["closure_claim_ids"]))
    row["stagnant"] = 0 if progress["advanced"] else row["stagnant"] + 1
    from .policy import strict_research
    strict = strict_research(workbench)
    if strict:
        _count_progress(row, progress)
    row["last_round_at"] = now()
    row["updated_at"] = now()
    workbench.store.put("obligation", route["node"], row)
    from .strategy import state as strategy_state
    strategy = strategy_state(workbench)
    if strategy is not None:
        strategy["checkpoint_rounds"] = strategy.get("checkpoint_rounds", 0) + 1
        workbench.store.put("strategy_state", "main", strategy)
    spent = row.get("rounds_since_closure", 0) if strict else row["stagnant"]
    if spent >= limit(workbench) and not (strict and has_continuation(workbench, row)):
        from .review_gate import trip
        trip(workbench, "obligation " + route["node"] + " reached its stagnation limit")
    return row


def check(workbench, node):
    """Refuse new work on an obligation whose stagnation limit is spent."""
    row = _row(workbench, node)
    if row is None:
        return None
    bound = limit(workbench)
    from .policy import strict_research
    if strict_research(workbench):
        if row.get("rounds_since_closure", 0) >= bound and not has_continuation(workbench, row):
            raise ResearchError("obligation " + node + " reached the no-closure limit; "
                                "record an independent gate review with a bounded continuation")
        return row
    if row["stagnant"] >= bound:
        raise ResearchError(
            "obligation " + node + " reached its stagnation limit (" + str(row["stagnant"]) + "/" + str(bound) +
            " rounds without credited progress); register a strategy.replan or switch, or close the obligation")
    return row


def release(workbench, node, decision):
    """A reviewed decision restarts the clock for the selected obligation."""
    row = _row(workbench, node)
    if row is None:
        return None
    row["stagnant"] = 0
    row["release"] = {"decision": decision, "at": now()}
    row["updated_at"] = now()
    workbench.store.put("obligation", node, row)
    return row


def view(workbench, node):
    row = _row(workbench, node)
    if row is None:
        return {"node": node, "runs": 0, "rounds": 0, "stagnant": 0, "claims": 0,
                "last_outcome": None, "limit": limit(workbench)}
    result = {"node": node, "runs": row["runs"], "rounds": row["rounds"], "stagnant": row["stagnant"],
              "claims": len(row["claims"]), "last_outcome": row["last_outcome"],
              "limit": limit(workbench), "release": row["release"]}
    from .policy import strict_research
    if strict_research(workbench):
        result.update(runs_by_role=row.get("runs_by_role", {}), decision_changes=row.get("decision_changes", 0),
                      closures=row.get("closures", 0), rounds_since_closure=row.get("rounds_since_closure", 0))
    return result


def debt(workbench):
    """Open obligation count, used by the net-debt rule on node.add.

    Batch 8.0: proof debt only. Exploration candidates have their own budget and
    must not inflate (or be inflated by) the goal's proof obligations.
    """
    from .scope import debt as proof_debt
    return proof_debt(workbench)

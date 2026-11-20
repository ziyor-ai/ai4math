"""One call that lists every dispatch prerequisite (Batch 8.x audit fix F12).

The gates are layered (strategy decision -> literature plan -> generation -> review gate
-> route budget). Each raises its own error, so a caller discovers them one at a time.
``task.preflight`` runs the same read-only checks and returns the full list plus the
order in which they must be satisfied. It never writes.
"""
from .store import ResearchError

RESEARCH_ROLES = {"explore", "produce"}


def preflight(w, p, actor):
    if not isinstance(p, dict):
        raise ResearchError("task.preflight needs an object payload")
    route_id = p.get("route")
    if not isinstance(route_id, str) or not route_id.strip():
        raise ResearchError("task.preflight needs a route")
    role = p.get("role") or "explore"
    route = w.store.get("route", route_id)
    blockers = []
    if route["status"] != "active":
        blockers.append({"gate": "route", "reason": "route is " + route["status"] +
                         "; re-open with route.status active and a reason"})
    if route["runs"] >= route["max_runs"]:
        blockers.append({"gate": "route_budget", "reason": "route run budget exhausted (" +
                         str(route["runs"]) + "/" + str(route["max_runs"]) + ")"})
    from .strategy import state as strategy_state, due
    settings = strategy_state(w)
    if settings is None:
        blockers.append({"gate": "strategy", "reason": "strategy review is not enabled"})
    else:
        if due(settings):
            blockers.append({"gate": "strategy", "reason": "global strategy review is due; "
                             "run strategy-brief then strategy.decide"})
        if settings.get("focus_routes") and role in RESEARCH_ROLES and route_id not in settings["focus_routes"]:
            blockers.append({"gate": "strategy", "reason": "route is outside the reviewed focus routes: " +
                             ", ".join(settings["focus_routes"])})
        if role in RESEARCH_ROLES and settings.get("decision") is None:
            blockers.append({"gate": "literature", "reason": "no strategy decision yet; "
                             "literature.search and literature.plan must cite one"})
    from .review_gate import halted, due as gate_due
    if halted(w):
        blockers.append({"gate": "review_gate", "reason": "the review gate is halted"})
    elif gate_due(w):
        blockers.append({"gate": "review_gate", "reason": "an independent review is due"})
    from .generation import pending
    requests = pending(w, route=route_id)
    if requests and role not in {"validate"}:
        blockers.append({"gate": "generation", "reason": "pending generation obligation: " +
                         requests[0]["id"] + "; generate, skip with evidence, or resolve it"})
    sequence = ["strategy-brief -> strategy.decide (first decision must be 'switch' to the route)",
                "literature.search + literature.plan citing that decision",
                "task.add (role=explore/produce needs start-external; role=validate runs locally)"]
    return {"route": route_id, "role": role, "ready": not blockers,
            "blockers": blockers, "sequence": sequence}


HANDLERS = {"task.preflight": preflight}

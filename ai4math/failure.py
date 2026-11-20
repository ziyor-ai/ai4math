"""Failure ledger: repeated failures on one route stop the loop.

A failed or timed-out task is not progress.  This module records every terminal
failure, keeps a per-route streak and parks the route (and halts the workspace)
when the streak reaches the configured retry limit.
"""

from .store import ResearchError, digest, now

DEFAULT_RETRY_LIMIT = 2
FAILURE_STATES = ("failed", "unknown_timeout", "output_limit", "interrupted")


def limit(workbench):
    from .strategy import state
    row = state(workbench)
    if row and isinstance(row.get("retry_limit"), int):
        return row["retry_limit"]
    return DEFAULT_RETRY_LIMIT


def state(workbench, route_id):
    try:
        return workbench.store.get("failure_state", route_id)
    except ResearchError:
        return None


def record(workbench, task, status, reason=None):
    store = workbench.store
    route_id = task.get("route")
    entry = {"id": digest({"task": task.get("id"), "status": status, "at": now()}),
             "task": task.get("id"), "route": route_id,
             "node": task.get("node"), "status": status, "reason": reason,
             "at": now(), "actor": task.get("executed_by") or task.get("dispatched_by")}
    try:
        store.get("failure", entry["id"])
    except ResearchError:
        store.put("failure", entry["id"], entry, new=True)
    row = state(workbench, route_id) or {"id": route_id, "route": route_id, "streak": 0, "total": 0, "last": None}
    row["streak"] += 1
    row["total"] += 1
    row["last"] = {"task": entry["task"], "status": status, "at": entry["at"]}
    store.put("failure_state", route_id, row)
    if row["streak"] >= limit(workbench):
        route = store.get("route", route_id)
        if route["status"] == "active":
            route.update(status="parked", status_reason="repeated failure")
            store.put("route", route_id, route)
        from .review_gate import trip
        trip(workbench, "route " + route_id + " failed " + str(row["streak"]) + " times in a row")
    return row


def clear(workbench, route_id):
    row = state(workbench, route_id)
    if row is None or not row.get("streak"):
        return row
    row["streak"] = 0
    workbench.store.put("failure_state", route_id, row)
    return row


def summary(workbench):
    rows = workbench.store.all("failure_state")
    failures = workbench.store.all("failure")
    return {"total": len(failures), "streaks": {row["route"]: row["streak"] for row in rows if row.get("streak")},
            "limit": limit(workbench)}

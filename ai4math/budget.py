"""Exploration round budget: reserved at open, counted when settled (v3.4 §6.8).

One accounting unit: the exploration round. A round reserves a slot while it is open,
running or assessed, and counts as spent once it is settled. Re-opening or re-settling
the same round never spends a second slot, because the round id is the unit.
"""
from .store import ResearchError

CLOSED = {"settled", "aborted"}


def usage(w):
    routes = {row["id"]: row for row in w.store.all("route")}
    settled_ids = {row["id"] for row in w.store.all("round_settled")}
    settled = 0
    open_rounds = []
    for item in w.store.all("round"):
        route = routes.get(item.get("route"))
        if route is None or route.get("kind") != "exploration":
            continue
        if item["id"] in settled_ids:
            settled += 1
        elif item["state"] not in CLOSED:
            open_rounds.append(item["id"])
    return {"settled": settled, "open": len(open_rounds), "open_rounds": sorted(open_rounds)}


def reserve(w, route):
    """Refuse a new exploration round when the workspace budget is already spent."""
    if route.get("kind") != "exploration":
        return usage(w)
    from .scope import rounds_limit
    limit = rounds_limit(w)
    current = usage(w)
    if current["settled"] + current["open"] >= limit:
        raise ResearchError("exploration round budget is exhausted (settled " +
                            str(current["settled"]) + " + open " + str(current["open"]) +
                            " / " + str(limit) + "); settle or abort a round first")
    return current

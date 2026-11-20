"""Ablation switches (Batch 8.4, frozen baseline §4.13).

An ablation run must be *labelled*, not silently different. The switches only change
which process gates are enforced; they never change how a proof is judged.
"""
from .store import ResearchError, now

MODULES = ("critique", "sketch", "generation", "inference", "verification", "intent")


def configure(w, p, actor):
    if not isinstance(p, dict):
        raise ResearchError("ablation.configure needs an object payload")
    disable = p.get("disable")
    if not isinstance(disable, list) or any(not isinstance(x, str) for x in disable):
        raise ResearchError("disable must be a list of module names")
    unknown = sorted(set(disable) - set(MODULES))
    if unknown:
        raise ResearchError("unknown ablation module: " + ", ".join(unknown))
    from .policy import current
    row = current(w)
    row.update(ablation={"disable": sorted(set(disable))}, ablated_by=actor, ablated_at=now())
    w.store.put("policy", "main", row)
    return {"disable": sorted(set(disable))}


def state(w):
    from .policy import current
    row = current(w)
    return {"disable": list((row.get("ablation") or {}).get("disable") or [])}


def disabled(w, module):
    return module in state(w)["disable"]


HANDLERS = {"ablation.configure": configure}

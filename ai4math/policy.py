"""Workspace write policy: the framework injects the version, callers never supply it.

Frozen baseline `docs/AI4Math_Batch8_研究策略改造方案_v3.4_代码落点与接口契约修订稿.md`
§4.1 separates two things that used to be one:

  record_schema_version     label of an already stored record; read compatibility only
  workspace_policy_version  which rules a *new* write must satisfy

A caller that supplies either field is rejected outright. Silently ignoring it would
let the caller believe a version was applied that the framework never used, which is
exactly the bypass the v3.3 review found.
"""
from .store import ResearchError, now

VERSIONS = ("v2c0", "v2c1", "v2c2", "v2c3", "v2c4")
CLIENT_KEYS = ("record_schema_version", "workspace_policy_version",
               "min_write_schema", "require_intent", "compliance")
DEFAULT = {"id": "main", "workspace_policy_version": "v2c0", "min_write_schema": "v2c0",
           "require_intent": False, "compliance": "formal"}


def guard(payload):
    """Reject caller-supplied policy / schema fields before any handler runs."""
    if not isinstance(payload, dict):
        return
    supplied = sorted(key for key in CLIENT_KEYS if key in payload)
    if supplied:
        raise ResearchError(
            "the framework injects " + ", ".join(supplied) +
            " from the workspace policy; a caller must not supply it (batch 8 contract §4.1)")


def current(w):
    rows = w.store.all("policy")
    row = dict(rows[0]) if rows else dict(DEFAULT)
    if row.get("workspace_policy_version") not in VERSIONS:
        raise ResearchError("workspace policy version is not recognized")
    return row


def require_intent(w):
    from .ablation import disabled
    if disabled(w, "intent"):
        return False
    return bool(current(w).get("require_intent"))


def allow_local_research(w):
    """Explicit opt-in: explore/produce tasks may run in the local process.

    Off by default. Enabling it removes the literature-gated external dispatch
    requirement; the local task still runs under the normal workspace sandbox and
    still cannot accept any claim without a validation task.
    """
    return bool(current(w).get("local_research"))


def min_write_schema(w):
    return current(w).get("min_write_schema")


def strict_research(w):
    return current(w)["workspace_policy_version"] == "v2c4"


def route_kind_required(w):
    """v2c1+ requires every new route to name its kind (v3.4 §7)."""
    return _rank(current(w)["workspace_policy_version"]) >= 1


def proof_route_required(w):
    """v2c1+ requires every new claim to name its proof route (v3.4 §7)."""
    return _rank(current(w)["workspace_policy_version"]) >= 1


def _rank(version):
    if version not in VERSIONS:
        raise ResearchError("unknown workspace policy version: " + str(version))
    return VERSIONS.index(version)


def migrate(w, p, actor):
    """Advance the workspace policy; never backwards, never by request on a write."""
    if not isinstance(p, dict):
        raise ResearchError("policy.migrate needs an object payload")
    target = p.get("to")
    if not isinstance(target, str) or target not in VERSIONS:
        raise ResearchError("policy.migrate needs to in " + ", ".join(VERSIONS))
    row = current(w)
    before = row["workspace_policy_version"]
    if _rank(target) <= _rank(before):
        raise ResearchError("workspace policy only moves forward: " + before + " -> " + target)
    unsettled = [item["id"] for item in w.store.all("round")
                 if item["state"] in {"open", "running", "assessed"}]
    if unsettled:
        raise ResearchError("cannot migrate with unsettled rounds: " + ", ".join(sorted(unsettled)) +
                            "; settle or abort them first (v3.4 §10)")
    if target == "v2c4":
        active = [task["id"] for task in w.store.all("task") if task["status"] in {"pending", "running"}]
        if active:
            raise ResearchError("settle running tasks or abandon pending contracts before migrating: " +
                                ", ".join(sorted(active)))
        from .obligation import rebuild_dispatches
        rebuild_dispatches(w)
    row.update(workspace_policy_version=target, min_write_schema=target,
               require_intent=_rank(target) >= 1, compliance="formal",
               migrated_by=actor, migrated_at=now(), previous=before)
    w.store.put("policy", "main", row)
    return row


def configure(w, p, actor):
    """Adjust workspace budgets; never a record version or compliance claim."""
    if not isinstance(p, dict):
        raise ResearchError("policy.configure needs an object payload")
    budget = p.get("exploration_budget")
    formalization = p.get("formalization_budget")
    share = p.get("lean_budget_share")
    local_research = p.get("local_research")
    if budget is None and formalization is None and share is None and local_research is None:
        raise ResearchError("policy.configure needs exploration_budget, formalization_budget, "
                            "lean_budget_share or local_research")
    row = current(w)
    if local_research is not None:
        if type(local_research) is not bool:
            raise ResearchError("local_research must be a boolean")
        row["local_research"] = local_research
    if budget is not None:
        if not isinstance(budget, dict) or not budget:
            raise ResearchError("exploration_budget must be a nonempty object")
        for key, value in budget.items():
            if key not in {"rounds", "registrations"}:
                raise ResearchError("unknown exploration budget key: " + str(key))
            if type(value) is not int or value < 0:
                raise ResearchError("exploration_budget." + key + " must be a non-negative integer")
        merged = dict(row.get("exploration_budget") or {})
        merged.update(budget)
        row["exploration_budget"] = merged
    if formalization is not None:
        if not isinstance(formalization, dict) or not formalization:
            raise ResearchError("formalization_budget must be a nonempty object")
        for key, value in formalization.items():
            if key != "attempts":
                raise ResearchError("unknown formalization budget key: " + str(key))
            if type(value) is not int or value < 1:
                raise ResearchError("formalization_budget.attempts must be a positive integer")
        merged = dict(row.get("formalization_budget") or {})
        merged.update(formalization)
        row["formalization_budget"] = merged
    if share is not None:
        if type(share) is not float or not 0.0 <= share <= 1.0:
            raise ResearchError("lean_budget_share must be a float in [0, 1]")
        row["lean_budget_share"] = share
    row.update(configured_by=actor, configured_at=now())
    w.store.put("policy", "main", row)
    return row

"""Read-only changes to the locked proof plan across an audited event interval."""

import json
from types import SimpleNamespace

from .progress_evidence import claim_evidence
from .store import ResearchError, digest


KINDS = {"project", "node", "claim", "validation", "artifact", "checker_policy", "inference",
         "strategy_state", "route", "round", "round_settled", "round_checkpoint", "task",
         "obstacle", "approach", "correspondence"}


class Projection:
    """The small read surface used by verification, restricted to one event prefix."""

    def __init__(self, source):
        self.source = source
        self.groups = {kind: {} for kind in KINDS}
        self.revisions = {}

    def get(self, kind, key):
        if not self.exists(kind, key):
            raise ResearchError(f"missing {kind} at event cutoff: {key}")
        return self.groups[kind][key]

    def exists(self, kind, key):
        return key in self.groups.get(kind, {})

    def all(self, kind):
        return list(self.groups.get(kind, {}).values())

    def revision(self, kind, key):
        return self.revisions.get((kind, key), 0)

    def verify_blob(self, sha):
        self.get("artifact", sha)
        return self.source.verify_blob(sha)

    def copy(self):
        snapshot = Projection(self.source)
        snapshot.groups = {kind: dict(rows) for kind, rows in self.groups.items()}
        snapshot.revisions = self.revisions.copy()
        return snapshot

    def apply(self, change, seq):
        kind, key = change["kind"], change["id"]
        if kind in self.groups:
            self.groups[kind][key] = change["value"]
            self.revisions[(kind, key)] = seq


def _history(store, from_event):
    if from_event is not None and (type(from_event) is not int or from_event < 1):
        raise ResearchError("impact-from-event needs a positive event sequence")
    current = Projection(store)
    previous = {"seq": 0, "sha256": "0" * 64, "timestamp": None}
    before = first_goal = None
    latest_decision = None
    for row in store.db.execute("SELECT * FROM events ORDER BY seq"):
        event = {k: row[k] for k in ("seq", "timestamp", "operation", "actor", "previous")}
        event["changes"] = json.loads(row["changes"])
        seq = event["seq"]
        if seq != previous["seq"] + 1 or event["previous"] != previous["sha256"] or digest(event) != row["hash"]:
            raise ResearchError(f"cannot project goal impact from damaged event chain at {seq}")
        for change in event["changes"]:
            if change["kind"] == "strategy_decision":
                decision = change["value"]
                latest_decision = {"id": change["id"], "event": seq,
                                   "basis_brief_sha256": (decision.get("brief") or {}).get("brief_sha256"),
                                   "focus_routes": decision.get("focus_routes") or []}
                if from_event is None:
                    before = (current.copy(), previous, latest_decision)
        for change in event["changes"]:
            current.apply(change, seq)
        previous = {"seq": seq, "sha256": row["hash"], "timestamp": row["timestamp"]}
        if from_event == seq:
            before = (current.copy(), previous, latest_decision)
        elif from_event is None and first_goal is None and current.exists("project", "main"):
            if current.get("project", "main").get("goal"):
                first_goal = (current.copy(), previous, latest_decision)
    if from_event is not None and before is None:
        raise ResearchError("impact-from-event is beyond the workspace's event history")
    if before is None:
        before = first_goal
    return before, current, previous, latest_decision


def _plan(snapshot):
    if not snapshot.exists("project", "main"):
        return None, {}
    goal = snapshot.get("project", "main").get("goal")
    nodes = {}
    pending = [goal] if goal else []
    while pending:
        key = pending.pop()
        if key in nodes:
            continue
        node = snapshot.get("node", key)
        deps = node.get("proof_dependencies") or []
        nodes[key] = {"id": key, "statement": node["statement"], "statement_digest": node["statement_digest"],
                      "scope": node["quantifier"], "domain": node["domain"], "proof_dependencies": deps,
                      "statement_dependencies": node.get("statement_dependencies") or []}
        pending.extend(deps)
    return goal, nodes


def _proof_state(snapshot, plan):
    state = {key: {**row, "status": "open", "evidence": [], "unverified_claim_ids": []}
             for key, row in plan.items()}
    workspace = SimpleNamespace(store=snapshot)
    cache = {}
    for claim in snapshot.all("claim"):
        node = state.get(claim["node"])
        if node is None or claim.get("status") != "accepted":
            continue
        proof = claim_evidence(workspace, claim, cache=cache)
        if proof is None:
            if claim.get("evidence_level") in {"proved", "disproved"}:
                node["unverified_claim_ids"].append(claim["id"])
            continue
        node["evidence"].append(proof)
    for node in state.values():
        levels = {row["evidence_level"] for row in node["evidence"]}
        if len(levels) == 1:
            node["status"] = levels.pop()
        elif levels:
            node["status"] = "conflicting"
    return state


def _plan_digest(goal, plan):
    return digest({"goal": goal, "nodes": {key: {"statement_digest": row["statement_digest"],
                                                  "proof_dependencies": row["proof_dependencies"]}
                                            for key, row in plan.items()}})


def _settlements(snapshot, start, in_plan):
    rows, changes, affected = [], [], set()
    for settled in sorted(snapshot.all("round_settled"), key=lambda row: snapshot.revision("round_settled", row["id"])):
        event = snapshot.revision("round_settled", settled["id"])
        if event <= start:
            continue
        checkpoint = snapshot.get("round_checkpoint", settled["checkpoint"])
        record = snapshot.get("round", settled["round"])
        node = record["node"]
        affected.add(node)
        tasks = [snapshot.get("task", task) for task in record["tasks"]]
        decisions = sorted({t["dispatched_under_strategy"] for t in tasks if t.get("dispatched_under_strategy")})
        progress = settled.get("progress") or {}
        rows.append({"id": settled["id"], "event": event, "checkpoint": checkpoint["id"],
                     "node": node, "route": settled["route"], "decision_ids": decisions,
                     "in_proof_plan": node in in_plan, "evidence_qualification": "progress" if progress else "legacy",
                     "completed_task_ids": [t["id"] for t in tasks if t["status"] == "completed"],
                     "outcome": settled["outcome"]})
        for change in progress.get("decision_changes") or []:
            changes.append({**change, "round": settled["id"], "event": event,
                            "in_proof_plan": change.get("node", node) in in_plan})
    return rows, changes, affected


def _mapped_approaches(snapshot, obstacle):
    """Keep obstacle identity explicit when a different representation refers to it."""
    mappings = []
    for approach in snapshot.all("approach"):
        if approach.get("core_obstacle") != obstacle["id"]:
            continue
        node = approach["node"]
        if node == obstacle.get("node"):
            mappings.append({"id": approach["id"], "node": node})
            continue
        # A cross-node link requires a correspondence on the locked target
        # that names both objects exactly. Shared names and text similarity
        # carry no mathematical correspondence here.
        record = max((row for row in snapshot.all("correspondence") if row["node"] == obstacle.get("node")),
                     key=lambda row: snapshot.revision("correspondence", row["id"]), default=None)
        if record and record["verdict"] == "aligned" and any(
                row.get("status") == "mapped" and row.get("model_claim") == node
                and row.get("locked_target") == obstacle["id"] for row in record["mapping"]):
            try:
                for art in record["artifacts"]:
                    snapshot.verify_blob(art)
            except (OSError, ResearchError):
                continue
            mappings.append({"id": approach["id"], "node": node, "correspondence": record["id"],
                             "artifact_ids": list(record["artifacts"])})
    return mappings


def _summary(basis, final, anchor, latest_decision):
    initial, start, basis_decision = basis
    before_goal, before_plan = _plan(initial)
    goal, plan = _plan(final)
    if not goal or before_goal != goal:
        raise ResearchError("goal impact requires the same locked goal at both event cutoffs")
    before = _proof_state(initial, before_plan)
    after = _proof_state(final, plan)
    tested_rounds, decision_changes, affected = _settlements(final, start["seq"], after)
    added = after.keys() - before.keys()
    removed = before.keys() - after.keys()
    common = before.keys() & after.keys()
    reopened = sorted(key for key in common if before[key]["status"] == "proved" and after[key]["status"] != "proved")
    discharged = sorted(key for key in after if after[key]["status"] == "proved"
                        and before.get(key, {}).get("status") != "proved")
    refuted = sorted(key for key in after if after[key]["status"] == "disproved")
    changed_plan = sorted(key for key in common if before[key]["proof_dependencies"] != after[key]["proof_dependencies"])
    remaining = sorted(key for key in after if after[key]["status"] != "proved")

    focus = (latest_decision or {}).get("focus_routes", [])
    candidates = {final.get("route", key)["node"] for key in focus if final.exists("route", key)}
    counterfactual = [{"assume_proved": key, "statement_digest": final.get("node", key)["statement_digest"],
                       "in_proof_plan": key in plan,
                       "remaining_in_recorded_plan": [node for node in remaining if node != key]}
                      for key in sorted(candidates) if after.get(key, {}).get("status", "open") == "open"]
    for node in after.values():
        node["unresolved_proof_dependencies"] = [dep for dep in node["proof_dependencies"] if dep in remaining]
    obstacles = [{"id": o["id"], "node": o["node"], "description": o["description"],
                  "verification_status": o.get("verification_status"), "evidence": o.get("evidence"),
                  "approaches": _mapped_approaches(final, o)}
                 for o in final.all("obstacle") if o.get("node") in plan and o.get("status") == "open"]
    return {"schema_version": 1, "mode": "shadow", "goal": goal, "goal_statement_digest": plan[goal]["statement_digest"],
            "before": {**start, "proof_plan_sha256": _plan_digest(goal, before_plan)},
            "after": {**anchor, "proof_plan_sha256": _plan_digest(goal, plan)},
            "basis_decision": basis_decision, "current_decision": latest_decision,
            "settled_rounds": tested_rounds, "decision_changes": decision_changes,
            "affected_obligation_ids": sorted(affected & after.keys() | added | set(reopened + discharged + changed_plan)),
            "validated_discharged_ids": discharged, "reopened_ids": reopened, "refuted_ids": refuted,
            "new_open_obligation_ids": sorted(key for key in added if key in remaining),
            "removed_from_plan_ids": sorted(removed), "changed_plan_ids": changed_plan,
            "remaining_obligation_ids": remaining, "obligations": [after[key] for key in sorted(after)],
            "open_bottlenecks": obstacles,
            "counterfactual": {"kind": "hypothetical_no_propagation", "cases": counterfactual}}


def view(w, *, from_event=None):
    """Read an event range without changing records, the brief digest or dispatch."""
    with w.store.snapshot():
        basis, final, anchor, latest = _history(w.store, from_event)
        if basis is None:
            return {"schema_version": 1, "mode": "shadow", "status": "goal_not_locked", "after": anchor}
        return _summary(basis, final, anchor, latest)

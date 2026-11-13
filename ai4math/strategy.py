"""Workspace-wide review clock, reproducible brief and dispatch gate."""
from .store import ResearchError, digest, now


RESEARCH_ROLES = {"explore", "produce"}
DEFAULT_INTERVAL = 3


def state(w):
    rows = w.store.all("strategy_state")
    return rows[0] if rows else None


def enable(store, payload, actor):
    interval = payload.get("round_interval", DEFAULT_INTERVAL)
    if type(interval) is not int or interval < 1:
        raise ResearchError("strategy round_interval must be a positive integer")
    external_research = payload.get("external_research", False)
    if type(external_research) is not bool:
        raise ResearchError("strategy external_research must be a boolean")
    if store.all("strategy_state"):
        raise ResearchError("strategy review is already enabled")
    row = {"id": "main", "round_interval": interval, "rounds": 0, "reviewed_rounds": 0,
           "pending_triggers": [], "last_round": None, "last_route": None, "decision": None,
           "focus_routes": [], "enabled_by": actor}
    if external_research:
        row["external_research"] = True
    store.put("strategy_state", "main", row, new=True)
    return row


def due(row):
    return bool(row and (row["pending_triggers"] or row["rounds"] - row["reviewed_rounds"] >= row["round_interval"]))


def trigger(w, kind, reference, node, route=None):
    row = state(w)
    if row is None or node not in w.cone():
        return
    signal = {"kind": kind, "reference": reference, "node": node, "route": route}
    if signal not in row["pending_triggers"]:
        row["pending_triggers"].append(signal)
        w.store.put("strategy_state", "main", row)


def finish_round(w, task):
    row = state(w)
    if row is None:
        return
    if task["role"] in RESEARCH_ROLES:
        row["rounds"] += 1
        row["last_round"] = {k: task[k] for k in ("id", "node", "route", "status")}
        row["last_route"] = task["route"]
        w.store.put("strategy_state", "main", row)
    route = w.store.get("route", task["route"])
    if route["runs"] >= route["max_runs"]:
        trigger(w, "route_run_limit", route["id"], route["node"], route["id"])


def require_dispatch(w, route, role):
    row = state(w)
    if due(row):
        raise ResearchError("global strategy review is due; read strategy-brief and register strategy.decide before dispatch")
    if row and row.get("dispatch_deferred") and role in RESEARCH_ROLES:
        raise ResearchError("research dispatch is deferred; register a strategy decision with an available focus route")
    if row and row["focus_routes"] and role in RESEARCH_ROLES and route not in row["focus_routes"]:
        raise ResearchError("research task is outside the reviewed focus routes: " + ", ".join(row["focus_routes"]))
    return row["decision"] if row else None


def brief(w):
    with w.store.snapshot():
        row = state(w)
        if row is None:
            return {"enabled": False, "due": False}
        cone = w.cone()
        goal = w.store.get("project", "main")["goal"]
        nodes = [{**node, "status": w.node_status(node["id"])} for node in w.store.all("node") if node["id"] in cone]
        routes = []
        for route in w.store.all("route"):
            if route["node"] not in cone:
                continue
            routes.append({k: route[k] for k in ("id", "node", "status", "rationale", "next_question", "stop_condition",
                                                 "runs", "max_runs", "stagnant", "stagnation_limit")})
            routes[-1]["remaining_runs"] = route["max_runs"] - route["runs"]
            routes[-1]["latest_checkpoint"] = route["checkpoints"][-1] if route["checkpoints"] else None
        pools = [{"id": p["id"], "node": p["node"], "remaining_task_slots": p["max_tasks"] - p["tasks_reserved"],
                  "current_plan": w.store.get("node", p["node"]) == p["node_snapshot"]}
                 for p in w.store.all("exploration_pool") if p["node"] in cone]
        triggers = list(row["pending_triggers"])
        rounds = row["rounds"] - row["reviewed_rounds"]
        if rounds >= row["round_interval"]:
            triggers.append({"kind": "round_interval", "rounds": rounds})
        running = [{k: t[k] for k in ("id", "route", "role", "external_stage", "worker_receipt_sha256") if k in t}
                   for t in w.store.all("task") if t["status"] == "running"]
        claims = [{k: c[k] for k in ("id", "node", "evidence_level", "scope", "summary")} for c in w.store.all("claim")
                  if c["node"] in cone and c["status"] == "accepted"]
        obstacles = [{k: c[k] for k in ("id", "candidate", "obligation", "hypothesis", "case_id", "search_domain", "receipt")}
                     for c in w.store.all("exploration_counterexample") if c["obligation"] in cone]
        current = row["focus_routes"][0] if row["focus_routes"] else row["last_route"]
        if current is None:
            current = next((t["route"] for t in reversed(row["pending_triggers"]) if t["route"]), None)
        previous = w.store.get("strategy_decision", row["decision"]) if row["decision"] else None
        payload = {"enabled": True, "due": bool(triggers), "triggers": triggers, "round_interval": row["round_interval"],
                   "rounds_since_review": rounds, "total_rounds": row["rounds"], "last_round": row["last_round"],
                   "current_route": current, "focus_routes": row["focus_routes"], "last_decision": row["decision"], "goal": goal, "nodes": nodes,
                   "last_decision_summary": {k: previous[k] for k in ("decision", "reason", "next_question")} if previous else None,
                   "open_obligations": [n["id"] for n in nodes if n["id"] != goal and n["status"] == "open"],
                   "ready_obligations": [n["id"] for n in nodes if n["id"] != goal and n["status"] == "open"
                                         and all(w.node_status(d) == "proved" for d in n["proof_dependencies"])],
                   "routes": routes, "pools": pools, "accepted_claims": claims, "verified_obstacles": obstacles,
                   "running_tasks": running}
        from .literature import status as literature_status
        literature = literature_status(w, row["decision"], row["focus_routes"])
        if literature:
            payload["literature"] = literature
        if row.get("dispatch_deferred"):
            payload["dispatch_deferred"] = True
        return {**payload, "brief_sha256": digest(payload)}


def decide(w, p, actor):
    for key in ("id", "brief_sha256", "decision", "reason", "next_question"):
        if not isinstance(p.get(key), str) or not p[key].strip():
            raise ResearchError(f"strategy decision needs nonempty {key}")
    reviewed = brief(w)
    if not reviewed["enabled"] or reviewed["goal"] is None:
        raise ResearchError("strategy review requires an enabled workspace and locked goal")
    if reviewed["brief_sha256"] != p["brief_sha256"]:
        raise ResearchError("strategy brief changed; read the latest strategy-brief")
    if reviewed["running_tasks"]:
        raise ResearchError("settle or recover running tasks before registering the strategy decision")
    choice, selected = p["decision"], p.get("next_route")
    parallel = p.get("parallel_routes", [])
    if (selected is not None and (not isinstance(selected, str) or not selected)) or not isinstance(parallel, list) or any(
            not isinstance(key, str) or not key for key in parallel) or len(parallel) > 4 or (parallel and selected is None):
        raise ResearchError("parallel review selects one primary route and up to four distinct additional routes")
    focus = ([selected] if selected else []) + parallel
    if len(set(focus)) != len(focus):
        raise ResearchError("reviewed focus routes must be distinct")
    if choice not in ("continue", "switch", "replan"):
        raise ResearchError("strategy decision must be continue, switch or replan")
    defer_dispatch = p.get("defer_dispatch", False)
    if type(defer_dispatch) is not bool or (defer_dispatch and (choice != "switch" or focus)):
        raise ResearchError("defer_dispatch requires a switch with no focus routes")
    if (choice == "replan") != ("plan" in p):
        raise ResearchError("only a replan decision takes a proof plan")
    if choice == "continue" and (not selected or selected != reviewed["current_route"]):
        raise ResearchError("continue must select the current route")
    if choice == "switch" and not defer_dispatch and (not selected or selected == reviewed["current_route"]):
        raise ResearchError("switch must select a different route")
    plan = None
    if choice == "replan":
        plan = p["plan"]
        if not isinstance(plan, dict) or set(plan) != {"node", "proof_dependencies"}:
            raise ResearchError("replan needs node and proof_dependencies")
        deps = plan["proof_dependencies"]
        if not isinstance(deps, list) or any(not isinstance(key, str) for key in deps) or len(set(deps)) != len(deps):
            raise ResearchError("proof_dependencies must be distinct node IDs")
        if not isinstance(plan["node"], str) or plan["node"] not in w.cone():
            raise ResearchError("replan must address the current goal dependency cone")
        before = w.store.get("node", plan["node"])
        if set(deps) == set(before["proof_dependencies"]):
            raise ResearchError("replan must change the proof dependencies")
        after = w._plan({**plan, "reason": p["reason"]}, actor, from_strategy=True)
        plan = {"before": before, "after": after}
    routes = [w.store.get("route", key) for key in focus]
    for route in routes:
        w._available(route)
    if parallel:
        targets = {route["node"] for route in routes}
        if len(targets) != len(routes) or reviewed["goal"] in targets or any(
                w.node_status(dep) != "proved" for node in targets for dep in w.store.get("node", node)["proof_dependencies"]):
            raise ResearchError("parallel routes need distinct ready proof obligations")
        for target in targets:
            seen, pending = set(), [target]
            while pending:
                node = pending.pop()
                if node not in seen:
                    seen.add(node)
                    record = w.store.get("node", node)
                    pending.extend(record["proof_dependencies"] + record["statement_dependencies"])
            if (seen - {target}) & targets:
                raise ResearchError("parallel route targets must not depend on one another")
    if routes:
        route = routes[0]
        route["next_question"] = p["next_question"]
        w.store.put("route", route["id"], route)
    record = {"id": p["id"], "decision": choice, "next_route": selected, "reason": p["reason"],
              "next_question": p["next_question"], "focus_routes": focus, "plan": plan, "brief": reviewed,
              "actor": actor, "created_at": now(), "evidence_level": "unknown"}
    if defer_dispatch:
        record["dispatch_deferred"] = True
    w.store.put("strategy_decision", record["id"], record, new=True)
    row = state(w)
    if len(focus) > 1 or len(row["focus_routes"]) > 1:
        row["parallel_research"] = True
    row.update(reviewed_rounds=row["rounds"], pending_triggers=[], decision=record["id"], focus_routes=focus)
    if defer_dispatch:
        row["dispatch_deferred"] = True
    elif focus:
        row.pop("dispatch_deferred", None)
    w.store.put("strategy_state", "main", row)
    return record

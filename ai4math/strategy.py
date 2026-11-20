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
    min_route_rounds = payload.get("min_route_rounds", 1)
    if type(min_route_rounds) is not int or min_route_rounds < 1:
        raise ResearchError("strategy min_route_rounds must be a positive integer")
    gate_interval = payload.get("gate_interval", 20)
    if type(gate_interval) is not int or gate_interval < 1:
        raise ResearchError("strategy gate_interval must be a positive integer")
    row = {"id": "main", "round_interval": interval, "rounds": 0, "reviewed_rounds": 0,
           "pending_triggers": [], "last_round": None, "last_route": None, "decision": None,
           "focus_routes": [], "gate_interval": gate_interval, "gate_rounds": 0, "checkpoint_rounds": 0,
           "min_route_rounds": min_route_rounds,
           "enabled_by": actor}
    if external_research:
        row["external_research"] = True
    store.put("strategy_state", "main", row, new=True)
    return row


def configure(w, payload, actor):
    row = state(w)
    if row is None:
        raise ResearchError("strategy review is not enabled")
    changed = {}
    for key in ("gate_interval", "min_route_rounds", "obligation_stagnation_limit",
                "correspondence_after", "retry_limit", "critique_interval"):
        if key not in payload:
            continue
        value = payload[key]
        if type(value) is not int or value < 1:
            raise ResearchError(key + " must be a positive integer")
        if row.get(key) != value:
            changed[key] = {"from": row.get(key), "to": value}
            row[key] = value
    if "literature_mode" in payload:
        value = payload["literature_mode"]
        if value not in {"always", "on-demand"}:
            raise ResearchError("literature_mode must be always or on-demand")
        if row.get("literature_mode") != value:
            changed["literature_mode"] = {"from": row.get("literature_mode"), "to": value}
            row["literature_mode"] = value
    if "adversarial_review" in payload:
        value = payload["adversarial_review"]
        if type(value) is not bool:
            raise ResearchError("adversarial_review must be a boolean")
        if row.get("adversarial_review") != value:
            changed["adversarial_review"] = {"from": row.get("adversarial_review"), "to": value}
            row["adversarial_review"] = value
    if not changed:
        raise ResearchError("strategy.configure needs at least one changed setting")
    row["configured_by"] = actor
    row["configured_at"] = now()
    w.store.put("strategy_state", "main", row)
    return {"configured": changed,
            "gate_interval": row.get("gate_interval"), "min_route_rounds": row.get("min_route_rounds", 1)}


def due(row):
    return bool(row and (row["pending_triggers"] or row["rounds"] - row["reviewed_rounds"] >= row["round_interval"]))


def map_view(w):
    """One readable page: what the project is trying, on what evidence (Batch 8.1)."""
    from .approach import view as approach_view
    from .scope import exploration as exploration_view
    from .generation import view as generation_view
    from .metrics import maturity as maturity_view
    from .formalization import view as formalization_view
    approaches = approach_view(w)
    hypotheses = w.store.all("research_hypothesis")
    obstacles = w.store.all("obstacle")
    intents = w.store.all("research_intent")
    routes = w.store.all("route")
    return {
        "goal": w.store.get("project", "main")["goal"],
        "approaches": approaches,
        "hypotheses": {"total": len(hypotheses),
                       "by_status": {status: len([h for h in hypotheses if h["status"] == status])
                                     for status in ("open", "supported", "refuted", "abandoned")}},
        "obstacles": {"total": len(obstacles),
                      "by_kind": {kind: len([o for o in obstacles if o["kind"] == kind])
                                  for kind in ("mathematical_refutation", "unproved_dependency",
                                               "experimental_limit", "method_failure", "resource_limit")},
                      "refuted": len([o for o in obstacles if o["status"] == "refuted"])},
        "intents": {"total": len(intents),
                    "unconsumed": len([i for i in intents if not i.get("consumed_by")])},
        "routes": {"total": len(routes),
                   "proof": len([r for r in routes if r.get("kind", "proof") == "proof"]),
                   "exploration": len([r for r in routes if r.get("kind") == "exploration"])},
        "exploration": exploration_view(w),
        "generation": generation_view(w),
        "maturity": maturity_view(w),
        "formalization": formalization_view(w),
    }


def schedule(w):
    """Ranked work items (Batch 8.2 P2, §4.13): a suggestion, never an auto-dispatch.

    Priority order: obligations whose dependencies are all proved, then smaller depth
    from the goal, then more planned children (a broader foundation first). The
    reading deliberately keeps ``active_routes`` so a coordinator can see whether the
    item already has a route instead of creating another one.
    """
    from .metrics_closure import _depth
    goal = w.store.get("project", "main")["goal"]
    depth = _depth(w)
    cone = w.cone()
    items = []
    for node in w.store.all("node"):
        if node["id"] == goal or node["id"] not in cone:
            continue
        if w.node_status(node["id"]) != "open":
            continue
        dependencies = node.get("proof_dependencies") or []
        ready = all(w.node_status(dep) == "proved" for dep in dependencies)
        routes = [r["id"] for r in w.store.all("route")
                  if r["node"] == node["id"] and r["status"] == "active"]
        priority = (0 if ready else 1, depth.get(node["id"], 99), -len(dependencies))
        items.append({"node": node["id"], "depth": depth.get(node["id"], 99),
                      "ready": ready, "active_routes": routes, "priority": priority})
    items.sort(key=lambda item: item["priority"])
    for item in items:
        item.pop("priority")
    return {"items": items, "goal": goal,
            "note": "scheduling suggestion only; dispatch stays manual"}


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
        from .obligation import view as obligation_view, debt as obligation_debt
        obligations = [obligation_view(w, node) for node in sorted({route["node"] for route in routes})]
        debt = obligation_debt(w)
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
        from datetime import datetime
        def _duration(task):
            try:
                return (datetime.fromisoformat(task["finished_at"]) - datetime.fromisoformat(task["started_at"])).total_seconds()
            except (KeyError, TypeError, ValueError):
                return 0.0
        every_task = list(w.store.all("task"))
        completed = [t for t in every_task if t.get("status") == "completed"]
        failed = [t for t in every_task if t.get("status") == "failed"]
        cost = {"completed": len(completed), "failed": len(failed),
                "demo_rounds": sum(1 for t in completed if t.get("thin_inputs_ack")),
                "wall_seconds": round(sum(_duration(t) for t in completed), 1),
                "per_route": {r["id"]: sum(1 for t in completed if t.get("route") == r["id"])
                              for r in w.store.all("route") if r["node"] in cone},
                "tokens": "unknown (no metered stream for subagent rounds)"}
        payload = {"enabled": True, "due": bool(triggers), "triggers": triggers, "round_interval": row["round_interval"],
                   "rounds_since_review": rounds, "total_rounds": row["rounds"], "last_round": row["last_round"],
                   "current_route": current, "focus_routes": row["focus_routes"], "last_decision": row["decision"], "goal": goal, "nodes": nodes,
                   "last_decision_summary": {k: previous[k] for k in ("decision", "reason", "next_question")} if previous else None, "cost": cost,
                   "open_obligations": [n["id"] for n in nodes if n["id"] != goal and n["status"] == "open"],
                   "ready_obligations": [n["id"] for n in nodes if n["id"] != goal and n["status"] == "open"
                                         and all(w.node_status(d) == "proved" for d in n["proof_dependencies"])],
                   "routes": routes, "pools": pools, "accepted_claims": claims, "verified_obstacles": obstacles,
                   "obligations": obligations, "obligation_debt": debt, "running_tasks": running}
        from .literature import status as literature_status
        literature = literature_status(w, row["decision"], row["focus_routes"])
        if literature:
            payload["literature"] = literature
        from .scope import exploration as exploration_view
        payload["exploration"] = exploration_view(w)
        payload["exploration_routes"] = [
            {k: route[k] for k in ("id", "node", "status", "runs", "max_runs", "stagnant")}
            for route in w.store.all("route")
            if route.get("kind") == "exploration" and route["node"] in payload["exploration"]["nodes"]]
        if row.get("dispatch_deferred"):
            payload["dispatch_deferred"] = True
        from .review_gate import brief as gate_brief
        payload["gate"] = gate_brief(w)
        from .metrics import maturity as maturity_view
        payload["maturity"] = maturity_view(w)
        from .formalization import view as formalization_view
        payload["formalization"] = formalization_view(w)
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
        proof_routes = [route for route in routes if route.get("kind", "proof") == "proof"]
        targets = {route["node"] for route in proof_routes}
        if len(targets) != len(proof_routes) or reviewed["goal"] in targets or any(
                w.node_status(dep) != "proved" for node in targets for dep in w.store.get("node", node)["proof_dependencies"]):
            raise ResearchError("parallel proof routes need distinct ready proof obligations")
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
    from .policy import strict_research
    strict = strict_research(w)
    if strict and routes:
        from .research_flow import freeze_steps
        record["research_steps"] = freeze_steps(w, p.get("research_steps"), routes)
    if defer_dispatch:
        record["dispatch_deferred"] = True
    w.store.put("strategy_decision", record["id"], record, new=True)
    if choice != "continue" and not strict:
        from .obligation import release as obligation_release
        for route in routes:
            obligation_release(w, route["node"], record["id"])
    row = state(w)
    if len(focus) > 1 or len(row["focus_routes"]) > 1:
        row["parallel_research"] = True
    row.update(reviewed_rounds=row["rounds"], pending_triggers=[], decision=record["id"], focus_routes=focus)
    if choice != "continue" and not strict:
        row.pop("halt", None)
    if defer_dispatch:
        row["dispatch_deferred"] = True
    elif focus:
        row.pop("dispatch_deferred", None)
    w.store.put("strategy_state", "main", row)
    return record

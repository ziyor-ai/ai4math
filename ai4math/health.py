"""Read-only health panel: what a line of attack is actually producing.

Everything here is derived.  It changes no budget, claim or route state; it
only makes the failure modes of a long autonomous run visible: rounds since the
last accepted claim, artifacts per round, scope inflation and literature load.
"""

import json
from collections import Counter
from statistics import median

from .obligation import DEBT_SOFT_LIMIT, limit as stagnation_limit, view as obligation_view


def _records(root):
    folder = root / "reports" / "rounds"
    if not folder.is_dir():
        return None
    rows = []
    for path in sorted(folder.glob("*.json")):
        try:
            body = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        sources = body.get("sources") or []
        artifacts = body.get("artifacts") or []
        scope = body.get("scope")
        gaps = body.get("gaps")
        rows.append({"file": path.name,
                     "sources": len(sources) if isinstance(sources, list) else 0,
                     "artifacts": len(artifacts) if isinstance(artifacts, list) else 0,
                     "scope_chars": len(scope) if isinstance(scope, str) else 0,
                     "gap_chars": len(gaps) if isinstance(gaps, str) else 0})
    return rows or None


def _trend(rows, key):
    values = [row[key] for row in rows]
    half = len(values) // 2
    if half == 0:
        return {"first": None, "second": None}
    return {"first": median(values[:half]), "second": median(values[-half:])}


def _legacy(workbench, node):
    """Fallback for workspaces whose routes predate obligation accounting."""
    routes = [r for r in workbench.store.all("route") if r["node"] == node]
    stamps = []
    for route in routes:
        for index, entry in enumerate(route.get("checkpoints") or []):
            stamps.append((entry.get("timestamp") or "", index, entry))
    stamps.sort()
    rounds = len(stamps)
    since = 0
    for _, _, entry in reversed(stamps):
        if entry.get("outcome") in ("claim", "discharged", "falsified") or entry.get("new_progress"):
            break
        since += 1
    return rounds, since


def _credited(entry):
    progress = entry.get("progress")
    if progress is not None:
        return progress["advanced"]
    return entry.get("outcome") in ("claim", "discharged", "falsified")


def snapshot(workbench):
    store = workbench.store
    goal = store.get("project", "main")["goal"]
    nodes = store.all("node")
    from .scope import debt as proof_debt, exploration as exploration_view
    proof = proof_debt(workbench)
    open_nodes = [n for n in nodes if n["id"] in set(proof["nodes"])]
    exploration = exploration_view(workbench)
    routes = store.all("route")
    claims = [c for c in store.all("claim") if c["status"] == "accepted"]
    tasks = store.all("task")
    bound = stagnation_limit(workbench)

    obligations = []
    for route in routes:
        view = obligation_view(workbench, route["node"])
        try:
            row = store.get("obligation", route["node"])
        except Exception:
            row = None
        since = 0
        if row:
            for entry in reversed(row.get("outcomes") or []):
                if _credited(entry):
                    break
                since += 1
        else:
            legacy_rounds, since = _legacy(workbench, route["node"])
            if legacy_rounds:
                view = {**view, "rounds": legacy_rounds,
                        "runs": sum(r["runs"] for r in routes if r["node"] == route["node"])}
        entry = {**view, "since_claim": since,
                 "node_status": workbench.node_status(route["node"]),
                 "accepted_claims": len([c for c in claims if c["node"] == route["node"]]),
                 "routes": [r["id"] for r in routes if r["node"] == route["node"]]}
        if entry not in obligations:
            obligations.append(entry)
    obligations.sort(key=lambda item: (-item["since_claim"], -item["rounds"]))

    checkpoints = [entry for route in routes for entry in (route.get("checkpoints") or [])]
    outcomes = Counter(entry.get("outcome") or ("claim" if entry.get("new_progress") else "no-progress")
                       for entry in checkpoints)
    depth = {"routes": len(routes),
             "routes_without_checkpoints": len([r for r in routes if not (r.get("checkpoints") or [])]),
             "median_checkpoints": median([len(r.get("checkpoints") or []) for r in routes]) if routes else 0,
             "routes_per_obligation": round(len(routes) / max(1, len(obligations)), 1),
             "outcomes": dict(outcomes)}
    records = _records(store.root)
    totals = {
        "nodes": len(nodes), "open_obligations": len(open_nodes), "routes": len(routes),
        "active_routes": len([r for r in routes if r["status"] == "active"]),
        "accepted_claims": len(claims),
        "tasks": len(tasks), "tasks_running": len([t for t in tasks if t["status"] == "running"]),
        "tasks_failed": len([t for t in tasks if t["status"] == "failed"]),
        "artifacts": len(store.all("artifact")),
        "failures": len(store.all("failure")),
        "obligation_rounds": sum(item["rounds"] for item in obligations),
        "exploration_registered": exploration["registered"],
        "exploration_open": exploration["open"],
        "exploration_limit": exploration["limit"],
        "exploration_rounds_settled": exploration["rounds_settled"],
        "discoveries": len(store.all("discovery")),
    }

    signals = []
    for item in obligations:
        if item["stagnant"] >= item["limit"]:
            signals.append({"level": "stop", "code": "stagnation_limit", "node": item["node"],
                            "detail": str(item["stagnant"]) + "/" + str(item["limit"]) + " rounds without credited progress"})
        elif item.get("rounds_since_closure", item["since_claim"]) >= bound:
            signals.append({"level": "warn", "code": "long_drought", "node": item["node"],
                            "detail": (str(item["rounds_since_closure"]) + " rounds since verified closure"
                                       if "rounds_since_closure" in item else
                                       str(item["since_claim"]) + " rounds since the last credited progress")})
    if len(open_nodes) >= DEBT_SOFT_LIMIT:
        signals.append({"level": "warn", "code": "obligation_debt", "node": goal,
                        "detail": str(len(open_nodes)) + " open obligations (soft limit " + str(DEBT_SOFT_LIMIT) + ")"})
    if totals["obligation_rounds"] >= 20 and not claims:
        signals.append({"level": "warn", "code": "no_claim", "node": goal,
                        "detail": "no accepted claim after " + str(totals["obligation_rounds"]) + " obligation rounds"})
    from .failure import summary as failure_summary
    failures = failure_summary(workbench)
    if failures["streaks"]:
        signals.append({"level": "warn", "code": "failure_streak", "node": goal,
                        "detail": ", ".join(key + "=" + str(value) for key, value in sorted(failures["streaks"].items()))})
    if records:
        if _trend(records, "scope_chars")["second"] and _trend(records, "scope_chars")["first"]:
            if _trend(records, "scope_chars")["second"] > 2 * max(1, _trend(records, "scope_chars")["first"]):
                signals.append({"level": "warn", "code": "scope_inflation", "node": goal,
                                "detail": "round-record scope text more than doubled between the halves"})
        heavy = [row for row in records if row["sources"] > 12]
        if heavy and totals["accepted_claims"] == 0:
            signals.append({"level": "info", "code": "literature_load", "node": goal,
                            "detail": str(len(heavy)) + " rounds cite more than 12 sources without an accepted claim"})

    if depth["routes"] >= 10 and depth["median_checkpoints"] <= 1:
        signals.append({"level": "warn", "code": "mechanism_churn", "node": goal,
                        "detail": str(depth["routes"]) + " routes with a median of " +
                                  str(depth["median_checkpoints"]) + " checkpoints each"})
    if depth["routes_per_obligation"] > 5:
        signals.append({"level": "warn", "code": "route_proliferation", "node": goal,
                        "detail": str(depth["routes_per_obligation"]) + " routes per obligation"})
    from .metrics import closure as closure_metrics, entailment as entailment_metrics, effort as effort_metrics, lifecycle, maturity as maturity_metrics, profile
    from .verification import summary as verification_summary
    maturity = maturity_metrics(workbench)
    closure = closure_metrics(workbench)
    effort = effort_metrics(workbench)
    if maturity["unverified_findings_accumulating"]:
        signals.append({"level": "warn", "code": "unverified_findings_accumulating", "node": goal,
                        "detail": str(maturity["load_bearing_unverified"]) +
                                  " load-bearing findings and no verified claim since the oldest"})
    payload = {"goal": {"node": goal, "status": workbench.node_status(goal)},
               "totals": totals, "depth": depth, "failures": failures, "obligations": obligations,
               "exploration": exploration, "entailment": entailment_metrics(workbench),
               "verification": verification_summary(workbench),
               "maturity": maturity,
               "closure": closure,
               "effort": effort,
               "lifecycle": lifecycle(workbench), "profile": profile(workbench),
               "records": {"files": len(records),
                           "sources": round(median([r["sources"] for r in records]), 1),
                           "artifacts": round(median([r["artifacts"] for r in records]), 1),
                           "scope_trend": _trend(records, "scope_chars"),
                           "gap_trend": _trend(records, "gap_chars")} if records else None,
               "signals": signals}
    return payload


def panel(state):
    """Compact text rendering of a snapshot."""
    totals = state["totals"]
    lines = ["goal: " + state["goal"]["node"] + " (" + state["goal"]["status"] + ")",
             "obligations open: " + str(totals["open_obligations"]) + " / nodes " + str(totals["nodes"]) +
             " | routes " + str(totals["routes"]) + " (" + str(totals["active_routes"]) + " active)" +
             " | accepted claims " + str(totals["accepted_claims"]),
             "obligation rounds: " + str(totals["obligation_rounds"]) + " | tasks " + str(totals["tasks"]) +
             " (running " + str(totals["tasks_running"]) + ", failed " + str(totals["tasks_failed"]) + ")" +
             " | artifacts " + str(totals["artifacts"]),
             "", "node                              rounds  runs  stagnant  since claim  claims"]
    for item in state["obligations"]:
        name = item["node"] if len(item["node"]) <= 34 else "…" + item["node"][-33:]
        lines.append("{:<34} {:>6} {:>5} {:>9} {:>11} {:>7}".format(
            name, item["rounds"], item["runs"], item["stagnant"], item["since_claim"], item["accepted_claims"]))
        if "rounds_since_closure" in item:
            by_role = ", ".join(f"{role}={count}" for role, count in sorted(item["runs_by_role"].items())) or "none"
            lines.append(f"  started by role {by_role} | decisions {item['decision_changes']}"
                         f" | closures {item['closures']} | rounds since closure {item['rounds_since_closure']}")
    depth = state["depth"]
    lines += ["", "routes " + str(depth["routes"]) + " | median checkpoints/route " + str(depth["median_checkpoints"]) +
              " | routes per obligation " + str(depth["routes_per_obligation"]),
              "outcomes: " + ", ".join(k + "=" + str(v) for k, v in sorted(depth["outcomes"].items()))]
    maturity = state.get("maturity")
    if maturity:
        claims = maturity["claims"]
        findings = maturity["findings"]
        lines += ["", "maturity: verified " + str(claims["by_maturity"]["verified"]) +
                  " | candidates " + str(claims["by_maturity"]["proof_candidate"]) +
                  " | findings " + str(findings["total"]) + " (checked " + str(findings["checked"]) + ")" +
                  " | formalization pending " + str(len(maturity["formalization"]["pending"]))]
    if state.get("closure"):
        closure = state["closure"]
        lines += ["closure: merge_ready " + str(closure["merge_ready_structured"]) +
                  " | composition without verified entailment " +
                  str(closure["new_composition_accept_without_verified_entailment"]) +
                  " | declared receipts " + str(closure["declared_kernel_pass_with_targets"]) +
                  "+" + str(closure["declared_kernel_pass_without_targets"])]
    records = state["records"]
    if records:
        lines += ["", "round records: " + str(records["files"]) + " | median sources/round " + str(records["sources"]) +
                  " | median artifacts/round " + str(records["artifacts"]),
                  "scope chars trend: " + str(records["scope_trend"]) + " | gap chars trend: " + str(records["gap_trend"])]
    lines += ["", "signals: " + ("none" if not state["signals"] else "")]
    for signal in state["signals"]:
        lines.append("  [" + signal["level"] + "] " + signal["code"] + " @ " + signal["node"] + ": " + signal["detail"])
    return "\n".join(lines) + "\n"

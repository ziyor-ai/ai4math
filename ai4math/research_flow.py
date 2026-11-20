"""The proof basis and independent review handoff for v2c4 research."""

from .store import ResearchError, digest


def basis(w, node_id):
    from .scope import node_scope
    from .verification import effective

    root = w.store.get("project", "main")["goal"]
    goal = w.store.get("node", root)
    nodes = {}
    pending = [node_id]
    while pending:
        ident = pending.pop()
        if ident in nodes:
            continue
        node = w.store.get("node", ident)
        nodes[ident] = {key: node[key] for key in ("statement_digest", "statement_dependencies", "proof_dependencies")}
        pending.extend(node["proof_dependencies"] + node["statement_dependencies"])
    claims = {claim["id"]: {"revision": w.store.revision("claim", claim["id"]),
                            "state": effective(w, claim)}
              for claim in w.store.all("claim")
              if claim["node"] in nodes and claim["node"] != node_id and claim["status"] == "accepted"
              and claim["evidence_level"] in {"proved", "disproved"}}
    snapshot = {"goal": root, "goal_statement_digest": goal["statement_digest"], "node": node_id,
                "scope": node_scope(w, node_id), "nodes": nodes, "claims": claims}
    return {**snapshot, "sha256": digest(snapshot)}


def freeze_steps(w, supplied, routes):
    ids = {route["id"] for route in routes}
    if not isinstance(supplied, dict) or set(supplied) != ids:
        raise ResearchError("research_steps must name every v2c4 focus route exactly once")
    goal = w.store.get("project", "main")["goal"]
    steps = {}
    for route in routes:
        key = route["id"]
        row = supplied[key]
        if (not isinstance(row, dict) or set(row) != {"mode", "probe", "falsifier"}
                or row["mode"] not in ("direct", "obligation", "bootstrap")
                or any(not isinstance(row[k], str) or not row[k].strip() for k in ("probe", "falsifier"))):
            raise ResearchError("each research step needs a mode, probe and falsifier: " + key)
        node = w.store.get("node", route["node"])
        if row["mode"] == "bootstrap" and (node["id"] != goal or node["proof_dependencies"]
                                            or w.store.all("gate_review")):
            raise ResearchError("bootstrap applies only to the undecomposed goal before the first independent gate")
        if row["mode"] == "obligation":
            if node["id"] == goal and not node["proof_dependencies"]:
                raise ResearchError("register proof obligations or choose an explicit direct or bootstrap step")
            if any(w.node_status(dep) != "proved" for dep in node["proof_dependencies"]):
                raise ResearchError("choose a ready proof obligation: " + node["id"])
        steps[key] = {**row, "basis": basis(w, node["id"])}
    return steps


def freeze_task(w, task, decision):
    if not decision:
        raise ResearchError("v2c4 research needs a reviewed strategy decision")
    row = w.store.get("strategy_decision", decision)
    step = row.get("research_steps", {}).get(task["route"])
    if not step or step["basis"] != basis(w, task["node"]):
        raise ResearchError("research step is missing or its proof basis changed; review the strategy again")
    if step["mode"] == "bootstrap" and w.store.all("gate_review"):
        raise ResearchError("bootstrap ended at the independent gate; record the next strategy step")
    task["research_step"] = step


def stale(w, task):
    step = task.get("research_step")
    return bool(step and step["basis"] != basis(w, task["node"]))


def review_action(w, payload):
    action = payload.get("next_action")
    if not isinstance(action, dict) or action.get("kind") not in ("decompose", "validate", "switch", "blocked"):
        raise ResearchError("v2c4 continuation needs next_action with kind decompose, validate, switch or blocked")
    node = action.get("node")
    if not isinstance(node, str) or node not in w.cone() or w.node_status(node) != "open":
        raise ResearchError("review next_action must identify an open proof obligation")
    kind = action["kind"]
    if kind == "validate":
        ident = action.get("claim")
        claim = w.store.get("claim", ident) if isinstance(ident, str) else None
        if not claim or claim["node"] != node or claim["status"] != "proposed" or claim["evidence_level"] not in {"proved", "disproved"}:
            raise ResearchError("validation priority needs a proposed proof or refutation on that obligation")
    if kind == "blocked" and any(not isinstance(action.get(key), str) or not action[key].strip()
                                 for key in ("blocker", "reopen_condition")):
        raise ResearchError("a blocked obligation needs a blocker and reopen_condition")
    return dict(action)


def check_dispatch(w, task, decision):
    from .strategy import RESEARCH_ROLES, state

    step = task.get("research_step")
    if task["role"] in RESEARCH_ROLES and (
            not step or not task.get("round") or decision != task.get("prepared_under_strategy") or stale(w, task)):
        raise ResearchError("research task has a stale strategy or proof basis; prepare a new contract")
    previous = (state(w) or {}).get("last_gate_review")
    if not previous:
        return
    action = w.store.get("gate_review", previous).get("next_action")
    if step and step["mode"] == "bootstrap":
        raise ResearchError("bootstrap ended at the independent gate; record the next strategy step")
    if not action:
        return
    if (task["role"] in RESEARCH_ROLES and action["kind"] == "decompose"
            and not w.store.get("node", action["node"])["proof_dependencies"]):
        raise ResearchError("complete the proof decomposition recorded at the independent gate before dispatch")
    if action["kind"] == "validate" and w.store.get("claim", action["claim"])["status"] == "proposed":
        if task["role"] in RESEARCH_ROLES:
            raise ResearchError("resolve the gate's priority validation or record a new independent review of the blocker")
        if (task["role"] == "validate" and task["node"] == action["node"]
                and task.get("validation_request", {}).get("claim") != action["claim"]):
            raise ResearchError("validate the claim selected by the independent gate for this obligation")

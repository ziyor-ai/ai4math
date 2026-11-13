"""Literature gates for externally coordinated research tasks."""

from urllib.parse import urlsplit

from .store import ResearchError, digest, file_hash, now


WORKER_RECEIPT_VERSION = 3
REVIEW_STAGE = "awaiting_coordinator_review"


def external_only(w):
    return bool(w.store.all("literature_policy"))


def external_required(w):
    from .strategy import state

    strategy = state(w)
    return external_only(w) or bool(strategy and (
        strategy.get("external_research") or strategy.get("parallel_research") or len(strategy["focus_routes"]) > 1))


def enable(w, p, actor):
    if p:
        raise ResearchError("literature.enable takes an empty payload")
    record = {"id": "main", "external_research": True, "actor": actor, "created_at": now()}
    w.store.put("literature_policy", "main", record, new=True)
    return record


def _text(p, key):
    value = p.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ResearchError(f"literature record needs nonempty {key}")
    return value


def _receipt(w, p):
    queries = p.get("queries")
    if not isinstance(queries, list) or not queries or any(not isinstance(q, str) or not q.strip() for q in queries):
        raise ResearchError("literature receipt needs the actual search queries")
    trace = _text(p, "search_artifact")
    w.store.verify_blob(trace)
    sources = p.get("sources")
    if not isinstance(sources, list) or any(not isinstance(row, dict) for row in sources):
        raise ResearchError("literature receipt needs a sources list, empty when no suitable original was read")
    checked = []
    for row in sources:
        source = {key: _text(row, key) for key in ("url", "locator", "finding", "artifact_id")}
        url = urlsplit(source["url"])
        if url.scheme not in {"http", "https"} or not url.hostname:
            raise ResearchError("literature source needs an original HTTP(S) URL")
        w.store.verify_blob(source["artifact_id"])
        checked.append(source)
    return {"queries": queries, "search_artifact": trace, "sources": checked, "limitations": _text(p, "limitations")}


def _require_new_artifact(w, key, actor, after, description):
    artifact = w.store.get("artifact", key)
    if artifact["actor"] != actor or artifact["imported_at"] < after:
        raise ResearchError(f"{description} needs a fresh artifact first imported by {actor} after the preceding stage")


def _decision(w, p):
    from .strategy import due, state

    row = state(w)
    key = _text(p, "decision")
    if not row or due(row) or row["decision"] != key or not row["focus_routes"]:
        raise ResearchError("literature work needs the current strategy decision with focus routes")
    if any(task["status"] == "running" for task in w.store.all("task")):
        raise ResearchError("settle in-flight tasks before the coordinator literature phase")
    return w.store.get("strategy_decision", key)


def _search_id(decision, route):
    return digest({"decision": decision, "route": route})


def coordinator_search(w, p, actor):
    decision = _decision(w, p)
    route = _text(p, "route")
    if actor != decision["actor"] or route not in decision["focus_routes"]:
        raise ResearchError("coordinator search must be filed by the strategy actor for a selected route")
    record = {**_receipt(w, p), "id": _search_id(decision["id"], route), "decision": decision["id"],
              "route": route, "actor": actor, "created_at": now()}
    _require_new_artifact(w, record["search_artifact"], actor, decision["created_at"], "coordinator search")
    w.store.put("literature_search", record["id"], record, new=True)
    return record


def coordinator_plan(w, p, actor):
    decision = _decision(w, p)
    if actor != decision["actor"]:
        raise ResearchError("the strategy actor must freeze the unified literature assessment and plan")
    routes = p.get("routes")
    if not isinstance(routes, dict) or set(routes) != set(decision["focus_routes"]):
        raise ResearchError("the unified plan must cover all and only the selected routes")
    plan_artifact = _text(p, "plan_artifact")
    w.store.verify_blob(plan_artifact)
    plans = {}
    last_search_at = decision["created_at"]
    for route in decision["focus_routes"]:
        search_id = _search_id(decision["id"], route)
        if not w.store.exists("literature_search", search_id):
            raise ResearchError(f"missing coordinator literature search for {route}")
        search = w.store.get("literature_search", search_id)
        last_search_at = max(last_search_at, search["created_at"])
        inputs = _artifacts(search)
        if plan_artifact in inputs:
            raise ResearchError("the unified plan needs an artifact distinct from the search inputs")
        for key in inputs:
            w.store.verify_blob(key)
        node = w.store.get("node", w.store.get("route", route)["node"])
        plans[route] = {"search": search_id, "plan": _text(routes, route), "node_sha256": digest(node)}
    _require_new_artifact(w, plan_artifact, actor, last_search_at, "unified plan")
    record = {"id": decision["id"], "actor": actor, "created_at": now(), "plan_artifact": plan_artifact,
              "assessment": _text(p, "assessment"), "routes": plans}
    w.store.put("literature_plan", record["id"], record, new=True)
    return record


def _artifacts(receipt):
    return [receipt["search_artifact"], *(source["artifact_id"] for source in receipt["sources"])]


def task_fields(w, task, decision):
    if task["role"] not in {"explore", "produce"}:
        return
    if not decision or not w.store.exists("literature_plan", decision):
        if external_required(w):
            if not decision:
                raise ResearchError("register a strategy decision with focus routes before the coordinator literature plan")
            raise ResearchError("freeze the coordinator literature plan before preparing research tasks")
        return
    plan = w.store.get("literature_plan", decision)
    route_plan = plan["routes"].get(task["route"])
    if not route_plan or route_plan["node_sha256"] != digest(task["node_snapshot"]):
        raise ResearchError("research task is outside the current literature plan or its proof plan changed")
    search = w.store.get("literature_search", route_plan["search"])
    task["inputs"] = list(dict.fromkeys([*task["inputs"], plan["plan_artifact"], *_artifacts(search)]))
    task["literature"] = {"plan": plan["id"], "plan_artifact": plan["plan_artifact"],
                          "assessment": plan["assessment"], "route_plan": route_plan["plan"], "search": search,
                          "acquisition_mode": "async",
                          "worker_receipt_version": WORKER_RECEIPT_VERSION, "external_return_version": 1,
                          "deadline_contract_version": 1}


def _require_current_plan(task, decision):
    if not task.get("literature") or task["literature"]["plan"] != decision:
        raise ResearchError("external research requires a current coordinator literature plan; prepare a new task")


def _gated(w, task):
    return task["role"] in {"explore", "produce"} and (task.get("literature") or external_required(w))


def check_export(w, task, decision):
    if _gated(w, task):
        _require_current_plan(task, decision)


def check_dispatch(w, task, decision, external, actor):
    if not external:
        if _gated(w, task):
            raise ResearchError("literature-gated research uses start-external")
        return
    _require_current_plan(task, decision)
    if actor != task["literature"]["search"]["actor"]:
        raise ResearchError("external research must be dispatched by the strategy coordinator")


def status(w, decision, routes):
    required = external_required(w)
    if not decision and not required:
        return None
    searched = [route for route in routes if w.store.exists("literature_search", _search_id(decision, route))]
    planned = bool(decision and w.store.exists("literature_plan", decision))
    if not (searched or planned or required):
        return None
    return {"decision": decision, "external_research": required, "searched_routes": searched,
            "pending_search_routes": [route for route in routes if route not in searched], "plan_frozen": planned}


def _verify_handoff(w, task):
    folder = w.store.inside("tasks/" + task["id"])
    for name, key in (("input.json", task["contract_artifact"]), ("PROMPT.md", task["prompt_artifact"])):
        w.store.verify_blob(key)
        path = folder / name
        if not path.is_file() or file_hash(path) != key:
            raise ResearchError("frozen worker handoff changed")
    for key in task["inputs"]:
        w.store.verify_blob(key)


def worker_ready(w, p, actor):
    task = w.store.get("task", _text(p, "task"))
    from .external_deadline import ensure_live, enter
    ensure_live(task)
    if task["status"] != "running" or task.get("external_stage") != "literature":
        raise ResearchError("worker literature receipt needs an external task in the literature stage")
    if actor in {task["dispatched_by"], task["literature"]["search"]["actor"]}:
        raise ResearchError("worker literature receipt needs a separate actor label from the coordinator")
    if task.get("codex_workflow") and actor != task["codex_workflow"]["worker_actor"]:
        raise ResearchError("managed Codex literature receipt requires the pinned worker_actor")
    contract = task["contract_artifact"]
    if p.get("contract_sha256") != contract:
        raise ResearchError("worker literature receipt must bind the frozen contract")
    _verify_handoff(w, task)
    inputs = p.get("read_input_artifacts")
    if not isinstance(inputs, list) or any(not isinstance(key, str) for key in inputs) or sorted(inputs) != sorted(task["inputs"]):
        raise ResearchError("worker must account for every frozen input artifact")
    receipt = _receipt(w, p)
    if receipt["search_artifact"] in {*inputs, contract, task["prompt_artifact"]}:
        raise ResearchError("worker literature search needs its own frozen trace")
    version = task["literature"].get("worker_receipt_version", 1)
    if version >= 2:
        trace = receipt["search_artifact"]
        _require_new_artifact(w, trace, actor, task["started_at"], "worker literature search")
        if any(previous["search_artifact"] == trace for previous in w.store.all("worker_literature")):
            raise ResearchError("worker literature search trace has already been used for another task")
    record = {**receipt, "id": task["id"], "contract_sha256": contract, "read_input_artifacts": inputs,
              "exploration_plan": _text(p, "exploration_plan"), "actor": actor, "created_at": now()}
    w.store.put("worker_literature", task["id"], record, new=True)
    stage = REVIEW_STAGE if version >= 3 else "exploration"
    ensure_live(task)
    task.update(external_stage=stage, worker_literature=task["id"])
    enter(task, stage)
    result = {"task": task["id"], "external_stage": stage, "receipt": record}
    if version >= 3:
        result["worker_receipt_sha256"] = task["worker_receipt_sha256"] = digest(record)
    w.store.put("task", task["id"], task)
    return result


def release_exploration(w, p, actor):
    task = w.store.get("task", _text(p, "task"))
    from .external_deadline import ensure_live, enter
    ensure_live(task)
    if task["status"] != "running" or task.get("external_stage") != REVIEW_STAGE:
        raise ResearchError("exploration release needs a running task awaiting coordinator literature review")
    if actor != task["dispatched_by"]:
        raise ResearchError("only the dispatching coordinator may release exploration")
    receipt = w.store.get("worker_literature", task["worker_literature"])
    receipt_sha = digest(receipt)
    if p.get("worker_receipt_sha256") != receipt_sha:
        raise ResearchError("exploration release must bind the exact worker literature receipt")
    _verify_handoff(w, task)
    for key in _artifacts(receipt):
        w.store.verify_blob(key)
    record = {"id": task["id"], "worker_receipt_sha256": receipt_sha,
              "contract_sha256": task["contract_artifact"], "assessment": _text(p, "assessment"),
              "actor": actor, "created_at": now()}
    w.store.put("exploration_release", task["id"], record, new=True)
    ensure_live(task)
    task.update(external_stage="exploration", exploration_release=record["id"])
    enter(task, "exploration")
    w.store.put("task", task["id"], task)
    return {"task": task["id"], "external_stage": task["external_stage"], "release": record}


def check_completed_outputs(w, task, artifacts):
    version = task.get("literature", {}).get("worker_receipt_version", 1)
    if version < 2:
        return
    if version >= 3:
        release = w.store.get("exploration_release", task["exploration_release"])
        if any(w.store.get("artifact", key)["imported_at"] < release["created_at"] for key in artifacts):
            raise ResearchError("each completed research output must be first imported after coordinator release")
        return
    receipt = w.store.get("worker_literature", task["id"])
    if not any(w.store.get("artifact", key)["imported_at"] >= receipt["created_at"] for key in artifacts):
        raise ResearchError("external research needs an output first imported after the worker literature receipt")


HANDLERS = {"literature.enable": enable, "literature.search": coordinator_search,
            "literature.plan": coordinator_plan, "task.literature_ready": worker_ready,
            "task.release_exploration": release_exploration}

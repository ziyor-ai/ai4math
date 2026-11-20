"""Research intents: what one task is for, checked before dispatch (Batch 8.1, §4.1, §6.1).

The intent is the pre-dispatch contract. It carries the question, the link to the
goal, the predictions and the stop condition, and it is consumed by exactly one task.
"""
import json

from .store import ResearchError, digest, now

KINDS = ("exploration", "probe", "entailment_analysis", "verification", "settlement")
RELATIONS = ("proof_dependency", "proposed_dependency", "research_relevance")
DECISIONS = ("refute_hypothesis", "request_theoretical_followup", "request_verification",
             "record_bounded_negative_search", "keep_route", "retry_or_mark_inconclusive",
             "reassess_or_pause")
LIMITS = ("timeout_seconds", "output_limit_bytes")
EXPANSIONS = ("domain_scan", "batch_formalization")


def _bounds(plan):
    limits = plan.get("budget")
    if not isinstance(limits, dict) or set(limits) != set(LIMITS) or any(
            type(limits[key]) is not int or limits[key] < 1 for key in LIMITS):
        raise ResearchError("scout or expansion needs positive timeout_seconds and output_limit_bytes")
    return {key: limits[key] for key in LIMITS}


def _scout(w, plan, approach, kind, predicted):
    fields = ("obstacle", "goal_interface", "fragile_condition", "input_domain", "verification")
    if kind != "probe" or not isinstance(plan, dict) or any(
            not isinstance(plan.get(key), str) or not plan[key].strip() for key in fields):
        raise ResearchError("a scout must be a probe with an obstacle, interface, fragile condition, domain and verification")
    obstacle = w.store.get("obstacle", plan["obstacle"])
    if obstacle.get("node") != approach["node"] or obstacle["status"] != "open":
        raise ResearchError("a scout needs an open obstacle on its approach's node")
    decisions = plan.get("decisions")
    if (not isinstance(decisions, dict) or set(decisions) != {"success", "failure", "unknown"}
            or any(not isinstance(value, str) or value not in DECISIONS for value in decisions.values())
            or len(set(decisions.values())) != 3
            or not set(decisions.values()) <= {row["decision"] for row in predicted}):
        raise ResearchError("a scout needs three distinct predicted decisions for success, failure and unknown")
    result = {**{key: plan[key] for key in fields}, "decisions": decisions, "budget": _bounds(plan)}
    if "exploration_probe" in plan:
        key = plan["exploration_probe"]
        if not isinstance(key, str) or not key:
            raise ResearchError("exploration_probe must name a registered probe")
        probe = w.store.get("exploration_probe", key)
        if w.store.get("exploration_pool", probe["pool"])["node"] != approach["node"]:
            raise ResearchError("scout and exploration probe must target the same obligation")
        result["exploration_probe"] = key
    return result


def _expansion(w, plan, approach, kind):
    if (not isinstance(plan, dict) or plan.get("kind") not in EXPANSIONS
            or not isinstance(plan.get("scout_task"), str) or not isinstance(plan.get("decision"), str)
            or not isinstance(plan.get("input_domain"), str) or not plan["input_domain"].strip()
            or kind not in {"exploration", "verification"}):
        raise ResearchError("an expansion needs its kind, input_domain, scout_task and strategy decision")
    if kind != ("exploration" if plan["kind"] == "domain_scan" else "verification"):
        raise ResearchError("expansion kind must match the research intent")
    task = w.store.get("task", plan["scout_task"])
    scout = w.store.get("research_intent", task["intent"]) if task.get("intent") else {}
    if not scout.get("scout") or approach["node"] != task["node"]:
        raise ResearchError("an expansion must cite a scout on the same obligation")
    return {"kind": plan["kind"], "scout_task": task["id"], "decision": plan["decision"],
            "input_domain": plan["input_domain"], "budget": _bounds(plan)}


def task_plan(w, task, decision):
    """Resolve the optional bounded scout or reviewed expansion for a task."""
    if not task.get("intent"):
        return None
    intent = w.store.get("research_intent", task["intent"])
    scout, expansion = intent.get("scout"), intent.get("expansion")
    plan = scout or expansion
    if plan is None:
        return None
    if w.store.get("approach", intent["approach"])["node"] != task["node"]:
        raise ResearchError("task and bounded probe intent must target the same obligation")
    if any(type(task.get(key)) is not int or not 0 < task[key] <= plan["budget"][key] for key in LIMITS):
        raise ResearchError("task time and output limits must fit the bounded probe or expansion budget")
    if scout:
        if task["role"] not in {"explore", "produce"} or w.store.get("obstacle", scout["obstacle"])["status"] != "open":
            raise ResearchError("a scout needs a research task and a still-open obstacle")
        if task.get("codex_workflow"):
            from .research_return import deduction_first
            if not deduction_first(task):
                raise ResearchError("a managed scout requires target_progress_v2 for its first probe return")
        context = task.get("exploration_context")
        if context or scout.get("exploration_probe"):
            if not context or not scout.get("exploration_probe"):
                raise ResearchError("a pool-bound scout needs its registered exploration probe and candidate")
            probe = w.store.get("exploration_probe", scout["exploration_probe"])
            if (context["pool"] != probe["pool"] or context["candidate"]["id"] not in probe["predictions"]
                    or not set(probe["artifact_ids"]) <= set(task["inputs"])):
                raise ResearchError("the scout must bind the probe's candidate and frozen design artifacts")
        return {"scout": scout}

    source = w.store.get("task", expansion["scout_task"])
    roles = {"explore", "produce"} if expansion["kind"] == "domain_scan" else {"validate"}
    if task["role"] not in roles:
        raise ResearchError("task role must match the expansion kind")
    round_id = source.get("round")
    if (source["status"] != "completed" or not _returned_probe(w, source) or source.get("stale_basis")
            or not round_id or not w.store.exists("round_settled", round_id)):
        raise ResearchError("expansion needs a completed and settled scout")
    checkpoint = w.store.get("round_checkpoint", w.store.get("round_settled", round_id)["checkpoint"])
    evaluation = checkpoint.get("eval") or {}
    if evaluation.get("intent") != source["intent"] or evaluation.get("prediction_match") not in {"matched", "mismatched"}:
        raise ResearchError("expansion needs the scout prediction assessed in its checkpoint")
    if (not decision or expansion["decision"] != decision
            or w.store.revision("strategy_decision", decision) <= w.store.revision("round_settled", round_id)):
        raise ResearchError("expansion needs a new current strategy decision after scout settlement")
    reviews = [w.store.get("round_review", f"{source['id']}-{role}-review")
               for role in ("adversarial", "strategy")]
    authors = {source.get(key) for key in ("actor", "dispatched_by", "executed_by", "worker_actor", "outcome_reported_by")}
    authors.add((source.get("codex_workflow") or {}).get("worker_actor"))
    if w.store.exists("worker_literature", source["id"]):
        authors.add(w.store.get("worker_literature", source["id"])["actor"])
    if (len({row["reviewer"] for row in reviews}) != 2
            or any(row["verdict"] != "pass" or row["reviewer"] in authors for row in reviews)):
        raise ResearchError("expansion needs independent adversarial and strategy reviews of the scout")
    for review in reviews:
        key = review["artifact"]
        path = w.store.verify_blob(key)
        if w.store.get("artifact", key)["actor"] != review["reviewer"]:
            raise ResearchError("expansion review evidence must be frozen by its reviewer")
        if key not in task["inputs"]:
            raise ResearchError("expansion inputs must freeze both scout review artifacts")
        if review["role"] == "strategy":
            expected = {key: expansion[key] for key in ("kind", "scout_task", "input_domain", "budget")}
            try:
                approved = json.loads(path.read_text(encoding="utf-8-sig"))
            except (ValueError, UnicodeError) as exc:
                raise ResearchError("strategy review artifact must approve the expansion scope and budget as JSON") from exc
            if approved != expected:
                raise ResearchError("expansion scope and budget must match the frozen strategy review")
        if w.store.revision("round_review", review["id"]) >= w.store.revision("strategy_decision", decision):
            raise ResearchError("the current strategy decision must follow both scout reviews")
    return {"expansion": {**expansion, "scout_round": round_id, "reviews": [row["id"] for row in reviews]}}


def _returned_probe(w, task):
    if task.get("execution_mode") == "external":
        return bool(task.get("output_artifacts"))
    if not task.get("result_artifact"):
        return False
    result = w.store.verify_blob(task["result_artifact"])
    try:
        body = json.loads(result.read_text(encoding="utf-8-sig"))
    except (ValueError, UnicodeError):
        return False
    return isinstance(body, dict) and bool(body)


def _scout_batches(w, settled):
    round_row = w.store.get("round", settled["round"])
    evaluation = w.store.get("round_checkpoint", settled["checkpoint"]).get("eval") or {}
    for key in round_row["tasks"]:
        task = w.store.get("task", key)
        scout = (task.get("probe_plan") or {}).get("scout")
        decision = task.get("dispatched_under_strategy")
        if (scout and decision and task["status"] == "completed" and evaluation.get("intent") == task["intent"]
                and evaluation.get("prediction_match") in {"matched", "mismatched", "untested"} and _returned_probe(w, task)):
            yield scout["obstacle"], decision


def _batch_open(w, obstacle, decision):
    for task in w.store.all("task"):
        scout = (task.get("probe_plan") or {}).get("scout")
        if (scout and scout["obstacle"] == obstacle and task.get("round")
                and (task.get("dispatched_under_strategy") or task.get("prepared_under_strategy")) == decision
                and w.store.get("round", task["round"])["state"] not in {"settled", "aborted"}):
            return True
    return False


def _changed(settled, checkpoint, obstacle, node):
    progress = settled.get("progress") or {}
    changes = [change for change in progress.get("decision_changes", [])
               if change.get("obstacle") == obstacle or (
                   not change.get("obstacle") and change.get("node") == node
                   and (checkpoint.get("eval") or {}).get("obstacle") == obstacle)]
    return changes, node in progress.get("validated_discharged_ids", [])


def reassess_scout(w, settled, checkpoint, actor):
    """Route a substantive probe's review signal through the existing generation gate."""
    from . import generation
    node = w.store.get("route", settled["route"])["node"]
    for obstacle, decision in sorted(set(_scout_batches(w, settled))):
        if any(row.get("obstacle_id") == obstacle for row in generation.pending(w)):
            continue
        changes, closed = _changed(settled, checkpoint, obstacle, node)
        effect = checkpoint.get("eval") or {}
        reason = None
        basis = [settled["id"]]
        if changes and (effect.get("obstacle_effect") == "reformulated" or settled["outcome"] == "falsified"):
            reason = "bottleneck_interface_review" if settled["outcome"] != "falsified" else "bottleneck_refutation_review"
        elif not changes and not closed and not _batch_open(w, obstacle, decision):
            by_decision = {}
            for earlier in w.store.all("round_settled"):
                relevant = [key for identity, key in _scout_batches(w, earlier) if identity == obstacle]
                if not relevant:
                    continue
                prior_checkpoint = w.store.get("round_checkpoint", earlier["checkpoint"])
                delta, discharged = _changed(earlier, prior_checkpoint, obstacle, node)
                for key in relevant:
                    by_decision[key] = by_decision.get(key, False) or bool(delta) or discharged
            basis = sorted(by_decision, key=lambda key: w.store.revision("strategy_decision", key))[-2:]
            if len(basis) == 2 and decision == basis[-1] and not any(by_decision[key] for key in basis):
                reason = "bottleneck_no_decision_delta"
        if reason:
            key = "BR-" + digest({"obstacle": obstacle, "reason": reason, "basis": basis})[:20]
            if not w.store.exists("generation_request", key):
                generation.request(w, {"id": key, "route": settled["route"], "parent": node,
                                       "obstacle_id": obstacle, "trigger_event_id": settled["id"],
                                       "trigger_reason": reason}, actor)


def add(w, p, actor):
    if not isinstance(p, dict):
        raise ResearchError("research_intent.add needs an object payload")
    for key in ("id", "approach", "question", "connection_to_goal", "stop_condition"):
        if not isinstance(p.get(key), str) or not p[key].strip():
            raise ResearchError("research_intent.add needs nonempty " + key)
    if w.store.exists("research_intent", p["id"]):
        raise ResearchError("intent already exists: " + p["id"])
    kind = p.get("kind", "exploration")
    if kind not in KINDS:
        raise ResearchError("intent kind must be one of " + ", ".join(KINDS))
    relation = p.get("relation", "research_relevance")
    if relation not in RELATIONS:
        raise ResearchError("intent relation must be one of " + ", ".join(RELATIONS))
    approach = w.store.get("approach", p["approach"])
    if p.get("hypothesis") is not None:
        w.store.get("research_hypothesis", p["hypothesis"])
    decisive = p.get("decisive", True)
    if type(decisive) is not bool:
        raise ResearchError("decisive must be a boolean")
    predicted = p.get("predicted") or []
    if not isinstance(predicted, list) or any(
            not isinstance(x, dict) or not isinstance(x.get("observe"), str) or not x["observe"].strip()
            or x.get("decision") not in DECISIONS for x in predicted):
        raise ResearchError("each prediction needs an observe text and a known decision")
    if decisive and len({x["decision"] for x in predicted}) < 2:
        raise ResearchError("a decisive intent needs at least two different predicted decisions")
    nodes = [node["id"] for node in w.store.all("node")]
    if not any(node in p["connection_to_goal"] for node in nodes):
        raise ResearchError("connection_to_goal must name at least one registered node id")
    allowance = p.get("allowance") or {}
    if not isinstance(allowance, dict):
        raise ResearchError("allowance must be an object")
    tasks = allowance.get("tasks", 1)
    if type(tasks) is not int or tasks < 1:
        raise ResearchError("allowance.tasks must be a positive integer")
    row = {"id": p["id"], "approach": approach["id"], "hypothesis": p.get("hypothesis"),
           "kind": kind, "relation": relation, "question": p["question"],
           "connection_to_goal": p["connection_to_goal"], "decisive": decisive,
           "predicted": predicted, "stop_condition": p["stop_condition"],
           "allowance": {"tasks": tasks, "estimated_cost": allowance.get("estimated_cost")},
           "consumed_by": None, "author": actor, "created_at": now()}
    if "scout" in p and "expansion" in p:
        raise ResearchError("an intent may scout or expand, not both")
    if "scout" in p:
        row["scout"] = _scout(w, p["scout"], approach, kind, predicted)
    if "expansion" in p:
        row["expansion"] = _expansion(w, p["expansion"], approach, kind)
    w.store.put("research_intent", row["id"], row, new=True)
    return row


def consume(w, intent_id, task_id):
    row = w.store.get("research_intent", intent_id)
    if row.get("consumed_by"):
        raise ResearchError("intent already used by task " + row["consumed_by"])
    row.update(consumed_by=task_id, consumed_at=now())
    w.store.put("research_intent", row["id"], row)
    return row


HANDLERS = {"research_intent.add": add}

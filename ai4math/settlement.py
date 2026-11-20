"""Round lifecycle: open -> running -> ready_to_assess -> assessed -> settled.

Batch 8.0 slice 2 (frozen baseline §4.2, §4.3, §4.0).

Two transactions, deliberately:
  assess  writes exactly one checkpoint `cp-<round>` and nothing else;
  settle  applies the route/obligation bookkeeping and emits `round_settled`.
A crash between them is therefore recoverable, and the counters can move only once.
`route.checkpoint` keeps working on its own (legacy path) for rounds that were opened
before this contract existed.
"""
from .store import ResearchError, now

TASK_EXECUTION = {"completed": "completed", "failed": "failed", "interrupted": "interrupted",
                  "abandoned": "abandoned", "unknown_timeout": "timed_out", "output_limit": "output_limited"}
EXECUTION = set(TASK_EXECUTION.values()) | {"not_run"}
SCIENCE = {"proved", "disproved", "proof_candidate", "supporting_evidence",
           "counterexample_candidate", "inconclusive", "no_progress", "not_assessed"}
TERMINAL = set(TASK_EXECUTION)
OPEN_STATES = {"open", "running", "ready_to_assess"}
OUTCOMES = ("claim", "finding", "discharged", "falsified", "no-progress")


def _text(p, *keys):
    for key in keys:
        if not isinstance(p.get(key), str) or not p[key].strip():
            raise ResearchError("nonempty text required: " + key)


def row(w, rid):
    return w.store.get("round", rid)


def state(w, rid):
    return row(w, rid)["state"]


def open_round(w, p, actor):
    _text(p, "id", "route")
    route = w.store.get("route", p["route"])
    if w.store.exists("round", p["id"]):
        raise ResearchError("round already exists: " + p["id"])
    from .budget import reserve
    reserve(w, route)
    tasks = list(p.get("tasks") or [])
    for tid in tasks:
        w.store.get("task", tid)
    record = {"id": p["id"], "route": route["id"], "node": route["node"], "tasks": tasks,
              "state": "running" if tasks else "open", "opened_by": actor, "opened_at": now(),
              "checkpoint": None, "settled_at": None}
    w.store.put("round", p["id"], record, new=True)
    return record


def attach(w, rid, tid, actor):
    """Bind one task to a round; a task may belong to at most one round."""
    record = row(w, rid)
    if record["state"] not in {"open", "running"}:
        raise ResearchError("round " + rid + " no longer accepts tasks (" + record["state"] + ")")
    task = w.store.get("task", tid)
    if task.get("round") and task["round"] != rid:
        raise ResearchError("task " + tid + " already belongs to round " + task["round"])
    if tid not in record["tasks"]:
        record["tasks"].append(tid)
    record["state"] = "running"
    w.store.put("round", rid, record)
    return record


def _task_is_terminal(w, tid):
    return w.store.get("task", tid)["status"] in TERMINAL


def ready(w, rid):
    """True when every attached task is terminal; updates the derived state."""
    record = row(w, rid)
    if record["state"] not in OPEN_STATES:
        return record["state"] == "ready_to_assess"
    if record["tasks"] and all(_task_is_terminal(w, tid) for tid in record["tasks"]):
        if record["state"] != "ready_to_assess":
            record["state"] = "ready_to_assess"
            w.store.put("round", rid, record)
        return True
    return False


def _all_closeable(w, record, assessments):
    """Readiness that also accepts tasks declared not_run (never started)."""
    declared = {item.get("task_id") for item in assessments if isinstance(item, dict)
                and item.get("execution_status") == "not_run"}
    for tid in record["tasks"]:
        if tid in declared:
            task = w.store.get("task", tid)
            if task["status"] != "pending" or task.get("started_at") or task.get("pid"):
                return False
            continue
        if not _task_is_terminal(w, tid):
            return False
    return bool(record["tasks"])


def _check_assessments(w, record, assessments):
    from .policy import strict_research
    strict = strict_research(w)
    tasks = set(record["tasks"])
    if not isinstance(assessments, list) or not assessments:
        raise ResearchError("a checkpoint needs task_assessments")
    seen = []
    for item in assessments:
        if not isinstance(item, dict):
            raise ResearchError("each task assessment must be an object")
        tid = item.get("task_id")
        if tid not in tasks:
            raise ResearchError("assessment for a task outside this round: " + str(tid))
        if tid in seen:
            raise ResearchError("duplicate assessment for task " + tid)
        seen.append(tid)
        execution = item.get("execution_status")
        science = item.get("scientific_outcome")
        if execution not in EXECUTION:
            raise ResearchError("invalid execution_status: " + str(execution))
        if science not in SCIENCE:
            raise ResearchError("invalid scientific_outcome: " + str(science))
        if execution != "completed" and science != "not_assessed":
            raise ResearchError("an unfinished task must be not_assessed: " + tid)
        task = w.store.get("task", tid)
        if execution == "not_run":
            if task["status"] != "pending" or task.get("started_at") or task.get("pid"):
                raise ResearchError("not_run only applies to a task that never started: " + tid)
        elif execution != TASK_EXECUTION.get(task["status"]):
            raise ResearchError(f"execution_status does not match registered task state: {tid} is {task['status']}")
        if strict and science not in {"inconclusive", "no_progress", "not_assessed"}:
            if task.get("stale_basis") or w._stale_basis(task):
                raise ResearchError("stale proof basis cannot receive a scientific verdict: " + tid)
            result = task.get("validation_outcome")
            if result and (result["protocol_status"] != "valid" or result["verdict"] != "passed"):
                raise ResearchError("the validation task lacks a passing protocol verdict: " + tid)
        if science in {"proved", "disproved"}:
            refs = item.get("evidence_refs") or []
            if not refs:
                raise ResearchError(science + " needs an accepted claim reference in evidence_refs: " + tid)
            claim = w.store.get("claim", refs[0])
            node = task["node"]
            if claim["status"] != "accepted" or claim["node"] != node:
                raise ResearchError("evidence claim must be accepted for the task's own node: " + tid)
            if science == "proved" and claim["evidence_level"] != "proved":
                raise ResearchError("proved needs an accepted proved claim: " + tid)
            if science == "disproved" and claim["evidence_level"] != "disproved":
                raise ResearchError("disproved needs an accepted disproved claim: " + tid)
    missing = sorted(tasks - set(seen))
    if missing:
        raise ResearchError("checkpoint misses the assessments of: " + ", ".join(missing))


OBSTACLE_EFFECTS = ("none", "weakened", "reformulated", "resolved", "unclear")
PREDICTIONS = ("matched", "mismatched", "untested")
EVIDENCE_KINDS = ("artifact", "claim", "inference", "task", "validation",
                  "round_checkpoint", "round_settled", "research_finding")


def _eval(w, p, record):
    """The 8.3 checkpoint eval segment (v3.4 §6.2): record what changed, not a verdict."""
    raw = p.get("eval")
    if raw is None:
        return None
    if not isinstance(raw, dict) or not raw:
        raise ResearchError("eval segment must be a nonempty object")
    out = {}
    if "obstacle_effect" in raw:
        effect = raw["obstacle_effect"]
        if effect not in OBSTACLE_EFFECTS:
            raise ResearchError("obstacle_effect must be one of " + ", ".join(OBSTACLE_EFFECTS))
        out["obstacle_effect"] = effect
        if "obstacle" in raw:
            obstacle = w.store.get("obstacle", raw["obstacle"])
            if obstacle.get("node") != record["node"]:
                raise ResearchError("eval obstacle belongs to a different node")
            out["obstacle"] = obstacle["id"]
        if effect in {"reformulated", "resolved"} or "evidence_refs" in raw:
            refs = raw.get("evidence_refs") or []
            if not isinstance(refs, list) or not refs:
                raise ResearchError("obstacle_effect=" + effect + " needs evidence_refs")
            for ref in refs:
                if not any(w.store.exists(kind, ref) for kind in EVIDENCE_KINDS):
                    raise ResearchError("eval evidence_ref is not a registered object: " + str(ref))
            out["evidence_refs"] = list(refs)
    if "prediction_match" in raw:
        value = raw["prediction_match"]
        if value not in PREDICTIONS:
            raise ResearchError("prediction_match must be one of " + ", ".join(PREDICTIONS))
        out["prediction_match"] = value
        if "intent" in raw:
            w.store.get("research_intent", raw["intent"])
            out["intent"] = raw["intent"]
    if "critique_ref" in raw:
        w.store.get("critique", raw["critique_ref"])
        out["critique_ref"] = raw["critique_ref"]
    if not out:
        raise ResearchError("eval segment needs obstacle_effect, prediction_match or critique_ref")
    return out


def _derive_outcome(assessments, claims, findings=()):
    if claims:
        return "claim"
    if findings:
        return "finding"
    if any(item.get("scientific_outcome") in {"proved", "disproved"} for item in assessments):
        return "no-progress"
    return "no-progress"


def _check_findings(w, record, ids):
    """Findings referenced by a round must exist, be findings, and match its node."""
    if not isinstance(ids, list):
        raise ResearchError("round findings must be a list of research_finding ids")
    seen = []
    rows = []
    for key in ids:
        if key in seen:
            raise ResearchError("duplicate finding in round: " + str(key))
        seen.append(key)
        row = w.store.get("research_finding", key)
        if row["node"] != record["node"]:
            raise ResearchError("round finding belongs to a different node: " + key)
        rows.append(row)
    return rows


def assess(w, p, actor):
    _text(p, "round", "finding", "next_question")
    record = row(w, p["round"])
    if record["state"] in {"assessed", "settled"}:
        raise ResearchError("round already assessed: " + p["round"])
    if record["state"] == "aborted":
        raise ResearchError("round is aborted: " + p["round"])
    if not record["tasks"]:
        raise ResearchError("round has no attached tasks; attach a finished task "
                            "(round.attach) or abort it (round.recover mode=abort)")
    assessments = p.get("task_assessments")
    if not isinstance(assessments, list) or not assessments:
        raise ResearchError("a checkpoint needs task_assessments")
    if not _all_closeable(w, record, assessments):
        raise ResearchError("round has unfinished tasks; assess only after every task is terminal "
                            "(a never-started task may be declared execution_status=not_run)")
    _check_assessments(w, record, assessments)
    for item in assessments:
        if item.get("execution_status") == "not_run":
            task = w.store.get("task", item["task_id"])
            task.update(status="abandoned", abandoned_at=now(), abandoned_by=actor,
                        abandon_reason="never started before the round was assessed")
            w.store.put("task", task["id"], task)
    claims = list(p.get("claims") or [])
    from .policy import strict_research
    if strict_research(w):
        from .progress_evidence import claim_evidence
        for ident in claims:
            if not claim_evidence(w, w.store.get("claim", ident)):
                raise ResearchError("checkpoint claim has no current verified closure evidence: " + ident)
        for item in assessments:
            if item["scientific_outcome"] in {"proved", "disproved"} and item["evidence_refs"][0] not in claims:
                raise ResearchError("include each assessed proof or refutation in checkpoint claims")
    finding_ids = list(p.get("findings") or [])
    findings = _check_findings(w, record, finding_ids)
    eval_segment = _eval(w, p, record)
    outcome = p.get("outcome") or _derive_outcome(assessments, claims, findings)
    if outcome == "finding" and not findings:
        raise ResearchError("a finding outcome needs at least one research_finding id")
    checkpoint = {"id": "cp-" + record["id"], "round": record["id"], "route": record["route"],
                  "task_assessments": assessments, "finding": p["finding"],
                  "next_question": p["next_question"], "claims": claims,
                  "findings": finding_ids,
                  "discovery_kind": p.get("discovery_kind"),
                  "eval": eval_segment,
                  "artifacts": list(p.get("artifacts") or []),
                  "outcome": outcome, "assessed_by": actor, "assessed_at": now(),
                  "claim_revisions": {key: w.store.revision("claim", key) for key in claims}}
    w.store.put("round_checkpoint", checkpoint["id"], checkpoint, new=True)
    record.update(state="assessed", checkpoint=checkpoint["id"])
    w.store.put("round", record["id"], record)
    return checkpoint


def record_attempt(w, p, actor):
    """Record a round that produced an attempt but no dispatched task (audit fix F13).

    A failed or refused exploration should still be a first-class round: it writes a
    checkpoint with no task assessments and settles like any other no-progress round.
    It can never carry claims, so it cannot promote anything.
    """
    _text(p, "round", "finding", "next_question")
    record = row(w, p["round"])
    if record["state"] in {"assessed", "settled", "aborted"}:
        raise ResearchError("round already " + record["state"] + ": " + p["round"])
    if record["tasks"]:
        raise ResearchError("round has dispatched tasks; record their assessments with round.assess")
    outcome = p.get("outcome") or "no-progress"
    finding_ids = list(p.get("findings") or [])
    findings = _check_findings(w, record, finding_ids)
    eval_segment = _eval(w, p, record)
    if outcome == "no-progress" and findings:
        outcome = "finding"
    if outcome == "finding" and not finding_ids:
        raise ResearchError("a finding outcome needs at least one research_finding id")
    if outcome not in OUTCOMES:
        raise ResearchError("round outcome must be one of " + ", ".join(OUTCOMES))
    if p.get("claims"):
        raise ResearchError("a task-less attempt cannot carry claims")
    artifacts = list(p.get("artifacts") or [])
    for key in artifacts:
        w.store.verify_blob(key)
    checkpoint = {"id": "cp-" + record["id"], "round": record["id"], "route": record["route"],
                  "task_assessments": [], "finding": p["finding"],
                  "next_question": p["next_question"], "claims": [], "artifacts": artifacts,
                  "findings": finding_ids, "discovery_kind": p.get("discovery_kind"),
                  "eval": eval_segment,
                  "outcome": outcome, "attempt": True, "attempt_reason": p.get("reason"),
                  "assessed_by": actor, "assessed_at": now(), "claim_revisions": {}}
    if w.store.exists("round_checkpoint", checkpoint["id"]):
        raise ResearchError("round already has a checkpoint: " + p["round"])
    w.store.put("round_checkpoint", checkpoint["id"], checkpoint, new=True)
    record.update(state="assessed", attempt=True, assessed_at=checkpoint["assessed_at"])
    w.store.put("round", record["id"], record)
    return checkpoint


def settle(w, rid, actor):
    """Apply the checkpoint's bookkeeping exactly once, in one transaction."""
    if not isinstance(rid, str) or not rid.strip():
        raise ResearchError("round.settle needs a nonempty round id")
    record = row(w, rid)
    if w.store.exists("round_settled", rid):
        return w.store.get("round_settled", rid)
    if record["state"] != "assessed":
        raise ResearchError("round is not assessed; write the checkpoint first")
    checkpoint = w.store.get("round_checkpoint", "cp-" + rid)
    from .policy import strict_research
    invalidated = []
    if strict_research(w):
        from .progress_evidence import claim_evidence
        invalidated = [ident for ident, revision in checkpoint["claim_revisions"].items()
                       if w.store.revision("claim", ident) != revision or not claim_evidence(w, w.store.get("claim", ident))]
        if invalidated:
            remaining = [ident for ident in checkpoint["claims"] if ident not in invalidated]
            assessments = [dict(item, scientific_outcome="inconclusive")
                           if item["scientific_outcome"] in {"proved", "disproved"}
                           and item["evidence_refs"][0] in invalidated else item
                           for item in checkpoint["task_assessments"]]
            checkpoint = {**checkpoint, "claims": remaining, "task_assessments": assessments,
                          "outcome": _derive_outcome(assessments, remaining, checkpoint["findings"])}
    # Reuse the audited route/obligation bookkeeping instead of a second copy.
    route = w._checkpoint({"route": checkpoint["route"], "finding": checkpoint["finding"],
                           "next_question": checkpoint["next_question"],
                           "claims": checkpoint["claims"], "artifacts": checkpoint["artifacts"],
                           "findings": checkpoint.get("findings") or [],
                           "eval": checkpoint.get("eval"),
                           "outcome": checkpoint["outcome"]}, actor,
                          assessments=checkpoint["task_assessments"], bound_round=True)
    settled = {"id": rid, "round": rid, "route": checkpoint["route"],
               "checkpoint": checkpoint["id"], "outcome": checkpoint["outcome"],
               "claims": checkpoint["claims"],
               "findings": checkpoint.get("findings") or [], "progress": route["checkpoints"][-1]["progress"],
               "settled_by": actor, "settled_at": now()}
    if invalidated:
        settled["invalidated_claims"] = invalidated
    w.store.put("round_settled", rid, settled, new=True)
    record.update(state="settled", settled_at=settled["settled_at"])
    w.store.put("round", record["id"], record)
    from .intent import reassess_scout
    reassess_scout(w, settled, checkpoint, actor)
    return record


def attach_finished(w, p, actor):
    """Attach an already terminal task to an open round (Batch 8.1 audit fix F3).

    The intended order is round.open -> task.add(round=...) -> run -> assess, but a
    task created before the round (or a rerun) had no way in. Only terminal tasks may
    be attached, and only while the round is still open/running.
    """
    _text(p, "round", "task")
    record = row(w, p["round"])
    if record["state"] not in {"open", "running"}:
        raise ResearchError("round " + record["id"] + " no longer accepts tasks (" + record["state"] + ")")
    task = w.store.get("task", p["task"])
    if not _task_is_terminal(w, task["id"]):
        raise ResearchError("only a terminal task can be attached after the fact; run it first")
    if task.get("round") and task["round"] != record["id"]:
        raise ResearchError("task already belongs to round " + task["round"])
    if task["id"] in record["tasks"]:
        return record
    record["tasks"].append(task["id"])
    record["state"] = "running"
    w.store.put("round", record["id"], record)
    task["round"] = record["id"]
    w.store.put("task", task["id"], task)
    return record


def recover(w, p, actor):
    _text(p, "round", "mode")
    record = row(w, p["round"])
    if p["mode"] == "settle":
        return settle(w, record["id"], actor)
    if p["mode"] != "abort":
        raise ResearchError("round.recover mode must be settle or abort")
    if record["state"] in {"settled", "aborted"}:
        return record
    if record["state"] == "assessed":
        raise ResearchError("an assessed round must be settled, not aborted")
    _text(p, "reason")
    record.update(state="aborted", aborted_by=actor, aborted_at=now(), abort_reason=p["reason"])
    w.store.put("round", record["id"], record)
    return record


HANDLERS = {"round.open": open_round,
            "round.attach": attach_finished,
            "round.record_attempt": record_attempt,
            "round.assess": assess,
            "round.recover": recover}

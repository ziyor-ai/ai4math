"""Inspect mathematical dependencies and register narrowly scoped diagnostic evidence."""

import json

from .knowledge import strings, text_fields
from .request_identity import result_binding
from .store import ResearchError, digest, now


def save(w, kind, p, actor, fields, *, level="unknown", **details):
    row = {field: p[field] for field in ("id", *fields)}
    row.update(details, author=actor, created_at=now(), evidence_level=level)
    w.store.put(kind, row["id"], row, new=True)
    return row


def revision(w, p, actor):
    text_fields(p, "id", "original", "revised", "reason")
    old, new = [w.store.get("node", p[key]) for key in ("original", "revised")]
    if old["statement_digest"] == new["statement_digest"]:
        raise ResearchError("revision needs a distinct locked statement, domain or quantifier")
    parents = {r["revised"]: r["original"] for r in w.store.all("diagnosis_revision")}
    if new["id"] in parents:
        raise ResearchError("revised node already has a recorded parent")
    ancestor = old["id"]
    while ancestor is not None:
        if ancestor == new["id"]:
            raise ResearchError("revision cycle")
        ancestor = parents.get(ancestor)
    changes = {field: {"before": old[field], "after": new[field]}
               for field in ("statement", "domain", "quantifier") if old[field] != new[field]}
    return save(w, "diagnosis_revision", p, actor, ("original", "revised", "reason"),
                original_digest=old["statement_digest"], revised_digest=new["statement_digest"], changes=changes)


def checked_result(w, p, actor, node, inputs, kind):
    text_fields(p, "task", "trust_basis")
    task = w.store.get("task", p["task"])
    if (task["status"] != "completed" or task["role"] != "validate" or task["node"] != node["id"]
            or actor in {task["actor"], task.get("executed_by")} or set(task["inputs"]) != set(inputs)
            or not task.get("result_artifact")):
        raise ResearchError("diagnosis needs a completed independent validator with exact node and inputs")
    if not task.get("validator_artifact"):
        raise ResearchError("diagnosis needs a frozen validator")
    for artifact in [*inputs, task["validator_artifact"]]:
        w.store.verify_blob(artifact)
    raw = json.loads(w.store.verify_blob(task["result_artifact"]).read_text(encoding="utf-8-sig"))
    expected = {"task_id": task["id"], "statement_digest": node["statement_digest"],
                "input_artifacts": sorted(inputs), "scope": "finite", **result_binding(task)}
    if (not isinstance(raw, dict) or raw.get("passed") is not True
            or any(raw.get(k) != v for k, v in expected.items())
            or not isinstance(raw.get("diagnosis"), dict) or raw["diagnosis"].get("kind") != kind):
        raise ResearchError("diagnosis receipt does not bind the finite independent check")
    strings(raw, "checks", 1)
    strings(raw, "limitations", 1)
    return task, raw


def replay(w, p, actor):
    text_fields(p, "id", "revision", "counterexample", "witness")
    change = w.store.get("diagnosis_revision", p["revision"])
    claim = w.store.get("claim", p["counterexample"])
    validation = w.store.get("validation", claim["validation"]) if claim.get("validation") else {}
    if (claim["status"] != "accepted" or claim["evidence_level"] != "disproved"
            or claim["node"] != change["original"] or p["witness"] not in claim["artifact_ids"]
            or validation.get("method") != "counterexample" or not validation.get("passed")):
        raise ResearchError("replay needs a witness from an independently accepted counterexample to the original")
    for aid in validation["artifact_ids"]:
        w.store.verify_blob(aid)
    revised = w.store.get("node", change["revised"])
    task, raw = checked_result(w, p, actor, revised, [p["witness"]], "revision_replay")
    if claim["author"] in {task["actor"], task.get("executed_by")}:
        raise ResearchError("replay validator must differ from the counterexample author")
    result = raw["diagnosis"]
    text_fields(result, "reason")
    if result.get("outcome") not in {"still_refutes", "excluded_by_condition", "conclusion_holds", "inconclusive"}:
        raise ResearchError("invalid witness replay outcome")
    if result["outcome"] == "excluded_by_condition":
        text_fields(result, "condition")
    if any(r["task"] == task["id"] for r in w.store.all("diagnosis_replay")):
        raise ResearchError("revision replay receipt already registered")
    return save(w, "diagnosis_replay", p, actor, ("revision", "counterexample", "witness", "task", "trust_basis"),
                level="unknown" if result["outcome"] == "inconclusive" else "finite_support",
                result=result, receipt=task["result_artifact"], limitations=raw["limitations"])


def indistinguishability(w, p, actor):
    text_fields(p, "id", "node", "question", "next_step")
    witnesses = strings(p, "witnesses", 2, 2)
    if len(set(witnesses)) != 2:
        raise ResearchError("indistinguishability needs two different witness artifacts")
    node = w.store.get("node", p["node"])
    task, raw = checked_result(w, p, actor, node, witnesses, "indistinguishability")
    rows = raw["diagnosis"].get("witnesses")
    if (not isinstance(rows, dict) or set(rows) != set(witnesses)
            or any(not isinstance(row, dict) or set(row) != {"realization", "invariants", "conclusion"}
                   or row["realization"] not in ("actual", "formal") or not isinstance(row["invariants"], dict)
                   or not row["invariants"] for row in rows.values())):
        raise ResearchError("receipt must describe each exact witness, invariants, conclusion and realization")
    left, right = (rows[key] for key in witnesses)
    if (digest(left["invariants"]) != digest(right["invariants"])
            or digest(left["conclusion"]) == digest(right["conclusion"])):
        raise ResearchError("witnesses must agree on the recorded invariants and differ on the conclusion")
    if any(row["realization"] == "formal" for row in rows.values()):
        text_fields(raw["diagnosis"], "realizability_gap")
    if any(r["task"] == task["id"] for r in w.store.all("diagnosis_indistinguishability")):
        raise ResearchError("indistinguishability receipt already registered")
    return save(w, "diagnosis_indistinguishability", p, actor,
                ("node", "question", "witnesses", "task", "trust_basis", "next_step"), level="finite_support",
                result=raw["diagnosis"], receipt=task["result_artifact"], limitations=raw["limitations"])


def source_check(w, p, actor):
    text_fields(p, "id", "node", "passage", "theorem", "use_point", "limitations")
    node = w.store.get("node", p["node"])
    record = w.store.get("source_passage", p["passage"])
    passage = record["content"]
    w.store.verify_blob(record["artifact"])
    document = w.store.get("source_document", passage["document"])
    original = w.store.get("literature_source", passage["source_id"])
    doc, source = document["content"], original["content"]
    for attached in (document, original):
        w.store.verify_blob(attached["artifact"])
        if digest(attached["content"]) != attached["id"] or attached["artifact"] != attached["id"]:
            raise ResearchError("source check attachment differs from its content identity")
    if (digest(passage) != p["passage"] or doc["source_id"] != passage["source_id"]
            or (source["kind"] == "provider_metadata" and source.get("raw_artifact") == doc["raw_artifact"])):
        raise ResearchError("source check requires an attached original passage and exact document")
    w.store.verify_blob(doc["raw_artifact"])
    if "supersedes" in p:
        prior = w.store.get("diagnosis_source_check", p["supersedes"])
        if (prior["node"], prior["use_point"]) != (node["id"], p["use_point"]):
            raise ResearchError("source correction must bind the same use point and node")
        if any(row.get("supersedes") == prior["id"] for row in w.store.all("diagnosis_source_check")):
            raise ResearchError("source check already superseded")
    conditions = p.get("conditions")
    if not isinstance(conditions, dict) or not 1 <= len(conditions) <= 32:
        raise ResearchError("map each original hypothesis to an assessment")
    for hypothesis, check in conditions.items():
        text_fields({"hypothesis": hypothesis}, "hypothesis")
        text_fields(check, "status", "reason")
        status = check["status"]
        if status == "satisfied":
            text_fields(check, "claim")
            claim = w.store.get("claim", check["claim"])
            if claim["status"] != "accepted" or claim["evidence_level"] != "proved":
                raise ResearchError("a satisfied hypothesis needs an accepted proved claim")
            if node["id"] in ({claim["node"]} | dependency_nodes(w, claim["node"])):
                raise ResearchError("hypothesis evidence depends on the theorem application")
        elif status == "open":
            text_fields(check, "obligation")
            if check["obligation"] == node["id"] or w.node_status(check["obligation"]) != "open":
                raise ResearchError("unmet hypothesis needs a distinct open obligation")
        elif status != "mismatch":
            raise ResearchError("source hypothesis status must be satisfied, open or mismatch")
    return save(w, "diagnosis_source_check", p, actor,
                ("node", "passage", "theorem", "use_point", "conditions", "limitations"),
                supersedes=p.get("supersedes"), document=passage["document"], raw_artifact=doc["raw_artifact"],
                source={k: source[k] for k in ("title", "url", "version")}, locator=passage["locator"])


def dependency_nodes(w, key):
    seen = set()
    todo = [key]
    while todo:
        node = w.store.get("node", todo.pop())
        for dep in node["statement_dependencies"] + node["proof_dependencies"]:
            if dep not in seen:
                seen.add(dep)
                todo.append(dep)
    return seen


def impact(w, key):
    """Use declared edges. A dependency hit requires review, never automatic refutation."""
    graph = {}
    for node in w.store.all("node"):
        for field in ("statement_dependencies", "proof_dependencies"):
            for dep in node[field]:
                graph.setdefault(dep, []).append({"from": dep, "to": node["id"], "kind": field})
    affected, todo, edges = {key}, [key], []
    while todo:
        for edge in graph.get(todo.pop(), []):
            edges.append(edge)
            if edge["to"] not in affected:
                affected.add(edge["to"])
                todo.append(edge["to"])
    claims = w.store.all("claim")
    affected_claims = {claim["id"] for claim in claims if claim["node"] in affected}
    while True:
        downstream = {claim["id"] for claim in claims if affected_claims.intersection(claim["dependencies"])}
        if downstream <= affected_claims:
            break
        affected_claims.update(downstream)
    goal = w.store.get("project", "main")["goal"]
    return {"nodes_to_review": sorted(affected - {key}), "dependency_edges": edges,
            "claims_to_review": sorted(affected_claims),
            "ledger_to_review": [row for row in w.store.all("ledger") if row["claim"] in affected_claims],
                "goal": goal, "goal_status": w.node_status(goal) if goal else "unset",
            "other_accepted_proofs": [claim["id"] for claim in claims if claim["status"] == "accepted"
                                      and claim["evidence_level"] == "proved" and claim["id"] not in affected_claims]}


def current_source_check(w, row):
    conditions = row["conditions"]
    stale = [name for name, check in conditions.items() if check["status"] == "satisfied"
             and w.store.get("claim", check["claim"])["status"] != "accepted"]
    open_items = {name: check["obligation"] for name, check in conditions.items() if check["status"] == "open"}
    state = ("mismatch" if any(check["status"] == "mismatch" for check in conditions.values())
             else "needs_review" if stale else "conditional" if open_items else "mapped")
    return {**row, "assessment": state, "stale_hypotheses": stale, "open_hypotheses": open_items,
            "obligation_status": {key: w.node_status(key) for key in set(open_items.values())},
            "impact": impact(w, row["node"]) if state != "mapped" or row["supersedes"] else None}


def report(w, node):
    with w.store.snapshot():
        target = w.store.get("node", node)
        impacts = []
        claims = w.store.all("claim")
        for claim in claims:
            if claim["node"] != node or claim["status"] != "accepted" or claim["evidence_level"] != "disproved":
                continue
            validation = w.store.get("validation", claim["validation"])
            if validation["method"] == "counterexample" and validation["passed"]:
                for aid in claim["artifact_ids"] + validation["artifact_ids"]:
                    w.store.verify_blob(aid)
                impacts.append({"claim": claim["id"], "witness_artifacts": claim["artifact_ids"],
                                "trust_basis": validation["trust_basis"], **impact(w, node)})
        candidate_witnesses = [r for r in w.store.all("exploration_counterexample") if r["obligation"] == node]
        for row in candidate_witnesses:
            w.store.verify_blob(row["receipt"])
        revisions = [r for r in w.store.all("diagnosis_revision") if node in (r["original"], r["revised"])]
        revision_ids = {r["id"] for r in revisions}
        replays = [{**r, "original_counterexample_active": w.store.get("claim", r["counterexample"])["status"] == "accepted"}
                   for r in w.store.all("diagnosis_replay") if r["revision"] in revision_ids]
        seen_replays = {(r["revision"], r["counterexample"], r["witness"]) for r in replays}
        pending = [{"revision": r["id"], "counterexample": c["id"], "artifact": aid}
                   for r in revisions for c in claims
                   if c["node"] == r["original"] and c["status"] == "accepted" and c["evidence_level"] == "disproved"
                   for aid in c["artifact_ids"] if (r["id"], c["id"], aid) not in seen_replays]
        sources = w.store.all("diagnosis_source_check")
        superseded = {r["supersedes"] for r in sources}
        affected = {node, *(item for hit in impacts for item in hit["nodes_to_review"])}
        collisions = [r for r in w.store.all("diagnosis_indistinguishability") if r["node"] == node]
        for row in replays + collisions:
            w.store.verify_blob(row["receipt"])
            for aid in ([row["witness"]] if "witness" in row else row["witnesses"]):
                w.store.verify_blob(aid)
        for row in sources:
            if row["node"] == node:
                for aid in (row["passage"], row["raw_artifact"]):
                    w.store.verify_blob(aid)
        return {"node": target, "node_status": w.node_status(node), "counterexample_impact": impacts,
                "candidate_counterexamples": candidate_witnesses,
                "revisions": revisions, "witness_replays": replays, "unreplayed_artifacts": pending,
                "indistinguishability": collisions,
                "source_checks": [current_source_check(w, r) for r in sources
                                  if r["node"] == node and r["id"] not in superseded],
                "superseded_source_checks": [r for r in sources if r["node"] == node and r["id"] in superseded],
                "routes_to_review": [{k: route[k] for k in ("id", "node", "status", "next_question", "checkpoints")}
                                     for route in w.store.all("route") if route["node"] in affected]}


HANDLERS = {"diagnosis.revision": revision, "diagnosis.replay": replay,
            "diagnosis.indistinguishability": indistinguishability, "diagnosis.source_check": source_check}

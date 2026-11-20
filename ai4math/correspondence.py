"""Correspondence gate: keep the model tied to the locked statement.

A branch that drifts into "two waves plus a slowly varying cap" is only useful
if someone maps every model claim back to the locked node.  This module records
that mapping and refuses a new mechanism on an obligation that has been running
without one.
"""

from .store import ResearchError, now

DEFAULT_AFTER = 6
VERDICTS = ("aligned", "partial", "mismatch")
STATUSES = ("mapped", "open", "mismatch")


def threshold(workbench):
    from .strategy import state
    row = state(workbench)
    if row and isinstance(row.get("correspondence_after"), int):
        return row["correspondence_after"]
    return DEFAULT_AFTER


def latest(workbench, node):
    rows = [row for row in workbench.store.all("correspondence") if row["node"] == node]
    if not rows:
        return None
    rows.sort(key=lambda row: row.get("created_at") or "")
    return rows[-1]


def _stamps(workbench, node):
    return [entry.get("timestamp") for route in workbench.store.all("route") if route["node"] == node
            for entry in (route.get("checkpoints") or []) if entry.get("timestamp")]


def check_route(workbench, node):
    stamps = _stamps(workbench, node)
    if len(stamps) < threshold(workbench):
        return
    last_work = max(stamps)
    record = latest(workbench, node)
    if record is not None and (record.get("created_at") or "") >= last_work:
        return
    raise ResearchError(
        "correspondence gate: " + node + " has " + str(len(stamps)) + " rounds without a recorded " +
        "model-to-statement mapping; record correspondence before opening another mechanism")


def record(workbench, payload, actor):
    if workbench.store.pending is None:
        with workbench.store.transaction("correspondence.record", actor):
            return record(workbench, payload, actor)
    node = payload.get("node")
    if not isinstance(node, str) or node not in workbench.cone():
        raise ResearchError("correspondence must name a node in the goal dependency cone")
    if workbench.node_status(node) != "open":
        raise ResearchError("correspondence targets a settled node")
    verdict = payload.get("verdict")
    if verdict not in VERDICTS:
        raise ResearchError("correspondence verdict must be one of " + ", ".join(VERDICTS))
    mapping = payload.get("mapping")
    if not isinstance(mapping, list) or not mapping:
        raise ResearchError("correspondence needs a nonempty mapping list")
    for row in mapping:
        if not isinstance(row, dict) or row.get("status") not in STATUSES:
            raise ResearchError("each mapping row needs model_claim, locked_target and a status of " + ", ".join(STATUSES))
        for key in ("model_claim", "locked_target"):
            if not isinstance(row.get(key), str) or not row[key].strip():
                raise ResearchError("each mapping row needs nonempty " + key)
    artifacts = payload.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        raise ResearchError("correspondence needs at least one frozen artifact")
    for key in artifacts:
        workbench.store.verify_blob(key)
    kind = payload.get("reviewer_kind", "model")
    if kind not in {"model", "human"}:
        raise ResearchError("reviewer_kind must be model or human")
    if kind == "human":
        if not isinstance(payload.get("attestation"), str) or not payload["attestation"].strip():
            raise ResearchError("a human correspondence review needs a nonempty attestation")
    else:
        from .review_gate import _last_dispatcher
        if actor == _last_dispatcher(workbench):
            raise ResearchError("correspondence review needs an actor other than the last dispatcher")
    statement = workbench.store.get("node", node)["statement_digest"]
    entry = {"id": payload.get("id") or ("corr-" + node + "-" + str(len([r for r in workbench.store.all("correspondence")
                                                                        if r["node"] == node]) + 1)),
              "node": node, "statement_digest": statement, "verdict": verdict, "mapping": mapping,
              "artifacts": artifacts, "reviewer": actor, "reviewer_kind": kind,
              "attestation": payload.get("attestation"), "created_at": now(),
              "evidence_level": "unknown"}
    workbench.store.put("correspondence", entry["id"], entry, new=True)
    if verdict == "mismatch":
        from .review_gate import trip
        trip(workbench, "correspondence mismatch on " + node + " (" + entry["id"] + ")")
    return entry


def view(workbench, node):
    record = latest(workbench, node)
    stamps = _stamps(workbench, node)
    return {"node": node, "rounds": len(stamps), "threshold": threshold(workbench),
            "latest": None if record is None else {k: record[k] for k in ("id", "verdict", "reviewer_kind", "created_at")},
            "due": bool(stamps) and len(stamps) >= threshold(workbench) and
                   (record is None or (record.get("created_at") or "") < max(stamps))}

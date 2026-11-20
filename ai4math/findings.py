"""Research findings: level 1 of the evidence ladder (Batch 8.5, v3.5).

A finding records a mathematical observation, the kind and boundary of its evidence,
its relation to a target obligation, and one concrete next action. It is never a
proof: the record stays ``unverified`` forever, it cannot be a claim dependency, and
it only counts as research progress in the narrow sense defined by ``checked``.

The ladder is:

    research_finding          level 1, no Lean required, stored by this module
    claim(maturity=candidate) level 2, a stated proposition with a proof sketch
    claim(maturity=verified)  level 3, accepted only after a passing validation

Levels 2 and 3 are the existing ``claim`` object (see workbench), not a second system.
"""
import re

from .store import ResearchError, now

KINDS = ("new_structure", "new_bound", "new_reformulation", "counterexample_candidate",
         "obstacle_identified", "failed_route_analysis", "literature_connection", "other")
EVIDENCE = ("theoretical_argument", "finite_experiment", "literature", "conjecture")
RELATIONS = ("load_bearing", "supporting", "exploratory", "speculative_bridge")
CHECKED_RELATIONS = ("load_bearing", "supporting")
REFERENCE_KINDS = ("artifact", "task", "discovery", "inference", "claim", "obstacle",
                   "research_hypothesis", "validation", "round_checkpoint", "round_settled",
                   "approach", "research_intent")
SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _text(p, *keys):
    for key in keys:
        if not isinstance(p.get(key), str) or not p[key].strip():
            raise ResearchError("nonempty text required: " + key)


def _reference_exists(w, key):
    if not isinstance(key, str) or not key.strip():
        return False
    if SHA256.fullmatch(key):
        w.store.verify_blob(key)
        return True
    return any(w.store.exists(kind, key) for kind in REFERENCE_KINDS)


def add(w, p, actor):
    if not isinstance(p, dict):
        raise ResearchError("finding.add needs an object payload")
    _text(p, "id", "node", "kind", "statement", "scope", "evidence_kind")
    if w.store.exists("research_finding", p["id"]):
        raise ResearchError("research finding already exists: " + p["id"])
    node = w.store.get("node", p["node"])
    if p["kind"] not in KINDS:
        raise ResearchError("finding kind must be one of " + ", ".join(KINDS))
    if p["evidence_kind"] not in EVIDENCE:
        raise ResearchError("evidence_kind must be one of " + ", ".join(EVIDENCE))
    if "verification" in p:
        raise ResearchError("the state machine keeps findings unverified; "
                            "a verified statement must go through claim.add")
    relation = p.get("relation")
    if not isinstance(relation, dict) or not isinstance(relation.get("target"), str):
        raise ResearchError("finding needs relation {target, type}")
    if relation.get("type") not in RELATIONS:
        raise ResearchError("relation type must be one of " + ", ".join(RELATIONS))
    w.store.get("node", relation["target"])
    action = p.get("next_action")
    if not isinstance(action, dict):
        raise ResearchError("finding needs a next_action object")
    if not isinstance(action.get("kind"), str) or not action["kind"].strip():
        raise ResearchError("next_action.kind must be a nonempty string")
    if not isinstance(action.get("question"), str) or not action["question"].strip():
        raise ResearchError("next_action.question must be a nonempty string")
    refs = p.get("evidence_refs")
    if not isinstance(refs, list) or not refs:
        raise ResearchError("a finding needs at least one evidence_ref")
    for ref in refs:
        if not _reference_exists(w, ref):
            raise ResearchError("evidence_ref is not a frozen, registered object: " + str(ref))
    task = p.get("task")
    if task is not None:
        if w.store.get("task", task)["node"] != node["id"]:
            raise ResearchError("finding task must belong to the finding node")
    round_id = p.get("round")
    if round_id is not None:
        bound = w.store.get("round", round_id)
        if bound["node"] != node["id"]:
            raise ResearchError("finding round must belong to the finding node")
    row = {"id": p["id"], "node": node["id"], "round": round_id, "task": task,
           "intent": p.get("intent"), "kind": p["kind"],
           "statement": p["statement"].strip(), "scope": p["scope"].strip(),
           "evidence_kind": p["evidence_kind"],
           "evidence_refs": list(dict.fromkeys(refs)),
           "relation": {"target": relation["target"], "type": relation["type"]},
           "next_action": {"kind": action["kind"], "question": action["question"]},
           "verification": {"status": "unverified", "basis": None, "ref": None},
           "author": actor, "created_at": now()}
    if p.get("note") is not None:
        row["note"] = p["note"]
    w.store.put("research_finding", row["id"], row, new=True)
    return row


def revise(w, p, actor):
    """Update the next action or the relation; never the statement or the verification."""
    row = w.store.get("research_finding", p.get("id"))
    changed = False
    if "next_action" in p:
        action = p["next_action"]
        if not isinstance(action, dict):
            raise ResearchError("next_action must be an object")
        _text(action, "kind", "question")
        row["next_action"] = {"kind": action["kind"], "question": action["question"]}
        changed = True
    if "relation" in p:
        relation = p["relation"]
        if not isinstance(relation, dict) or relation.get("type") not in RELATIONS:
            raise ResearchError("relation needs a known type")
        target = relation.get("target", row["relation"]["target"])
        w.store.get("node", target)
        row["relation"] = {"target": target, "type": relation["type"]}
        changed = True
    if "note" in p:
        row["note"] = p["note"]
        changed = True
    if not changed:
        raise ResearchError("finding.revise needs next_action, relation or note")
    row["revised_by"] = actor
    row["revised_at"] = now()
    w.store.put("research_finding", row["id"], row)
    return row


def checked(row):
    """A finding that may reset stagnation: load-bearing evidence, not a conjecture."""
    return (row["relation"]["type"] in CHECKED_RELATIONS
            and row["evidence_kind"] != "conjecture"
            and bool(row["evidence_refs"]))


def view(w, node=None):
    rows = [row for row in w.store.all("research_finding")
            if node is None or row["node"] == node]
    return {"total": len(rows),
            "checked": len([row for row in rows if checked(row)]),
            "by_kind": {kind: len([row for row in rows if row["kind"] == kind])
                        for kind in KINDS},
            "by_evidence": {kind: len([row for row in rows if row["evidence_kind"] == kind])
                            for kind in EVIDENCE},
            "by_relation": {kind: len([row for row in rows if row["relation"]["type"] == kind])
                            for kind in RELATIONS}}


HANDLERS = {"finding.add": add, "finding.revise": revise}

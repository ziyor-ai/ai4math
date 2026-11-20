"""Discovery records: new structure, bounds, reformulations — not a proof.

Batch 8.0 slice 9 (frozen baseline §6.7). A discovery points at a frozen artifact
and a location inside it; there is no ``finding`` table and no ``FIND-*`` object.
``counterexample_candidate`` stays on the ``scientific_outcome`` axis, so it is
rejected here to avoid double counting.
"""
from .store import ResearchError, now

KINDS = ("new_structure_found", "new_bound", "new_reformulation",
         "literature_connection", "replication", "other")


def record(w, p, actor):
    if not isinstance(p, dict):
        raise ResearchError("discovery.record needs an object payload")
    for key in ("id", "node", "discovery_kind", "artifact_ref", "finding_kind"):
        if not isinstance(p.get(key), str) or not p[key].strip():
            raise ResearchError("discovery.record needs nonempty " + key)
    if p["discovery_kind"] not in KINDS:
        raise ResearchError("discovery_kind must be one of " + ", ".join(KINDS))
    if p["discovery_kind"] == "other" and not isinstance(p.get("note"), str):
        raise ResearchError("an 'other' discovery needs a note")
    w.store.get("node", p["node"])
    w.store.verify_blob(p["artifact_ref"])
    location = p.get("evidence_location")
    if not isinstance(location, dict) or not location:
        raise ResearchError("discovery.record needs an evidence_location object")
    if not any(key in location for key in ("file", "page", "offset", "label")):
        raise ResearchError("evidence_location needs file, page, offset or label")
    if w.store.exists("discovery", p["id"]):
        raise ResearchError("discovery already exists: " + p["id"])
    row = {"id": p["id"], "node": p["node"], "discovery_kind": p["discovery_kind"],
           "artifact_ref": p["artifact_ref"], "finding_kind": p["finding_kind"],
           "evidence_location": location, "note": p.get("note"),
           "author": actor, "created_at": now()}
    w.store.put("discovery", row["id"], row, new=True)
    return row


def count(w):
    return len(w.store.all("discovery"))


HANDLERS = {"discovery.record": record}

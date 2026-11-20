"""Optional sketch: an incomplete proof outline that must say what is missing.

Batch 8.3 (frozen baseline §6.6). A sketch is never evidence and never enters the
claim ladder; the only mechanical check is that referenced ids exist and that every
gap names why it is load-bearing and what the next step is.
"""
from .store import ResearchError, now


def add(w, p, actor):
    if not isinstance(p, dict):
        raise ResearchError("sketch.add needs an object payload")
    for key in ("id", "approach"):
        if not isinstance(p.get(key), str) or not p[key].strip():
            raise ResearchError("sketch.add needs nonempty " + key)
    if w.store.exists("sketch", p["id"]):
        raise ResearchError("sketch already exists: " + p["id"])
    approach = w.store.get("approach", p["approach"])
    content = p.get("content")
    artifact = p.get("content_artifact")
    if not (isinstance(content, str) and content.strip()) and not artifact:
        raise ResearchError("a sketch needs content text or a content artifact")
    if artifact:
        w.store.verify_blob(artifact)
    gaps = p.get("gaps")
    if not isinstance(gaps, list) or not gaps:
        raise ResearchError("a sketch must list its gaps; a complete sketch is not a sketch")
    for gap in gaps:
        if not isinstance(gap, dict):
            raise ResearchError("each gap must be an object")
        for key in ("missing", "why_load_bearing", "next_step"):
            if not isinstance(gap.get(key), str) or not gap[key].strip():
                raise ResearchError("a gap needs " + key)
    references = p.get("references") or []
    if not isinstance(references, list):
        raise ResearchError("references must be a list")
    for reference in references:
        if not isinstance(reference, dict):
            raise ResearchError("each reference must be an object with kind and id")
        kind, key = reference.get("kind"), reference.get("id")
        if kind not in {"node", "claim", "inference", "approach"}:
            raise ResearchError("sketch reference kind must be node, claim, inference or approach")
        w.store.get(kind, key)
    row = {"id": p["id"], "approach": approach["id"], "node": approach["node"],
           "content": content, "content_artifact": artifact,
           "gaps": gaps, "references": references,
           "author": actor, "created_at": now()}
    w.store.put("sketch", row["id"], row, new=True)
    return row


HANDLERS = {"sketch.add": add}

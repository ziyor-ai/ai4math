"""Blinded critique of one approach (Batch 8.3, frozen baseline §4.9, §6.3).

A critique is a strategy review, not a mathematical verdict: it can say "I do not
know" (``insufficient_evidence`` / ``unresolved`` / ``requires_verification``) and
that is a first-class outcome. It sees a blind bundle, must keep conflicts on the
record, and may compare up to three alternative mechanisms.
"""
from .store import ResearchError, digest, now

VERDICTS = ("plausible", "duplicate", "contradiction", "needs_lemma",
            "insufficient_evidence", "unresolved", "requires_verification")
ALTERNATIVE_VERDICTS = ("insufficient_evidence", "unresolved", "requires_verification")
REJECTING = ("duplicate", "contradiction")


def _text(p, *keys):
    for key in keys:
        if not isinstance(p.get(key), str) or not p[key].strip():
            raise ResearchError("nonempty text required: " + key)


def _blind_ok(bundle, approach, node):
    text = bundle.lower()
    for token in (approach["id"].lower(), node["id"].lower(), approach.get("author", "").lower()):
        if token and token in text:
            raise ResearchError("the blind bundle names " + token + "; strip identifiers before review")


def add(w, p, actor):
    if not isinstance(p, dict):
        raise ResearchError("critique.add needs an object payload")
    _text(p, "id", "approach", "bundle", "verdict", "reason", "reviewer", "auditor")
    if w.store.exists("critique", p["id"]):
        raise ResearchError("critique already exists: " + p["id"])
    approach = w.store.get("approach", p["approach"])
    if approach["status"] not in {"candidate", "reviewed"}:
        raise ResearchError("only a candidate or reviewed approach can be critiqued (" +
                            approach["status"] + ")")
    node = w.store.get("node", approach["node"])
    verdict = p["verdict"]
    if verdict not in VERDICTS:
        raise ResearchError("critique verdict must be one of " + ", ".join(VERDICTS))
    if p["reviewer"] == actor or p["auditor"] == actor:
        raise ResearchError("the critique author must differ from reviewer and auditor")
    if p["reviewer"] == p["auditor"]:
        raise ResearchError("reviewer and feedback auditor must differ")
    _blind_ok(p["bundle"], approach, node)
    gaps = p.get("gaps") or []
    if verdict == "insufficient_evidence":
        if not isinstance(gaps, list) or not gaps:
            raise ResearchError("insufficient_evidence must name the missing evidence in gaps")
        if any(not isinstance(g, str) or not g.strip() for g in gaps):
            raise ResearchError("each gap must be a nonempty description")
    required = p.get("required_evidence")
    if verdict == "requires_verification":
        if not isinstance(required, list) or not required or any(
                not isinstance(r, str) or not r.strip() for r in required):
            raise ResearchError("requires_verification must name the required evidence")
    alternatives = p.get("alternatives") or []
    if not isinstance(alternatives, list) or len(alternatives) > 3:
        raise ResearchError("critique accepts 0..3 alternatives")
    seen = set()
    for alternative in alternatives:
        if not isinstance(alternative, dict):
            raise ResearchError("each alternative must be an object")
        for key in ("mechanism", "differs_from", "first_probe"):
            if not isinstance(alternative.get(key), str) or not alternative[key].strip():
                raise ResearchError("an alternative needs " + key)
        from .boilerplate import canonical
        form = canonical(alternative["mechanism"])
        if form in seen:
            raise ResearchError("alternatives must be structurally different from each other")
        seen.add(form)
    conflicts = p.get("conflicts") or []
    if not isinstance(conflicts, list):
        raise ResearchError("conflicts must be a list")
    for conflict in conflicts:
        if not isinstance(conflict, dict) or not isinstance(conflict.get("item"), str) \
                or not isinstance(conflict.get("positions"), list) or len(conflict["positions"]) < 2:
            raise ResearchError("a conflict needs an item and at least two positions")
        if type(conflict.get("resolved")) is not bool:
            raise ResearchError("a conflict needs resolved: true or false")
    if any(not conflict["resolved"] for conflict in conflicts) and verdict not in ALTERNATIVE_VERDICTS:
        raise ResearchError("an unresolved conflict cannot yield verdict " + verdict +
                            "; record unresolved or requires_verification")
    row = {"id": p["id"], "approach": approach["id"], "node": approach["node"],
           "verdict": verdict, "reason": p["reason"],
           "blind_sha256": digest({"bundle": p["bundle"]}),
           "gaps": list(gaps), "required_evidence": required or [],
           "alternatives": alternatives, "conflicts": conflicts,
           "reviewer": p["reviewer"], "auditor": p["auditor"],
           "author": actor, "created_at": now()}
    w.store.put("critique", row["id"], row, new=True)
    if verdict in REJECTING:
        approach.update(status="rejected", review_reason=p["reason"], reviewed_by=p["reviewer"],
                        reviewed_at=now(), critique=row["id"])
        w.store.put("approach", approach["id"], approach)
    elif verdict in {"plausible", "needs_lemma"} and approach["status"] == "candidate":
        approach.update(status="reviewed", review_reason=p["reason"], reviewed_by=p["reviewer"],
                        reviewed_at=now(), critique=row["id"])
        w.store.put("approach", approach["id"], approach)
    else:
        approach["critique"] = row["id"]
        w.store.put("approach", approach["id"], approach)
    return row


def view(w, approach_id):
    rows = [r for r in w.store.all("critique") if r["approach"] == approach_id]
    return {"total": len(rows),
            "verdicts": {v: len([r for r in rows if r["verdict"] == v]) for v in VERDICTS},
            "alternatives": sum(len(r["alternatives"]) for r in rows),
            "unresolved_conflicts": sum(len([c for c in r["conflicts"] if not c["resolved"]])
                                        for r in rows)}


HANDLERS = {"critique.add": add}

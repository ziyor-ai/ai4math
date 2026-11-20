"""Research hypotheses: global conjectures, separate from local case assumptions.

Batch 8.1 (frozen baseline §3.2, §4.7, §6.3). A hypothesis is not a proof and never
enters the claim evidence ladder; ``refuted`` needs a verified mathematical
refutation or an accepted disproof, never a model opinion.
"""
from .store import ResearchError, now

STATUSES = ("open", "supported", "refuted", "abandoned")


def add(w, p, actor):
    if not isinstance(p, dict):
        raise ResearchError("research_hypothesis.add needs an object payload")
    for key in ("id", "statement", "scope"):
        if not isinstance(p.get(key), str) or not p[key].strip():
            raise ResearchError("research_hypothesis.add needs nonempty " + key)
    if w.store.exists("research_hypothesis", p["id"]):
        raise ResearchError("hypothesis already exists: " + p["id"])
    approaches = p.get("related_approaches") or []
    if not isinstance(approaches, list) or any(not isinstance(x, str) for x in approaches):
        raise ResearchError("related_approaches must be a list of approach ids")
    for key in approaches:
        w.store.get("approach", key)
    if p.get("decisive_test") is not None:
        w.store.get("research_intent", p["decisive_test"])
    row = {"id": p["id"], "statement": p["statement"], "scope": p["scope"],
           "status": "open", "supporting": list(p.get("supporting") or []),
           "refuting": list(p.get("refuting") or []),
           "related_approaches": list(approaches),
           "decisive_test": p.get("decisive_test"),
           "author": actor, "created_at": now()}
    w.store.put("research_hypothesis", row["id"], row, new=True)
    return row


def link(w, p, actor):
    row = w.store.get("research_hypothesis", p.get("id"))
    approaches = p.get("related_approaches")
    if not isinstance(approaches, list) or not approaches:
        raise ResearchError("hypothesis.link needs a nonempty related_approaches list")
    for key in approaches:
        w.store.get("approach", key)
    row["related_approaches"] = sorted(set(row.get("related_approaches") or []) | set(approaches))
    w.store.put("research_hypothesis", row["id"], row)
    return row


def support(w, p, actor):
    row = w.store.get("research_hypothesis", p.get("id"))
    ref = p.get("claim")
    if not isinstance(ref, str) or not ref.strip():
        raise ResearchError("hypothesis.support needs a claim id")
    claim = w.store.get("claim", ref)
    if claim["status"] != "accepted":
        raise ResearchError("supporting evidence must be an accepted claim")
    row["supporting"] = sorted(set(row.get("supporting") or []) | {ref})
    row["status"] = "supported"
    w.store.put("research_hypothesis", row["id"], row)
    return row


def refute(w, p, actor):
    row = w.store.get("research_hypothesis", p.get("id"))
    obstacle_id = p.get("obstacle")
    if obstacle_id is not None:
        obstacle = w.store.get("obstacle", obstacle_id)
        if obstacle["kind"] != "mathematical_refutation" or obstacle["verification_status"] != "verified":
            raise ResearchError("a hypothesis is refuted only by a verified mathematical_refutation")
    else:
        claim_id = p.get("claim")
        if not isinstance(claim_id, str) or not claim_id.strip():
            raise ResearchError("hypothesis.refute needs an obstacle or an accepted disproof claim")
        claim = w.store.get("claim", claim_id)
        if claim["status"] != "accepted" or claim["evidence_level"] != "disproved":
            raise ResearchError("a hypothesis is refuted only by an accepted disproved claim")
        obstacle_id = None
    row["refuting"] = sorted(set(row.get("refuting") or []) |
                             ({obstacle_id} if obstacle_id else set()))
    row.update(status="refuted", refuted_by=actor, refuted_at=now())
    w.store.put("research_hypothesis", row["id"], row)
    return row


def abandon(w, p, actor):
    row = w.store.get("research_hypothesis", p.get("id"))
    reason = p.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        raise ResearchError("hypothesis.abandon needs a reason")
    if row["status"] != "open":
        raise ResearchError("only an open hypothesis can be abandoned")
    row.update(status="abandoned", abandon_reason=reason)
    w.store.put("research_hypothesis", row["id"], row)
    return row


HANDLERS = {"research_hypothesis.add": add, "hypothesis.link": link,
            "hypothesis.support": support, "hypothesis.refute": refute,
            "hypothesis.abandon": abandon}

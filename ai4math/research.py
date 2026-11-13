"""Theory-first research design, bounded probes and honestly attributed review.

Method review authorizes exploration only. It never validates a mathematical
claim, even when the reviewer is an independent actor.
"""
from pathlib import Path

from .store import ResearchError, now

CHECKS = {"scope", "mechanism", "counterexample", "noncomputational_value"}
KINDS = {"new_representation", "new_lemma", "new_construction", "theorem_transfer"}


def texts(p, *names):
    for name in names:
        if not isinstance(p.get(name), str) or not p[name].strip():
            raise ResearchError(f"nonempty text required: {name}")


def policy(w):
    rows = w.store.all("research_policy")
    return rows[0] if rows else None


def allowed_source(w, path):
    checkpoints = w.store.all("checkpoint")
    rule = max(checkpoints, key=lambda row: row["parent_event"]) if checkpoints else policy(w)
    resolved = Path(path).resolve()
    if rule and not any(resolved.is_relative_to(Path(root)) for root in [str(w.store.root), *rule["allowed_roots"]]):
        raise ResearchError("source excluded by clean-start allowlist")


def configure(w, p, actor):
    if w.store.all("artifact") or w.store.all("task") or w.store.all("claim"):
        raise ResearchError("clean-start policy must precede any evidence, claim or task intake")
    if p.get("mode") != "theory_first" or p.get("exclude_history") is not True:
        raise ResearchError("research policy requires theory_first and exclude_history=true")
    roots = p.get("allowed_roots")
    if not isinstance(roots, list) or not roots or any(not isinstance(x, str) or not Path(x).is_absolute() for x in roots):
        raise ResearchError("allowed_roots must contain explicit absolute paths")
    maximum = p.get("max_probe_cases", 3)
    if type(maximum) is not int or not 1 <= maximum <= 8:
        raise ResearchError("max_probe_cases must be in 1..8")
    texts(p, "rationale")
    record = {"id": "main", "mode": "theory_first", "exclude_history": True,
              "allowed_roots": sorted({str(Path(x).resolve()) for x in roots}), "max_probe_cases": maximum,
              "rationale": p["rationale"], "actor": actor, "created_at": now()}
    w.store.put("research_policy", "main", record, new=True)
    return record


def add_method(w, p, actor):
    texts(p, "id", "route", "kind", "proposed_tool", "mechanism", "falsifier", "minimal_probe", "learning_goal")
    route = w.store.get("route", p["route"])
    if route["node"] not in w.cone() or p["kind"] not in KINDS:
        raise ResearchError("method must have a recognized contribution and a goal-related route")
    obligations = p.get("obligations")
    if not isinstance(obligations, list) or not obligations or any(x not in w.cone() for x in obligations):
        raise ResearchError("method needs explicit goal-related proof obligations")
    arts = p.get("artifact_ids")
    if not isinstance(arts, list) or not arts:
        raise ResearchError("method needs a frozen mathematical design argument")
    for a in arts:
        w.store.verify_blob(a)
    if p.get("parent"):
        parent = w.store.get("method", p["parent"])
        if parent["route"] != route["id"]:
            raise ResearchError("method revision must retain its route")
        texts(p, "revision_reason")
    record = {k: p[k] for k in ("id", "route", "kind", "proposed_tool", "mechanism", "falsifier", "minimal_probe", "learning_goal")}
    record.update(obligations=obligations, artifact_ids=arts, status="proposed", evidence_level="unknown",
                  author=actor, parent=p.get("parent"), revision_reason=p.get("revision_reason"), created_at=now())
    if p.get("card_ids"):
        from .exploration import verify_card
        from .knowledge import strings
        for key in strings(p, "card_ids", 1, 8):
            verify_card(w, key)
        record["card_ids"] = p["card_ids"]
    w.store.put("method", p["id"], record, new=True)
    return record


def review_method(w, p, actor):
    texts(p, "id", "method", "review_kind", "verdict", "reason")
    method = w.store.get("method", p["method"])
    if p["review_kind"] not in {"self", "independent"} or (p["review_kind"] == "independent" and actor == method["author"]):
        raise ResearchError("same author must honestly use review_kind=self")
    checks = p.get("checks")
    if not isinstance(checks, list) or len(checks) != len(CHECKS) or {c.get("check") for c in checks} != CHECKS:
        raise ResearchError("method review needs scope, mechanism, counterexample and noncomputational_value checks")
    for c in checks:
        texts(c, "detail")
        if c.get("outcome") not in {"clear", "open", "blocked"}:
            raise ResearchError("invalid method check outcome")
    if p["verdict"] not in {"revise", "trial", "park"}:
        raise ResearchError("method review can revise, trial or park; never prove")
    if p["verdict"] == "trial" and any(c["outcome"] == "blocked" for c in checks):
        raise ResearchError("blocked mathematical design cannot enter trial")
    record = {**p, "actor": actor, "same_author": actor == method["author"], "created_at": now(),
              "scope": "research_design_only", "upgrades_mathematical_evidence": False}
    w.store.put("method_review", p["id"], record, new=True)
    method.update(status=p["verdict"], latest_review=p["id"])
    w.store.put("method", method["id"], method)
    return record


def add_note(w, p, actor):
    texts(p, "id", "method", "summary", "kind")
    if p["kind"] not in {"theoretical_derivation", "tool_design", "proof_attempt", "focused_counterexample", "reflection"}:
        raise ResearchError("invalid research note kind")
    method = w.store.get("method", p["method"])
    if not p.get("artifact_ids"):
        raise ResearchError("research note needs frozen reasoning or a concrete counterexample")
    for a in p["artifact_ids"]:
        w.store.verify_blob(a)
    level = p.get("evidence_level", "unknown")
    if level not in {"unknown", "finite_support", "proved", "disproved"}:
        raise ResearchError("invalid evidence level")
    if level in {"proved", "disproved"}:
        claim = w.store.get("claim", p.get("claim", ""))
        if claim["status"] != "accepted" or claim["evidence_level"] != level or claim["node"] not in method["obligations"]:
            raise ResearchError("settled research note needs an accepted claim for its obligation")
    record = {**p, "evidence_level": level, "actor": actor, "created_at": now()}
    w.store.put("research_note", p["id"], record, new=True)
    return record


def task_fields(w, p, route):
    rule = policy(w)
    if not rule:
        return {}
    texts(p, "method", "activity", "learning_question")
    method = w.store.get("method", p["method"])
    if method["route"] != route["id"] or method["status"] != "trial":
        raise ResearchError("task needs a reviewed trial method on this route")
    allowed = {"explore": {"symbolic_analysis", "focused_probe"}, "produce": {"symbolic_analysis", "focused_probe"},
               "validate": {"validation"}, "review": {"review"}}
    if p["activity"] not in allowed[p["role"]]:
        raise ResearchError("task activity does not match its role")
    if p["activity"] == "focused_probe" and (type(p.get("cases")) is not int or not 1 <= p["cases"] <= rule["max_probe_cases"]):
        raise ResearchError("focused probe exceeds declared case budget; do mathematical design first")
    return {"method": p["method"], "method_review": method["latest_review"], "activity": p["activity"],
            "learning_question": p["learning_question"], "cases": p.get("cases")}


HANDLERS = {"research.configure": configure, "method.add": add_method, "method.review": review_method, "research.note": add_note}

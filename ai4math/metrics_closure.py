"""Closure and effort metrics (Batch 8.2 P2, frozen baseline §8.1/§8.2).

Every number here is a reading, not a verdict. A direct formal proof does not have to
fabricate an explanatory ``inference``; ``*_without_inference`` counts are metadata
hints, never a claim that the mathematics is wrong.
"""
from .inference import DERIVATION_RULES


def _nodes(w):
    return {node["id"]: node for node in w.store.all("node")}


def _depth(w):
    """Distance from the goal along proof_dependencies (goal = 0)."""
    goal = w.store.get("project", "main").get("goal")
    nodes = _nodes(w)
    depth = {goal: 0}
    frontier = [goal]
    while frontier:
        current = frontier.pop(0)
        for child in nodes.get(current, {}).get("proof_dependencies") or []:
            if child not in depth:
                depth[child] = depth[current] + 1
                frontier.append(child)
    return depth


def closure(w):
    """§8.1: direct proofs and composition derivations counted apart."""
    nodes = _nodes(w)
    parents = [node for node in nodes.values() if node.get("proof_dependencies")]
    claims = [c for c in w.store.all("claim") if c.get("status") == "accepted"]
    validations = w.store.all("validation")
    inferences = w.store.all("inference")

    def passed(claim_id):
        return [v for v in validations if v.get("claim") == claim_id and v.get("passed")]

    def trusted(claim_id):
        return [v for v in passed(claim_id) if v.get("trust_class") == "trusted"]

    def verified_inference(parent):
        return [i for i in inferences if i.get("parent") == parent
                and (i.get("verification") or {}).get("status") == "verified"]

    children_closed = 0
    for parent in parents:
        deps = parent.get("proof_dependencies") or []
        if deps and all(w.node_status(dep) == "proved" for dep in deps):
            children_closed += 1
    merge_ready, merge_succeeded, merge_attempted = [], [], []
    for parent in parents:
        deps = parent.get("proof_dependencies") or []
        kids = [c for c in claims if c["node"] in deps]
        ready = (deps and len({c["node"] for c in kids}) == len(set(deps))
                 and all(c["evidence_level"] == "proved" for c in kids)
                 and bool(verified_inference(parent["id"])))
        if ready:
            merge_ready.append(parent["id"])
        for inference in inferences:
            if inference.get("parent") != parent["id"] or inference.get("rule") != "composition":
                continue
            merge_attempted.append(inference["id"])
            if (inference.get("verification") or {}).get("status") == "verified":
                merge_succeeded.append(inference["id"])
    without_meta = [c for c in claims if c["node"] in {p["id"] for p in parents}
                    and not verified_inference(c["node"])]
    declared_with = [v for v in validations
                     if v.get("trust_class") == "declared" and v.get("passed")
                     and v.get("exact_goal_type_digest")]
    declared_without = [v for v in validations
                        if v.get("trust_class") == "declared" and v.get("passed")
                        and not v.get("exact_goal_type_digest")]
    direct_proved = [c for c in claims if c.get("proof_route") == "direct" and trusted(c["id"])]
    composition_unverified = [c for c in claims if c.get("proof_route") == "composition"
                              and c["evidence_level"] == "proved"
                              and not verified_inference(c["node"])]
    direct_without_inference = [c for c in claims if c.get("proof_route") == "direct"
                                and c["evidence_level"] == "proved"
                                and not verified_inference(c["node"])]
    dependency_only = [p for p in parents
                       if not any(v.get("claim") in {c["id"] for c in claims}
                                  for v in validations)]
    return {
        "parents": len(parents),
        "children_closed_recorded": children_closed,
        "inferences": len(inferences),
        "structured_entailment_verified": len([i for i in inferences
                                               if (i.get("verification") or {}).get("status")
                                               == "verified"]),
        "merge_ready_structured": len(merge_ready),
        "merge_attempted_structured": len(merge_attempted),
        "merge_succeeded_structured": len(merge_succeeded),
        "legacy_parent_claims_without_inference_metadata": len(without_meta),
        "declared_kernel_pass_with_targets": len(declared_with),
        "declared_kernel_pass_without_targets": len(declared_without),
        "dependency_only_unjustified": len(dependency_only),
        "direct_formal_parent_proved_structured": len(direct_proved),
        "new_composition_accept_without_verified_entailment": len(composition_unverified),
        "new_direct_formal_proof_without_explanatory_inference": len(direct_without_inference),
        "accepted_without_structured_entailment": len(without_meta),
        "nodes_merge_ready": sorted(merge_ready),
        "nodes_composition_unverified": sorted({c["node"] for c in composition_unverified}),
    }


def effort(w):
    """§8.2: where the work actually went, by distance from the goal."""
    from .scope import debt as proof_debt
    depth = _depth(w)
    routes = w.store.all("route")
    by_depth = {}
    for route in routes:
        level = depth.get(route["node"])
        if level is None:
            continue
        by_depth[level] = by_depth.get(level, 0) + 1
    tasks = w.store.all("task")
    root_side = [t for t in tasks if depth.get(t.get("node") or t.get("route"), depth.get("goal")) in (0, 1, 2)]
    # Tasks point at routes, not nodes; map through the route when needed.
    route_node = {r["id"]: r["node"] for r in routes}
    root_side = [t for t in tasks
                 if depth.get(route_node.get(t.get("route"), ""), 99) <= 2]
    open_children = [n for n in w.store.all("node") if n.get("proof_dependencies")
                     and w.node_status(n["id"]) == "open"]
    generations = w.store.all("strategy_generation")
    candidates = sum(len(g.get("candidates") or []) for g in generations)
    pending = [r for r in w.store.all("generation_request") if r.get("status") == "pending"]
    from .verification import effective
    accepted = [c for c in w.store.all("claim") if c.get("status") == "accepted"]
    effective_unverified = [c for c in accepted if effective(w, c) == "unverified"]
    return {
        "effort_by_depth": {str(level): count for level, count in sorted(by_depth.items())},
        "root_side_funding": round(len(root_side) / max(len(open_children), 1), 3),
        "debt_on_cone": proof_debt(w)["open"],
        "merge_ready_on_goal_path": len([n for n in closure(w)["nodes_merge_ready"]
                                         if n in depth]),
        "generation_runs": len(generations),
        "generated_candidates": candidates,
        "generation_pending": len(pending),
        "effective_unverified": len(effective_unverified),
    }

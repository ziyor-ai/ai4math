"""Open coordinated subagent rounds (single or batch) with the framework boilerplate."""
from __future__ import annotations

import json
import time
from pathlib import Path

from . import ledger, research, review_gate
from .codex_runner import prepare_subagent
from .codex_skills import skill_manifest
from .store import ResearchError
from .strategy import brief


def _precheck(w):
    """Refuse early with the exact blockers instead of a raw strategy error."""
    blockers = [t["id"] for t in w.store.all("task") if t.get("status") in {"running", "pending"}]
    if blockers:
        raise ResearchError("unsettled tasks block the strategy decision: " + ", ".join(sorted(blockers)[:8]) +
                            "; recover or finish them first (ai4math --workspace W apply task.recover ...)")
    orphans = [r["id"] for r in w.store.all("route")
               if r.get("status") in {"active", "proposed"} and not any(t.get("route") == r["id"] and t.get("status") == "running"
                                                                      for t in w.store.all("task"))]
    if orphans:
        raise ResearchError("open routes without a running task: " + ", ".join(sorted(orphans)[:8]) +
                            "; record a route.assess verdict before opening a new round")


def _check_direction(w, note, actor):
    """The direction note must be a real artifact from an independent reviewer."""
    if not isinstance(note, str) or not note.strip():
        raise ResearchError("open-round needs an independently imported direction note")
    try:
        artifact = w.store.get("artifact", note)
    except Exception:
        raise ResearchError("direction note must be the artifact id of an imported review")
    if artifact.get("actor") in {actor, "dsh-coordinator", "human"}:
        raise ResearchError("direction note must be imported by an actor other than the coordinator")
    if int(artifact.get("size") or 0) < 400:
        raise ResearchError("direction note artifact is too small to be a real review")


def _exists(w, kind, key):
    return w.store.exists(kind, key)


def _plan(w, name, node, question, kind, capability, ledger_sha, direction_note, card, kill_at=2,
          design=None):
    """Route, decision, literature, method, pool and candidate for one round."""
    actor = "dsh-coordinator"
    from . import boilerplate
    boilerplate.check_design(design)
    _precheck(w)
    # Batch 8.0: the research loop no longer waits for a human confirmation.
    # review.CRITIQUE_ROLE replaces it; the human checkpoint stays an optional record.
    finished = [t for t in w.store.all("task") if t.get("status") == "completed"]
    current = ledger.digest(w.store.root)
    if finished and not ledger_sha:
        raise ResearchError("open-round must cite the research ledger digest; record the last round first")
    if finished and ledger_sha != current:
        raise ResearchError("research ledger changed; cite the current digest")
    if finished:
        _check_direction(w, direction_note, actor)
    route, pool = f"{name}-route", f"{name}-pool"
    candidate, method = f"{name}-candidate", f"{name}-method"
    if _exists(w, "route", route):
        raise ResearchError("round already open: " + route)
    w.apply("route.add", {"id": route, "node": node, "rationale": "coordinated subagent round",
                          "next_question": question, "stop_condition": "one sealed probe",
                          "max_runs": 2, "stagnation_limit": 2, "kill_at": kill_at}, actor)
    w.apply("route.status", {"id": route, "status": "active", "reason": "open round"}, actor)
    w.apply("round.open", {"id": name, "route": route}, actor)
    if review_gate.halted(w) or review_gate.due(w):
        review_gate.review(w, {"decision": "switch", "reason": "open round " + str(time.time()),
                               "next_question": question}, "independent-reviewer")
    snapshot = brief(w)
    decision = f"{name}-decision"
    choice = "continue" if snapshot["current_route"] == route else "switch"
    w.apply("strategy.decide", {"id": decision, "brief_sha256": snapshot["brief_sha256"],
                                "decision": choice, "next_route": route,
                                "reason": "coordinated subagent round", "next_question": question}, actor)
    stamp = str(time.time())
    for suffix, purpose, op in (("search", "literature search", "literature.search"),
                                ("plan", "literature plan", "literature.plan")):
        path = w.store.root / f"{name}-{suffix}.md"
        path.write_text(f"{purpose}: {question} {stamp}", encoding="utf-8")
        artifact = w.apply("artifact.import", {"path": str(path), "purpose": purpose}, actor)["id"]
        if op == "literature.search":
            w.apply(op, {"decision": decision, "route": route, "queries": [question],
                         "search_artifact": artifact, "sources": [],
                         "limitations": "bounded coordinator search"}, actor)
        else:
            w.apply(op, {"decision": decision, "assessment": "Design the load-bearing probe.",
                         "plan_artifact": artifact, "routes": {route: question}}, actor)
    design_path = w.store.root / f"{name}-method.md"
    design_path.write_text("\n".join([
        "# Method design", "", "Target node: " + node, "Question: " + question, "",
        "Proposed tool: " + design["proposed_tool"],
        "Mechanism: " + design["mechanism"],
        "Falsifier: " + design["falsifier"],
        "Minimal probe: " + question,
        "Learning goal: " + question, "",
        "Representation: " + design["representation"],
        "Bridge: " + design["bridge"],
        "Plan: " + design["plan"],
        "Assumptions: " + str(design.get("assumptions") or "stated by the coordinator"),
        "Scope: finite; evidence level stays unknown unless an independent validator closes it.",
        "Opened: " + stamp]), encoding="utf-8")
    design_id = w.apply("artifact.import", {"path": str(design_path),
                                           "purpose": "method design"}, actor)["id"]
    w.apply("method.add", {"id": method, "route": route, "kind": kind,
                           "proposed_tool": design["proposed_tool"],
                           "mechanism": design["mechanism"],
                           "falsifier": design["falsifier"],
                           "minimal_probe": question, "learning_goal": question,
                           "obligations": [node], "artifact_ids": [design_id]}, actor)
    checks = [{"check": n, "detail": "design only", "outcome": "clear"} for n in sorted(research.CHECKS)]
    w.apply("method.review", {"id": method + "-review", "method": method, "review_kind": "self",
                              "verdict": "trial", "reason": "design only", "checks": checks}, actor)
    w.apply("exploration.open", {"id": pool, "node": node, "question": question,
                                 "max_candidates": 2, "max_tasks": 2, "max_probes": 1,
                                 "card_ids": [card], "metric_directions": {"quality": "max"}}, actor)
    candidate_path = w.store.root / f"{name}-candidate.md"
    candidate_path.write_text("\n".join([
        "# Candidate design", "", "Target node: " + node, "Question: " + question, "",
        "Family: " + str(design.get("family") or "unstated"),
        "Representation: " + design["representation"],
        "Bridge: " + design["bridge"],
        "Falsifier: " + design["falsifier"],
        "Plan: " + design["plan"],
        "Mechanism: " + design["mechanism"],
        "Assumptions: " + str(design.get("assumptions") or "stated by the coordinator"),
        "Admissibility: the candidate is a design proposal, not a proof; an admissible",
        "counter-construction refutes it.",
        "Review: an independent reviewer must check scope, mechanism, counterexample and",
        "non-computational value before the design enters a trial.",
        "Opened: " + stamp]), encoding="utf-8")
    candidate_id = w.apply("artifact.import", {"path": str(candidate_path),
                                               "purpose": "candidate design"}, actor)["id"]
    mechanism_key = design.get("mechanism_key") or f"{name}-mechanism"
    w.apply("exploration.propose", {"id": candidate, "pool": pool, "capability": capability,
                                    "mechanism_key": mechanism_key,
                                    "representation": design["representation"],
                                    "bridge": design["bridge"],
                                    "falsifier": design["falsifier"],
                                    "plan": design["plan"],
                                    "minimal_probe": question,
                                    "assumptions": list(design.get("assumptions") or ["stated by the coordinator"]),
                                    "card_ids": [card], "artifact_ids": [candidate_id]}, actor)
    return {"name": name, "route": route, "decision": decision, "method": method,
            "pool": pool, "candidate": candidate, "node": node, "question": question}


def card_of(w):
    rows = w.store.all("knowledge_card")
    if not rows:
        raise ResearchError("the workspace has no attached mechanism card; attach one first")
    return rows[0]["id"]


def _start(w, spec, library, allow_thin, toolchain=None):
    actor = "dsh-coordinator"
    task = f"{spec['name']}-task"
    candidate = w.store.get("exploration_candidate", spec["candidate"])
    manifest = w.store.root / f"{spec['name']}-skill-manifest.json"
    manifest.write_text(json.dumps(skill_manifest(check_installed=False), sort_keys=True), encoding="utf-8")
    skill = w.apply("artifact.import", {"path": str(manifest), "purpose": "frozen skill manifest"}, actor)["id"]
    from .context import bundle as build_context
    context_path = w.store.root / f"{spec['name']}-context.md"
    context_path.write_text(build_context(w, spec["node"]), encoding="utf-8")
    context_id = w.apply("artifact.import", {"path": str(context_path),
                                             "purpose": "research context bundle"}, actor)["id"]
    extra = [toolchain] if toolchain else []
    inputs = sorted(set([skill, context_id] + list(candidate["artifact_ids"]) + extra))
    if not allow_thin:
        from .inputs import MIN_BYTES
        real = [(key, w.store.get("artifact", key), w.store.verify_blob(key).stat().st_size) for key in inputs]
        context = [row for row in real if row[1].get("purpose") == "research context bundle" and row[2] >= MIN_BYTES]
        if not context:
            thin = [row for row in real if row[1].get("purpose") not in {"frozen skill manifest"}
                    and not row[1].get("purpose", "").endswith("design")]
            raise ResearchError("no real research context input (a context bundle of at least " + str(MIN_BYTES) +
                                " bytes); instantiate the rows or pass --allow-thin-inputs: " +
                                ", ".join(f"{k[:12]}({n}B)" for k, _, n in thin))
    w.apply("task.add", {"id": task, "route": spec["route"], "role": "explore", "round": spec["name"], "argv": ["python", "-c", "pass"],
                         "timeout_seconds": None, "output_limit_bytes": None, "inputs": inputs,
                         "external_supervision": "process", "worker_driver": "subagent",
                         "method": spec["method"], "method_review": spec["method"] + "-review",
                         "activity": "symbolic_analysis", "learning_question": spec["question"],
                         "exploration_candidate": spec["candidate"],
                         "codex_workflow": {"skill_manifest_artifact": skill, "worker_actor": "dsh-subagent",
                         "source_roots": [str(Path(library).resolve())], **({"toolchain_artifact": toolchain} if toolchain else {}),
                                            "replicates": int(spec.get("replicates") or 1)}}, actor)
    if allow_thin:
        with w.store.transaction("task.thin_inputs_ack", actor):
            row = w.store.get("task", task)
            row["thin_inputs_ack"] = {"reason": "engineering demo", "by": actor}
            w.store.put("task", task, row)
    prepare_subagent(w, task, actor, library)
    return task


def open_round(w, name, node, library, question, kind="new_lemma", capability="bridge_lemma",
               ledger_sha="", direction_note=None, allow_thin=False, card=None, kill_at=2, replicates=1,
               toolchain=None, design=None):
    card = card or __import__("os").environ.get("AI4MATH_DEFAULT_CARD")
    if not card:
        raise ResearchError("a mechanism card id is required (--card or AI4MATH_DEFAULT_CARD)")
    from . import boilerplate
    boilerplate.check_design(design)
    spec = _plan(w, name, node, question, kind, capability, ledger_sha, direction_note, card,
                 kill_at, design)
    spec["replicates"] = replicates
    task = _start(w, spec, library, allow_thin, toolchain)
    return {**spec, "task": task}


def open_batch(w, specs, library, ledger_sha="", direction_note=None, allow_thin=False, card=None, kill_at=2):
    """Plan one reviewed focus set over distinct obligations, then start the tasks."""
    import os
    card = card or os.environ.get("AI4MATH_DEFAULT_CARD")
    if not isinstance(specs, list) or not 1 <= len(specs) <= 4:
        raise ResearchError("batch needs 1..4 round specifications")
    from . import boilerplate
    for spec in specs:
        boilerplate.check_design(spec.get("design"))
    boilerplate.check_families(specs)
    actor = "dsh-coordinator"
    _precheck(w)
    # Batch 8.0: the research loop no longer waits for a human confirmation.
    # review.CRITIQUE_ROLE replaces it; the human checkpoint stays an optional record.
    finished = [t for t in w.store.all("task") if t.get("status") == "completed"]
    current = ledger.digest(w.store.root)
    if finished and (not ledger_sha or ledger_sha != current):
        raise ResearchError("open-batch must cite the current research ledger digest")
    if finished:
        _check_direction(w, direction_note, actor)
    effective = [spec.get("card") or card for spec in specs]
    if any(not key for key in effective):
        raise ResearchError("each round needs a mechanism card id")
    if len(specs) > 1 and len(set(effective)) < 2:
        raise ResearchError("parallel rounds need at least two structurally distinct mechanism cards")
    routes = []
    for spec in specs:
        route = f"{spec['name']}-route"
        if w.store.exists("route", route):
            raise ResearchError("round already open: " + route)
        w.apply("route.add", {"id": route, "node": spec["node"], "rationale": "coordinated batch round",
                              "next_question": spec["question"], "stop_condition": "one sealed probe",
                              "max_runs": 2, "stagnation_limit": 2, "kill_at": kill_at}, actor)
        w.apply("route.status", {"id": route, "status": "active", "reason": "open batch round"}, actor)
        w.apply("round.open", {"id": spec["name"], "route": route}, actor)
        routes.append(route)
    if review_gate.halted(w) or review_gate.due(w):
        review_gate.review(w, {"decision": "switch", "reason": "batch " + str(time.time()),
                               "next_question": specs[0]["question"]}, "independent-reviewer")
    snapshot = brief(w)
    decision = f"{specs[0]['name']}-decision"
    choice = "continue" if snapshot["current_route"] == routes[0] else "switch"
    w.apply("strategy.decide", {"id": decision, "brief_sha256": snapshot["brief_sha256"],
                                "decision": choice, "next_route": routes[0],
                                "parallel_routes": routes[1:],
                                "reason": "coordinated batch", "next_question": specs[0]["question"]}, actor)
    planned = []
    for spec, route in zip(specs, routes):
        name, question = spec["name"], spec["question"]
        stamp = str(time.time())
        path = w.store.root / f"{name}-search.md"
        path.write_text(f"literature search: {question} {stamp}", encoding="utf-8")
        artifact = w.apply("artifact.import", {"path": str(path), "purpose": "literature search"}, actor)["id"]
        w.apply("literature.search", {"decision": decision, "route": route, "queries": [question],
                                      "search_artifact": artifact, "sources": [],
                                      "limitations": "bounded coordinator search"}, actor)
        design = w.store.root / f"{name}-method.md"
        fields = spec["design"]
        design.write_text("\n".join([
            "# Method design", "", "Target node: " + spec["node"], "Question: " + question, "",
            "Proposed tool: " + fields["proposed_tool"],
            "Mechanism: " + fields["mechanism"],
            "Falsifier: " + fields["falsifier"],
            "Representation: " + fields["representation"],
            "Bridge: " + fields["bridge"],
            "Plan: " + fields["plan"],
            "Family: " + spec["family"],
            "Opened: " + stamp]), encoding="utf-8")
        design_id = w.apply("artifact.import", {"path": str(design), "purpose": "method design"}, actor)["id"]
        method = f"{name}-method"
        w.apply("method.add", {"id": method, "route": route, "kind": spec.get("kind", "new_lemma"),
                               "proposed_tool": fields["proposed_tool"],
                               "mechanism": fields["mechanism"],
                               "falsifier": fields["falsifier"],
                               "minimal_probe": question, "learning_goal": question,
                               "obligations": [spec["node"]], "artifact_ids": [design_id]}, actor)
        checks = [{"check": n, "detail": "design only", "outcome": "clear"} for n in sorted(research.CHECKS)]
        w.apply("method.review", {"id": method + "-review", "method": method, "review_kind": "independent",
                                  "verdict": "trial", "reason": "design only", "checks": checks}, "method-reviewer")
        pool, candidate = f"{name}-pool", f"{name}-candidate"
        w.apply("exploration.open", {"id": pool, "node": spec["node"], "question": question,
                                     "max_candidates": 2, "max_tasks": 2, "max_probes": 1,
                                     "card_ids": [spec.get("card") or card],
                                     "metric_directions": {"quality": "max"}}, actor)
        candidate_design = w.store.root / f"{name}-candidate.md"
        candidate_design.write_text("\n".join([
            "# Candidate design", "", "Target node: " + spec["node"], "Question: " + question, "",
            "Family: " + spec["family"],
            "Representation: " + fields["representation"],
            "Bridge: " + fields["bridge"],
            "Falsifier: " + fields["falsifier"],
            "Plan: " + fields["plan"],
            "Mechanism: " + fields["mechanism"],
            "Opened: " + stamp]), encoding="utf-8")
        candidate_id = w.apply("artifact.import", {"path": str(candidate_design),
                                                   "purpose": "candidate design"}, actor)["id"]
        w.apply("exploration.propose", {"id": candidate, "pool": pool, "capability": "bridge_lemma",
                                        "mechanism_key": fields.get("mechanism_key") or f"{name}-mechanism",
                                        "representation": fields["representation"],
                                        "bridge": fields["bridge"],
                                        "falsifier": fields["falsifier"],
                                        "plan": fields["plan"],
                                        "minimal_probe": question,
                                        "assumptions": list(fields.get("assumptions") or ["stated by the coordinator"]),
                                        "card_ids": [spec.get("card") or card],
                                        "artifact_ids": [candidate_id]}, actor)
        planned.append({"name": name, "route": route, "decision": decision, "method": method,
                        "pool": pool, "candidate": candidate, "node": spec["node"], "question": question})
    plan_art = w.store.root / f"{specs[0]['name']}-plan.md"
    plan_art.write_text("unified plan for " + ", ".join(routes), encoding="utf-8")
    plan_id = w.apply("artifact.import", {"path": str(plan_art), "purpose": "literature plan"}, actor)["id"]
    w.apply("literature.plan", {"decision": decision, "assessment": "Design the load-bearing probe.",
                                "plan_artifact": plan_id,
                                "routes": {spec["route"]: spec["question"] for spec in planned}}, actor)
    tasks = [_start(w, spec, library, allow_thin) for spec in planned]
    return {"rounds": planned, "tasks": tasks}

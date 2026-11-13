"""Read-only historical intake. No solver, graph adapter or target validator imports."""
from __future__ import annotations

import json
from pathlib import Path

from .config import control_root, load_project
from .gate import check_phase
from .store import ResearchError, file_hash, now


def tree_manifest(root):
    root = Path(root).resolve()
    if not root.is_dir():
        raise ResearchError(f"missing original imports directory: {root}")
    files = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ResearchError(f"original import symlink requires explicit handling: {path}")
        if path.is_file():
            files[path.relative_to(root).as_posix()] = {"size": path.stat().st_size, "sha256": file_hash(path)}
    return {"root": str(root), "files": files, "file_count": len(files)}


def import_coprime(workbench, config, actor="human"):
    """One atomic intake of phase 248..251, conservatively retaining finite_support."""
    config = Path(config).resolve()
    project = load_project(config)
    target = Path(project["root"]).resolve()
    store = workbench.store
    if store.root.is_relative_to(target) or target.is_relative_to(store.root):
        raise ResearchError("framework workspace and historical target must be separate trees")
    if project["project_id"] != "coprime-path4":
        raise ResearchError("this adapter is specific to coprime-path4")
    if store.get("project", "main")["goal"] is not None or store.all("node"):
        raise ResearchError("intake requires an empty initialized workspace")
    before = tree_manifest(project["original_root"])
    gate = check_phase(config, 251).to_dict()
    if not gate["passed"]:
        raise ResearchError("historical phase251 gate failed; inspect it before intake")
    intake = {"created_at": now(), "source_project": str(target), "original_imports_before": before,
              "legacy_phase251_gate": gate, "evidence_level": "finite_support",
              "scope": "historical finite artifacts; no all-n/all-k proof; no new phase authorized"}
    frozen = []
    with store.transaction("adapter.coprime.intake", actor):
        focused = workbench._node({"id": "all-k", "statement": "For every integer k >= 1, the minimum coprime labeling number of P_(10k)^4 is 18k-1.",
            "domain": "Distinct positive integer vertex labels; gcd(a_i,a_j)=1 whenever 0<|i-j|<=4; k is any positive integer.",
            "quantifier": "universal"}, actor)
        workbench._node({"id": "remaining-orders", "statement": "For every integer n >= 8 with n not divisible by 10, f(n)=3t-1-(t mod 2), where n=5q+r, 0<=r<=4 and t=3q+max(0,r-2).",
            "domain": "f(n) is the minimum maximum of distinct positive labels of P_n^4 with gcd 1 at every distance 1..4; integers n>=8 not divisible by 10.",
            "quantifier": "universal"}, actor)
        goal = workbench._node({"id": "all-n", "statement": "For every integer n >= 8, f(n)=L23(n)=3t-1-(t mod 2), where n=5q+r, 0<=r<=4 and t=3q+max(0,r-2).",
            "domain": "f(n) is the minimum maximum of distinct positive integer labels of P_n^4 with gcd 1 whenever 0<|i-j|<=4; n is any integer >=8. Orders 1..7 are separate finite base cases, outside this formula.",
            "quantifier": "universal", "proof_dependencies": ["all-k", "remaining-orders"]}, actor)
        workbench._goal({"node": goal["id"]}, actor)
        # Historical records bind their finite intake scope, not a newly proved theorem.
        for phase in (248, 249, 250, 251):
            candidates = sorted(control_root(project).glob("claims/*.json"))
            originals = []
            for candidate in candidates:
                body = json.loads(candidate.read_text(encoding="utf-8"))
                if body.get("phase") == phase:
                    originals.append(candidate)
            if not originals:
                raise ResearchError(f"no registered historical claim for phase {phase}")
            arts = [workbench._artifact({"path": str(p), "purpose": f"unaltered legacy claim Phase{phase}"}, actor)["id"] for p in originals]
            frozen.extend(arts)
            claim = workbench._claim({"id": f"historical-phase{phase}", "node": "all-k", "statement_digest": focused["statement_digest"],
                "evidence_level": "finite_support", "scope": "finite", "artifact_ids": arts,
                "evidence_domain": f"Finite record domains in the attached original Phase{phase} claims; no universal scope inferred during intake.",
                "summary": f"Phase{phase} historical finite computations and audit; retained as finite_support; not re-proved during intake."}, "historical-import")
            review = workbench._review({"id": f"intake-review-{phase}", "claim": claim["id"], "reviewer": "legacy-scope-reader",
                "reviewer_kind": "automation", "auditor": "intake-scope-audit", "coverage_complete": True,
                "issues": [{"description": "Historical finite evidence does not establish the all-k theorem.",
                            "audit": "confirmed", "severity": "minor"}], "verdict": "ready_for_human",
                "purpose": "automated migration of labels and scope only; no mathematical proof review"}, "intake-scope-audit")
            workbench._accept({"claim": claim["id"], "review": review["id"]}, "migration-custodian")
        for relative in ("docs/literature/ai_math_methodology_2026-10-28.md", "docs/literature/open_source_math_repositories_2026-10-28.md",
                         "docs/literature/review_2026-10-28.md", "notes/phase3_lower_bound_proof.md",
                         "notes/phase1_251_retrospective.md", "notes/ai4math_review.md"):
            path = target / relative
            if path.is_file():
                frozen.append(workbench._artifact({"path": str(path), "purpose": "research method and historical limitations"}, actor)["id"])
        frozen.append(workbench._artifact({"path": str(config), "purpose": "read-only source project configuration"}, actor)["id"])
        after = tree_manifest(project["original_root"])
        if before != after:
            raise ResearchError("original imports changed during intake")
        intake.update(original_imports_after=after, original_bytes_unchanged=True, frozen_artifacts=frozen)
        intake_file = store.inside("intake/coprime-path4.json")
        intake_file.parent.mkdir(parents=True, exist_ok=True)
        with intake_file.open("x", encoding="utf-8") as f:
            json.dump(intake, f, ensure_ascii=False, indent=2)
            f.write("\n")
        manifest = workbench._artifact({"path": str(intake_file), "purpose": "independent intake byte audit and legacy gate receipt"}, actor)
        for route_id, rationale, question, stop in (
            ("proof-obligations", "Extract sufficient universal lemmas from the finite repair record.",
             "What invariant plus lifting lemma would imply every k, and what is still unproved?",
             "Park if two reviews only repair finite instances without a new proof obligation."),
            ("new-construction", "Preserve a route outside the previously refuted signature interfaces.",
             "Can a different parameterized construction admit a uniform coprimality argument?",
             "Reject candidates whose verifier or all-k interface is not independently specified.")):
            workbench._route({"id": route_id, "node": "all-k", "rationale": rationale, "next_question": question,
                              "stop_condition": stop, "max_runs": 4, "stagnation_limit": 2}, actor)
        workbench._route({"id": "remaining-orders", "node": "remaining-orders", "rationale": "Keep the original all-n target distinct from its 10k subproblem.",
            "next_question": "Which residue classes need additional constructions after the all-k subproblem?",
            "stop_condition": "Do not claim all-n closure from the 10k subproblem alone.", "max_runs": 4, "stagnation_limit": 2}, actor)
        return {"goal": "all-n", "goal_status": "unknown", "historical_claims": 4, "original_file_count": before["file_count"],
                "original_bytes_unchanged": True, "intake_artifact": manifest["id"], "routes_status": "proposed",
                "phase252_created": False, "legacy_gate_warnings": gate["warnings"]}

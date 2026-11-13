"""Run the bounded public School Dance development exercise in a new directory."""
import argparse
import itertools
import json
import sys
from pathlib import Path

from ai4math import __version__
from ai4math.execution import run_task
from ai4math.exploration import board
from ai4math.knowledge import AcademicLibrary
from ai4math.report import markdown
from ai4math.strategy import brief
from ai4math.workbench import Workbench


PROBLEM = "https://cses.fi/problemset/task/1696/"
SOURCE = "https://web.cs.dal.ca/~nzeh/Teaching/4113/book/matching/bipartite_maximum_matching/augmenting_paths.html"
EXCERPT = "An augmenting path is an alternating path both of whose endpoints are unmatched."
FINITE_STATEMENT = "The bound matching table covers the frozen corpus and contains a maximum matching for each graph."
CHECKER_TRUST = "Separate Python checker exhausts edge subsets and independently enumerates the finite corpus. One development session authored the example and checker."


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")


def build_corpus():
    cases = []
    for n in range(1, 4):
        for m in range(1, 5 - n):
            edges = list(itertools.product(range(1, n + 1), range(1, m + 1)))
            for mask in range(1, 1 << len(edges)):
                cases.append({"id": f"g-{n}-{m}-{mask:02x}", "n": n, "m": m,
                              "edges": [edge for i, edge in enumerate(edges) if mask & (1 << i)]})
    cases.append({"id": "cses-sample", "n": 3, "m": 2, "edges": [[1, 1], [1, 2], [2, 1], [3, 1]]})
    return {"problem": PROBLEM, "domain": "All nonempty labeled simple bipartite graphs with n+m<=4, plus the public sample", "cases": cases}


def prepare_library(root, lecture):
    lib = AcademicLibrary.create(root / "library")
    try:
        src = lib.add_source({"title": "Algorithms II, Augmenting Paths", "url": SOURCE, "kind": "researcher_guidance",
            "version": "Frozen HTML snapshot", "locator": "§7.4.1, definition and Corollary 7.9",
            "summary": "Reassign along an alternating path to increase matching size by one.",
            "limitations": "Public course notes. Transfer to an implementation needs separate checking.",
            "tags": ["bipartite matching", "augmenting path"]})
        lib.add_card({"name": "Augmenting path instead of irrevocable choices", "source_ids": [src["id"]],
            "capabilities": ["construction"], "bottleneck": "Irrevocable greedy choices can block a larger matching.",
            "operator": "Find an augmenting path and flip the alternating edges.",
            "bridge": "Each allowed pair is a bipartite edge and each valid set of pairs is a matching.",
            "prediction": "Reassignment can increase matching size on a minimal greedy obstruction.",
            "limitations": "The exercise checks a finite corpus only.", "tags": ["augmenting path", "bipartite matching"],
            "conditions": ["A finite bipartite graph and a feasible matching."],
            "failure_modes": ["Confusing a maximal matching with a maximum matching."]})
        doc = lib.add_document(src["id"], lecture, "html")
        hit, = lib.search("augmenting path bipartite matching", capability="construction", limit=1)
        return lib.store.root, hit["id"], doc["document"], lib.audit()
    finally:
        lib.store.close()


def run(root, lecture):
    root = Path(root).resolve()
    root.mkdir()
    library, card, document, library_audit = prepare_library(root, lecture)
    w = Workbench.create(root / "workspace", "school-dance-development", "School Dance public development exercise")
    try:
        def import_artifact(path, purpose):
            return w.apply("artifact.import", {"path": str(path), "purpose": purpose})["id"]

        corpus = build_corpus()
        corpus_path = root / "corpus.json"
        write_json(corpus_path, corpus)
        benchmark = import_artifact(corpus_path, "Complete small graph corpus and public example")
        producer = import_artifact(Path(__file__).with_name("school_dance_producer.py"), "Two frozen candidate constructors")
        validator = import_artifact(Path(__file__).with_name("school_dance_validator.py"), "Independent exhaustive subset checker")
        obligation = w.apply("node.add", {"id": "corpus-optimality", "statement": FINITE_STATEMENT,
            "domain": f"Frozen corpus SHA-256 {benchmark}.", "quantifier": "finite"})
        w.apply("node.add", {"id": "school-dance", "statement": "Produce a maximum valid pairing for every legal School Dance input.",
            "domain": "CSES 1696. 1 <= n,m <= 500 and 1 <= k <= 1000 allowed pairs.", "quantifier": "universal",
            "proof_dependencies": [obligation["id"]]})
        w.apply("goal.set", {"node": "school-dance"})
        w.apply("knowledge.attach", {"library": str(library), "card_ids": [card], "document_ids": [document],
                                    "reason": "Use the selected public augmenting-path passage"})
        protocol = {"validator_artifact": validator, "benchmark_artifacts": [benchmark],
                    "metrics": {"quality": {"definition": "Count of checked optimal matchings on the frozen corpus", "unit": "graphs"}}}
        pool = w.apply("exploration.open", {"id": "school-dance-pool", "node": "school-dance",
            "question": "Can reassignment repair irrevocable greedy choices?", "card_ids": [card], "document_ids": [document],
            "max_candidates": 2, "max_tasks": 4, "max_probes": 1, "evaluation_protocol": protocol})
        transfer = w.apply("exploration.transfer", {"id": "transfer-augmenting-path", "pool": pool["id"],
            "card": card, "document": document, "obligation": obligation["id"], "excerpt": EXCERPT,
            "locator": "§7.4.1, definition of augmenting path. Corollary 7.9 provides the optimality bridge.",
            "conditions": ["The current pairs form a matching.", "The path alternates and has two unmatched endpoints."],
            "mapping": "Students are vertices, allowed pairs are edges, reassignment flips path edges.",
            "gap": "Validate maximum cardinality for each case in the frozen development corpus."})
        for route, node, limit in [("compare-constructors", "school-dance", 4), ("certify-corpus", obligation["id"], 1)]:
            w.apply("route.add", {"id": route, "node": node, "rationale": "Bounded public development check",
                "next_question": "Check the locked finite obligation", "stop_condition": "Stop when the fixed task sequence finishes",
                "max_runs": limit, "stagnation_limit": 1})
            w.apply("route.status", {"id": route, "status": "active", "reason": "Public development exercise"})

        def propose(key, hypothesis, plan, **revision):
            return w.apply("exploration.propose", {"id": key, "pool": pool["id"], "capability": "construction",
                "mechanism_key": key, "representation": "Finite bipartite matching", "transfer": transfer["id"],
                "hypothesis": hypothesis, "bridge": "A larger feasible matching falsifies optimality of the candidate output.",
                "falsifier": "A corpus graph with a larger valid matching", "plan": plan,
                "minimal_probe": "Compare by number of vertices and then number of edges",
                "assumptions": ["Nonempty simple bipartite graphs in the frozen benchmark"], "card_ids": [card],
                "artifact_ids": [benchmark], **revision}, "development-designer")

        def execute(key, route, inputs, candidate=None, validate=False):
            args = [sys.executable, "{validator}" if validate else str(w.store.blob(producer)), "{input}"]
            task = {"id": key, "route": route, "role": "validate" if validate else "produce", "argv": args,
                    "timeout_seconds": 8, "output_limit_bytes": 32000, "inputs": inputs}
            if candidate:
                task["exploration_candidate"] = candidate
            if validate:
                task["validator_artifact"] = validator
            w.apply("task.add", task, "independent-checker-program" if validate else "producer-program")
            out = run_task(w, key, "development-runner")
            if out["status"] != "completed" or not out.get("result_artifact"):
                raise RuntimeError(f"{key} did not yield a result: {out['status']}")
            return out

        def assess(candidate):
            produced = execute(f"produce-{candidate}", "compare-constructors", [benchmark], candidate)
            audited = execute(f"audit-{candidate}", "compare-constructors", [benchmark, produced["result_artifact"]], candidate, True)
            return w.apply("exploration.evaluate", {"id": f"assessment-{candidate}", "candidate": candidate,
                "task": produced["id"], "audit_task": audited["id"], "trust_basis": CHECKER_TRUST})

        propose("lex-greedy", "Lexicographic edge-greedy is maximum on every corpus graph.", "Commit each feasible pair irrevocably.")
        rejected = assess("lex-greedy")
        witness = w.apply("exploration.counterexample", {"id": "minimal-greedy-obstruction", "evaluation": rejected["id"]})
        w.apply("strategy.decide", {"id": "review-greedy-obstruction", "brief_sha256": brief(w)["brief_sha256"],
            "decision": "continue", "next_route": "compare-constructors", "next_question": "Retest with reversible choices",
            "reason": "The verified witness defeats the greedy candidate. The existing comparison route can test the augmenting-path repair."},
            "development-coordinator")
        propose("augmenting-path", "Reassignment via augmenting paths yields a maximum matching on the corpus.",
                "Use alternating reachability to reassign existing pairs.", parents=["lex-greedy"],
                parent_reason="The two-by-two obstruction requires reassigning the first committed pair.",
                counterexample=witness["id"], revision_change="Replace irrevocable choices with alternating-path flips.")
        repaired = assess("augmenting-path")
        if not repaired["valid"]:
            raise RuntimeError("revised candidate failed the frozen evaluation")

        inputs = [benchmark, repaired["producer_result"]]
        w.apply("strategy.decide", {"id": "review-comparison-budget", "brief_sha256": brief(w)["brief_sha256"],
            "decision": "switch", "next_route": "certify-corpus", "next_question": "Certify the finite corpus obligation",
            "reason": "The comparison budget is exhausted. The repaired output is ready for the separate finite proof check."},
            "development-coordinator")
        claim = w.apply("claim.add", {"id": "proved-frozen-corpus", "node": obligation["id"],
            "statement_digest": obligation["statement_digest"], "evidence_level": "proved", "scope": "finite",
            "summary": "The revised output contains a maximum matching for every frozen development graph.",
            "artifact_ids": inputs}, "development-claim-author")
        cert = execute("certify-revised-corpus", "certify-corpus", inputs, validate=True)
        validation = w.apply("validation.add", {"id": "corpus-exhaustive-validation", "claim": claim["id"],
            "method": "exhaustive_search", "task": cert["id"], "trust_basis": CHECKER_TRUST}, "checker-receipt-registrar")
        audit = w.audit()
        if not audit["passed"] or not validation["passed"]:
            raise RuntimeError("automated coverage review failed")
        review = w.apply("review.add", {"id": "corpus-coverage-review", "claim": claim["id"],
            "reviewer": "coverage-automation", "reviewer_kind": "automation", "auditor": "audit-automation",
            "coverage_complete": True, "issues": [], "verdict": "ready_for_human"}, "audit-automation")
        # Role labels identify the procedural steps. The proof basis is the executed checker.
        accepted = w.apply("claim.accept", {"claim": claim["id"], "review": review["id"],
                                            "validation": validation["id"]}, "development-custodian")
        resolution = w.apply("exploration.resolve", {"id": "school-dance-chain", "evaluation": repaired["id"], "claim": accepted["id"]})
        w.apply("ledger.add", {"id": "finite-corpus-result", "node": obligation["id"], "claim": accepted["id"],
            "use": "theorem", "text": "The revised matching table is optimal on the explicitly frozen finite development corpus."})
        portfolio = board(w, pool["id"])
        work = w.board()
        result = {"version": __version__, "problem": PROBLEM, "source": SOURCE, "engineering_evidence_level": "finite_support",
            "library_audit": library_audit, "workspace_audit": w.audit(), "corpus_size": len(corpus["cases"]),
            "protocol": pool["evaluation_protocol"], "transfer": transfer, "minimal_counterexample": witness,
            "assessments": [rejected, repaired], "resolution": resolution, "accepted_claim": accepted,
            "frontier": [x["candidate"]["id"] for x in portfolio["evaluated_frontier"]],
            "task_slots": portfolio["pool"]["tasks_reserved"], "processes": sum(t["status"] == "completed" for t in work["tasks"]),
            "goal_evidence_level": work["goal_evidence_level"], "delivery": w.delivery_check(),
            "model_calls": 0, "strict_study_admitted": False, "research_effectiveness": "unknown"}
        write_json(root / "acceptance.json", result)
        write_json(root / "portfolio.json", portfolio)
        (root / "report.md").write_text(markdown(w), encoding="utf-8")
        return result
    finally:
        w.store.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("lecture", type=Path)
    args = parser.parse_args()
    print(json.dumps(run(args.root, args.lecture), ensure_ascii=False, indent=2))

"""Offline demonstration of four research diagnoses on a public hyperfield problem."""

import argparse
import json
from pathlib import Path
import sys

from ai4math.diagnosis import report
from ai4math.execution import run_task
from ai4math.knowledge import AcademicLibrary
from ai4math.workbench import Workbench


TARGET = "Does every null triple of nonzero hyperfield elements have a null six-term cubic sum?"
ORIGINAL = "Every null triple of nonzero elements in a hyperfield has a common quadratic witness."
REVISED = "Every such null triple has a common quadratic witness when at least one quadratic sum is a singleton."
PAIRWISE = "In the given five-element hyperfield all null triples of units have pairwise intersecting quadratic sums."
SOURCE_NOTE = """Singleton intersection lemma
Let A, B and C be pairwise intersecting sets. If A is a singleton {q}, then q belongs to B and C, so the total intersection contains q. The argument is symmetric in the three sets.
"""


def run(root):
    root = Path(root).resolve()
    root.mkdir(parents=True)
    materials = root / "materials"
    materials.mkdir()
    source_path = materials / "singleton-lemma.txt"
    source_path.write_text(SOURCE_NOTE, encoding="utf-8")
    lib = AcademicLibrary.create(root / "academic")
    try:
        source = lib.add_source({"title": "AI4Math demonstration note on singleton intersections",
            "url": "https://example.org/ai4math-demo/singleton-lemma",
            "version": "offline-demo-v1", "locator": "Singleton intersection lemma",
            "kind": "researcher_guidance", "summary": "A singleton turns pairwise overlap into total overlap.",
            "limitations": "Original local demonstration note. The URL is an illustrative identifier.",
            "tags": ["demo", "intersection", "hyperfield"]})
        doc = lib.add_document(source["id"], source_path)
        card = lib.add_card({"name": "Singleton intersection", "bottleneck": "Pairwise versus total overlap",
            "operator": "Identify a singleton in the three sets", "bridge": "Its element belongs to both other sets",
            "prediction": "The singleton hypothesis is essential for this set-theoretic argument",
            "limitations": "The hypothesis must be proved in the current domain", "capabilities": ["bridge_lemma"],
            "source_ids": [source["id"]], "conditions": ["pairwise intersection", "a singleton"],
            "failure_modes": ["three overlapping two-element sets with empty total intersection"], "tags": ["demo"]})
    finally:
        lib.store.close()

    w = Workbench.create(root / "workspace", "hyperfield-diagnosis", "Hyperfield research diagnosis")
    try:
        def apply(op, data, actor="demo-coordinator"):
            return w.apply(op, data, actor)

        def node(key, statement, domain, *deps, quantifier="universal"):
            return apply("node.add", {"id": key, "statement": statement, "domain": domain,
                                      "quantifier": quantifier, "proof_dependencies": list(deps)})

        def freeze(path, purpose):
            return apply("artifact.import", {"path": str(path), "purpose": purpose})["id"]

        table = [[([y] if x == 4 else [x]) if 4 in (x, y) else
                  ([4, x] if x == y else [i for i in range(4) if i not in (x, y)])
                  for y in range(5)] for x in range(5)]
        witnesses = []
        for key, triple in (("distinct", [1, 2, 0]), ("equal", [0, 0, 0])):
            path = materials / f"{key}.json"
            path.write_text(json.dumps({"addition": table, "triple": triple}), encoding="utf-8")
            witnesses.append(freeze(path, "Explicit five-element hyperfield and null triple"))
        witness = witnesses[0]
        checker = freeze(Path(__file__).with_name("hyperfield_diagnosis_validator.py"), "Independent finite table validator")
        domain = "Nonzero triples in hyperfields with zero in x ⊞ y ⊞ z."
        old = node("common", ORIGINAL, domain)
        pairwise = node("pairwise", PAIRWISE,
            "Only the frozen five-element Klein hyperfield in " + witness, quantifier="finite")
        node("singleton", "Does one quadratic sum have a single element in the relevant subcase?",
             "A singleton proof obligation for the proposed restricted route.")
        node("singleton-route", REVISED, domain + " At least one quadratic sum is a singleton.", "singleton")
        node("common-route", "The cubic target can be approached via a universally available common witness.",
             "Proposed bridge proof route for nonzero null triples.", "common")
        target = node("cubic", TARGET, domain, "common-route", "singleton-route", "pairwise")
        apply("goal.set", {"node": "cubic"})
        for key in ("common", "singleton-route", "pairwise", "cubic"):
            apply("route.add", {"id": key, "node": key, "rationale": "Check this exact mathematical step",
                "next_question": "Check the explicit witness", "stop_condition": "A check on the declared scope",
                "max_runs": 3, "stagnation_limit": 2})
            apply("route.status", {"id": key, "status": "active", "reason": "Offline demonstration"})
        apply("route.checkpoint", {"route": "cubic", "finding": "A common witness is a sufficient bridge.",
            "next_question": "Can distinct quadratic witnesses be chosen to make their weighted sum null?"})
        apply("diagnosis.revision", {"id": "restrict-singleton", "original": "common", "revised": "singleton-route",
            "reason": "Add the singleton hypothesis that makes pairwise intersection sufficient."})

        def check(task, route, inputs):
            apply("task.add", {"id": task, "route": route, "role": "validate",
                "argv": [sys.executable, "{validator}", "{input}"], "validator_artifact": checker,
                "inputs": inputs, "timeout_seconds": 20, "output_limit_bytes": 100000}, "demo-checker")
            assert run_task(w, task, "demo-checker")["status"] == "completed"

        def accept(key, target_node, task, method, level):
            apply("claim.add", {"id": key, "node": target_node["id"], "statement_digest": target_node["statement_digest"],
                "scope": "finite", "evidence_level": level, "summary": target_node["statement"],
                "artifact_ids": [witness]}, "demo-proposer")
            apply("validation.add", {"id": key, "claim": key, "method": method, "task": task,
                "trust_basis": "Shipped independent exact table checker"}, "demo-checker")
            apply("review.add", {"id": key, "claim": key, "reviewer": "demo-reviewer", "reviewer_kind": "automation",
                "auditor": "demo-auditor", "coverage_complete": True, "issues": [], "verdict": "ready_for_human",
                "purpose": "Synthetic review roles for the offline demonstration"}, "demo-auditor")
            apply("claim.accept", {"claim": key, "review": key, "validation": key}, "demo-custodian")

        for task, route, inputs in (("original-check", "common", [witness]), ("replay-check", "singleton-route", [witness]),
                                    ("pairwise-check", "pairwise", [witness]), ("collision-check", "cubic", witnesses)):
            check(task, route, inputs)
        accept("pairwise-fact", pairwise, "pairwise-check", "finite_certificate", "proved")
        apply("ledger.add", {"id": "pairwise-note", "node": "pairwise", "claim": "pairwise-fact", "use": "theorem",
                             "text": "Pairwise overlap on the fixed five-element table."})
        bridge = w.store.get("node", "common-route")
        apply("claim.add", {"id": "draft-route", "node": "common-route", "statement_digest": bridge["statement_digest"],
            "scope": "universal", "evidence_level": "unknown", "summary": "Draft strategy depending on the bridge."}, "demo-proposer")
        apply("review.add", {"id": "draft-review", "claim": "draft-route", "reviewer": "demo-reviewer",
            "reviewer_kind": "automation", "auditor": "demo-auditor", "coverage_complete": True, "issues": [],
            "verdict": "ready_for_human", "purpose": "Synthetic review of conjectural wording"}, "demo-auditor")
        apply("claim.accept", {"claim": "draft-route", "review": "draft-review"}, "demo-custodian")
        apply("ledger.add", {"id": "bridge-note", "node": "common-route", "claim": "draft-route", "use": "conjecture",
                             "text": "A proposed proof via a common witness."})
        accept("common-refuted", old, "original-check", "counterexample", "disproved")
        apply("diagnosis.replay", {"id": "old-witness", "revision": "restrict-singleton", "counterexample": "common-refuted",
            "witness": witness, "task": "replay-check", "trust_basis": "Independent exact table checker"})
        apply("diagnosis.indistinguishability", {"id": "pairwise-insufficient", "node": target["id"], "witnesses": witnesses,
            "question": "Do order, unit group and pairwise overlap determine common-witness existence?",
            "next_step": "Analyze compatibility of the three weighted witness choices.",
            "task": "collision-check", "trust_basis": "Independent exact table checker"})
        apply("knowledge.attach", {"library": str(root / "academic"), "card_ids": [card["id"]],
            "document_ids": [doc["document"]], "passage_ids": doc["passages"], "reason": "Check the singleton lemma"})
        apply("diagnosis.source_check", {"id": "singleton-use", "node": "singleton-route", "passage": doc["passages"][0],
            "theorem": "Singleton intersection lemma", "use_point": "Derive a common witness in a restricted subcase of the fixed table",
            "limitations": "The pairwise certificate applies to this table. The new domain condition remains to be checked.",
            "conditions": {"pairwise intersection": {"status": "satisfied", "claim": "pairwise-fact",
                                                      "reason": "Checked exhaustively for the fixed table."},
                           "at least one singleton": {"status": "open", "obligation": "singleton",
                                                      "reason": "Check the condition for the triples to which the lemma is applied."}}})
        results = {key: report(w, key) for key in ("common", "cubic", "singleton-route")}
        assert w.audit()["passed"]
        assert results["common"]["counterexample_impact"][0]["goal_status"] == "open"
        assert results["common"]["witness_replays"][0]["result"]["outcome"] == "excluded_by_condition"
        assert results["cubic"]["indistinguishability"] and results["singleton-route"]["source_checks"]
        output = root / "diagnosis.json"
        output.write_text(json.dumps(results, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return output
    finally:
        w.store.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", help="new directory for the offline demo")
    print(run(parser.parse_args().directory))

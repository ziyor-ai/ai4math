"""Offline end-to-end example. All reviewer identities are synthetic demo labels."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ai4math.execution import run_task
from ai4math.report import markdown
from ai4math.workbench import Workbench


def run(root):
    source = Path(__file__).resolve().parent / "finite_labeling"
    wb = Workbench.create(root, "finite-demo", "Finite coprime labeling workflow demonstration", "demo-owner")
    try:
        def apply(op, p, actor="demo-owner"):
            return wb.apply(op, p, actor)
        node = apply("node.add", {"id": "p2-exact", "statement": "The minimum coprime labeling number of P_2^4 is 2.",
            "domain": "Two distinct positive integer labels; adjacent vertices must have gcd 1.", "quantifier": "finite"})
        apply("goal.set", {"node": node["id"]})
        apply("route.add", {"id": "witness-and-bound", "node": node["id"], "rationale": "Pair a candidate with an independent capacity bound.",
            "next_question": "Does the witness attain the unavoidable lower bound?", "stop_condition": "Stop after validation or two unsuccessful runs.",
            "max_runs": 2, "stagnation_limit": 2})
        apply("route.status", {"id": "witness-and-bound", "status": "active", "reason": "Run a bounded offline demonstration."})
        producer = apply("artifact.import", {"path": str(source / "producer.py"), "purpose": "demo producer source"})
        validator = apply("artifact.import", {"path": str(source / "validator.py"), "purpose": "independent demo checker source"})
        apply("task.add", {"id": "produce", "route": "witness-and-bound", "role": "produce",
              "argv": [sys.executable, str(wb.store.blob(producer["id"]))], "timeout_seconds": 10, "output_limit_bytes": 100000})
        produced = run_task(wb, "produce", "demo-producer")
        if produced["status"] != "completed":
            raise RuntimeError("demo producer failed")
        witness = apply("artifact.import", {"path": str(wb.store.inside("tasks/produce/witness.json")), "purpose": "n=2 witness"})
        apply("claim.add", {"id": "p2-minimum", "node": node["id"], "statement_digest": node["statement_digest"],
            "evidence_level": "proved", "scope": "finite", "summary": "Exact n=2 optimum: witness upper bound and distinct-label capacity lower bound.",
            "artifact_ids": [witness["id"]]}, "demo-producer")
        apply("task.add", {"id": "validate", "route": "witness-and-bound", "role": "validate",
            "argv": [sys.executable, "{validator}", "{input}"], "validator_artifact": validator["id"],
            "inputs": [witness["id"]], "timeout_seconds": 10, "output_limit_bytes": 100000}, "demo-independent-checker")
        validated = run_task(wb, "validate", "demo-independent-checker")
        if validated["status"] != "completed":
            raise RuntimeError("demo validator failed")
        apply("validation.add", {"id": "p2-certificate", "claim": "p2-minimum", "method": "finite_certificate", "task": "validate",
            "trust_basis": "Explicit independent Python checker; exact witness plus necessary capacity bound, restricted to n=2."}, "demo-independent-checker")
        apply("review.add", {"id": "p2-review", "claim": "p2-minimum", "reviewer": "demo-reviewer", "reviewer_kind": "model",
            "auditor": "demo-feedback-auditor", "coverage_complete": True, "issues": [], "verdict": "ready_for_human",
            "purpose": "synthetic workflow fixture, not a claim that a human or external model reviewed this output"}, "demo-feedback-auditor")
        apply("claim.accept", {"claim": "p2-minimum", "review": "p2-review", "validation": "p2-certificate"}, "demo-custodian")
        apply("route.checkpoint", {"route": "witness-and-bound", "finding": "Witness attains independently proved lower bound at n=2.",
                                  "claims": ["p2-minimum"], "next_question": "No further work on this finite goal."})
        apply("route.status", {"id": "witness-and-bound", "status": "closed", "reason": "Finite target settled; no automatic larger-n task."})
        apply("ledger.add", {"id": "finite-result", "node": node["id"], "claim": "p2-minimum", "use": "theorem",
                             "text": "For the two-vertex graph P_2^4, the minimum coprime labeling number equals 2."})
        output = wb.store.inside("exports")
        output.mkdir(exist_ok=True)
        (output / "report.md").write_text(markdown(wb), encoding="utf-8")
        (output / "events.json").write_text(json.dumps(wb.store.export_events(), indent=2), encoding="utf-8")
        result = {"workspace": str(wb.store.root), "audit": wb.audit(), "delivery": wb.delivery_check(),
                  "synthetic_reviewers": True, "evidence_level": "proved", "scope": "only P_2^4 (n=2)"}
        (output / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return result
    finally:
        wb.store.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("workspace", help="fresh directory; never reuse a previous run")
    print(json.dumps(run(parser.parse_args().workspace), ensure_ascii=False, indent=2))

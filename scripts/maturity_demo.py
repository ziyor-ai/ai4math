"""Demonstrate the v3.5 ladder: a finding-only round, then a candidate, then verified.

Round 1 produces a research finding with no Lean at all.
Round 2 states a proof candidate (with a frozen sketch), which opens a formalization
request; a validate task replays a small Lean proof; acceptance promotes the claim to
``verified``; the request is resolved by the state machine.

Run:
    python scripts/maturity_demo.py
"""
import json
import sys
from pathlib import Path

ROOT = Path("workspaces/maturity-demo-20261118")
CHECKER = Path("scripts") / "lean_replay_checker.py"


def main() -> int:
    from ai4math.execution import run_task
    from ai4math.health import snapshot
    from ai4math.workbench import Workbench

    if not (ROOT / "research.sqlite3").exists():
        Workbench.create(ROOT, "maturity-demo", "Maturity demo")
    inputs = ROOT / "inputs"
    inputs.mkdir(exist_ok=True)
    (inputs / "proof.lean").write_text(
        "import Mathlib\n\ntheorem demo_two_plus_two : 2 + 2 = 4 := by\n  norm_num\n",
        encoding="utf-8")
    (inputs / "lean_replay.py").write_text(CHECKER.read_text(encoding="utf-8"), encoding="utf-8")
    (inputs / "note.md").write_text(
        "# Attempt 1\n\nThe direct bound is not reachable: a transfer lemma is missing.\n",
        encoding="utf-8")
    w = Workbench(ROOT)
    actor = "coordinator"
    try:
        if not w.store.exists("node", "demo-goal"):
            w.apply("node.add", {"id": "demo-goal", "statement": "2 + 2 = 4",
                                 "domain": "natural numbers", "quantifier": "universal",
                                 "lean_statement": "theorem demo_two_plus_two : 2 + 2 = 4"},
                    actor)
            w.apply("goal.set", {"node": "demo-goal"}, actor)
        if not w.store.exists("route", "demo-route"):
            w.apply("route.add", {"id": "demo-route", "node": "demo-goal",
                                  "rationale": "one finding round, then one verified round",
                                  "next_question": "which part is formalizable?",
                                  "stop_condition": "one finding, then one proof",
                                  "max_runs": 4, "stagnation_limit": 4}, actor)
            w.apply("route.status", {"id": "demo-route", "status": "active", "reason": "demo"}, actor)
        note = w.apply("artifact.import", {"path": str(inputs / "note.md"), "purpose": "attempt note"},
                       actor)["id"]
        checker = w.apply("artifact.import", {"path": str(inputs / "lean_replay.py"),
                                              "purpose": "checker"}, actor)["id"]
        proof = w.apply("artifact.import", {"path": str(inputs / "proof.lean"),
                                            "purpose": "lean proof"}, actor)["id"]
        # ---- round 1: a finding, no Lean -------------------------------------------
        if not w.store.exists("research_finding", "F-demo-1"):
            w.apply("finding.add", {"id": "F-demo-1", "node": "demo-goal",
                                    "kind": "obstacle_identified",
                                    "statement": "the direct identity route needs a transfer step",
                                    "scope": "natural numbers, current formulation",
                                    "evidence_kind": "theoretical_argument",
                                    "evidence_refs": [note],
                                    "relation": {"target": "demo-goal", "type": "load_bearing"},
                                    "next_action": {"kind": "state_transfer",
                                                    "question": "is a transfer step even needed?"}},
                    actor)
        if not w.store.exists("round", "r-demo-1"):
            w.apply("round.open", {"id": "r-demo-1", "route": "demo-route"}, actor)
            w.apply("round.record_attempt", {"round": "r-demo-1",
                                             "finding": "located the missing transfer step",
                                             "next_question": "is the step formalizable?",
                                             "findings": ["F-demo-1"],
                                             "artifacts": [note]}, actor)
            w.apply("round.settle", {"round": "r-demo-1"}, actor)
            print("[round 1] finding-only round settled:",
                  w.store.get("round_settled", "r-demo-1")["outcome"])
        # ---- round 2: candidate -> formalization -> verified ----------------------
        if not w.store.exists("claim", "c-demo"):
            w.apply("claim.add", {"id": "c-demo", "node": "demo-goal",
                                  "statement_digest": w.store.get("node", "demo-goal")["statement_digest"],
                                  "evidence_level": "proved", "scope": "universal",
                                  "summary": "two plus two is four", "proof_route": "direct",
                                  "contribution": "load_bearing",
                                  "artifact_ids": [note], "proof_sketch": [note]}, "author")
        pending = [r for r in w.store.all("formalization_request")
                   if r["claim"] == "c-demo" and r["status"] == "pending"]
        print("[formalization] pending:", [r["id"] for r in pending])
        if not w.store.exists("task", "demo-validate"):
            w.apply("round.open", {"id": "r-demo-2", "route": "demo-route"}, actor)
            w.apply("task.add", {"id": "demo-validate", "route": "demo-route", "role": "validate",
                                 "round": "r-demo-2",
                                 "argv": [sys.executable, "{validator}", "{input}"],
                                 "timeout_seconds": 300, "output_limit_bytes": 100000,
                                 "inputs": [note, proof], "validator_artifact": checker}, "runner")
            run_task(w, "demo-validate", "runner")
            w.apply("validation.add", {"id": "demo-validation", "claim": "c-demo",
                                       "method": "formal_kernel",
                                       "trust_basis": "lean kernel replay of the frozen proof",
                                       "task": "demo-validate"}, "registrar")
            w.apply("review.add", {"id": "demo-review", "claim": "c-demo",
                                   "reviewer": "model-reviewer", "reviewer_kind": "model",
                                   "auditor": "model-auditor", "coverage_complete": True,
                                   "issues": [], "verdict": "ready_for_human",
                                   "notes": "checked statement, exit code and type"},
                    "model-auditor")
            w.apply("claim.accept", {"claim": "c-demo", "review": "demo-review",
                                     "validation": "demo-validation"}, "custodian")
            w.apply("round.assess", {"round": "r-demo-2", "finding": "proof accepted",
                                     "next_question": "done",
                                     "task_assessments": [{"task_id": "demo-validate",
                                                           "execution_status": "completed",
                                                           "scientific_outcome": "proved",
                                                           "evidence_refs": ["c-demo"]}],
                                     "claims": ["c-demo"]}, actor)
            w.apply("round.settle", {"round": "r-demo-2"}, actor)
            if pending:
                status = w.store.get("formalization_request", pending[0]["id"])["status"]
                print("[formalization] after acceptance:", status,
                      w.store.get("formalization_request", pending[0]["id"])["resolution"]["kind"])
        print("[round 2] claim maturity:", w.store.get("claim", "c-demo")["maturity"])
        state = snapshot(w)
        print(json.dumps({"maturity": state["maturity"],
                          "signals": [s["code"] for s in state["signals"]]},
                         ensure_ascii=False, indent=1))
        return 0
    finally:
        w.store.close()


if __name__ == "__main__":
    raise SystemExit(main())

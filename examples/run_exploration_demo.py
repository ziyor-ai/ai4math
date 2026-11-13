"""Five-capability handoff and real finite producer/validator engineering flow."""
import json
import sys
from pathlib import Path

from ai4math.execution import export_task, run_task
from ai4math.exploration import RECIPES, board
from ai4math.knowledge import AcademicLibrary
from ai4math.workbench import Workbench


def run(root, library_root):
    root = Path(root).resolve()
    if root.exists():
        raise ValueError("demo requires a new directory")
    lib = AcademicLibrary(library_root)
    w = Workbench.create(root, "exploration-engineering", "Synthetic exploration acceptance; no research claims")
    try:
        w.apply("node.add", {"id": "target", "statement": "Synthetic unresolved universal target; not a research theorem",
                            "domain": "all positive n", "quantifier": "universal"})
        w.apply("goal.set", {"node": "target"})
        cards = {}
        names = {"representation": "目标卡点的表示", "bridge_lemma": "双向桥梁", "construction": "固定评估器",
                 "discriminating_probe": "最小区分实验", "branch_portfolio": "承重机制不同"}
        for capability, prefix in names.items():
            cards[capability] = next(c["id"] for c in lib.store.all("mechanism") if c["content"]["name"].startswith(prefix))
        w.apply("knowledge.attach", {"library": str(lib.store.root), "card_ids": list(cards.values()), "reason": "Explicit synthetic demo selection"})
        w.apply("route.add", {"id": "engineering", "node": "target", "rationale": "Exercise task and evidence wiring only",
            "next_question": "Does the finite contract remain bound?", "stop_condition": "Eight executions at most", "max_runs": 8, "stagnation_limit": 2})
        w.apply("route.status", {"id": "engineering", "status": "active", "reason": "engineering test only"})
        design = root / "design.json"
        design.write_text(json.dumps({"n": 6}), encoding="utf-8")
        design_id = w.apply("artifact.import", {"path": str(design), "purpose": "finite synthetic input"})["id"]
        program = w.apply("artifact.import", {"path": str(Path(__file__).with_name("exploration_producer.py")), "purpose": "frozen producer"})["id"]
        checker = w.apply("artifact.import", {"path": str(Path(__file__).with_name("exploration_validator.py")), "purpose": "separate finite checker"})["id"]
        w.apply("exploration.open", {"id": "portfolio", "node": "target", "question": "Exercise five mechanisms without a model call",
            "max_candidates": 8, "max_tasks": 10, "max_probes": 3, "card_ids": list(cards.values()),
            "metric_directions": {"quality": "max", "span": "min"},
            "evaluation_protocol": {"validator_artifact": checker, "benchmark_artifacts": [design_id], "metrics": {
                "quality": {"definition": "Number of distinct values", "unit": "values"},
                "span": {"definition": "Largest minus smallest value", "unit": "integer steps"}}}})
        def propose(key, capability, parents=None):
            return w.apply("exploration.propose", {"id": key, "pool": "portfolio", "capability": capability,
                "mechanism_key": capability + ("-reverse" if parents else "-initial"), "representation": "Synthetic finite list interface",
                "bridge": "Check a finite interface, not the universal target", "falsifier": "Duplicates or missing values",
                "plan": "Exercise " + RECIPES[capability]["name"], "minimal_probe": "n=6", "assumptions": ["n=6 fixture"],
                "card_ids": [cards[capability]], "artifact_ids": [design_id], "parents": parents or [],
                "parent_reason": "Reverse order while retaining the parent's finite properties"}, "design-agent")
        def task(key, candidate, role="produce", inputs=None):
            args = [sys.executable, "{validator}", "{input}"] if role == "validate" else [sys.executable, str(w.store.blob(program)), "{input}"]
            p = {"id": key, "route": "engineering", "role": role, "argv": args, "timeout_seconds": 5,
                 "output_limit_bytes": 16000, "inputs": inputs or [design_id], "exploration_candidate": candidate}
            if role == "validate":
                p["validator_artifact"] = checker
            w.apply("task.add", p, "separate-checker" if role == "validate" else "producer-agent")
            return export_task(w, key)
        exported = {}
        for capability in RECIPES:
            propose(capability, capability)
            exported[capability] = task("handoff-" + capability, capability)["directory"]
        def execute(candidate_id, task_id):
            produced = run_task(w, task_id)
            if produced["status"] != "completed":
                raise ValueError("synthetic producer failed")
            task("audit-" + task_id, candidate_id, "validate", [design_id, produced["result_artifact"]])
            audited = run_task(w, "audit-" + task_id)
            if audited["status"] != "completed":
                raise ValueError("synthetic independent checker failed")
            return w.apply("exploration.evaluate", {"id": "eval-" + candidate_id, "candidate": candidate_id,
                "task": task_id, "audit_task": "audit-" + task_id,
                "trust_basis": "Separately implemented finite list checker; same framework developer, no independent human mathematical review"})
        first = execute("construction", "handoff-construction")
        propose("construction-child", "construction", ["construction"])
        task("child", "construction-child")
        second = execute("construction-child", "child")
        for key, cost in [("boundary", 1), ("expensive-boundary", 4)]:
            w.apply("exploration.probe", {"id": key, "pool": "portfolio", "question": "Synthetic opposing predictions",
                "predictions": {"representation": "holds", "bridge_lemma": "fails"}, "estimated_tool_calls": cost,
                "decision_if_success": "Revisit the bridge hypothesis", "decision_if_failure": "Inspect representation loss",
                "artifact_ids": [design_id]})
        w.apply("exploration.feedback", {"id": "finite-limit", "pool": "portfolio", "task": "child",
            "obstacle": "Finite list checks do not address the synthetic universal target",
            "next_change": "Require a mathematical bridge before further experiments", "artifact_ids": [design_id]})
        out = board(w, "portfolio")
        result = {"scope": "synthetic engineering acceptance", "exported_capabilities": exported,
            "evaluations": [first, second], "frontier": [x["candidate"]["id"] for x in out["evaluated_frontier"]],
            "first_probe": out["probe_ranking"][0]["id"], "actual_processes": 4,
            "tasks_reserved": out["pool"]["tasks_reserved"], "goal_evidence_level": w.board()["goal_evidence_level"],
            "accepted_claims": len([x for x in w.board()["claims"] if x["status"] == "accepted"]),
            "audit": w.audit(), "model_calls": 0}
        (root/"exports").mkdir(exist_ok=True)
        (root/"exports/acceptance.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        return result
    finally:
        w.store.close();lib.store.close()


if __name__ == "__main__":
    print(json.dumps(run(sys.argv[1], sys.argv[2]), ensure_ascii=False, indent=2))

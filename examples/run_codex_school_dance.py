"""Prepare an isolated public Codex School Dance trial; never launch or register.

Run as ``python -m examples.run_codex_school_dance prepare NEW_DIRECTORY
--library ABS_LIBRARY --skill-manifest FILE``. Real main/worker source readings,
method reviews, strategy dispatch and all three AppServer sessions are separate
operations. No previous School Dance constructor/solution is exposed to workers.
"""
import argparse
import hashlib
import json
from pathlib import Path

from examples.run_school_dance_chain import build_corpus, PROBLEM, SOURCE
from examples.codex_school_dance_validator import node_payload, node_digest

DESIGNS = {
    "augmenting-path": {
        "mechanism": "Maintain a feasible matching; use alternating reachability and path flips to repair choices.",
        "probe": "Check a graph where an initially matched endpoint must be reassigned; preserve distinct endpoints after each flip.",
        "falsifier": "A feasible larger matching exists, or a flip reuses an endpoint or a forbidden edge.",
        "learning": "Distinguish maximal from maximum and record the alternating-path invariant.",
        "kind": "new_construction"},
    "hall-deficiency": {
        "mechanism": "Pair a feasible matching with a left subset S and its exact neighborhood, producing a tight combinatorial upper bound.",
        "probe": "Check deficient and left-saturating graphs, including empty S; verify the exact neighborhood rather than a superset.",
        "falsifier": "The proposed neighborhood omits an adjacent vertex, or the feasible matching fails to attain n-|S|+|N(S)|.",
        "learning": "Separate feasibility from optimality via a dual obstruction certificate; no assumption of a perfect matching.",
        "kind": "new_representation"},
    "subset-dp": {
        "mechanism": "Use a state indexed by processed left vertices and the subset of already used right vertices, with explicit reconstruction.",
        "probe": "Check skip and match transitions, empty used set, and reconstruction without reusing a right vertex.",
        "falsifier": "A transition loses a feasible matching, or reconstruction gives forbidden/reused endpoints or a suboptimal cardinality.",
        "learning": "Explain state sufficiency and transition completeness rather than relying on output agreement.",
        "kind": "new_construction"},
}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def put(path, value):
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, sort_keys=True, indent=2)
        stream.write("\n")


def prepare(root, library, skill_manifest, sources=()):
    root, library, skill_manifest = Path(root).resolve(), Path(library).resolve(), Path(skill_manifest).resolve()
    if not library.is_dir() or not skill_manifest.is_file():
        raise ValueError("actual library directory and skill manifest file required")
    # Fails rather than overwriting any prior trial or historical receipt.
    root.mkdir(parents=True, exist_ok=False)
    corpus_path = root / "corpus.json"
    put(corpus_path, build_corpus())
    corpus_sha = digest(corpus_path.read_bytes())
    inventory = []
    for name in ("school_dance_validator.py", "codex_school_dance_validator.py"):
        raw = Path(__file__).with_name(name).read_bytes()
        (root / name).write_bytes(raw)
        inventory.append({"path": str(root / name), "sha256": digest(raw), "worker_input": False})
    raw = skill_manifest.read_bytes()
    (root / "skill-manifest.json").write_bytes(raw)
    skill_sha = digest(raw)
    source_inventory = []
    for index, source in enumerate(sources):
        source = Path(source).resolve()
        raw = source.read_bytes()
        dest = root / f"source-{index:02d}{source.suffix}"
        dest.write_bytes(raw)
        source_inventory.append({"original_path": str(source), "path": str(dest), "sha256": digest(raw),
                                 "reading_status": "pending_actual_main_and_worker_reading"})
    routes = []
    for mechanism, design in DESIGNS.items():
        prefix = "school-dance-" + mechanism
        schema = {"task_id": prefix + "-worker-r1", "mechanism": mechanism,
                  "corpus_sha256": corpus_sha, "matchings": {"CASE_ID": [["left integer", "right integer"]]}}
        if mechanism == "hall-deficiency":
            schema["hall_witnesses"] = {"CASE_ID": {"S": ["left integer"], "N": ["right integer"]}}
        assignment = {"mechanism": mechanism, **design, "corpus_sha256": corpus_sha,
                      "output_schema": schema, "output_file": "matching-table.json",
                      "scope": "Exactly the 37 frozen corpus graphs. General correctness remains unproved.",
                      "instructions": ["Read actual exported PROMPT.md and input.json; independently search the bound academic library and read selected original passages before submitting worker literature.",
                                       "Wait for the real coordinator literature release before mathematical work.",
                                       "Use this route's mechanism and explain assumptions, invariant/state/witness, failures and reconstruction. Submit source and argument as separate sealed artifacts.",
                                       "Write exactly one matching-table.json with every case ID, and no extra JSON fields. No precomputed solution is supplied.",
                                       "Do not read sibling outputs or historical producers. Do not import or execute the independent checker.",
                                       "All phase clocks are observations, not deadlines. Preserve the 2,000,000-byte cap and single route attempt.",
                                       "Seal actual outputs through the bound workflow tools; an unfinished or failed session is not a completed result."]}
        path = root / f"{mechanism}-assignment.json"
        put(path, assignment)
        routes.append({"mechanism": mechanism, "node": {"id": prefix + "-finite", **node_payload(corpus_sha, mechanism),
                       "proof_dependencies": []}, "route_id": prefix + "-route-r1",
                       "method_id": prefix + "-method-r1", "candidate_id": prefix + "-candidate-r1",
                       "task_id": prefix + "-worker-r1", "worker_actor": prefix + "-worker",
                       "assignment_path": str(path), "assignment_sha256": digest(path.read_bytes())})
    plan = {"schema": "codex-school-dance-preparation-v1", "status": "proposal_only_not_registered",
            "proposal_author_role": "independent checker/preparation agent; root must adopt actual method designs before independent review",
            "root": str(root), "library": str(library), "corpus_sha256": corpus_sha, "corpus_cases": 37,
            "skill_manifest_artifact": skill_sha, "sources": source_inventory, "routes": routes,
            "checker_sources": inventory, "engineering_evidence_level": "unknown",
            "goal": {"id": "school-dance-public", "statement": "Produce a maximum valid pairing for every legal School Dance input.",
                     "domain": "CSES 1696: 1 <= n,m <= 500; 1 <= k <= 1000 allowed pairs.", "quantifier": "universal",
                     "proof_dependencies": [r["node"]["id"] for r in routes]},
            "main_literature_plan": {"status": "pending_real_receipt", "sources": [
                {"url": PROBLEM, "required_passage": "Problem, input constraints and sample; map allowed pairs to graph edges"},
                {"url": SOURCE, "required_passage": "Definition of augmenting path and Corollary 7.9; finite graph and matching hypotheses"},
                {"topic": "Hall neighborhood/deficiency upper bound", "required_passage": "Root must select/read a primary source and record exact provenance; upper bound also needs elementary counting argument"},
                {"topic": "Dynamic programming principle/state sufficiency", "required_passage": "Root must select/read primary source; this exact subset recurrence remains a worker obligation"}]},
            "independence": ["Three actual external AppServer sessions, distinct actors and output directories; no simulated worker literature.",
                             "Main and each worker source receipts describe only actually read passages.",
                             "Separate independent method reviewer after root authors/adopts frozen design; no generated ready review.",
                             "Independent finite validator runs only after seal, with exact selected output SHA and producer-task binding.",
                             "Finite accepted claims do not accept the universal parent or demonstrate research effectiveness."],
            "prerequisites": ["Real main literature gate", "Actual selected source cards/documents", "Root adoption and separate actual independent method reviews",
                              "Current strategy brief and genuine parallel decision", "Explicit root launch authorization and completed runner"]}
    put(root / "trial-plan.json", plan)
    return plan


def registration_payloads(plan, selected_cards, selected_documents):
    """Pure payload builder. Caller imports exact artifacts and obtains real reviews.

    No application, review, literature receipt, strategy brief or worker is forged.
    selected_cards maps mechanism to an actually attached compatible card ID list.
    """
    if set(selected_cards) != set(DESIGNS) or any(not v for v in selected_cards.values()):
        raise ValueError("actual selected cards required per mechanism")
    cards = sorted({c for values in selected_cards.values() for c in values})
    corpus = plan["corpus_sha256"]
    operations = [{"operation": "node.add", "payload": r["node"]} for r in plan["routes"]]
    operations += [{"operation": "node.add", "payload": plan["goal"]},
                   {"operation": "goal.set", "payload": {"node": plan["goal"]["id"]}},
                   {"operation": "exploration.open", "payload": {"id": "school-dance-public-pool-r1", "node": plan["goal"]["id"],
                    "question": "Compare three distinct finite matching mechanisms without inferring general correctness.",
                    "max_candidates": 3, "max_tasks": 3, "max_probes": 3, "card_ids": cards,
                    "document_ids": selected_documents}}]
    tasks = []
    for route in plan["routes"]:
        key, design = route["mechanism"], DESIGNS[route["mechanism"]]
        assignment = route["assignment_sha256"]
        operations.extend([
            {"operation": "route.add", "payload": {"id": route["route_id"], "node": route["node"]["id"],
             "rationale": design["mechanism"], "next_question": design["learning"], "stop_condition": "One sealed submission or one terminal failure; no retry.", "max_runs": 1, "stagnation_limit": 1}},
            {"operation": "route.status", "payload": {"id": route["route_id"], "status": "active", "reason": "Adopted public finite trial design"}},
            {"operation": "exploration.propose", "payload": {"id": route["candidate_id"], "pool": "school-dance-public-pool-r1",
             "capability": "representation" if key == "hall-deficiency" else "construction", "mechanism_key": key,
             "representation": design["mechanism"], "bridge": "A feasible table plus independent finite optimality verification can close only this leaf.",
             "falsifier": design["falsifier"], "plan": design["mechanism"], "minimal_probe": design["probe"],
             "assumptions": ["The exact frozen finite bipartite graph corpus; no perfect matching assumption"],
             "card_ids": selected_cards[key], "artifact_ids": [corpus, assignment]}},
            {"operation": "method.add", "payload": {"id": route["method_id"], "route": route["route_id"], "kind": design["kind"],
             "proposed_tool": "Independent Codex AppServer worker with logged workflow tools", "mechanism": design["mechanism"],
             "falsifier": design["falsifier"], "minimal_probe": design["probe"], "learning_goal": design["learning"],
             "obligations": [route["node"]["id"]], "artifact_ids": [assignment], "card_ids": selected_cards[key]}}])
        tasks.append({"id": route["task_id"], "route": route["route_id"], "role": "explore",
                      "argv": ["codex", "app-server"], "timeout_seconds": None, "output_limit_bytes": 2_000_000,
                      "inputs": [corpus, assignment, plan["skill_manifest_artifact"], *[s["sha256"] for s in plan["sources"]]],
                      "external_supervision": "process", "exploration_candidate": route["candidate_id"],
                      "method": route["method_id"], "activity": "symbolic_analysis", "learning_question": design["learning"],
                      "codex_workflow": {"skill_manifest_artifact": plan["skill_manifest_artifact"],
                                         "worker_actor": route["worker_actor"], "source_roots": [plan["library"]]}})
    return {"setup_after_policy_artifact_import_and_knowledge_attach": operations,
            "tasks_after_actual_independent_reviews_and_literature_strategy_gates": tasks,
            "policy": {"mode": "theory_first", "exclude_history": True, "allowed_roots": [plan["root"], plan["library"]],
                       "max_probe_cases": 3, "rationale": "Isolated public trial; no old matching solutions or G0 evidence."},
            "strategy_decision_template": {"decision": "switch", "next_route": plan["routes"][0]["route_id"],
                "parallel_routes": [r["route_id"] for r in plan["routes"]],
                "reason": "Three distinct independent finite leaves; no mutual assumptions.",
                "next_question": "Can each mechanism produce auditable finite matching certificates?",
                "requires_real_fields": ["id", "brief_sha256"]},
            "not_included": ["Actual method.review", "Actual main/worker literature", "Task launch", "Validator/claim acceptance"]}


def materialize_validator(preparation, mechanism, producer_task, validator_task, output, destination):
    """Freeze checker binding after the caller independently verifies actual sealing.

    This function does NOT establish seal provenance or run/register validation.
    """
    preparation, destination, output = map(Path, (preparation, destination, output))
    plan = json.loads((preparation / "trial-plan.json").read_text(encoding="utf-8"))
    route, = [r for r in plan["routes"] if r["mechanism"] == mechanism]
    if producer_task != route["task_id"]:
        raise ValueError("wrong producer task")
    template = (preparation / "codex_school_dance_validator.py").read_bytes()
    base = preparation / "school_dance_validator.py"
    for item in plan["checker_sources"]:
        if digest((preparation / Path(item["path"]).name).read_bytes()) != item["sha256"]:
            raise ValueError("checker source changed")
    if digest((preparation / "corpus.json").read_bytes()) != plan["corpus_sha256"]:
        raise ValueError("corpus changed")
    binding = {"mechanism": mechanism, "producer_task": producer_task, "validator_task": validator_task,
               "node": route["node"]["id"], "corpus_sha256": plan["corpus_sha256"],
               "output_sha256": digest(output.read_bytes()), "base_checker_sha256": digest(base.read_bytes()),
               "statement_digest": node_digest(plan["corpus_sha256"], mechanism)}
    if template.count(b"BOUND = None") != 1:
        raise ValueError("template binding sentinel changed")
    destination.mkdir(parents=True, exist_ok=False)
    checker = template.replace(b"BOUND = None", ("BOUND = " + repr(binding)).encode())
    (destination / "validator.py").write_bytes(checker)
    put(destination / "binding.json", binding)
    return {"binding": binding, "validator_sha256": digest(checker),
            "inputs": sorted(binding[k] for k in ("corpus_sha256", "output_sha256", "base_checker_sha256")),
            "status": "prepared_not_registered_not_run_seal_provenance_requires_external_review"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    prep = sub.add_parser("prepare")
    prep.add_argument("root", type=Path)
    prep.add_argument("--library", type=Path, required=True)
    prep.add_argument("--skill-manifest", type=Path, required=True)
    prep.add_argument("--source", type=Path, action="append", default=[])
    args = parser.parse_args()
    plan = prepare(args.root, args.library, args.skill_manifest, args.source)
    print(json.dumps({"plan": str(args.root / "trial-plan.json"), "corpus_sha256": plan["corpus_sha256"],
                      "status": plan["status"]}, sort_keys=True))


if __name__ == "__main__":
    main()

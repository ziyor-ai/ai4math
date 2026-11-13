import json
import sys

import pytest

from ai4math.execution import export_task, run_task
from ai4math.exploration import board
from ai4math.knowledge import AcademicLibrary
from ai4math.store import ResearchError
from ai4math.workbench import Workbench
from test_knowledge import source, card


@pytest.fixture
def env(tmp_path):
    lib = AcademicLibrary.create(tmp_path / "library")
    src = lib.add_source(source())
    mechanism = lib.add_card(card(src["id"]))
    w = Workbench.create(tmp_path / "workspace", "engineering", "Exploration engineering")
    w.apply("node.add", {"id": "goal", "statement": "Synthetic unresolved target", "domain": "all positive n", "quantifier": "universal"})
    w.apply("goal.set", {"node": "goal"})
    w.apply("knowledge.attach", {"library": str(lib.store.root), "card_ids": [mechanism["id"]], "reason": "approved engineering source"})
    path = w.store.inside("design.txt")
    path.write_text("Synthetic design, no mathematical proof", encoding="utf-8")
    art = w.apply("artifact.import", {"path": str(path), "purpose": "design"})["id"]
    checker = w.store.inside("independent_checker.py")
    checker.write_text('''import json, sys
from pathlib import Path
c = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
protocol = c["exploration"]["evaluation_protocol"]
data = next(json.loads(Path(a["path"]).read_text(encoding="utf-8"))
            for a in c["input_artifacts"] if a["id"] not in protocol["benchmark_artifacts"])
v = data["values"]
r = {"candidate": c["exploration"]["candidate"]["id"], "candidate_digest": c["exploration"]["candidate"]["candidate_digest"],
     "task_id": c["task_id"], "evaluation_protocol_sha256": protocol["sha256"],
     "input_artifacts": sorted(a["id"] for a in c["input_artifacts"]), "scope": "finite", "valid": len(set(v)) == len(v),
     "metrics": {"quality": len(v)}, "checks": ["values are distinct"], "limitations": ["Synthetic finite data"]}
r.update(data.get("test_updates", {}))
Path("result.json").write_text(json.dumps(r), encoding="utf-8")
''', encoding="utf-8")
    validator = w.apply("artifact.import", {"path": str(checker), "purpose": "test fixture checker"})["id"]
    w.apply("exploration.open", {"id": "pool", "node": "goal", "question": "Which mechanism closes the gap?",
        "max_candidates": 4, "max_tasks": 6, "max_probes": 3, "card_ids": [mechanism["id"]],
        "evaluation_protocol": {"validator_artifact": validator, "benchmark_artifacts": [art],
                                "metrics": {"quality": {"definition": "List length", "unit": "elements"}}}})
    w.apply("route.add", {"id": "route", "node": "goal", "rationale": "Synthetic bounded engineering route", "next_question": "Compare mechanisms",
        "stop_condition": "Stop after eight executions", "max_runs": 8, "stagnation_limit": 2})
    w.apply("route.status", {"id": "route", "status": "active", "reason": "engineering acceptance"})
    yield w, lib, mechanism["id"], art
    w.store.close()
    lib.store.close()


def candidate(env, key="candidate", mechanism="Hall-matching", capability="representation", **updates):
    w, _, card_id, art = env
    return w.apply("exploration.propose", {"id": key, "pool": "pool", "capability": capability,
        "mechanism_key": mechanism, "representation": "Finite graph", "bridge": "A sufficient capacity condition",
        "falsifier": "A tight boundary violation", "plan": "Prove or refute the sufficient condition",
        "minimal_probe": "One boundary instance", "assumptions": ["Finite sets"], "card_ids": [card_id],
        "artifact_ids": [art], **updates}, "designer")


def job(env, key="produce", role="produce", code=None, inputs=None, **extra):
    w, _, _, art = env
    payload = {"id": key, "route": "route", "role": role, "exploration_candidate": "candidate",
        "argv": [sys.executable, "-c", code or "print('engineering')"], "timeout_seconds": 3,
        "output_limit_bytes": 12000, "inputs": inputs or [art], **extra}
    return w.apply("task.add", payload, "checker" if role == "validate" else "producer")


def evaluated(env, receipt_updates=None):
    w, _, _, art = env
    c = candidate(env)
    data = json.dumps({"values": [1, 2, 3], "test_updates": receipt_updates or {}})
    job(env, code=f"from pathlib import Path; Path('result.json').write_text({data!r})")
    result = run_task(w, "produce")
    assert result["status"] == "completed"
    validator = w.store.get("exploration_pool", "pool")["evaluation_protocol"]["validator_artifact"]
    job(env, "audit", "validate", inputs=[art, result["result_artifact"]], validator_artifact=validator,
        argv=[sys.executable, "{validator}", "{input}"])
    assert run_task(w, "audit")["status"] == "completed"
    return c


def test_attach_study_material_requires_selected_document(env, tmp_path):
    w, lib, card_id, _ = env
    sid = lib.store.get("mechanism", card_id)["content"]["source_ids"][0]
    original = tmp_path / "source.txt"
    original.write_text("A finite bipartite graph with a deficient subset.", encoding="utf-8")
    doc = lib.add_document(sid, original)
    profile = lib.add_profile({"document": doc["document"], "summary": "Deficiency obstructs matching",
        "contributions": ["Obstruction"], "fields": ["Graph theory"], "keywords": ["Hall"], "methods": ["Counting"],
        "assumptions": ["Finite bipartite graph"], "limitations": "Only applies with the given hypotheses.",
        "anchors": doc["passages"]})
    intake = {"library": str(lib.store.root), "card_ids": [card_id], "reason": "Selected study material",
              "profile_ids": [profile["id"]], "passage_ids": doc["passages"]}
    with pytest.raises(ResearchError, match="explicitly selected document"):
        w.apply("knowledge.attach", intake)
    assert not w.store.exists("artifact", profile["id"])
    result = w.apply("knowledge.attach", {**intake, "document_ids": [doc["document"]]})
    assert result["evidence_level"] == "unknown"
    assert result["profile_ids"] == [profile["id"]] and result["passage_ids"] == doc["passages"]
    assert w.store.verify_blob(profile["id"]).is_file() and w.audit()["passed"]


def test_candidate_deduplication_uses_mechanism_not_notation(env):
    w, _, _, _ = env
    original = candidate(env)
    with pytest.raises(ResearchError, match="duplicate structural"):
        candidate(env, "same", mechanism="  HALL-MATCHING ", representation="Renamed coordinates")
    other = candidate(env, "other", mechanism="potential-function")
    assert original["structural_signature"] != other["structural_signature"]
    assert w.board()["goal_evidence_level"] == "unknown"


def test_probe_requires_disagreement_and_ranks_predicted_information_per_cost(env):
    w, _, _, art = env
    candidate(env)
    candidate(env, "other", mechanism="potential-function")
    p = {"pool": "pool", "question": "Does the boundary close?", "predictions": {"candidate": "yes", "other": "no"},
         "estimated_tool_calls": 1, "decision_if_success": "Keep matching", "decision_if_failure": "Inspect potential",
         "artifact_ids": [art]}
    w.apply("exploration.probe", {**p, "id": "cheap"})
    w.apply("exploration.probe", {**p, "id": "expensive", "estimated_tool_calls": 4})
    with pytest.raises(ResearchError, match="does not distinguish"):
        w.apply("exploration.probe", {**p, "id": "same", "predictions": {"candidate": "yes", "other": "yes"}})
    out = board(w, "pool")
    assert out["probe_ranking"][0]["id"] == "cheap" and out["mathematical_evidence_level"] == "unknown"
    assert w.store.get("route", "route")["stagnant"] == 0


def test_exported_agent_contract_freezes_sources_methods_and_obligations(env):
    w, _, _, _ = env
    c = candidate(env)
    task = job(env)
    exported = export_task(w, task["id"])
    context = exported["contract"]["exploration"]
    assert context["candidate"]["candidate_digest"] == c["candidate_digest"]
    assert context["sources"] and context["cards"] and context["recipe"]["deliver"]
    assert "数学探索指导" in open(exported["prompt"], encoding="utf-8").read()
    assert exported["contract"]["result_contract"]["evidence_level"] == "unknown"


def test_task_reservations_survive_reopen_and_failed_preparation_rolls_back(env):
    w, _, _, _ = env
    candidate(env)
    with pytest.raises(ResearchError):
        job(env, inputs=["0"*64])
    assert board(w, "pool")["remaining_task_reservations"] == 6
    for i in range(6):
        job(env, key=f"task-{i}")
    reopened = Workbench(w.store.root)
    try:
        assert board(reopened, "pool")["remaining_task_reservations"] == 0
        with pytest.raises(ResearchError, match="budget exhausted"):
            reopened.apply("task.add", {**w.store.get("task", "task-0"), "id": "new", "exploration_candidate": "candidate"})
    finally:
        reopened.store.close()


def test_pool_cannot_be_bypassed_by_dropping_candidate_field(env):
    w, _, _, _ = env
    with pytest.raises(ResearchError, match="bypassing"):
        w.apply("task.add", {"id":"bypass", "route":"route", "role":"explore", "argv":[sys.executable,"-c","pass"],
                             "timeout_seconds":2,"output_limit_bytes":1000})
    assert w.store.get("exploration_pool", "pool")["tasks_reserved"] == 0


def test_pending_task_prepared_before_pool_cannot_be_dispatched(env):
    w, _, card_id, _ = env
    w.apply("node.add", {"id": "aux", "statement": "Auxiliary target", "domain": "finite", "quantifier": "finite"})
    w.apply("node.plan", {"node": "goal", "proof_dependencies": ["aux"], "reason": "Exercise the launch gate"})
    w.apply("route.add", {"id": "aux", "node": "aux", "rationale": "Fixture", "next_question": "Fixture",
        "stop_condition": "One run", "max_runs": 1, "stagnation_limit": 1})
    w.apply("route.status", {"id": "aux", "status": "active", "reason": "Fixture"})
    w.apply("task.add", {"id": "older", "route": "aux", "role": "explore", "argv": [sys.executable, "-c", "pass"],
                        "timeout_seconds": 2, "output_limit_bytes": 1000})
    export_task(w, "older")
    w.apply("exploration.open", {"id": "aux-pool", "node": "aux", "question": "Fixture",
        "max_candidates": 1, "max_tasks": 1, "max_probes": 1, "card_ids": [card_id]})
    for operation in (export_task, run_task):
        with pytest.raises(ResearchError, match="bind a candidate"):
            operation(w, "older")
    assert w.store.get("task", "older")["status"] == "pending"
    assert w.store.get("route", "aux")["runs"] == 0
    assert w.store.get("exploration_pool", "aux-pool")["tasks_reserved"] == 0


def test_scored_pool_rejects_checker_and_benchmark_substitution(env):
    w, _, card_id, design = env
    checker = w.store.get("exploration_pool", "pool")["evaluation_protocol"]["validator_artifact"]
    benchmark_path = w.store.inside("benchmark.json")
    benchmark_path.write_text("{}", encoding="utf-8")
    benchmark = w.apply("artifact.import", {"path": str(benchmark_path), "purpose": "Separate benchmark"})["id"]
    w.apply("exploration.open", {"id": "scored", "node": "goal", "question": "Fix the comparison",
        "max_candidates": 2, "max_tasks": 2, "max_probes": 2, "card_ids": [card_id],
        "evaluation_protocol": {"validator_artifact": checker, "benchmark_artifacts": [benchmark],
                                "metrics": {"quality": {"definition": "Exact check count", "unit": "cases"}}}})
    candidate(env, pool="scored")
    argv = [sys.executable, "{validator}", "{input}"]
    other_path = w.store.inside("alternate_checker.py")
    other_path.write_text("print('different scoring program')", encoding="utf-8")
    other = w.apply("artifact.import", {"path": str(other_path), "purpose": "Substitution fixture"})["id"]
    with pytest.raises(ResearchError, match="protocol checker"):
        job(env, "swapped-checker", "validate", inputs=[design, benchmark], validator_artifact=other, argv=argv)
    with pytest.raises(ResearchError, match="evaluation benchmark"):
        job(env, "swapped-cases", "validate", inputs=[design], validator_artifact=checker, argv=argv)
    assert w.store.get("exploration_pool", "scored")["tasks_reserved"] == 0


def test_legacy_evaluations_remain_readable_but_unranked(env):
    w, _, card_id, _ = env
    w.apply("exploration.open", {"id": "legacy", "node": "goal", "question": "Fixture",
        "max_candidates": 1, "max_tasks": 1, "max_probes": 1, "card_ids": [card_id]})
    candidate(env, pool="legacy")
    historical = {"id": "legacy-assessment", "candidate": "candidate", "metrics": {"quality": 300}, "valid": True,
                  "evaluation_order": 1, "scope": "finite", "evidence_level": "finite_support"}
    with w.store.transaction("test.historical_snapshot", "fixture"):
        w.store.put("exploration_evaluation", historical["id"], historical, new=True)
    result = board(w, "legacy")
    assert result["evaluated_frontier"] == [] and result["unranked_evaluations"] == [historical]


def test_candidate_capacity_and_parent_lineage_are_persistent(env):
    w, _, _, _ = env
    parent = candidate(env)
    child = candidate(env, "child", "recursive-repair", capability="construction", parents=[parent["id"]], parent_reason="Repair a different structural interface")
    assert child["parents"] == [parent["id"]]
    candidate(env, "third", "duality")
    candidate(env, "fourth", "product")
    with pytest.raises(ResearchError, match="candidate budget"):
        candidate(env, "fifth", "new-label")


def test_rejected_finite_candidate_does_not_enter_valid_frontier(env):
    w, _, _, _ = env
    evaluated(env, {"valid":False, "metrics":{"quality":999}})
    e=w.apply("exploration.evaluate", {"id":"rejected", "candidate":"candidate", "task":"produce", "audit_task":"audit", "trust_basis":"synthetic rejection"})
    assert not e["valid"] and e["evidence_level"] == "unknown"
    assert not board(w,"pool")["evaluated_frontier"]


def test_full_demo_exports_all_five_capabilities_and_runs_four_processes(tmp_path):
    from examples.run_exploration_demo import run
    lib=AcademicLibrary.create(tmp_path/"demo-library")
    try:
        lib.seed()
    finally:
        lib.store.close()
    result=run(tmp_path/"demo",tmp_path/"demo-library")
    assert len(result["exported_capabilities"]) == 5 and result["actual_processes"] == 4
    assert len(result["frontier"]) == 2 and result["first_probe"] == "boundary"
    assert result["accepted_claims"] == 0 and result["goal_evidence_level"] == "unknown" and result["audit"]["passed"]


def test_target_plan_change_invalidates_prepared_exploration(env):
    w, _, _, _ = env
    candidate(env)
    job(env)
    w.apply("node.add", {"id": "lemma", "statement": "Auxiliary obligation", "domain": "finite", "quantifier": "finite"})
    w.apply("node.plan", {"node": "goal", "proof_dependencies": ["lemma"], "reason": "explicit revised proof plan"})
    with pytest.raises(ResearchError, match="target/plan changed"):
        run_task(w, "produce")
    assert w.store.get("route", "route")["runs"] == 0
    assert w.store.get("exploration_pool", "pool")["tasks_reserved"] == 1


def test_real_candidate_and_independent_checker_do_not_accept_mathematics(env):
    w, _, _, _ = env
    evaluated(env)
    evaluation = w.apply("exploration.evaluate", {"id": "eval", "candidate": "candidate", "task": "produce", "audit_task": "audit",
        "trust_basis": "Separately authored finite checker; synthetic contract exercise"})
    assert evaluation["evidence_level"] == "finite_support"
    assert board(w, "pool")["evaluated_frontier"][0]["candidate"]["id"] == "candidate"
    assert w.board()["claims"] == [] and w.board()["goal_evidence_level"] == "unknown"
    with pytest.raises(ResearchError, match="already consumed"):
        w.apply("exploration.evaluate", {"id": "again", "candidate": "candidate", "task": "produce", "audit_task": "audit", "trust_basis": "same receipt"})
    assert w.audit()["passed"]


@pytest.mark.parametrize("updates", [{"candidate_digest": "0"*64}, {"input_artifacts": []}, {"scope": "universal"},
    {"evaluation_protocol_sha256": "0"*64}, {"task_id": "another-task"},
    {"metrics": {"quality": float('nan')}}, {"limitations": []}])
def test_bad_evaluation_receipts_do_not_enter_frontier(env, updates):
    w, _, _, _ = env
    evaluated(env, updates)
    with pytest.raises(ResearchError):
        w.apply("exploration.evaluate", {"id": "bad", "candidate": "candidate", "task": "produce", "audit_task": "audit", "trust_basis": "test"})
    assert board(w, "pool")["evaluated_frontier"] == []


def test_failed_task_feedback_keeps_unknown_and_budget(env):
    w, _, _, art = env
    candidate(env)
    job(env, code="raise SystemExit(4)")
    assert run_task(w, "produce")["status"] == "failed"
    feedback = w.apply("exploration.feedback", {"id": "obstacle", "pool": "pool", "task": "produce",
        "obstacle": "Synthetic process failure, not a mathematical counterexample", "next_change": "Inspect failure before new task",
        "artifact_ids": [art], "evidence_level": "proved"})
    assert feedback["evidence_level"] == "unknown" and feedback["resets_budget"] is False
    assert board(w, "pool")["remaining_task_reservations"] == 5
    assert w.store.get("route", "route")["runs"] == 1


def test_clean_start_allowlist_still_applies_to_library_intake(tmp_path):
    lib = AcademicLibrary.create(tmp_path / "library")
    s = lib.add_source(source());c = lib.add_card(card(s["id"]))
    w = Workbench.create(tmp_path / "workspace", "clean", "Clean study")
    try:
        w.apply("research.configure", {"mode":"theory_first", "exclude_history":True, "allowed_roots":[str(w.store.root)], "rationale":"sealed inputs"})
        with pytest.raises(ResearchError, match="allowlist"):
            w.apply("knowledge.attach", {"library":str(lib.store.root), "card_ids":[c["id"]], "reason":"not approved"})
        assert not w.store.all("mechanism_card")
    finally:
        lib.store.close();w.store.close()


def test_school_dance_chain_rechecks_minimal_witness_and_links_only_accepted_claim(tmp_path, capsys):
    from ai4math.cli import main
    from examples.run_school_dance_chain import EXCERPT, run
    lecture = tmp_path / "fixture.html"
    lecture.write_text(f"<p>{EXCERPT}</p>", encoding="utf-8")
    root = tmp_path / "public-exercise"
    result = run(root, lecture)
    rejected, repaired = result["assessments"]
    assert (rejected["metrics"]["quality"], repaired["metrics"]["quality"], result["corpus_size"]) == (35, 37, 37)
    assert rejected["evaluation_protocol_sha256"] == repaired["evaluation_protocol_sha256"]
    witness = result["minimal_counterexample"]
    assert witness["witness"]["edges"] == [[1, 1], [1, 2], [2, 1]]
    assert len(witness["witness"]["greedy"]) == 1 and len(witness["witness"]["larger_matching"]) == 2
    assert result["accepted_claim"]["scope"] == "finite" and result["accepted_claim"]["evidence_level"] == "proved"
    assert result["goal_evidence_level"] == "unknown" and not result["delivery"]["passed"]
    w = Workbench(root / "workspace")
    try:
        decisions = {d["id"]: d for d in w.store.all("strategy_decision")}
        assert decisions["review-greedy-obstruction"]["brief"]["triggers"][0]["kind"] == "verified_counterexample"
        assert decisions["review-comparison-budget"]["brief"]["triggers"][0]["kind"] == "route_run_limit"
        transfer = result["transfer"]
        cli = ["--workspace", str(root / "workspace"), "apply"]
        assert main([*cli, "exploration.transfer", "--json", json.dumps({**transfer, "id": "wrong-excerpt", "excerpt": "A fabricated excerpt"})]) == 2
        assert "absent from the frozen document" in capsys.readouterr().err
        assert main([*cli, "exploration.counterexample", "--json", json.dumps({"id": "duplicate", "evaluation": rejected["id"]})]) == 2
        assert "already has a registered counterexample" in capsys.readouterr().err
        w.apply("claim.revoke", {"claim": result["accepted_claim"]["id"], "reason": "Test revocation visibility"})
        assert board(w, "school-dance-pool")["resolutions"][0]["claim_status"] == "revoked"
        assert main([*cli, "exploration.resolve", "--json", json.dumps({"id": "blocked", "evaluation": repaired["id"], "claim": result["accepted_claim"]["id"]})]) == 2
        assert "accepted proof" in capsys.readouterr().err
    finally:
        w.store.close()

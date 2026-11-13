"""Synthetic preparation/checker checks, not real worker runs or trial evidence."""
import copy
import itertools
import json
import subprocess
import sys
from pathlib import Path

import pytest

from examples import school_dance_validator as base
from examples import codex_school_dance_validator as checker
from examples.run_codex_school_dance import prepare, registration_payloads, materialize_validator, digest
from examples.run_school_dance_chain import build_corpus


def example(mechanism="augmenting-path"):
    corpus = json.loads(json.dumps(build_corpus()))
    binding = {"producer_task": "synthetic-worker", "mechanism": mechanism, "corpus_sha256": "synthetic-corpus"}
    output = {"task_id": binding["producer_task"], "mechanism": mechanism,
              "corpus_sha256": binding["corpus_sha256"], "matchings": {}}
    if mechanism == "hall-deficiency":
        output["hall_witnesses"] = {}
    for case in corpus["cases"]:
        matching = list(base.optimum(case["edges"], min(case["n"], case["m"])))
        output["matchings"][case["id"]] = matching
        if mechanism == "hall-deficiency":
            # Test data enumeration, not a production constructor or a worker input.
            for size in range(case["n"] + 1):
                for subset in itertools.combinations(range(1, case["n"] + 1), size):
                    neighbors = sorted({v for u, v in case["edges"] if u in subset})
                    if case["n"] - size + len(neighbors) == len(matching):
                        output["hall_witnesses"][case["id"]] = {"S": list(subset), "N": neighbors}
    return corpus, output, binding


@pytest.mark.parametrize("mechanism", ["augmenting-path", "hall-deficiency", "subset-dp"])
def test_finite_tables(mechanism):
    corpus, output, binding = example(mechanism)
    failures, correct = checker.assess_output(base.assess, corpus, output, binding)
    assert failures == [] and len(correct) == 37


@pytest.mark.parametrize("kind", ["wrong_task", "wrong_mechanism", "wrong_corpus", "missing_case", "extra_case", "reused_endpoint", "forbidden_edge", "bool_vertex", "extra_field"])
def test_matching_rejections(kind):
    corpus, output, binding = example()
    key = corpus["cases"][0]["id"]
    if kind.startswith("wrong_"):
        output[{"wrong_task": "task_id", "wrong_mechanism": "mechanism", "wrong_corpus": "corpus_sha256"}[kind]] = "wrong"
    elif kind == "missing_case": del output["matchings"][key]
    elif kind == "extra_case": output["matchings"]["extra"] = []
    elif kind == "reused_endpoint": output["matchings"][key] = [[1, 1], [1, 1]]
    elif kind == "forbidden_edge": output["matchings"][key] = [[9, 9]]
    elif kind == "bool_vertex": output["matchings"][key] = [[True, 1]]
    else: output["unbound"] = True
    with pytest.raises(ValueError): checker.assess_output(base.assess, corpus, output, binding)


def test_suboptimal_is_failed_not_proved():
    corpus, output, binding = example()
    output["matchings"][corpus["cases"][0]["id"]] = []
    failures, correct = checker.assess_output(base.assess, corpus, output, binding)
    assert len(failures) == 1 and len(correct) == 36


@pytest.mark.parametrize("kind", ["missing", "duplicate", "bool", "wrong_neighbor", "not_tight", "extra_field"])
def test_hall_rejections(kind):
    corpus, output, binding = example("hall-deficiency")
    key = corpus["cases"][0]["id"]
    witness = output["hall_witnesses"][key]
    if kind == "missing": del output["hall_witnesses"][key]
    elif kind == "duplicate": witness["S"] = [1, 1]
    elif kind == "bool": witness["S"] = [True]
    elif kind == "wrong_neighbor": witness.update(S=[1], N=[])
    elif kind == "not_tight":
        case = next(c for c in corpus["cases"] if c["n"] == 2 and c["m"] == 1 and len(c["edges"]) == 1)
        output["hall_witnesses"][case["id"]] = {"S": [], "N": []}
    else: witness["assume_hall"] = True
    with pytest.raises(ValueError): checker.assess_output(base.assess, corpus, output, binding)


@pytest.mark.parametrize("raw", [b'{"a":1,"a":2}', b'{"x":NaN}', b'{"x":Infinity}'])
def test_strict_json(raw):
    with pytest.raises(ValueError): checker.strict_json(raw)


@pytest.fixture
def prepared(tmp_path):
    library = tmp_path / "library"
    library.mkdir()
    skill = tmp_path / "skill.json"
    skill.write_text('{"test_only":true}', encoding="utf-8")
    root = tmp_path / "prep"
    plan = prepare(root, library, skill)
    return root, plan


def test_prepare_is_file_only_and_refuses_overwrite(prepared):
    root, plan = prepared
    assert len(plan["routes"]) == 3 and plan["corpus_cases"] == 37
    assert not list(root.rglob("*.sqlite*"))
    assert all(not r["node"]["proof_dependencies"] for r in plan["routes"])
    payloads = registration_payloads(plan, {r["mechanism"]: ["actual-selected-card"] for r in plan["routes"]}, [])
    assert not any(op["operation"] == "method.review" for op in payloads["setup_after_policy_artifact_import_and_knowledge_attach"])
    for task in payloads["tasks_after_actual_independent_reviews_and_literature_strategy_gates"]:
        assert task["timeout_seconds"] is None and "phase_budgets" not in task
        assert task["output_limit_bytes"] == 2_000_000
        assert task["codex_workflow"]["skill_manifest_artifact"] in task["inputs"]
        assert task["activity"] == "symbolic_analysis"
        assert all(x["sha256"] not in task["inputs"] for x in plan["checker_sources"])
    with pytest.raises(FileExistsError): prepare(root, plan["library"], root / "skill-manifest.json")


def bound_example(root, plan):
    corpus, output, _ = example()
    route = plan["routes"][0]
    output.update(task_id=route["task_id"], corpus_sha256=plan["corpus_sha256"])
    output_path = root / "synthetic-output.json"
    output_path.write_text(json.dumps(output), encoding="utf-8")
    config = materialize_validator(root, route["mechanism"], route["task_id"], "synthetic-validator", output_path, root / "bound")
    binding = config["binding"]
    contract = {"task_id": binding["validator_task"], "node": {**route["node"], "statement_digest": binding["statement_digest"]},
                "input_artifacts": [{"id": digest(p.read_bytes()), "path": str(p)} for p in
                                    (root / "corpus.json", output_path, root / "school_dance_validator.py")]}
    return contract, binding


def test_standalone_checker_synthetic_run(prepared):
    root, plan = prepared
    contract, binding = bound_example(root, plan)
    path = root / "input.json"
    path.write_text(json.dumps(contract), encoding="utf-8")
    result = subprocess.run([sys.executable, "-B", str(root / "bound" / "validator.py"), str(path)], cwd=root, capture_output=True)
    assert result.returncode == 0, result.stderr
    assert json.loads((root / "result.json").read_text())["passed"] is True
    # A repeated execution cannot silently overwrite its evidence.
    result2 = subprocess.run([sys.executable, "-B", str(root / "bound" / "validator.py"), str(path)], cwd=root, capture_output=True)
    assert result2.returncode != 0


@pytest.mark.parametrize("kind", ["task", "node", "digest", "domain", "missing_input", "extra_input", "bytes"])
def test_binding_rejections(prepared, kind):
    root, plan = prepared
    contract, binding = bound_example(root, plan)
    if kind == "task": contract["task_id"] = "foreign"
    elif kind == "node": contract["node"]["id"] = "foreign"
    elif kind == "digest": contract["node"]["statement_digest"] = "foreign"
    elif kind == "domain": contract["node"]["domain"] = "All graphs"
    elif kind == "missing_input": contract["input_artifacts"].pop()
    elif kind == "extra_input": contract["input_artifacts"].append(contract["input_artifacts"][0])
    else: (root / "synthetic-output.json").write_text("{}")
    with pytest.raises(ValueError): checker.verify(contract, binding)


def test_materializer_rejects_checker_drift(prepared):
    root, plan = prepared
    (root / "school_dance_validator.py").write_text("raise Exception('tampered')")
    with pytest.raises(ValueError, match="checker source changed"): bound_example(root, plan)

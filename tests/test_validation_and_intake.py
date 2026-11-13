import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from ai4math.adapters import import_coprime, tree_manifest
from ai4math.execution import run_task
from ai4math.store import ResearchError, file_hash
from ai4math.workbench import Workbench


@pytest.fixture
def scenario(tmp_path):
    wb = Workbench.create(tmp_path / "ws", "validation-tests", "Validation binding tests")
    n = wb.apply("node.add", {"id": "goal", "statement": "Exact universal statement", "domain": "all positive n", "quantifier": "universal"})
    wb.apply("goal.set", {"node": "goal"})
    wb.apply("route.add", {"id": "route", "node": "goal", "rationale": "independent checker test", "next_question": "certificate?",
             "stop_condition": "no progress", "max_runs": 4, "stagnation_limit": 2})
    wb.apply("route.status", {"id": "route", "status": "active", "reason": "run bounded check"})
    witness = tmp_path / "witness.json"
    witness.write_text('{"candidate": 1}', encoding="utf-8")
    a = wb.apply("artifact.import", {"path": str(witness), "purpose": "test witness"})["id"]
    wb.apply("claim.add", {"id": "claim", "node": "goal", "statement_digest": n["statement_digest"],
        "evidence_level": "proved", "scope": "universal", "artifact_ids": [a], "summary": "synthetic protocol test"}, "author")
    yield wb, a, tmp_path
    wb.store.close()


def run_checker(wb, a, folder, update, method="formal_kernel", bind_inputs=True):
    script = folder / "checker.py"
    script.write_text("import json,sys\nfrom pathlib import Path\nc=json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))\n"
        "r=dict(c['result_contract'])\nr.update(method=" + repr(method) + ", scope='universal', evidence_level='proved', "
        "checks=['test certificate'],limitations=['synthetic fixture'])\nr.update(" + repr(update) + ")\n"
        "Path('result.json').write_text(json.dumps(r), encoding='utf-8')\n", encoding="utf-8")
    checker = wb.apply("artifact.import", {"path": str(script), "purpose": "frozen protocol fixture"})["id"]
    wb.apply("task.add", {"id": "check", "route": "route", "role": "validate", "argv": [sys.executable, "{validator}", "{input}"],
        "validator_artifact": checker, "inputs": [a] if bind_inputs else [], "timeout_seconds": 3, "output_limit_bytes": 10000}, "checker")
    return run_task(wb, "check", "checker")


@pytest.mark.parametrize("change", [
    {"passed": False}, {"passed": 1}, {"task_id": "another-task"}, {"statement_digest": "0"*64},
    {"input_artifacts": []}, {"scope": "finite"}, {"checks": []}, {"limitations": []},
])
def test_invalid_result_binding_is_rejected(scenario, change):
    wb, a, folder = scenario
    assert run_checker(wb, a, folder, change)["status"] == "completed"
    with pytest.raises(ResearchError):
        wb.apply("validation.add", {"id": "v", "claim": "claim", "method": "formal_kernel", "task": "check", "trust_basis": "test checker only"}, "validator")
    assert wb.store.get("claim", "claim")["status"] == "proposed"


def test_finite_method_cannot_certify_universal_scope(scenario):
    wb, a, folder = scenario
    run_checker(wb, a, folder, {}, method="finite_certificate")
    with pytest.raises(ResearchError, match="finite checks"):
        wb.apply("validation.add", {"id": "v", "claim": "claim", "method": "finite_certificate", "task": "check", "trust_basis": "finite scan"}, "validator")


def test_unbound_witness_is_not_validated(scenario):
    wb, a, folder = scenario
    run_checker(wb, a, folder, {}, bind_inputs=False)
    with pytest.raises(ResearchError, match="bind all"):
        wb.apply("validation.add", {"id": "v", "claim": "claim", "method": "formal_kernel", "task": "check", "trust_basis": "test"}, "validator")


def test_author_cannot_self_validate(scenario):
    wb, a, folder = scenario
    with pytest.raises(ResearchError, match="differ"):
        wb.apply("validation.add", {"id": "v", "claim": "claim", "method": "human_proof", "trust_basis": "I agree"}, "author")


def fake_legacy(tmp_path, monkeypatch, missing_phase=None):
    source = tmp_path / "target"
    (source / "data/imports").mkdir(parents=True)
    (source / "data/imports/raw.dat").write_bytes(b"original\x00\xff\r\n")
    claims = source / ".ai4math/claims"
    claims.mkdir(parents=True)
    for phase in (248, 249, 250, 251):
        if phase != missing_phase:
            (claims / f"{phase}.json").write_text(json.dumps({"phase": phase, "evidence_level": "finite_support"}), encoding="utf-8")
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"project_id": "coprime-path4", "root": str(source), "phase_root": "data/verification",
        "original_root": "data/imports", "notes_root": "notes", "control_root": ".ai4math", "original_manifest": ".ai4math/original_manifest.json"}), encoding="utf-8")
    monkeypatch.setattr("ai4math.adapters.check_phase", lambda *args: SimpleNamespace(to_dict=lambda: {"passed": True, "warnings": ["synthetic test gate"]}))
    return source, config


def test_atomic_intake_preserves_target_and_both_research_goals(tmp_path, monkeypatch):
    source, config = fake_legacy(tmp_path, monkeypatch)
    before = tree_manifest(source)
    wb = Workbench.create(tmp_path / "framework", "coprime-path4", "Historical intake")
    try:
        result = import_coprime(wb, config)
        assert result["goal"] == "all-n" and result["original_bytes_unchanged"]
        assert tree_manifest(source) == before
        assert wb.cone() == {"all-n", "all-k", "remaining-orders"}
        assert wb.board()["goal_evidence_level"] == "unknown"
        assert all(c["evidence_level"] == "finite_support" for c in wb.store.all("claim"))
        assert all(r["status"] == "proposed" for r in wb.store.all("route"))
        assert wb.audit()["passed"] and wb.audit()["events"] == 2
        assert not wb.delivery_check()["passed"]
        with pytest.raises(ResearchError, match="empty"):
            import_coprime(wb, config)
    finally:
        wb.store.close()


def test_late_intake_failure_rolls_back_all_records(tmp_path, monkeypatch):
    source, config = fake_legacy(tmp_path, monkeypatch, missing_phase=251)
    before = tree_manifest(source)
    wb = Workbench.create(tmp_path / "framework", "coprime-path4", "Historical intake")
    try:
        with pytest.raises(ResearchError, match="phase 251"):
            import_coprime(wb, config)
        assert wb.store.all("node") == [] and wb.store.all("claim") == []
        assert wb.store.all("artifact") == []
        assert wb.audit()["passed"] and wb.audit()["events"] == 1
        assert tree_manifest(source) == before
    finally:
        wb.store.close()

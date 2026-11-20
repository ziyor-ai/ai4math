import json
import sys

import pytest

from ai4math.execution import export_task, run_task
from ai4math.store import ResearchError
from ai4math.validation_protocol import consume
from ai4math.workbench import Workbench


@pytest.fixture
def workspace(tmp_path):
    workbench = Workbench.create(tmp_path / "ws", "receipts", "Validation protocol", review_interval=99)
    workbench.apply("node.add", {"id": "goal", "statement": "Every positive integer is even",
                                "domain": "positive integers", "quantifier": "universal"})
    workbench.apply("goal.set", {"node": "goal"})
    workbench.apply("route.add", {"id": "route", "node": "goal", "kind": "proof", "rationale": "direct test",
                                 "next_question": "Does one refute parity?", "stop_condition": "witness verified",
                                 "max_runs": 12, "stagnation_limit": 12})
    workbench.apply("route.status", {"id": "route", "status": "active", "reason": "run independent check"})
    workbench.apply("approach.add", {"id": "method", "node": "goal", "approach": "exhibit a particular integer",
                                    "family": "parity", "bridge_to_goal": {"mode": "entailment",
                                    "statement": "A positive odd integer refutes the universal statement"}}, "author")
    workbench.apply("approach.review", {"id": "method", "verdict": "selected", "reason": "decisive witness"},
                    "reviewer")
    workbench.apply("approach.activate", {"id": "method"}, "coordinator")
    try:
        yield workbench
    finally:
        workbench.store.close()


def artifact(workbench, name, content):
    path = workbench.store.root / name
    path.write_text(content, encoding="utf-8")
    return workbench.apply("artifact.import", {"path": str(path), "purpose": name})["id"]


def intent(workbench, task):
    return workbench.apply("research_intent.add",
                           {"id": task + "-intent", "approach": "method", "kind": "verification",
                            "question": "Check frozen witness", "connection_to_goal": "Check evidence for goal",
                            "decisive": False, "stop_condition": "Record one independent validation result"},
                           "checker")["id"]


def claim_task(workbench, *, source="", request=None, task="validate", timeout_seconds=3):
    claim_artifact = artifact(workbench, "witness.json", '{"n": 1}')
    node = workbench.store.get("node", "goal")
    if not workbench.store.exists("claim", "claim"):
        workbench.apply("claim.add", {"id": "claim", "node": "goal", "statement_digest": node["statement_digest"],
                                      "evidence_level": "disproved", "scope": "universal", "proof_route": "direct",
                                      "artifact_ids": [claim_artifact], "summary": "One is odd"}, "author")
    script = artifact(workbench, task + ".py", source)
    return workbench.apply("task.add", {
        "id": task, "route": "route", "role": "validate", "intent": intent(workbench, task),
        "argv": [sys.executable, "{validator}", "{input}"], "inputs": [claim_artifact], "validator_artifact": script,
        "validation_request": request if request is not None else {"purpose": "claim", "claim": "claim",
                                                                   "method": "counterexample"},
        "timeout_seconds": timeout_seconds}, "checker")


CHECKER = """import json
import sys
from pathlib import Path
contract_path = Path(sys.argv[1])
contract = json.loads(contract_path.read_text(encoding="utf-8"))
receipt = dict(contract["result_contract"])
receipt.update(passed=True, checks=["n=1 is positive and odd"], limitations=["Single counterexample"])
"""


@pytest.mark.parametrize(("script", "protocol", "verdict", "raw"), [
    ("print('completed without result')", "rejected", "unavailable", False),
    ("Path('result.json').write_text('oops', encoding='utf-8')", "rejected", "unavailable", True),
    ("receipt['statement_digest']='0'*64", "rejected", "unavailable", True),
    ("receipt['passed']=1", "rejected", "unavailable", True),
    ("receipt['validation_request_sha256']='0'*64", "rejected", "unavailable", True),
    ("receipt['passed']=False", "valid", "failed", True),
    ("contract_path.write_text('{}', encoding='utf-8')", "rejected", "unavailable", True),
    ("", "valid", "passed", True),
])
def test_validator_admission_separates_process_protocol_and_verdict(workspace, script, protocol, verdict, raw):
    workspace.apply("policy.migrate", {"to": "v2c4"}, "admin")
    source = CHECKER + script + "\n"
    if "completed without result" not in script and "write_text('oops'" not in script:
        source += 'Path("result.json").write_text(json.dumps(receipt), encoding="utf-8")\n'
    task = claim_task(workspace, source=source)
    contract = export_task(workspace, task["id"])["contract"]
    assert contract["validation_request"] == task["validation_request"]
    assert contract["result_contract"]["validation_request_sha256"] == task["validation_request"]["sha256"]
    result = run_task(workspace, task["id"], "checker")
    outcome = result["validation_outcome"]
    assert result["status"] == outcome["process_status"] == "completed"
    assert result["exit_code"] == 0
    assert outcome["protocol_status"] == protocol
    assert outcome["verdict"] == verdict
    assert ("raw_artifact" in outcome) is raw
    if raw:
        assert workspace.store.verify_blob(outcome["raw_artifact"]).is_file()
    assert ("result_artifact" in result) is (protocol == "valid")


def test_claim_cannot_skip_frozen_validation(workspace):
    workspace.apply("policy.migrate", {"to": "v2c4"}, "admin")
    task = claim_task(workspace, source=CHECKER +
                      'Path("result.json").write_text(json.dumps(receipt), encoding="utf-8")')
    with pytest.raises(ResearchError, match="differ from claim author"):
        run_task(workspace, task["id"], "author")
    result = run_task(workspace, task["id"], "checker")
    assert result["validation_outcome"]["verdict"] == "passed"
    validation = workspace.apply("validation.add", {"id": "verified", "claim": "claim", "method": "counterexample",
                                                    "task": task["id"], "trust_basis": "independent explicit arithmetic"},
                                 "registrar")
    assert validation["artifact_ids"] == [result["result_artifact"]]
    assert validation["validation_request_sha256"] == task["validation_request"]["sha256"]
    workspace.apply("review.add", {"id": "review", "claim": "claim", "reviewer": "reviewer",
                                   "reviewer_kind": "model", "auditor": "auditor", "coverage_complete": True,
                                   "issues": [], "verdict": "ready_for_human"}, "auditor")
    accepted = workspace.apply("claim.accept", {"claim": "claim", "review": "review",
                                               "validation": validation["id"]}, "custodian")
    assert accepted["status"] == "accepted"
    assert workspace.node_status("goal") == "refuted"


def test_timeout_keeps_result_unavailable(workspace):
    workspace.apply("policy.migrate", {"to": "v2c4"}, "admin")
    task = claim_task(workspace, source="import time\ntime.sleep(30)\n", timeout_seconds=1)
    outcome = run_task(workspace, task["id"], "checker")["validation_outcome"]
    assert (outcome["process_status"], outcome["protocol_status"], outcome["verdict"]) == (
        "unknown_timeout", "not_run", "unavailable")


def test_finite_replay_needs_actual_producer_output(workspace):
    frozen_input = artifact(workspace, "candidate.json", '{"n": 1}')
    workspace.apply("task.add", {"id": "producer", "route": "route", "role": "produce",
                                 "argv": [sys.executable, "-c", "print(1)"], "inputs": [frozen_input]}, "author")
    producer = run_task(workspace, "producer", "producer-runner")
    actual = producer["output_artifacts"][0]
    workspace.apply("policy.migrate", {"to": "v2c4"}, "admin")
    validator = artifact(workspace, "checker.py", CHECKER +
                         'Path("result.json").write_text(json.dumps(receipt), encoding="utf-8")')
    definition = {"id": "replay", "route": "route", "role": "validate", "intent": intent(workspace, "replay"),
                  "argv": [sys.executable, "{validator}", "{input}"], "inputs": [frozen_input, actual],
                  "validator_artifact": validator, "validation_request": {"purpose": "finite_replay",
                  "method": "independent parity", "producer_task": "producer", "producer_artifacts": [frozen_input],
                  "cases": ["n=1"]}}
    with pytest.raises(ResearchError, match="exact frozen producer outputs"):
        workspace.apply("task.add", definition, "checker")
    definition["validation_request"]["producer_artifacts"] = [actual]
    workspace.apply("task.add", definition, "checker")
    with pytest.raises(ResearchError, match="differ from producer"):
        run_task(workspace, "replay", "producer-runner")
    result = run_task(workspace, "replay", "checker")
    payload = consume(workspace, result, "finite_replay", passed_required=True)
    assert payload["producer"]["artifacts"] == [actual]
    assert payload["cases"] == ["n=1"]
    assert workspace.node_status("goal") == "open"

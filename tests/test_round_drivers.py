"""Offline driver combinations exercise real sealing, handoffs and settlement."""

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sys
from time import monotonic, sleep

import pytest

from ai4math import codex_skills, identities, obligation, review
from ai4math.cli import main
from ai4math.codex_live import read_json
from ai4math.codex_runner import runtime_hashes
from ai4math.execution import export_task, run_task
from ai4math.external_supervisor import supervise_external
from ai4math.knowledge import AcademicLibrary
from ai4math.store import ResearchError, digest, file_hash
from ai4math.workbench import Workbench
from fake_worker_protocol import literature
from test_codex_monitored_contract import setup
from test_research_flow_v2c4 import choose, node, route
from test_round_lifecycle import finish_managed_round, managed_completion
from test_strategy import coordinator_literature

FAKE_CODEX = Path(__file__).with_name("fake_codex_round_agent.py").resolve()


@pytest.fixture
def prepared_round(tmp_path):
    w, payload = setup(tmp_path, timeout=60)
    library_path = tmp_path / "library"
    library = AcademicLibrary.create(library_path)
    library.store.close()
    try:
        w.apply("round.open", {"id": "r1", "route": payload["route"]})
        payload.update(round="r1", output_limit_bytes=None)
        payload["codex_workflow"]["source_roots"] = [str(library_path)]
        yield w, payload, library_path
    finally:
        w.store.close()


def release(w, receipt):
    w.apply("task.release_exploration", {"task": "managed", "worker_receipt_sha256": digest(receipt),
                                        "assessment": "Reviewed the scoped offline research fixture"})


def release_when_ready(root):
    coordinator = Workbench(root)
    try:
        cutoff = monotonic() + 25
        while monotonic() < cutoff:
            if coordinator.store.exists("worker_literature", "managed"):
                release(coordinator, coordinator.store.get("worker_literature", "managed"))
                return
            if coordinator.store.get("task", "managed")["status"] in {"failed", "interrupted", "unknown_timeout"}:
                raise AssertionError("offline Codex task ended before literature review")
            sleep(0.03)
        raise AssertionError("offline Codex task did not produce a literature receipt")
    finally:
        coordinator.store.close()


def test_supervised_codex_driver_seals_and_settles_prebound_round(prepared_round):
    w, payload, library_path = prepared_round
    w.apply("task.add", payload)
    folder = Path(export_task(w, "managed")["directory"])
    (folder / "codex-preflight.json").write_text(json.dumps({
        "runtime_modules": runtime_hashes(), "codex_sha256": file_hash(FAKE_CODEX),
        "python_sha256": file_hash(sys.executable), "input_sha256": file_hash(folder / "input.json"),
        "runtime_sha256": file_hash(FAKE_CODEX),
    }), encoding="utf-8")
    argv = [sys.executable, "-X", "utf8", "-B", str(FAKE_CODEX), "--worker",
            str(w.store.root), "managed", str(library_path)]
    with ThreadPoolExecutor(max_workers=1) as pool:
        coordinator = pool.submit(release_when_ready, w.store.root)
        task = supervise_external(w, "managed", argv, managed_codex=True)
        stderr = (folder / "supervisor.stderr.log").read_text(encoding="utf-8")
        assert task["external_stage"] == "sealed", stderr
        coordinator.result(timeout=2)
    capture = read_json(folder / "live/capture-receipt.json")
    assert capture["thread_id"] == "offline-thread" and capture["tool_calls"] > 0
    assert w.store.get("external_stop", "managed")["evidence"].startswith("Owned process containment terminated")
    finish_managed_round(w)


def record_subagent_reviews(w, output_id):
    reviews = {
        "adversarial": {"failures": [], "strongest_unchecked_assumption": "Fixed offline fixture scope"},
        "verify": {"your_numbers": {"marker_count": 1}, "their_numbers": {"marker_count": 1},
                   "relative_error": 0, "note": "Replay of a deterministic fixture only"},
    }
    for role, payload in reviews.items():
        actor = f"offline-{role}"
        with w.store.transaction("identity.enroll", "human"):
            identities.enroll(w, actor, "model", provider="offline-fixture", model="scripted", actor="human")
        with w.store.transaction("review.record", actor):
            review.record(w, "managed", role, "pass", output_id, "Offline regression receipt", actor, payload)


@pytest.fixture
def native_round(tmp_path):
    w = Workbench.create(tmp_path / "native", "host-fixture", "Native worker lifecycle", review_interval=99)
    library = AcademicLibrary.create(tmp_path / "library")
    library_path = library.store.root
    library.store.close()
    try:
        w.apply("policy.migrate", {"to": "v2c4"}, "admin")
        node(w, "goal")
        w.apply("goal.set", {"node": "goal"})
        route(w, "native-route")
        choose(w, "native-direction", "native-route")
        coordinator_literature(w, "native-direction", "native-route")
        w.apply("approach.add", {"id": "approach", "node": "goal", "approach": "Check one bounded witness",
                                "family": "fixture", "bridge_to_goal": {"mode": "sufficient_condition"}}, "author")
        w.apply("approach.review", {"id": "approach", "verdict": "selected", "reason": "Synthetic exercise"}, "reviewer")
        w.apply("approach.activate", {"id": "approach"})
        w.apply("round.open", {"id": "r1", "route": "native-route"})
        yield w, library_path
    finally:
        w.store.close()


def register_intent(w, identifier, kind="exploration", actor="human"):
    w.apply("research_intent.add", {"id": identifier, "approach": "approach", "kind": kind,
                                   "question": "Can the exact fixture be checked?",
                                   "connection_to_goal": "This checks one case on goal",
                                   "decisive": False, "stop_condition": "One fixture"}, actor)


CHECK_FROZEN_MARKER = """
import json
from pathlib import Path
import sys

contract = Path(sys.argv[1])
result = json.loads(contract.read_text(encoding="utf-8"))["result_contract"]
artifact, = result["producer"]["artifacts"]
frozen = contract.parents[2] / "objects" / artifact[:2] / artifact
marker = frozen.read_text(encoding="utf-8").strip()
result.update(passed=marker == "Offline fixture", checks=["Exact marker equality"],
              limitations=["Only the registered fixture"])
Path("result.json").write_text(json.dumps(result), encoding="utf-8")
"""


@pytest.mark.parametrize("host", ["codex-cli", "claude-code"])
def test_native_host_cli_seals_and_settles_prebound_round(host, native_round, tmp_path, monkeypatch, capsys):
    w, library_path = native_round
    manifest = codex_skills.skill_manifest(check_installed=False)
    source_root = codex_skills.REPOSITORY_SKILLS.parents[1]
    skill_names = manifest["skills"]
    if host == "claude-code":
        host_skills = source_root / ".claude" / "skills"
        for name in skill_names:
            wrapper = (host_skills / name / "SKILL.md").read_text(encoding="utf-8")
            assert f".agents/skills/{name}/SKILL.md" in wrapper
    skill_path = tmp_path / "fixture-skill-manifest.json"
    skill_path.write_text(json.dumps(manifest), encoding="utf-8")
    skill = w.apply("artifact.import", {"path": str(skill_path), "purpose": "frozen skill manifest"})["id"]
    context_path = tmp_path / "fixture-context.json"
    context_path.write_text(json.dumps({"scope": "standard output of the deterministic fixture",
        "cases": [{"id": i, "purpose": "Exercise the worker envelope without establishing a claim"}
                  for i in range(12)], "pagination_fixture": "z" * 26000}), encoding="utf-8")
    context = w.apply("artifact.import", {"path": str(context_path), "purpose": "research context bundle"})["id"]
    register_intent(w, "produce-intent")
    w.apply("task.add", {"id": "managed", "route": "native-route", "role": "explore", "round": "r1",
                         "intent": "produce-intent", "argv": [sys.executable, "-c", "pass"],
                         "inputs": [skill, context], "worker_driver": "subagent", "external_supervision": "process",
                         "codex_workflow": {"skill_manifest_artifact": skill, "worker_actor": "worker",
                                            "source_roots": [str(library_path)]}})
    monkeypatch.setattr(codex_skills, "_root", lambda destination: tmp_path / "absent-user-skills")

    def cli(*args):
        assert main(["--workspace", str(w.store.root), "--actor", "human", *args]) == 0
        return json.loads(capsys.readouterr().out)

    prepared = cli("dispatch-subagent", "--task", "managed", "--library", str(library_path), "--host", host)
    worker_skill = Path(prepared["directory"]) / ".agents" / "skills" / "ai4math-research" / "SKILL.md"
    assert file_hash(worker_skill) == manifest["skills"]["ai4math-research"]["SKILL.md"]

    def call(action, **payload):
        return cli("worker-task", "--task", "managed", "--library", str(library_path),
                   "--action", action, "--payload", json.dumps(payload))

    contract = literature(call)
    assert "progress_derivation" in contract["research_continuation"]
    tools = Path(prepared["directory"]) / "live" / "tools"
    assert any(
        arguments["action"] == "read" and arguments["payload"].get("artifact_id") == context
        and arguments["payload"].get("offset", 0) > 0
        for request in tools.glob("*/request.json")
        for arguments in [read_json(request)["arguments"]]
    )
    release(w, w.store.get("worker_literature", "managed"))
    probe = call("run", argv=["python", "-c", "print('Offline fixture')"], output_path="probe.txt")
    call("probe_review", first_probe={"test": "Read marker", "outcome": "Observed", "scope": "One fixture",
                                     "artifacts": ["probe.txt"]}, target_disposition="candidate_answer",
         target_relation="Marker checks one case on the selected obligation", next_attempt=None)
    handoff = {"summary": "One bounded observation", "scope": "Fixture only", "open_obligations": [],
               "suggested_evidence_level": "finite_support", "next_step": "Independent replay",
               "stop_reason": "candidate_answer", "follow_up": None, "source_checks": []}
    assert call("handoff", paths=["probe.txt"], return_record=handoff)["prepared"]
    host_run = f"offline-{host}-child"
    sealed = cli("seal-subagent", "--task", "managed", "--host-run-id", host_run)
    capture = read_json(Path(prepared["directory"]) / "live/capture-receipt.json")
    assert capture["driver"] == "subagent" and capture["executions"][0]["exit_code"] == 0
    assert capture["host_provenance"]["host"] == host
    assert capture["host_provenance"]["run_id"] == host_run
    w.apply("task.confirm_stop", {"task": "managed", "confirmed": True,
                                  "evidence": "In-process fixture returned and its probe subprocess exited"})
    with pytest.raises(ResearchError, match="both the adversarial"):
        w.apply("task.finish_external", managed_completion(w))
    record_subagent_reviews(w, sealed["receipt"]["output_artifacts"][0])
    assert w.apply("task.finish_external", managed_completion(w))["status"] == "completed"
    assert probe["sha256"] in sealed["receipt"]["output_artifacts"]

    validator_path = tmp_path / "checker.py"
    validator_path.write_text(CHECK_FROZEN_MARKER, encoding="utf-8")
    validator = w.apply("artifact.import", {"path": str(validator_path), "purpose": "independent checker"},
                        "checker")["id"]
    register_intent(w, "check-intent", kind="verification", actor="checker")
    w.apply("task.add", {"id": "replay", "route": "native-route", "role": "validate", "round": "r1",
                         "intent": "check-intent", "argv": [sys.executable, "{validator}", "{input}"],
                         "validator_artifact": validator, "inputs": [probe["sha256"]],
                         "validation_request": {"purpose": "finite_replay", "method": "exact marker",
                                                "producer_task": "managed", "producer_artifacts": [probe["sha256"]],
                                                "cases": ["marker-1"]}}, "checker")
    outcome = run_task(w, "replay", "checker")["validation_outcome"]
    assert (outcome["process_status"], outcome["protocol_status"], outcome["verdict"]) == (
        "completed", "valid", "passed")
    w.apply("round.assess", {"round": "r1", "finding": "The finite marker was independently reproduced",
                            "next_question": "Check the general obligation",
                            "task_assessments": [
                                {"task_id": "managed", "execution_status": "completed",
                                 "scientific_outcome": "inconclusive"},
                                {"task_id": "replay", "execution_status": "completed",
                                 "scientific_outcome": "supporting_evidence",
                                 "evidence_refs": [outcome["result_artifact"]]}]})
    settled = w.apply("round.settle", {"round": "r1"})
    assert w.apply("round.settle", {"round": "r1"})["id"] == settled["id"]
    balance = obligation.view(w, "goal")
    assert balance["runs_by_role"] == {"explore": 1, "validate": 1}
    assert (balance["rounds"], balance["closures"], balance["rounds_since_closure"]) == (1, 0, 1)
    assert w.node_status("goal") == "open" and w.audit()["passed"]

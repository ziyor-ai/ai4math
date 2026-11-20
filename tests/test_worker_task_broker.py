"""A project Skill can reconnect to a frozen task on each CLI tool call."""

import json
import subprocess
import sys

import pytest

from ai4math.codex_live import atomic_json, read_json
from ai4math.knowledge import AcademicLibrary
from ai4math.mcp_task import BROKER_STATE, TaskServer
from ai4math.research_return import DEDUCTION_POLICY
from ai4math.store import ResearchError
from ai4math.worker_finish import seal_subagent
from test_codex_monitored_contract import dispatch, setup


def release(w, receipt):
    w.apply("task.release_exploration", {
        "task": "managed", "worker_receipt_sha256": receipt["worker_receipt_sha256"],
        "assessment": "Synthetic source and exact probe scope were reviewed.",
    })


def test_reconnected_cli_reads_pages_and_completes_v2_handoff(tmp_path):
    w, payload = setup(tmp_path)
    library = AcademicLibrary.create(tmp_path / "library")
    try:
        url = "https://example.org/synthetic-matching"
        source = library.add_source({"title": "Synthetic matching", "url": url, "kind": "paper",
            "version": "v1", "locator": "Section 1", "summary": "Finite synthetic example",
            "limitations": "Exact small fixture", "tags": ["matching"]})
        exact = tmp_path / "example.txt"
        exact.write_text("The Hall obstruction here is the deficient vertex subset.", encoding="utf-8")
        document = library.add_document(source["id"], exact)
        long_input = tmp_path / "long.txt"
        long_input.write_text("bounded exact fixture\n" * 960, encoding="utf-8")
        artifact = w.apply("artifact.import", {"path": str(long_input), "purpose": "fixture input"})["id"]
        payload["inputs"].append(artifact)
        payload.update(output_limit_bytes=8000, worker_driver="subagent")
        payload["codex_workflow"].update(source_roots=[str(library.store.root)],
                                         research_stop_policy=DEDUCTION_POLICY)
        w.apply("task.add", payload)
        dispatch(w)
        argv = [sys.executable, "-X", "utf8", "-B", "-m", "ai4math", "--workspace", str(w.store.root),
                "worker-task", "--task", "managed", "--library", str(library.store.root)]

        def call(action, *, fails=None, **args):
            proc = subprocess.run([*argv, "--action", action, "--payload", json.dumps(args)],
                                  text=True, encoding="utf-8", capture_output=True, timeout=30)
            if fails:
                assert proc.returncode != 0 and fails in proc.stderr
                return None
            assert proc.returncode == 0, proc.stderr
            return json.loads(proc.stdout)

        contract = call("contract")
        assert "progress_derivation" in contract["research_continuation"]
        for aid in w.store.get("task", "managed")["inputs"]:
            offset, chunks = 0, []
            while offset is not None:
                page = call("read", artifact_id=aid, offset=offset)
                chunks.append(page["text"])
                offset = page["next_offset"]
            assert "".join(chunks) == w.store.verify_blob(aid).read_bytes().decode("utf-8-sig")
        call("search", query="Hall obstruction")
        initial = call("reuse_passage", passage_id=document["passages"][0])
        assert call("reuse_passage", passage_id=document["passages"][0]) == initial
        receipt = call("literature_ready", sources=[{
            "artifact_id": initial["artifact_id"], "url": url, "locator": initial["locator"],
            "finding": "Check the deficient subset in the synthetic examples.",
        }], limitations="Finite fixture.", exploration_plan="Distinguish the synthetic pair.")
        release(w, receipt)
        call("run", argv=["python", "-c", "print('probe')"], output_path="probe.txt")
        call("probe_review", first_probe={"test": "Read fixture", "outcome": "Limited",
            "scope": "Small example", "artifacts": ["probe.txt"]},
            target_disposition="limited", target_relation="Only one configuration observed.",
            next_attempt={"kind": "deduction", "mechanism": "Certificate comparison",
                "test": "Check a distinct certificate", "different_from_probe": "Compare hypotheses"})
        call("run", fails="actual inference", argv=["python", "-c", "print('premature')"])
        call("progress", summary="The first case checked one configuration.", bridge="Separate the cases.",
             next_step="Check the other certificate.", derivation={"gap": "Other configuration",
                 "deduction": "Obstruction reduces to the deficient subset.",
                 "next_decision": "Check only its counterpart."})
        follow = call("run", argv=["python", "-c", "print('follow-up')"], output_path="follow.txt",
                      decision="The counterpart could invalidate the claimed extension.")
        assert follow["exit_code"] == 0
        record = {"summary": "Compared two frozen cases.", "scope": "Two cases",
            "suggested_evidence_level": "finite_support", "open_obligations": ["General case"],
            "next_step": "Reduce the general case.", "stop_reason": "blocked",
            "follow_up": {"result": "The counterpart supports the restricted comparison.",
                          "scope": "One counterpart", "artifacts": ["follow.txt"]},
            "source_checks": [{"theorem": "Synthetic obstruction", "use_point": "Case comparison",
                "assumptions": {"finite": "Only frozen finite cases."}, "passage_id": initial["artifact_id"],
                "limitation": "No universal inference."}]}
        assert call("handoff", paths=["probe.txt", "follow.txt"], return_record=record)["prepared"]
        task_dir = w.store.root / "tasks" / "managed"
        handoff = read_json(task_dir / "return" / "return.json")
        assert handoff["source_passages"][initial["artifact_id"]]["raw_artifact"] == document["raw_artifact"]
        assert handoff["follow_up_written_after_review"] == "follow.txt"
        state = read_json(task_dir / "live" / BROKER_STATE)
        assert state["worker"]["source_bytes"] == initial["bytes"]
        assert state["worker"]["tool_bytes"] > follow["bytes"]
        assert state["in_flight"] is None
        call("contract", fails="handoff already frozen")
        sealed = seal_subagent(w.store.root, "managed", "human")
        assert sealed["external_stage"] == "sealed"
        assert seal_subagent(w.store.root, "managed", "human") == sealed
    finally:
        library.store.close()
        w.store.close()


def test_reconnection_preserves_failures_and_does_not_repeat_interrupted_action(tmp_path):
    w, payload = setup(tmp_path)
    library = AcademicLibrary.create(tmp_path / "library")
    try:
        payload["codex_workflow"]["source_roots"] = [str(library.store.root)]
        w.apply("task.add", payload)
        dispatch(w)

        def call(action, **args):
            return json.loads(TaskServer(w.store.root, "managed", library.store.root).execute(
                {"action": action, "payload": args}))

        call("contract")
        for aid in w.store.get("task", "managed")["inputs"]:
            call("read", artifact_id=aid)
        call("search", query="empty fixture query")
        release(w, call("literature_ready", sources=[], limitations="Synthetic data",
                         exploration_plan="Compare finite output."))
        call("run", argv=["python", "-c", "print('a' * 1200)"])
        with pytest.raises(ResearchError, match="output limit exceeded"):
            call("run", argv=["python", "-c", "print('b' * 1200)"])
        live = w.store.root / "tasks" / "managed" / "live"
        state = read_json(live / BROKER_STATE)
        failed_capture = live / "tools" / f"{state['next_call'] - 1:05d}"
        assert (failed_capture / "output.raw").stat().st_size < 1200
        assert read_json(failed_capture / "result.json")["success"] is False
        assert state["worker"]["tool_bytes"] > payload["output_limit_bytes"]
        with pytest.raises(ResearchError, match="output limit exhausted"):
            call("run", argv=["python", "-c", "print('never started')"])
        state = read_json(live / BROKER_STATE)
        atomic_json(live / BROKER_STATE, {**state, "in_flight": state["next_call"]})
        with pytest.raises(ResearchError, match="action was interrupted"):
            call("contract")
    finally:
        library.store.close()
        w.store.close()


def test_worker_rejects_other_library_and_changed_local_contract(tmp_path):
    w, payload = setup(tmp_path)
    library = AcademicLibrary.create(tmp_path / "library")
    try:
        payload["codex_workflow"]["source_roots"] = [str(library.store.root)]
        w.apply("task.add", payload)
        dispatch(w)
        args = {"action": "contract", "payload": {}}
        invalid = TaskServer(w.store.root, "managed", tmp_path)
        with pytest.raises(ResearchError, match="outside the frozen"):
            invalid.execute(args)
        server = TaskServer(w.store.root, "managed", library.store.root)
        server.execute(args)
        (server.directory / "input.json").write_text("{}", encoding="utf-8")
        with pytest.raises(ResearchError, match="differs from its frozen artifact"):
            server.execute(args)
    finally:
        library.store.close()
        w.store.close()

"""Regression coverage for live observations, phase routing, and steering."""

from threading import Event
from types import SimpleNamespace

import pytest

from ai4math.codex_live import EventLog, control_request, event_rows, queue_view, read_json
from ai4math.codex_runner import ResearchSession
from ai4math.codex_worker_tools import WorkerTools
from ai4math.knowledge import AcademicLibrary
from ai4math.store import ResearchError, file_hash
from test_codex_monitored_contract import dispatch, release, setup


def test_active_progress_is_readable_before_turn_completes(tmp_path):
    w, payload = setup(tmp_path)
    try:
        w.apply("task.add", payload)
        dispatch(w)
        directory = w.store.root / "tasks/managed/live"
        log = EventLog(directory, "managed")
        log.thread_id, log.turn_id = "thread-test", "turn-test"
        log.emit("turn_started")
        progress = log.emit("progress", summary="first hypothesis", next_step="probe", bridge="same target")
        log.heartbeat(alive=True, current_tool=None)
        with log.path.open("ab") as stream:
            stream.write(b"{\"partial\":")
        observed = queue_view(w.store.root, {"managed": 1})
        assert observed["events"] == [progress]
        assert observed["tasks"][0]["status"] == "running"
        assert observed["cursor"] == {"managed": 2}
        assert queue_view(w.store.root, observed["cursor"])["events"] == []
    finally:
        w.store.close()


def test_steer_binds_active_turn_and_records_server_acceptance(tmp_path):
    w, payload = setup(tmp_path)
    try:
        w.apply("task.add", payload)
        dispatch(w)
        log = EventLog(w.store.root / "tasks/managed/live", "managed")
        log.thread_id, log.turn_id = "thread-test", "turn-test"
        event = log.emit("progress", summary="probe in flight")
        with pytest.raises(ResearchError, match="triggering event"):
            control_request(w, "managed", "human", "steer", turn="old", event=event["seq"], text="check the bound")
        with pytest.raises(ResearchError, match="dispatching actor"):
            control_request(w, "managed", "worker", "steer", turn="turn-test", event=event["seq"], text="check the bound")
        request = control_request(w, "managed", "human", "steer", turn="turn-test", event=event["seq"], text="check the bound")
        session = ResearchSession(None, log, SimpleNamespace(root=w.store.root, tid="managed"), None)
        calls = []

        def rpc(method, params):
            calls.append((method, params))
            return {"turnId": "turn-test"}

        session.rpc = rpc
        session.controls()
        session.controls()
        assert len(calls) == 1
        assert calls[0][0] == "turn/steer"
        assert calls[0][1]["expectedTurnId"] == "turn-test"
        assert request["id"] in calls[0][1]["input"][0]["text"]
        assert read_json(log.directory / "responses" / f"{request['id']}.json")["accepted"]
        assert [row["kind"] for row in event_rows(log.path)][-1] == "steer_accepted"
        session.pool.shutdown()
    finally:
        w.store.close()


def test_phase_guard_blocks_research_before_release(tmp_path):
    w, payload = setup(tmp_path)
    try:
        w.apply("task.add", payload)
        dispatch(w)
        tools = WorkerTools(w.store.root, w.store.get("task", "managed"), tmp_path)
        with pytest.raises(ResearchError, match="not available in literature"):
            tools.execute("run", {"argv": ["python", "-c", "print(1)"]}, tmp_path)
        with pytest.raises(ResearchError, match="canonical"):
            tools.output("../wrong.txt")
        assert not tools.return_dir.exists()
    finally:
        w.store.close()


@pytest.mark.parametrize("direct", [False, True])
def test_target_progress_handoff_binds_first_probe_and_follow_up(tmp_path, direct):
    w, payload = setup(tmp_path)
    try:
        payload["output_limit_bytes"] = 20000
        payload["codex_workflow"].update(research_stop_policy="target_progress_v1", literature_acquisition="local_only")
        w.apply("task.add", payload)
        exported = dispatch(w)
        tools = WorkerTools(w.store.root, w.store.get("task", "managed"), tmp_path)
        with pytest.raises(ResearchError, match="only the selected local literature"):
            tools.execute("request_literature", {}, tmp_path)
        release(w, exported)
        view = tools.execute("contract", {}, tmp_path)
        assert "research_continuation" in view and "prompt" not in view
        tools.execute("write", {"path": "probe.md", "text": "First probe evidence"}, tmp_path)
        stop = "candidate_answer" if direct else "blocked"
        record = {"summary": "Observed result", "scope": "Frozen target with named limits",
                  "suggested_evidence_level": "unknown", "open_obligations": [] if direct else ["Unresolved implication"],
                  "next_step": "Independent review", "stop_reason": stop, "follow_up": None}
        handoff = {"paths": ["probe.md"], "return_record": record}
        with pytest.raises(ResearchError, match="requires a frozen probe_review"):
            tools.execute("handoff", handoff, tmp_path)
        raw_batch = {"task": "managed", "contract_sha256": exported["contract_sha256"],
                     "artifacts": [{"path": "probe.md", "sha256": file_hash(tools.return_dir / "probe.md")}]}
        preflight = w.apply("task.preflight_seal_batch", raw_batch)
        assert not preflight["passed"]
        assert preflight["errors"][0]["code"] == "invalid_research_continuation"
        attempt = None if direct else {"kind": "deduction", "mechanism": "Use the full relation",
                                       "test": "Derive its first consequence", "different_from_probe": "No size bound"}
        review = {"first_probe": {"test": "First probe", "outcome": "Observed evidence", "scope": "Precisely scoped",
                                  "artifacts": ["probe.md"]}, "target_disposition": "candidate_answer" if direct else "limited",
                  "target_relation": "Connection to the locked target", "next_attempt": attempt}
        tools.execute("probe_review", review, tmp_path)
        if not direct:
            with pytest.raises(ResearchError, match="executed follow_up"):
                tools.execute("handoff", handoff, tmp_path)
            record["follow_up"] = {"result": "A concrete obstruction", "scope": "Named assumptions",
                                   "artifacts": ["probe.md"]}
            with pytest.raises(ResearchError, match="new evidence"):
                tools.execute("handoff", handoff, tmp_path)
            assert not (tools.return_dir / "return.json").exists()
            tools.execute("write", {"path": "deduction.md", "text": "Evidence about the unresolved implication"}, tmp_path)
            record["follow_up"]["artifacts"] = ["deduction.md"]
            handoff["paths"].append("deduction.md")
        assert tools.execute("handoff", handoff, tmp_path)["prepared"]
        bound = read_json(tools.return_dir / "return.json")
        assert bound["first_probe"] == review["first_probe"]
        assert bound["follow_up_written_after_review"] == (None if direct else "deduction.md")
        assert w.apply("task.preflight_seal_batch", tools.handoff)["passed"]
        w.apply("task.begin_seal", {"task": "managed"})
        sealed = w.apply("task.submit_seal_batch", tools.handoff)
        w.apply("task.confirm_stop", {"task": "managed", "confirmed": True, "evidence": "offline fixture"})
        receipt = sealed["receipt"]
        task = w.apply("task.finish_external", {"task": "managed", "status": "completed", "summary": "Fixture only",
                      "artifact_ids": receipt["output_artifacts"], "seal_manifest_artifact": receipt["artifact_id"]})
        assert task["evidence_level"] == "unknown" and w.store.get("route", "a")["runs"] == 1
    finally:
        w.store.close()


def test_deduction_policy_requires_math_decision_and_binds_original_passage(tmp_path):
    w, payload = setup(tmp_path)
    library = AcademicLibrary.create(tmp_path / "library")
    try:
        original = tmp_path / "original.txt"
        original.write_text("Theorem 2. Assume X. Then Y.", encoding="utf-8")
        source = library.add_source({"title": "Original theorem", "url": "https://example.org/theorem",
                                     "version": "v2", "kind": "paper", "locator": "§2",
                                     "summary": "The implication from X to Y.", "limitations": "Requires X.",
                                     "tags": ["theorem"]})
        document = library.add_document(source["id"], original)
        passage = document["passages"][0]
        payload["output_limit_bytes"] = 20000
        payload["codex_workflow"].update(research_stop_policy="target_progress_v2", source_roots=[str(library.store.root)])
        w.apply("task.add", payload)
        exported = dispatch(w)
        tools = WorkerTools(w.store.root, w.store.get("task", "managed"), library.store.root)
        release(w, exported)
        tools.execute("write", {"path": "probe.md", "text": "Bounded initial evidence"}, tmp_path)
        review = {"first_probe": {"test": "Initial search", "outcome": "No witness", "scope": "Finite",
                                  "artifacts": ["probe.md"]}, "target_disposition": "limited",
                  "target_relation": "The implication for general X remains open",
                  "next_attempt": {"kind": "deduction", "mechanism": "Infer Y directly", "test": "Apply Y to X",
                                   "different_from_probe": "Uses hypotheses rather than enlarging the search"}}
        tools.execute("probe_review", review, tmp_path)
        command = {"argv": ["python", "-c", "print('discriminating check')"], "output_path": "out.txt"}
        capture = tmp_path / "capture"
        capture.mkdir()
        with pytest.raises(ResearchError, match="progress.derivation"):
            tools.execute("run", command, capture)
        derivation = {"gap": "Show Y under X", "deduction": "X reduces the target to Y",
                      "next_decision": "A failed Y check rejects this route"}
        tools.execute("progress", {"summary": "Reduction", "next_step": "Test Y", "bridge": "X to Y",
                                   "derivation": derivation}, tmp_path)
        with pytest.raises(ResearchError, match="decision"):
            tools.execute("run", command, capture)
        result = tools.execute("run", {**command, "decision": "Reject the route if Y fails"}, capture)
        assert result["exit_code"] == 0 and result["sha256"] == file_hash(tools.return_dir / "out.txt")
        assert (tools.return_dir / "out.txt").read_bytes() == (capture / "output.raw").read_bytes()
        assert tools.execute("read", {"path": "out.txt", "if_sha256": result["sha256"]}, tmp_path)["unchanged"]
        citation = {"theorem": "Theorem 2", "use_point": "X to Y", "assumptions": {"X": "Supplied by the problem"},
                    "passage_id": None, "limitation": "The theorem remains conditional until its original is read."}
        record = {"summary": "Derived reduction", "scope": "Conditional", "suggested_evidence_level": "unknown",
                  "open_obligations": ["Check the source"], "next_step": "Independent review",
                  "stop_reason": "candidate_answer", "source_checks": [citation],
                  "follow_up": {"result": "Reduction checked", "scope": "Requires X", "artifacts": ["out.txt"]}}
        handoff = {"paths": ["probe.md", "out.txt"], "return_record": record}
        with pytest.raises(ResearchError, match="conditional and blocked"):
            tools.execute("handoff", handoff, tmp_path)
        citation["passage_id"] = passage
        with pytest.raises(ResearchError, match="completely read archived passage"):
            tools.execute("handoff", handoff, tmp_path)
        read = tools.execute("reuse_passage", {"passage_id": passage}, tmp_path)
        assert read["artifact_id"] == passage
        assert tools.execute("handoff", handoff, tmp_path)["prepared"]
        returned = read_json(tools.return_dir / "return.json")
        assert returned["derivation"] == derivation
        assert returned["source_passages"][passage]["title"] == "Original theorem"
        assert returned["source_passages"][passage]["raw_artifact"] == file_hash(original)
        assert w.apply("task.preflight_seal_batch", tools.handoff)["passed"]
    finally:
        library.store.close()
        w.store.close()


def test_large_contract_returns_readable_frozen_handoff_references(tmp_path):
    w, payload = setup(tmp_path)
    try:
        w.apply("task.add", payload)
        dispatch(w)
        task = w.store.get("task", "managed")
        tools = WorkerTools(w.store.root, task, tmp_path)
        tools.contract["literature"]["assessment"] = "长" * 12000
        view = tools.execute("contract", {}, tmp_path)
        assert "prompt" not in view and view["input_count"] == len(task["inputs"])
        for key in ("contract_sha256", "prompt_artifact_id"):
            aid = view[key]
            offset, pages = 0, []
            while offset is not None:
                result = tools.execute("read", {"artifact_id": aid, "offset": offset}, tmp_path)
                pages.append(result["text"])
                offset = result["next_offset"]
            assert "".join(pages) == w.store.verify_blob(aid).read_bytes().decode("utf-8-sig")
        assert not tools.read_inputs
    finally:
        w.store.close()


def test_worker_uses_archived_passage_and_keeps_its_independent_receipt(tmp_path, monkeypatch):
    w, payload = setup(tmp_path)
    lib = AcademicLibrary.create(tmp_path / "library")
    try:
        url = "https://example.org/hall"
        source = lib.add_source({"title": "Matching theorem", "url": url, "kind": "paper", "version": "v1",
                                 "locator": "§2", "summary": "Bipartite graph", "limitations": "Finite case",
                                 "tags": ["matching"]})
        original = tmp_path / "paper.txt"
        original.write_text("The Hall obstruction is a deficient vertex subset.", encoding="utf-8")
        doc = lib.add_document(source["id"], original)
        payload["codex_workflow"]["source_roots"] = [str(lib.store.root)]
        w.apply("task.add", payload)
        dispatch(w)
        tools = WorkerTools(w.store.root, w.store.get("task", "managed"), lib.store.root)
        for aid in tools.inputs:
            offset = 0
            while offset is not None:
                offset = tools.execute("read", {"artifact_id": aid, "offset": offset}, tmp_path)["next_offset"]
        monkeypatch.setattr("ai4math.codex_worker_tools.build_opener", lambda *args: pytest.fail("network used"))
        with pytest.raises(ResearchError, match="independent worker search"):
            tools.execute("literature_ready", {}, tmp_path)
        first = tools.execute("search", {"query": "Hall obstruction"}, tmp_path)[0]
        assert first["passage"]["id"] == doc["passages"][0]
        assert tools.execute("search", {"query": " Hall  obstruction "}, tmp_path)[0] == first
        passage = tools.execute("reuse_passage", {"passage_id": first["passage"]["id"]}, tmp_path)
        size = tools.source_bytes
        assert tools.execute("reuse_passage", {"passage_id": first["passage"]["id"]}, tmp_path) == passage
        assert tools.source_bytes == size and tools.fetches == 0
        assert "deficient vertex subset" in passage["text"] and passage["raw_artifact"] == doc["raw_artifact"]
        citation = {"artifact_id": passage["artifact_id"], "url": url, "locator": passage["locator"],
                    "finding": "A deficient subset distinguishes the probe cases."}
        draft = {"artifact_id": passage["artifact_id"], "summary": "Hall obstruction", "limitations": "Finite case",
                 "contributions": ["Deficiency certificate"], "fields": ["graph theory"], "keywords": ["Hall"],
                 "methods": ["counting"], "assumptions": ["bipartite graph"]}
        receipt = {"sources": [citation], "limitations": "Finite graphs", "exploration_plan": "Test a two-vertex witness",
                   "profile_drafts": [draft]}
        with pytest.raises(ResearchError, match="cited original"):
            tools.execute("literature_ready", {**receipt, "sources": []}, tmp_path)
        ready = tools.execute("literature_ready", receipt, tmp_path)
        assert ready["stage"] == "awaiting_coordinator_review"
        record = w.store.get("worker_literature", "managed")
        trace = read_json(w.store.verify_blob(record["search_artifact"]))
        assert len(trace["searches"]) == 1 and trace["network_requests"] == 0
        assert trace["acquisitions"][citation["artifact_id"]]["mode"] == "library_passage"
        assert trace["profile_drafts"] == [draft] and lib.review_queue()["profiled_documents"] == 0
        w.apply("task.release_exploration", {"task": "managed", "worker_receipt_sha256": ready["worker_receipt_sha256"],
                                            "assessment": "original scope and applicability checked"})
        assert tools.execute("reuse_passage", {"passage_id": first["passage"]["id"]}, tmp_path) == passage
    finally:
        lib.store.close()
        w.store.close()


def test_unmanaged_app_server_item_blocks_handoff(tmp_path):
    log = EventLog(tmp_path, "test")
    session = ResearchSession(None, log, None, None)
    try:
        with pytest.raises(ResearchError, match="unmanaged App Server item"):
            session.event({"method": "item/started", "params": {"item": {"id": "bypass", "type": "commandExecution"}}})
    finally:
        session.pool.shutdown()


def test_parallel_protocol_requests_are_serialized_in_arrival_order(tmp_path):
    log = EventLog(tmp_path, "test")
    log.thread_id, log.turn_id = "thread", "turn"
    release = Event()
    calls, responses = [], []

    def execute(action, payload, capture):
        calls.append(payload["label"])
        if payload["label"] == "first":
            assert release.wait(2)
        return {"label": payload["label"]}

    tools = SimpleNamespace(execute=execute, tool_bytes=0, task={"output_limit_bytes": 10000})
    session = ResearchSession(SimpleNamespace(send=responses.append), log, tools, None)
    try:
        for rid, label in enumerate(("first", "second", "third"), 1):
            session.start_tool(rid, {"threadId": "thread", "turnId": "turn", "tool": "ai4math_task", "callId": label,
                                    "arguments": {"action": "read", "payload": {"label": label}}})
        assert len(session.pending_tools) == 2
        release.set()
        for _ in range(3):
            session.tool_future.result(timeout=2)
            session.finish_tool()
        assert calls == ["first", "second", "third"]
        assert [r["id"] for r in responses] == [1, 2, 3]
        assert all(r["result"]["success"] for r in responses)
        assert not session.pending_tools
    finally:
        release.set()
        session.pool.shutdown()

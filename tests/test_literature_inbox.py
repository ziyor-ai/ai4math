import json

import pytest

from ai4math import literature
from ai4math.cli import main
from ai4math.codex_live import read_json
from ai4math.codex_worker_tools import WorkerTools
from ai4math.knowledge import AcademicLibrary
from ai4math.literature_inbox import LiteratureInbox
from ai4math.store import ResearchError
from test_codex_monitored_contract import dispatch, setup
from test_knowledge import source


def gap():
    return {"question": "Does the  extremizer preserve the boundary invariant?", "purpose": "Choose the next bridge lemma.",
            "assumptions": ["Fixed eigenvalue", "Closed surface"], "queries": ["boundaryextremizer"]}


def test_inbox_deduplicates_and_preserves_attempt_history(tmp_path):
    lib = AcademicLibrary.create(tmp_path / "library")
    try:
        inbox = LiteratureInbox(lib.store)
        original = inbox.submit(gap(), {"workspace": "A", "route": "first"}, "main")
        same = inbox.submit({**gap(), "question": gap()["question"].replace("  ", " "),
                             "assumptions": list(reversed(gap()["assumptions"]))},
                            {"workspace": "B", "route": "other"}, "worker")
        different = inbox.submit({**gap(), "assumptions": ["Varying eigenvalue"]}, {"workspace": "B"}, "worker")
        key = original["request_id"]
        assert key == same["request_id"] != different["request_id"]
        assert same["subscription_id"] != original["subscription_id"]
        assert inbox.list(workspace="A")[0]["subscriptions"][0]["origin"]["route"] == "first"
        assert len(inbox.list(workspace="A")[0]["subscriptions"]) == 1
        with pytest.raises(ResearchError, match="distinct literature actor"):
            inbox.claim("main", key)
        first = inbox.claim("curator", key)["attempt"]
        with pytest.raises(ResearchError, match="unavailable"):
            inbox.claim("other-curator", key)
        interruption = {"outcome": "interrupted", "summary": "Curator stopped before searching."}
        with pytest.raises(ResearchError, match="claiming literature actor"):
            inbox.finish(key, first["id"], interruption, "worker")
        inbox.finish(key, first["id"], interruption, "curator")
        second = inbox.claim("other-curator", key)["attempt"]
        assert second["sequence"] == 2 and first["id"] != second["id"]
        missed = {"outcome": "no_match", "summary": "No result under the same boundary condition.", "queries": ["exact boundary"]}
        receipt = inbox.finish(key, second["id"], missed, "other-curator")
        assert receipt == inbox.finish(key, second["id"], missed, "other-curator")
        delivered = inbox.list(status="published", workspace="B")[0]
        assert delivered["result"]["outcome"] == "no_match"
        assert inbox.list(status="published", workspace="B", after=delivered["delivery_seq"]) == []
        inbox.submit(gap(), {"workspace": "C"}, "late-worker")
        late = inbox.list(status="published", workspace="C", after=delivered["delivery_seq"])[0]
        assert late["id"] == key and late["result"] == receipt
        assert lib.store.get("literature_attempt_end", first["id"])["outcome"] == "interrupted"
        assert lib.audit()["passed"]
    finally:
        lib.store.close()


def test_worker_starts_probe_before_literature_publication(tmp_path, monkeypatch):
    w, payload = setup(tmp_path)
    lib = AcademicLibrary.create(tmp_path / "library")
    try:
        payload["codex_workflow"]["source_roots"] = [str(lib.store.root)]
        w.apply("task.add", payload)
        dispatch(w)
        tools = WorkerTools(w.store.root, w.store.get("task", "managed"), lib.store.root)
        monkeypatch.setattr("ai4math.codex_worker_tools.build_opener", lambda *args: pytest.fail("network used"))
        run = lambda action, args: tools.execute(action, args, tmp_path)
        for aid in tools.inputs:
            assert run("read", {"artifact_id": aid})["next_offset"] is None
        with pytest.raises(ResearchError, match="request_literature"):
            run("fetch", {"url": "https://example.org/paper"})
        assert run("search", {"query": "boundaryextremizer"}) == []
        request = run("request_literature", gap())
        assert request == run("request_literature", gap())
        assert request["status"] == "queued"
        ready = run("literature_ready", {"sources": [], "limitations": "Awaiting specific theorem.",
                                        "exploration_plan": "Check a boundary example independently."})
        receipt = w.store.get("worker_literature", "managed")
        assert read_json(w.store.verify_blob(receipt["search_artifact"]))["literature_request_ids"] == [request["request_id"]]
        w.apply("task.release_exploration", {"task": "managed", "worker_receipt_sha256": ready["worker_receipt_sha256"],
                                             "assessment": "Independent finite probe has enough inputs."})
        assert run("literature_updates", {})[0]["status"] == "queued"
        assert run("write", {"path": "probe.txt", "text": "Independent finite check started."})["sha256"]

        inbox = LiteratureInbox(lib.store)
        claimed = inbox.claim("curator", request["request_id"])
        with pytest.raises(ResearchError, match="missing paper_profile"):
            inbox.finish(claimed["id"], claimed["attempt"]["id"],
                         {"outcome": "found", "summary": "Unarchived candidate.", "queries": ["gap"],
                          "profile_ids": ["0" * 64]}, "curator")
        assert run("literature_updates", {})[0]["status"] == "claimed"
        original = tmp_path / "original.txt"
        original.write_text("The boundaryextremizer preserves an invariant on a closed surface.", encoding="utf-8")
        document = lib.add_document(lib.add_source(source())["id"], original)
        profile = lib.add_profile({"document": document["document"], "summary": "A boundary extremizer.",
            "contributions": ["An invariant."], "fields": ["Geometry"], "keywords": ["boundaryextremizer"],
            "methods": ["Variational"], "assumptions": ["Closed surface"], "limitations": "Compare exact boundary data.",
            "anchors": document["passages"]})
        found = {"outcome": "found", "summary": "Check whether the surface hypotheses match.",
                 "queries": ["boundaryextremizer"], "profile_ids": [profile["id"]]}
        published = inbox.finish(claimed["id"], claimed["attempt"]["id"], found, "curator")
        assert published["evidence_level"] == "unknown"
        update = run("literature_updates", {"request_id": request["request_id"]})[0]["result"]
        assert update["profile_ids"] == [profile["id"]] and update["profiles"][0]["passage_ids"] == document["passages"]
        hit = run("search", {"query": "boundaryextremizer"})[0]
        assert hit["profile"]["id"] == profile["id"]
        assert "preserves an invariant" in run("reuse_passage", {"passage_id": hit["passage"]["id"]})["text"]
        assert not tools.fetches and w.store.all("claim") == []
    finally:
        lib.store.close()
        w.store.close()


def test_legacy_contract_retains_original_source_rules(tmp_path, monkeypatch):
    w, payload = setup(tmp_path)
    register = literature.task_fields

    def legacy_fields(wb, task, decision):
        register(wb, task, decision)
        task["literature"].pop("acquisition_mode")

    monkeypatch.setattr(literature, "task_fields", legacy_fields)
    try:
        w.apply("task.add", payload)
        dispatch(w)
        tools = WorkerTools(w.store.root, w.store.get("task", "managed"), tmp_path)
        assert tools.check("fetch")["external_stage"] == "literature"
        with pytest.raises(ResearchError, match="not available in literature"):
            tools.check("request_literature")
    finally:
        w.store.close()


def test_cli_routes_requests_without_requiring_librarian_workspace(tmp_path, capsys):
    w, _ = setup(tmp_path)
    lib = AcademicLibrary.create(tmp_path / "library")
    try:
        payload = tmp_path / "request.json"
        payload.write_text(json.dumps(gap()), encoding="utf-8")
        args = ["knowledge", "--root", str(lib.store.root)]
        assert main(["--workspace", str(w.store.root), *args, "request", "--route", "a", "--file", str(payload)]) == 0
        submitted = json.loads(capsys.readouterr().out)
        assert main(["--actor", "curator", *args, "request-claim"]) == 0
        claimed = json.loads(capsys.readouterr().out)
        assert claimed["id"] == submitted["request_id"] and claimed["status"] == "claimed"
        result = tmp_path / "result.json"
        result.write_text(json.dumps({"outcome": "no_match", "queries": ["exact gap"], "summary": "Bounded search ended."}), encoding="utf-8")
        assert main(["--actor", "curator", *args, "request-finish", "--id", claimed["id"], "--attempt",
                     claimed["attempt"]["id"], "--file", str(result)]) == 0
        assert json.loads(capsys.readouterr().out)["evidence_level"] == "unknown"
        assert main(["--workspace", str(w.store.root), *args, "inbox", "--status", "published"]) == 0
        assert json.loads(capsys.readouterr().out)[0]["id"] == submitted["request_id"]
    finally:
        lib.store.close()
        w.store.close()

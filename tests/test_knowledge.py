import json

import pytest

from ai4math import knowledge
from ai4math.cli import main
from ai4math.knowledge import AcademicLibrary, parse_metadata
from ai4math.store import ResearchError


def source(**updates):
    return {"title": "Independent matching study", "url": "https://example.org/paper", "kind": "paper",
            "version": "v1", "locator": "section 2", "summary": "A matching representation for a graph problem.",
            "limitations": "Applicability and mathematical correctness not checked.", "tags": ["matching", "匹配"], **updates}


def card(source_id, **updates):
    return {"name": "Hall 匹配与桥梁", "capabilities": ["representation", "bridge_lemma", "construction", "discriminating_probe", "branch_portfolio"],
            "source_ids": [source_id], "bottleneck": "Local choices fail", "operator": "Build a matching graph",
            "bridge": "Prove Hall conditions", "conditions": ["Finite sets"], "failure_modes": ["Lost hypotheses"],
            "prediction": "A boundary instance distinguishes the interface", "tags": ["互素", "Hall", "matching"],
            "limitations": "A design proposal only.", **updates}


@pytest.fixture
def library(tmp_path):
    lib = AcademicLibrary.create(tmp_path / "library", max_remote_requests=2)
    yield lib
    lib.store.close()


def test_content_addressed_versions_idempotence_and_bilingual_retrieval(library):
    first = library.add_source(source())
    n = library.audit()["events"]
    assert library.add_source(source())["id"] == first["id"]
    assert library.audit()["events"] == n
    second = library.add_source(source(version="v2"))
    assert second["id"] != first["id"] and library.store.get("source", first["id"])["content"]["version"] == "v1"
    mechanism = library.add_card(card(first["id"]))
    assert library.search("互素 Hall", capability="representation")[0]["id"] == mechanism["id"]
    assert library.search("unrelated_zebra") == []
    assert library.audit()["passed"]


def test_seed_is_packaged_complete_and_idempotent(library):
    result = library.seed()
    assert len(result["sources"]) == 41 and len(result["mechanisms"]) == 17
    events = library.audit()["events"]
    assert library.seed() == result
    assert library.audit()["events"] == events
    for capability in knowledge.CAPABILITIES:
        assert library.search("数学 构造 probe lemma representation", capability=capability)


@pytest.mark.parametrize("changes", [{"url": "file:///etc/passwd"}, {"url": "https://user:secret@example.org/a"},
    {"evidence_level": "proved"}, {"locator": ""}, {"kind": "proof_certificate"}])
def test_source_cannot_skip_scope_or_gain_proof_trust(library, changes):
    with pytest.raises(ResearchError):
        library.add_source(source(**changes))
    assert library.audit()["sources"] == 0


def test_source_tampering_blocks_retrieval(library):
    row = library.add_source(source())
    library.store.blob(row["artifact"]).write_bytes(b"tampered")
    with pytest.raises(ResearchError, match="integrity"):
        library.search("matching", kind="source")


def test_document_versions_cache_original_bytes_and_search_passages(library, tmp_path):
    s=library.add_source(source());p=tmp_path/"paper.html"
    p.write_text('<p>Hall matching 边界不变量</p><script>do not index this executable</script>',encoding='utf-8')
    d=library.add_document(s['id'],p,'html')
    assert library.search('边界不变量',kind='passage')
    assert not library.search('executable',kind='passage')
    original=library.store.verify_blob(d['raw_artifact']).read_bytes()
    p.write_text('<p>Revised source</p>',encoding='utf-8')
    revised=library.add_document(s['id'],p,'html')
    assert revised['document'] != d['document'] and library.store.verify_blob(d['raw_artifact']).read_bytes()==original
    assert library.audit()['documents']==2 and library.audit()['passed']


def test_pdf_is_retained_without_claiming_text_extraction(library,tmp_path):
    s=library.add_source(source());p=tmp_path/'paper.pdf';p.write_bytes(b'%PDF-1.7\nsynthetic byte fixture')
    result=library.add_document(s['id'],p,'pdf')
    assert result['text_extracted'] is False and result['passages']==[]


@pytest.mark.parametrize("format, header", [("pdf", b"%PDF-1.7\n"), ("text", b"An exact first passage.\n")])
def test_large_original_is_frozen_in_full_and_accepts_located_passages(library, tmp_path, format, header):
    source_id = library.add_source(source())["id"]
    original = tmp_path / f"large.{format}"
    raw = header + b"x" * (9 * 1024 * 1024) + b"\nexact tail\n"
    original.write_bytes(raw)
    document = library.add_document(source_id, original, format)
    assert not document["text_extracted"] and not document["passages"]
    assert library.store.verify_blob(document["raw_artifact"]).read_bytes() == raw
    passage = library.add_passage(document["document"], "final line", "exact tail")
    assert library.store.get("passage", passage["id"])["content"]["document"] == document["document"]
    assert library.audit()["passed"]


def test_profile_scan_revision_and_archived_passage_identity(library, tmp_path):
    sid = library.add_source(source(url="https://arxiv.org/html/2404.07738v2"))["id"]
    alias = library.add_source(source(kind="provider_metadata", url="https://arxiv.org/abs/2404.07738v1",
                                      summary="Retrieval-only abstractalias"))["id"]
    original = tmp_path / "paper.pdf"
    original.write_bytes(b"%PDF-1.7\noriginal fixture")
    doc = library.add_document(sid, original, "pdf")["document"]
    assert library.review_queue()["items"][0]["status"] == "needs_passage"
    original.write_bytes(b"%PDF-1.7\nrevised fixture")
    revised = library.add_document(sid, original, "pdf")["document"]
    anchor = library.add_passage(doc, "p. 3, §2", "An alternating witness and the sharpness construction.")
    assert library.review_queue()["items"][0]["status"] == "needs_profile"
    payload = {"document": doc, "summary": "A construction at the sharp boundary.",
               "contributions": ["Explicit extremizer"], "fields": ["极值组合"], "keywords": ["谱匹配"],
               "methods": ["Alternating witness"], "assumptions": ["Finite bipartite graph"],
               "limitations": "Requires the original hypotheses.", "anchors": [anchor["id"]]}
    with pytest.raises(ResearchError, match="exact document version"):
        library.add_profile({**payload, "document": revised})
    old = library.add_profile(payload)
    assert library.add_profile(payload)["id"] == old["id"]
    new = library.add_profile({**payload, "summary": "A sharper reading.", "supersedes": old["id"]})
    hits = library.search("极值 sharpness", kind="scan")
    assert len(hits) == 1 and hits[0]["profile"]["id"] == new["id"]
    assert hits[0]["passage"]["id"] == anchor["id"]
    assert library.store.get("paper_profile", old["id"])["content"]["evidence_level"] == "unknown"
    aliases = library.search("abstractalias", kind="scan")
    assert len(aliases) == 1 and aliases[0]["id"] == doc and aliases[0]["id"] != alias
    assert library.review_queue()["total_pending_documents"] == 1
    assert library.audit()["passed"]


def test_online_metadata_is_bound_to_raw_bytes(library, monkeypatch):
    raw = json.dumps({"message": {"items": [{"DOI": "10.1/test", "title": ["Matching theory"], "type": "journal-article"}]}}).encode()
    monkeypatch.setattr(knowledge, "fetch_metadata", lambda *a: (raw, "https://api.crossref.org/works?query=test"))
    receipt = library.discover("crossref", "matching", 1)
    assert receipt["status"] == "completed"
    assert library.store.verify_blob(receipt["raw_artifact"]).read_bytes() == raw
    record = library.store.get("source", receipt["source_ids"][0])["content"]
    assert record["kind"] == "provider_metadata" and record["evidence_level"] == "unknown"


def test_metadata_queries_reuse_completed_receipts_and_refresh_costs_quota(library, monkeypatch):
    calls = []

    def fetch(*args):
        calls.append(args)
        return b'{"message":{"items":[]}}', "https://api.crossref.org/works"

    monkeypatch.setattr(knowledge, "fetch_metadata", fetch)
    first = library.discover("crossref", "Hall matching", 1)
    cached = library.discover("crossref", " Hall  matching  ", 1)
    assert cached == {**first, "reused": True} and len(calls) == 1
    second = library.discover("crossref", "Hall matching", 1, refresh=True)
    assert second["id"] != first["id"] and len(calls) == 2
    assert library.discover("crossref", "Hall matching", 1)["id"] == second["id"]
    with pytest.raises(ResearchError, match="budget exhausted"):
        library.discover("arxiv", "Hall matching", 1)


def test_failed_and_malformed_requests_keep_cost_and_raw_response(library, monkeypatch):
    monkeypatch.setattr(knowledge, "fetch_metadata", lambda *a: (b"not json", "https://api.crossref.org/works"))
    for _ in range(2):
        row = library.discover("crossref", "matching", 1)
        assert row["status"] == "failed" and library.store.verify_blob(row["raw_artifact"]).read_bytes() == b"not json"
    with pytest.raises(ResearchError, match="budget exhausted"):
        library.discover("crossref", "matching", 1)


def test_interrupted_discovery_blocks_after_reopen_and_recovery_does_not_refund(library, monkeypatch):
    def interrupt(*args):
        raise KeyboardInterrupt()
    monkeypatch.setattr(knowledge, "fetch_metadata", interrupt)
    with pytest.raises(KeyboardInterrupt):
        library.discover("crossref", "matching", 1)
    reopened = AcademicLibrary(library.store.root)
    try:
        with pytest.raises(ResearchError, match="unfinished"):
            reopened.discover("crossref", "again", 1)
        row = reopened.recover("request-000001", "operator confirmed request stopped; usage is not refunded")
        assert row["status"] == "interrupted" and reopened.audit()["remote_requests"] == 1
    finally:
        reopened.store.close()


def test_arxiv_parser_retains_version_and_refuses_entities():
    raw = b'<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>http://arxiv.org/abs/1234.56789v2</id><title>Test</title><updated>2026-11-08</updated><summary>Scope</summary><category term="math.CO"/></entry></feed>'
    assert parse_metadata("arxiv", raw, 1)[0]["url"].endswith("v2")
    with pytest.raises(ResearchError, match="entities"):
        parse_metadata("arxiv", b'<!DOCTYPE bad>'+raw, 1)


def test_cli_library_does_not_require_mathematical_workspace(tmp_path, capsys):
    root = str(tmp_path / "cli-library")
    assert main(["knowledge", "--root", root, "init", "--empty"]) == 0
    assert main(["knowledge", "--root", root, "audit"]) == 0
    assert '"passed": true' in capsys.readouterr().out
    assert main(["knowledge", "--root", root, "init", "--empty"]) == 2


def test_cli_preserves_curator_attribution_for_discovery_and_archiving(tmp_path, capsys, monkeypatch):
    root = str(tmp_path / "library")
    argv = ["knowledge", "--root", root]
    assert main([*argv, "init", "--empty"]) == 0
    capsys.readouterr()

    def run(*args):
        assert main(["--actor", "literature-curator", *argv, *args]) == 0
        return json.loads(capsys.readouterr().out)

    metadata = {"message": {"items": [{"DOI": "10.1/test", "title": ["Fixture study"]}]}}
    monkeypatch.setattr(knowledge, "fetch_metadata", lambda *_: (json.dumps(metadata).encode(), "https://api.crossref.org/works"))
    request = run("discover", "--provider", "crossref", "--query", "Fixture study")
    assert request["actor"] == "literature-curator"
    sid, = request["source_ids"]
    assert run("show", "--kind", "source", "--id", sid)["actor"] == "literature-curator"

    path = tmp_path / "original.txt"
    path.write_text("A finite-interval estimate keeps both endpoints.", encoding="utf-8")
    doc = run("add-document", "--source", sid, "--file", str(path))
    passage = run("add-passage", "--document", doc["document"], "--locator", "§1", "--file", str(path))
    assert passage["actor"] == "literature-curator"
    assert run("show", "--kind", "document", "--id", doc["document"])["actor"] == "literature-curator"
    profile = {"document": doc["document"], "anchors": [passage["id"]], "summary": "Endpoint estimate.",
               "contributions": ["Two endpoint terms"], "fields": ["Analysis"], "keywords": ["endpoints"],
               "methods": ["Integration by parts"], "assumptions": ["Smooth amplitude"], "limitations": "Finite interval."}
    payload = tmp_path / "profile.json"
    payload.write_text(json.dumps(profile), encoding="utf-8")
    assert run("add-profile", "--file", str(payload))["actor"] == "literature-curator"

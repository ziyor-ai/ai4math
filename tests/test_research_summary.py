from concurrent.futures import ThreadPoolExecutor
import json

import pytest

from ai4math.knowledge import AcademicLibrary
from ai4math.research_summary import refresh
from ai4math.store import ResearchError
from ai4math.workbench import Workbench


def test_round_summary_preserves_history_and_frozen_links(tmp_path):
    w = Workbench.create(tmp_path / "workspace", "summary", "Research summary")
    library = AcademicLibrary.create(tmp_path / "papers", 1)
    try:
        summary = w.store.root / "reports/research-summary.md"
        history = summary.with_name("research-history.md")
        assert summary.is_file() and history.is_file()
        w.apply("node.add", {"id": "goal", "statement": "Exact conjecture", "domain": "Original domain",
                             "quantifier": "universal"})
        w.apply("goal.set", {"node": "goal"})
        w.apply("route.add", {"id": "route", "node": "goal", "rationale": "Bounded examples",
                              "next_question": "First probe", "stop_condition": "Gap remains",
                              "max_runs": 2, "stagnation_limit": 2})
        w.apply("route.checkpoint", {"route": "route", "finding": "Only finite support",
                                     "next_question": "Prove the extension step"})
        assert "Prove the extension step" in summary.read_text(encoding="utf-8")
        derivation = w.store.root / "derivation.md"
        derivation.write_text("A bounded computation", encoding="utf-8")
        artifact = w.apply("artifact.import", {"path": str(derivation), "purpose": "finite derivation"})["id"]
        node = w.store.get("node", "goal")
        w.apply("claim.add", {"id": "finite", "node": "goal", "statement_digest": node["statement_digest"],
                              "evidence_level": "finite_support", "scope": "finite", "summary": "Bounded result",
                              "evidence_domain": "Three small cases", "artifact_ids": [artifact]}, "author")
        w.apply("review.add", {"id": "finite-review", "claim": "finite", "reviewer": "reviewer",
                               "reviewer_kind": "automation", "auditor": "auditor", "coverage_complete": True,
                               "issues": [], "verdict": "ready_for_human"}, "auditor")
        w.apply("claim.accept", {"claim": "finite", "review": "finite-review"}, "custodian")
        assert "`finite_support`" in summary.read_text(encoding="utf-8")
        source = library.add_source({"title": "Original source", "url": "https://example.org/paper",
                                     "version": "v1", "locator": "Lemma 1", "summary": "Reference",
                                     "limitations": "Fixed domain", "kind": "paper", "tags": []})
        original = tmp_path / "paper.txt"
        original.write_text("An exact archived passage", encoding="utf-8")
        doc = library.add_document(source["id"], original)
        record = {"id": "round-1", "kind": "review", "title": "Review 1", "progress": "Only finite support",
                  "scope": "Three small cases", "gaps": "No extension lemma", "next_step": "Prove extension",
                  "artifacts": [artifact], "passages": doc["passages"], "claims": ["finite"]}
        first = refresh(w, payload=record, library=library.store.root, actor="coordinator")
        assert first["rounds"] == 1
        stored = summary.parent / "rounds/round-1.json"
        saved = stored.read_bytes()
        assert json.loads(saved)["sources"][0]["raw"] == doc["raw_artifact"]
        assert f"[段落](../../papers/objects/{doc['passages'][0][:2]}/{doc['passages'][0]})" in summary.read_text(encoding="utf-8")
        assert f"[derivation.md](../objects/{artifact[:2]}/{artifact})" in summary.read_text(encoding="utf-8")

        w.apply("codex.dispatch_hold", {"held": True, "reason": "Requested handoff"})
        assert "Requested handoff" in summary.read_text(encoding="utf-8")
        assert "`goal` · `unknown`" in summary.read_text(encoding="utf-8")
        second = {**record, "id": "round-1-pause", "kind": "pause", "title": "Pause 1"}
        refresh(w, payload=second, library=library.store.root, actor="coordinator")
        assert refresh(w, payload=record, library=library.store.root, actor="coordinator")["rounds"] == 2
        w.apply("claim.revoke", {"claim": "finite", "reason": "Example no longer usable"}, "custodian")
        assert "`finite` · revoked" in summary.read_text(encoding="utf-8")
        assert "轮次登记时为 `accepted`" in history.read_text(encoding="utf-8")
        assert stored.read_bytes() == saved
        assert "Pause 1" in summary.read_text(encoding="utf-8")
        for text in ("Review 1", "Pause 1", "Prove the extension step"):
            assert text in history.read_text(encoding="utf-8")
        with pytest.raises(ResearchError, match="already exists"):
            refresh(w, payload={**record, "gaps": "Overwrite attempt"}, library=library.store.root, actor="coordinator")
        assert stored.read_bytes() == saved
    finally:
        library.store.close()
        w.store.close()


def test_concurrent_round_publication(tmp_path):
    root = tmp_path / "workspace"
    w = Workbench.create(root, "summary", "Concurrent handoffs")
    w.store.close()

    def publish(index):
        reader = Workbench(root, read_only=True)
        try:
            record = {"id": f"round-{index}", "kind": "pause", "title": f"Handoff {index}",
                      "progress": "Partial work", "scope": "Bounded", "gaps": "Open",
                      "next_step": "Review"}
            refresh(reader, payload=record)
        finally:
            reader.store.close()

    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(publish, range(8)))
    assert len(list((root / "reports/rounds").glob("*.json"))) == 8
    history = (root / "reports/research-history.md").read_text(encoding="utf-8")
    assert all(f"Handoff {index}" in history for index in range(8))

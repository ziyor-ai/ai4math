from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
from pathlib import Path

import pytest

from ai4math.codex_dashboard import refresh
from ai4math.codex_skills import REPOSITORY_SKILLS, install_skills, skill_manifest
from ai4math.store import ResearchError, file_hash
from ai4math.workbench import Workbench


def test_skill_check_and_explicit_upgrade(tmp_path):
    destination = tmp_path / "skills"
    expected = skill_manifest(check_installed=False)
    assert set(expected["skills"]) == {"ai4math-coordinate", "ai4math-research", "ai4math-literature"}
    with pytest.raises(ResearchError, match="missing or different"):
        skill_manifest(destination=destination)
    assert not destination.exists()

    for skill, entries in expected["skills"].items():
        assert set(entries) == {"SKILL.md", "agents/openai.yaml"}
        for file, sha in entries.items():
            assert file_hash(REPOSITORY_SKILLS / skill / file) == sha
        assert "allow_implicit_invocation: false" in (
            REPOSITORY_SKILLS / skill / "agents/openai.yaml").read_text(encoding="utf-8")
    assert install_skills(destination=destination) == expected
    assert install_skills(destination=destination) == skill_manifest(destination=destination) == expected

    changed = destination / "ai4math-research" / "agents" / "openai.yaml"
    untouched = destination / "ai4math-coordinate" / "SKILL.md"
    local_note = destination / "ai4math-research" / "notes.txt"
    local_note.write_text("Local notes", encoding="utf-8")
    old = untouched.read_bytes()
    changed.write_bytes(b"Local metadata")
    with pytest.raises(ResearchError, match="--upgrade"):
        install_skills(destination=destination)
    assert untouched.read_bytes() == old and changed.read_bytes() == b"Local metadata"
    assert install_skills(destination=destination, upgrade=True) == expected
    assert local_note.read_text(encoding="utf-8") == "Local notes"


def fixture_workspace(root):
    w = Workbench.create(root, "dashboard", "<math>Research</math>")
    for key in ("child", "goal"):
        w.apply("node.add", {"id": key, "statement": f"Exact {key}", "domain": "fixed original domain",
                             "quantifier": "universal", "proof_dependencies": ["child"] if key == "goal" else []})
    w.apply("goal.set", {"node": "goal"})
    w.apply("route.add", {"id": "route", "node": "child", "rationale": "First approach",
                         "next_question": "First probe", "stop_condition": "Gap remains", "max_runs": 1,
                         "stagnation_limit": 1})
    now = datetime.now(timezone.utc).isoformat()
    with w.store.transaction("test.dashboard_projection", "fixture"):
        w.store.put("task", "live-task", {"id": "live-task", "route": "route", "node": "child",
                     "role": "explore", "status": "running", "external_stage": "awaiting_coordinator_review",
                     "started_at": now, "codex_workflow": {"worker_actor": "fixture"}}, new=True)
        w.store.put("claim", "accepted-fixture", {"id": "accepted-fixture", "node": "child",
                    "status": "accepted", "evidence_level": "proved", "created_at": now}, new=True)
        w.store.put("claim", "proposed-fixture", {"id": "proposed-fixture", "node": "goal",
                    "status": "proposed", "evidence_level": "proved", "created_at": now}, new=True)
        strategy = w.store.get("strategy_state", "main")
        strategy["pending_triggers"] = [{"kind": "route_run_limit", "reference": "route", "route": "route"}]
        w.store.put("strategy_state", "main", strategy)
    return w, now


def test_dashboard_projection_escapes_text_and_excludes_raw_protocol(tmp_path):
    root = tmp_path / "workspace"
    w, now = fixture_workspace(root)
    try:
        seq = w.store.db.execute("SELECT MAX(seq) FROM events").fetchone()[0]
        live = root / "tasks" / "live-task" / "live"
        live.mkdir(parents=True)
        result = live.parent / "return" / "result & proof.md"
        result.parent.mkdir()
        result.write_text("evidence", encoding="utf-8")
        (root / "private.html").write_text("outside task return", encoding="utf-8")

        def event(number, kind, **data):
            return json.dumps({"task": "live-task", "at": now, "seq": number, "kind": kind,
                               "thread_id": "thread", "turn_id": "turn", "data": data}, ensure_ascii=False)

        events = [event(1, "message", text="RAWPAYLOAD" * 30000),
                  event(2, "progress", summary="<script>substantive</script>", next_step="Inspect bound",
                        bridge="Original statement", artifacts=["return/result & proof.md", "../../private.html",
                                                               "javascript:alert(1)", "live/events.jsonl"]),
                  event(3, "steer_accepted", request_id="corr-1"), event(4, "steer_ack", request_id="corr-1"),
                  event(5, "tool_completed", arguments="RAWARGUMENTS", stdout="RAWOUT")]
        (live / "events.jsonl").write_bytes(("\n".join(events) + "\n" +
                                            event(6, "progress", summary="INCOMPLETE")).encode())
        (live / "heartbeat.json").write_text(json.dumps({"task": "live-task", "at": now, "seq": 5,
                                                          "turn_id": "turn", "token": "PRIVATEBEAT"}),
                                              encoding="utf-8")
        target = refresh(root)
        assert target == root / "reports" / "research-progress.html"
        report = target.read_text(encoding="utf-8")
        assert "&lt;math&gt;Research&lt;/math&gt;" in report and "<script>" not in report
        assert "&lt;script&gt;substantive&lt;/script&gt;" in report
        assert "等待主会话审核" in report and "全局复盘到期" in report
        assert 'href="../tasks/live-task/return/result%20%26%20proof.md"' in report
        for rejected in ("RAWPAYLOAD", "RAWARGUMENTS", "RAWOUT", "PRIVATEBEAT", "INCOMPLETE", "private.html",
                         "javascript:alert", "live/events.jsonl"):
            assert rejected not in report
        assert "#3 已确认" in report and "corr-1" in report and "事件序号 5" in report
        assert "已接纳 · 1" in report and "待审候选 · 1" in report and f"注册事件 #{seq}" in report
        assert w.store.db.execute("SELECT MAX(seq) FROM events").fetchone()[0] == seq
    finally:
        w.store.close()


def test_dashboard_refresh_is_serial_and_paths_stay_owned(tmp_path):
    root = tmp_path / "workspace"
    w, _ = fixture_workspace(root)
    w.store.close()
    with ThreadPoolExecutor(max_workers=4) as pool:
        paths = list(pool.map(refresh, [root] * 8))
    assert len(set(paths)) == 1
    report = paths[0].read_text(encoding="utf-8")
    assert report.startswith("<!doctype html>") and report.endswith("</html>")
    assert not list(paths[0].parent.glob(".research-progress.html.*"))
    with pytest.raises(ResearchError, match="workspace-relative"):
        refresh(root, output="../outside.html")
    custom = refresh(root, output=Path("exports/summary.html"))
    assert custom == root / "exports" / "summary.html"
    with pytest.raises(ResearchError, match="already exists"):
        refresh(root, output=Path("exports/summary.html"))

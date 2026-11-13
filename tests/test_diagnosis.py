import json

import pytest

from ai4math.cli import main
from ai4math.diagnosis import report
from ai4math.store import ResearchError
from ai4math.workbench import Workbench
from examples.run_hyperfield_diagnosis import run


def test_four_diagnoses_from_exact_hyperfield_witnesses(tmp_path, capsys):
    output = run(tmp_path / "demo")
    rows = json.loads(output.read_text(encoding="utf-8"))
    old, new, target = (rows[key] for key in ("common", "singleton-route", "cubic"))
    hit, = old["counterexample_impact"]
    assert hit["nodes_to_review"] == ["common-route", "cubic"]
    assert [row["id"] for row in hit["ledger_to_review"]] == ["bridge-note"]
    assert hit["other_accepted_proofs"] == ["pairwise-fact"]
    assert hit["goal_status"] == target["node_status"] == "open"
    replay, = new["witness_replays"]
    assert replay["result"]["outcome"] == "excluded_by_condition"
    assert new["unreplayed_artifacts"] == []
    assert replay["evidence_level"] == "finite_support"
    collision, = target["indistinguishability"]
    first, second = collision["result"]["witnesses"].values()
    assert first["invariants"] == second["invariants"] and first["conclusion"] != second["conclusion"]
    mapped, = new["source_checks"]
    assert mapped["assessment"] == "conditional"
    assert mapped["open_hypotheses"] == {"at least one singleton": "singleton"}
    workspace = output.parent / "workspace"
    w = Workbench(workspace)
    try:
        tail = w.audit()["tail_hash"]
        assert main(["--workspace", str(workspace), "diagnose", "--node", "common"]) == 0
        assert json.loads(capsys.readouterr().out) == old
        assert w.audit()["tail_hash"] == tail

        with pytest.raises(ResearchError, match="completed independent"):
            w.apply("diagnosis.replay", {"id": "wrong", "revision": replay["revision"],
                "counterexample": replay["counterexample"], "witness": replay["witness"],
                "task": "collision-check", "trust_basis": "mismatched receipt"}, "tester")
        with pytest.raises(ResearchError, match="already registered"):
            w.apply("diagnosis.replay", {**replay, "id": "duplicate"}, "tester")
        with pytest.raises(ResearchError, match="revision cycle"):
            w.apply("diagnosis.revision", {"id": "cycle", "original": "singleton-route",
                "revised": "common", "reason": "Bad lineage"}, "tester")

        corrected = {**mapped, "id": "corrected", "supersedes": mapped["id"],
                     "theorem": "Singleton intersection lemma, checked in original note"}
        w.apply("diagnosis.source_check", corrected, "tester")
        w.apply("claim.revoke", {"claim": "pairwise-fact", "reason": "Exercise stale dependency reporting"}, "tester")
        current = report(w, "singleton-route")
        assert [r["id"] for r in current["superseded_source_checks"]] == [mapped["id"]]
        assert current["source_checks"][0]["stale_hypotheses"] == ["pairwise intersection"]
        assert current["source_checks"][0]["impact"]["nodes_to_review"] == ["cubic"]
        w.apply("claim.revoke", {"claim": "common-refuted", "reason": "Exercise historical replay visibility"}, "tester")
        current = report(w, "common")
        assert not current["counterexample_impact"]
        assert current["witness_replays"][0]["original_counterexample_active"] is False
    finally:
        w.store.close()

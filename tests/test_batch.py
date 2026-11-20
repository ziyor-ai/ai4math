"""A batch registry groups independent one-goal workspaces (audit F1)."""
import pytest

from ai4math.store import ResearchError
from ai4math.workbench import Workbench


def make_proposition(root, name, prove=False):
    w = Workbench.create(root, name, name)
    w.apply("node.add", {"id": name, "statement": "Exact " + name, "domain": "all n",
                         "quantifier": "universal"})
    w.apply("goal.set", {"node": name})
    if prove:
        w.apply("route.add", {"id": name + "-route", "node": name, "rationale": "proof",
                              "next_question": "q", "stop_condition": "s",
                              "max_runs": 5, "stagnation_limit": 5})
        w.apply("route.status", {"id": name + "-route", "status": "active", "reason": "test"})
        digest = w.store.get("node", name)["statement_digest"]
        w.apply("claim.add", {"id": name + "-claim", "node": name, "statement_digest": digest,
                              "evidence_level": "proved", "scope": "universal", "summary": "s",
                              "proof_route": "direct"}, "author")
        with w.store.transaction("fixture.accept", "test"):
            w.store.put("validation", name + "-val",
                        {"id": name + "-val", "claim": name + "-claim",
                         "method": "formal_kernel", "passed": True, "artifact_ids": [],
                         "trust": "declared_external_checker", "trust_basis": "fixture",
                         "validator": "val"}, new=True)
            w.store.put("review", name + "-rev",
                        {"id": name + "-rev", "claim": name + "-claim",
                         "verdict": "ready_for_human", "reviewer": "rev",
                         "reviewer_kind": "model", "auditor": "aud", "review_order": 1,
                         "created_at": "2030-01-01T00:00:00+00:00", "issues": [],
                         "coverage_complete": True}, new=True)
        w.apply("claim.accept", {"claim": name + "-claim", "review": name + "-rev",
                                 "validation": name + "-val"}, "custodian")
    w.store.close()


@pytest.fixture
def registry(tmp_path):
    make_proposition(tmp_path / "p-one", "p-one", prove=True)
    make_proposition(tmp_path / "p-two", "p-two")
    w = Workbench.create(tmp_path / "control", "control", "Batch registry")
    yield w, tmp_path
    w.store.close()


def test_a_batch_records_independent_proposition_workspaces(registry):
    w, root = registry
    w.apply("batch.open", {"id": "B1", "title": "simple batch"}, "coordinator")
    for name in ("p-one", "p-two"):
        w.apply("batch.add", {"batch": "B1", "slug": name,
                              "workspace": str(root / name), "node": name,
                              "source": "test"}, "coordinator")
    page = w.apply("batch.status", {"batch": "B1"}, "coordinator")
    assert page["total"] == 2 and page["proved"] == 1
    by_slug = {item["slug"]: item for item in page["items"]}
    assert by_slug["p-one"]["state"] == "proved"
    assert by_slug["p-two"]["state"] == "open"


def test_a_missing_workspace_is_refused(registry):
    w, root = registry
    w.apply("batch.open", {"id": "B1", "title": "simple batch"}, "coordinator")
    with pytest.raises(ResearchError, match="existing workspace"):
        w.apply("batch.add", {"batch": "B1", "slug": "nope",
                              "workspace": str(root / "nope"), "node": "nope"}, "coordinator")


def test_a_slug_is_listed_once(registry):
    w, root = registry
    w.apply("batch.open", {"id": "B1", "title": "simple batch"}, "coordinator")
    payload = {"batch": "B1", "slug": "p-one", "workspace": str(root / "p-one"),
               "node": "p-one"}
    w.apply("batch.add", payload, "coordinator")
    with pytest.raises(ResearchError, match="already lists"):
        w.apply("batch.add", payload, "coordinator")

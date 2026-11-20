"""Batch 8.0: the workspace policy owns the write version; callers cannot supply it."""
import pytest

from ai4math import policy
from ai4math.store import ResearchError
from ai4math.workbench import Workbench


@pytest.fixture
def wb(tmp_path):
    instance = Workbench.create(tmp_path / "workspace", "test", "Policy contract")
    yield instance
    instance.store.close()


def node(wb):
    return wb.apply("node.add", {"id": "goal", "statement": "Exact goal", "domain": "all n",
                                 "quantifier": "universal"})


def test_default_policy_starts_at_the_lowest_schema(wb):
    row = policy.current(wb)
    assert row["workspace_policy_version"] == "v2c0"
    assert row["min_write_schema"] == "v2c0"
    assert policy.require_intent(wb) is False


@pytest.mark.parametrize("field", ["record_schema_version", "workspace_policy_version",
                                   "min_write_schema", "require_intent", "compliance"])
def test_a_caller_cannot_choose_the_write_version(wb, field):
    with pytest.raises(ResearchError, match="must not supply"):
        wb.apply("node.add", {"id": "goal", "statement": "s", "domain": "d",
                              "quantifier": "universal", field: "v2c0"})


def test_the_rejection_happens_before_any_handler_side_effect(wb):
    with pytest.raises(ResearchError):
        wb.apply("node.add", {"id": "goal", "statement": "s", "domain": "d",
                              "quantifier": "universal", "workspace_policy_version": "v2c1"})
    assert wb.store.all("node") == []


def test_migration_moves_forward_and_enables_the_intent_gate(wb):
    node(wb)
    row = wb.apply("policy.migrate", {"to": "v2c1"}, "admin")
    assert row["workspace_policy_version"] == "v2c1"
    assert row["min_write_schema"] == "v2c1"
    assert row["require_intent"] is True
    assert policy.current(wb)["workspace_policy_version"] == "v2c1"
    assert wb.audit()["passed"]


def test_migration_never_moves_backwards(wb):
    wb.apply("policy.migrate", {"to": "v2c2"}, "admin")
    with pytest.raises(ResearchError, match="only moves forward"):
        wb.apply("policy.migrate", {"to": "v2c1"}, "admin")


def test_unknown_versions_are_refused(wb):
    with pytest.raises(ResearchError, match="policy.migrate needs to in"):
        wb.apply("policy.migrate", {"to": "v9z9"}, "admin")

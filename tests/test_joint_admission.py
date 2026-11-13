"""Cross-ledger checks using local synthetic actions and no provider."""

import pytest

import test_evaluation as models
import test_resource_ledger as resources
from ai4math.evaluation import EvaluationError, EvaluationLedger
from ai4math.joint_admission import audit_joint_ledgers, reserve_engineering_resource
from ai4math.resource_ledger import ResourceLedger


def pair(tmp_path, **overrides):
    model = EvaluationLedger.create(tmp_path / "model", models.plan())
    plan = resources.plan()
    plan.update(tasks=model.plan["tasks"], model_plan_sha256=model.plan_hash,
                initial_methods_sha256=model.plan["initial_methods_sha256"])
    plan.update(overrides)
    return model, ResourceLedger.create(tmp_path / "resource", plan)


def test_joint_audit_preserves_pending_and_unknown_across_ledgers(tmp_path):
    model, resource = pair(tmp_path)
    model.reserve(models.request(model, key="model-1"))
    resource.reserve(resources.request(resource, key="resource-1", arm="B", task="task1"))
    resource.settle("resource-1", None, "timeout", "1" * 64)
    heads = [(ledger.root / "events.jsonl").read_bytes() for ledger in (model, resource)]
    result = audit_joint_ledgers(EvaluationLedger(model.root), ResourceLedger(resource.root))
    assert result["arm_blockers"] == {
        "A": [{"source": "model", "id": "model-1", "reason": "unfinished_or_unresolved"}],
        "B": [{"source": "resource", "id": "resource-1", "reason": "unfinished_or_unresolved"}],
        "C": [],
    }
    assert heads == [(ledger.root / "events.jsonl").read_bytes() for ledger in (model, resource)]


@pytest.mark.parametrize("field,value,message", [
    ("model_plan_sha256", "0" * 64, "another model plan"),
    ("initial_methods_sha256", "0" * 64, "initial_methods_sha256"),
])
def test_joint_audit_rejects_mismatched_plans(tmp_path, field, value, message):
    model, resource = pair(tmp_path, **{field: value})
    with pytest.raises(EvaluationError, match=message):
        audit_joint_ledgers(model, resource)


@pytest.mark.parametrize("parent_task,expected", [("task1", []), ("task2", [
    {"source": "resource", "id": "resource-1", "reason": "invalid_parent_model_action"},
])])
def test_joint_audit_binds_parent_model_context(tmp_path, parent_task, expected):
    model, resource = pair(tmp_path)
    model.reserve(models.request(model, key="model-1", task=parent_task))
    model.settle("model-1", models.usage())
    request = resources.request(resource, key="resource-1", task="task1")
    request["parent_model_request_id"] = "model-1"
    resource.reserve(request)
    resource.settle("resource-1", resources.usage(), "completed", "1" * 64)
    assert audit_joint_ledgers(model, resource)["arm_blockers"]["A"] == expected


def test_joint_audit_rejects_same_action_id_across_ledgers(tmp_path):
    model, resource = pair(tmp_path)
    model.reserve(models.request(model, key="duplicate"))
    resource.reserve(resources.request(resource, key="duplicate", task="task1"))
    with pytest.raises(EvaluationError, match="action ID reused"):
        audit_joint_ledgers(model, resource)


def test_joint_audit_rejects_in_memory_plan_changes(tmp_path):
    model, resource = pair(tmp_path)
    resource.plan["readiness"]["metering_complete"] = True
    with pytest.raises(EvaluationError, match="loaded joint plan changed"):
        audit_joint_ledgers(model, resource)


def test_joint_reservation_persists_prior_heads_and_consumes_id(tmp_path):
    model, resource = pair(tmp_path, mode="engineering_diagnostic")
    prior = audit_joint_ledgers(model, resource)
    request = resources.request(resource, key="tool-1", task="task1")
    result = reserve_engineering_resource(model, resource, request)
    assert result["joint_admission"] == {
        key: value for key, value in prior.items() if key.endswith("_sha256")
    } | {"schema": "joint-engineering-intent-v1"}
    assert ResourceLedger(resource.root).state()["calls"]["tool-1"]["status"] == "pending"
    with pytest.raises(EvaluationError, match="id already consumed"):
        reserve_engineering_resource(model, resource, request)


@pytest.mark.parametrize("case,error", [
    ("pending", "joint arm blocked"),
    ("unknown", "joint arm blocked"),
    ("parent", "invalid parent"),
    ("duplicate", "action ID reused"),
    ("locked", "ledger locked"),
])
def test_joint_reservation_refuses_without_writing(tmp_path, case, error):
    model, resource = pair(tmp_path, mode="engineering_diagnostic")
    model.reserve(models.request(model, key="model-1", task="task2"))
    request = resources.request(resource, key="tool-1", task="task1")
    if case != "pending":
        model.settle("model-1", None if case == "unknown" else models.usage())
    if case == "parent":
        request["parent_model_request_id"] = "model-1"
    if case == "duplicate":
        request["id"] = "model-1"
    journal = resource.root / "events.jsonl"
    prior = journal.read_bytes()
    if case == "locked":
        with model._lock(), pytest.raises(EvaluationError, match=error):
            reserve_engineering_resource(model, resource, request)
    else:
        with pytest.raises(EvaluationError, match=error):
            reserve_engineering_resource(model, resource, request)
    assert journal.read_bytes() == prior

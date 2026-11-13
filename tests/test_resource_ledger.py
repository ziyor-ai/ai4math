"""Synthetic state-machine tests. No provider or operating-system claims."""
import copy
import json

import pytest

from ai4math.evaluation import EvaluationError
from ai4math.resource_ledger import DIMS, MEASURES, READY, ResourceLedger


def plan(scope="fresh_evolution_full_cost", mode="synthetic_control_test"):
    return {"schema": "tool-resource-plan-v1", "mode": mode, "comparison_scope": scope,
            "arms": ["A", "B", "C"], "tasks": {"t1": "1" * 64, "t2": "2" * 64, "t3": "3" * 64},
            "per_task_cap": {k: 100 for k in DIMS}, "development_cap": {k: 100 for k in DIMS},
            "per_call_cap": {k: 100 for k in DIMS}, "peak_memory_cap_bytes": 1000,
            "initial_methods_sha256": "a" * 64, "model_plan_sha256": "b" * 64, "policy_sha256": "c" * 64,
            "evolved_methods_sha256": "d" * 64 if scope == "inference_only" else None,
            "readiness": {k: False for k in READY}}


@pytest.fixture
def ledger(tmp_path):
    return ResourceLedger.create(tmp_path / "resource", plan())


def request(ledger, key="r1", arm="A", phase="solve", task="t1", kind="tool", amount=40):
    bound = {k: amount for k in MEASURES}
    bound.update(tool_calls=int(kind == "tool"), verification_calls=int(kind == "verify"))
    bound["verification_wall_microseconds" if kind == "tool" else "tool_wall_microseconds"] = 0
    return {"id": key, "arm": arm, "phase": phase, "task": task, "task_sha256": ledger.plan["tasks"].get(task),
            "kind": kind, "tool_sha256": "e" * 64, "input_sha256": "f" * 64, "policy_sha256": ledger.plan["policy_sha256"],
            "parent_model_request_id": None, "methods_sha256": None if arm == "A" else ledger.plan["initial_methods_sha256"] if arm == "B" or phase == "develop" else ledger.state()["c_methods_sha256"],
            "bound": bound, "peak_memory_cap_bytes": 1000}


def usage(amount=30, kind="tool"):
    value = {k: amount for k in MEASURES}
    value["verification_wall_microseconds" if kind == "tool" else "tool_wall_microseconds"] = 0
    value["peak_memory_bytes"] = 500
    return value


def test_unknown_retains_each_reservation_after_reopen(ledger):
    ledger.reserve(request(ledger))
    ledger.settle("r1", {"cpu_microseconds": 12, "input_bytes": 0}, "timeout", "1" * 64)
    reopened = ResourceLedger(ledger.root)
    audit = reopened.audit()["arms"]["A"]
    assert audit["total"] is None and not audit["all_resources_known"]
    assert audit["known_subtotal"]["cpu_microseconds"] == 12
    assert audit["charged_or_reserved"]["cpu_microseconds"] == "12"
    assert audit["charged_or_reserved"]["tool_wall_microseconds"] == "40"
    assert audit["charged_or_reserved"]["tool_calls"] == "1"
    assert audit["terminal_failure_calls"] == ["r1"]
    with pytest.raises(EvaluationError, match="blocked"):
        reopened.reserve(request(reopened, "retry"))


def test_pending_survives_and_no_implicit_recovery(ledger):
    ledger.reserve(request(ledger))
    reopened = ResourceLedger(ledger.root)
    assert reopened.audit()["arms"]["A"]["pending_calls"] == ["r1"]
    with pytest.raises(EvaluationError, match="blocked"):
        reopened.reserve(request(reopened, "retry"))
    with pytest.raises(EvaluationError, match="settle original"):
        reopened.reconcile("r1", {"cpu_microseconds": 1}, None, "1" * 64, "local", "recovery")


@pytest.mark.parametrize("status", ["failed", "timeout", "output_limit", "interrupted", "completed"])
def test_known_terminal_usage_charged_regardless_of_execution_status(ledger, status):
    ledger.reserve(request(ledger))
    ledger.settle("r1", usage(), status, "1" * 64)
    a = ledger.audit()["arms"]["A"]
    assert a["total"]["cpu_microseconds"] == 30 and a["continuation_state_clear"]
    ledger.reserve(request(ledger, "explicit-next", amount=70))
    assert ResourceLedger(ledger.root).audit()["arms"]["A"]["charged_or_reserved"]["cpu_microseconds"] == "100"


def test_counts_not_released_by_zero_usage_and_budget_persists(tmp_path):
    p = plan()
    p["per_task_cap"]["tool_calls"] = 1
    instance = ResourceLedger.create(tmp_path / "one", p)
    instance.reserve(request(instance))
    instance.settle("r1", usage(0), "failed", "1" * 64)
    with pytest.raises(EvaluationError, match="budget"):
        ResourceLedger(instance.root).reserve(request(instance, "second"))
    assert instance.audit()["arms"]["A"]["total"]["tool_calls"] == 1


def test_reconciliation_append_only_partial_and_status_not_upgraded(ledger):
    ledger.reserve(request(ledger))
    ledger.settle("r1", None, "interrupted", "1" * 64)
    path = ledger.root / "events.jsonl"
    before = path.read_bytes()
    ledger.reconcile("r1", {"cpu_microseconds": 20}, None, "2" * 64, "local/test", "new independent receipt")
    assert not ledger.audit()["arms"]["A"]["continuation_state_clear"]
    ledger.reconcile("r1", {k: 20 for k in ("tool_wall_microseconds", "input_bytes", "output_bytes")}, 100, "3" * 64, "local/test", "remaining measurements")
    assert path.read_bytes().startswith(before)
    call = ResourceLedger(ledger.root).state()["calls"]["r1"]
    assert call["status"] == "interrupted" and len(call["reconciliations"]) == 2
    assert ledger.audit()["arms"]["A"]["continuation_state_clear"]
    assert ledger.audit()["arms"]["A"]["total"]["tool_calls"] == 1


@pytest.mark.parametrize("alter", ["known", "same_receipt", "no_actor", "negative", "bool", "empty"])
def test_bad_reconciliations_rejected_without_events(ledger, alter):
    ledger.reserve(request(ledger))
    ledger.settle("r1", {"input_bytes": 4}, "failed", "1" * 64)
    args = ["r1", {"cpu_microseconds": 3}, None, "2" * 64, "actor", "reason"]
    if alter == "known": args[1] = {"input_bytes": 5}
    if alter == "same_receipt": args[3] = "1" * 64
    if alter == "no_actor": args[4] = " "
    if alter == "negative": args[1] = {"cpu_microseconds": -1}
    if alter == "bool": args[1] = {"cpu_microseconds": True}
    if alter == "empty": args[1] = {}
    before = (ledger.root / "events.jsonl").read_bytes()
    with pytest.raises(EvaluationError): ledger.reconcile(*args)
    assert (ledger.root / "events.jsonl").read_bytes() == before


@pytest.mark.parametrize("field,value", [("cpu_microseconds", 41), ("peak_memory_bytes", 1001), ("verification_wall_microseconds", 1)])
def test_overrun_charged_and_never_cleared(ledger, field, value):
    ledger.reserve(request(ledger))
    u = usage(); u[field] = value
    ledger.settle("r1", u, "failed", "1" * 64)
    audit = ledger.audit()["arms"]["A"]
    assert audit["violations"] and not audit["continuation_state_clear"]
    if field != "peak_memory_bytes": assert audit["total"][field] == value
    with pytest.raises(EvaluationError, match="blocked"): ledger.reserve(request(ledger, "again"))
    with pytest.raises(EvaluationError, match="overwritten"): ledger.reconcile("r1", {"cpu_microseconds": 0}, None, "2" * 64, "actor", "hide overrun")


def test_reconciliation_overrun_remains_blocked(ledger):
    ledger.reserve(request(ledger)); ledger.settle("r1", None, "timeout", "1" * 64)
    ledger.reconcile("r1", {"cpu_microseconds": 50, "tool_wall_microseconds": 10, "input_bytes": 10, "output_bytes": 10}, 500, "2" * 64, "test", "synthetic later receipt")
    assert ledger.audit()["arms"]["A"]["charged_or_reserved"]["cpu_microseconds"] == "50"
    assert not ledger.audit()["arms"]["A"]["continuation_state_clear"]


@pytest.mark.parametrize("value", [-1, True, 2.5, "0"])
def test_malformed_usage_becomes_unknown_not_free(ledger, value):
    ledger.reserve(request(ledger)); u = usage(); u["cpu_microseconds"] = value
    result = ledger.settle("r1", u, "completed", "1" * 64)
    assert result["charge"]["cpu_microseconds"] is None and result["measurement_notes"]
    assert ledger.audit()["arms"]["A"]["charged_or_reserved"]["cpu_microseconds"] == "40"


def test_fixed_c_share_exact_thirds_before_any_solve(ledger):
    ledger.reserve(request(ledger, arm="C", phase="develop", task=None, amount=10))
    ledger.settle("r1", usage(10), "completed", "1" * 64)
    ledger.freeze_c("d" * 64)
    a = ledger.audit()["arms"]["C"]
    assert all(v["tool_calls"] == "1/3" and v["cpu_microseconds"] == "10/3" for v in a["task_exposure_with_development"].values())
    assert a["max_known_peak_memory_bytes"] == 500
    assert "peak_memory_bytes" not in a["charged_or_reserved"]
    with pytest.raises(EvaluationError, match="freeze"):
        ledger.reserve(request(ledger, "late-dev", "C", "develop", None))
    with pytest.raises(EvaluationError, match="fixed development"):
        ledger.reserve(request(ledger, "solve-over", "C", amount=97))
    ledger.reserve(request(ledger, "solve", "C", amount=96))


@pytest.mark.parametrize("status", ["failed", "interrupted", "timeout"])
def test_c_failed_development_cannot_be_frozen(ledger, status):
    ledger.reserve(request(ledger, arm="C", phase="develop", task=None))
    ledger.settle("r1", usage(), status, "1" * 64)
    with pytest.raises(EvaluationError, match="metered development"): ledger.freeze_c("d" * 64)


def test_unknown_completed_development_cannot_be_frozen(ledger):
    ledger.reserve(request(ledger, arm="C", phase="develop", task=None))
    ledger.settle("r1", None, "completed", "1" * 64)
    with pytest.raises(EvaluationError, match="metered development"): ledger.freeze_c("d" * 64)


def test_a_b_common_limits_and_verification_separate_count(ledger):
    for arm in ("A", "B"):
        ledger.reserve(request(ledger, arm + "v", arm=arm, kind="verify"))
        ledger.settle(arm + "v", usage(kind="verify"), "completed", "1" * 64)
        audit = ledger.audit()["arms"][arm]
        assert audit["total"]["verification_calls"] == 1 and audit["total"]["tool_calls"] == 0
        assert audit["total"]["tool_wall_microseconds"] == 0
        with pytest.raises(EvaluationError, match="budget"): ledger.reserve(request(ledger, arm + "over", arm=arm, amount=71))


def test_readiness_and_mode_not_falsely_promoted(tmp_path):
    p = plan(mode="prospective_study"); l = ResourceLedger.create(tmp_path / "blocked", p)
    with pytest.raises(EvaluationError, match="readiness"): l.reserve(request(l))
    assert not l.audit()["live_resource_admission_ready"]
    p = plan(); p["readiness"] = {k: True for k in READY}
    d = ResourceLedger.create(tmp_path / "diagnostic", p)
    assert not d.audit()["live_resource_admission_ready"]
    assert not d.audit()["os_limits_enforced_by_this_ledger"]


def test_inference_only_fixed_c_and_no_development(tmp_path):
    l = ResourceLedger.create(tmp_path / "inference", plan("inference_only"))
    l.reserve(request(l, arm="C"))
    l.settle("r1", usage(), "completed", "1" * 64)
    with pytest.raises(EvaluationError, match="development not permitted"): l.reserve(request(l, "dev", "C", "develop", None))


@pytest.mark.parametrize("field,value", [("policy_sha256", "9" * 64), ("tool_sha256", "not-a-hash"), ("methods_sha256", "9" * 64), ("task_sha256", "9" * 64), ("peak_memory_cap_bytes", 1001)])
def test_drift_rejected(ledger, field, value):
    r = request(ledger); r[field] = value
    with pytest.raises(EvaluationError): ledger.reserve(r)
    assert not ledger.state()["calls"]


@pytest.mark.parametrize("field,value", [("tool_calls", 0), ("tool_calls", True), ("verification_calls", 1), ("cpu_microseconds", 101), ("input_bytes", -1), ("verification_wall_microseconds", 1)])
def test_bad_bounds_rejected(ledger, field, value):
    r = request(ledger); r["bound"][field] = value
    with pytest.raises(EvaluationError): ledger.reserve(r)


def test_duplicate_id_settlement_and_receipt_required(ledger):
    ledger.reserve(request(ledger))
    with pytest.raises(EvaluationError): ledger.settle("r1", usage(), "completed", "missing")
    ledger.settle("r1", usage(), "completed", "1" * 64)
    with pytest.raises(EvaluationError, match="consumed"): ledger.reserve(request(ledger))
    with pytest.raises(EvaluationError, match="already settled"): ledger.settle("r1", usage(), "completed", "1" * 64)


def test_lock_partial_chain_and_frozen_plan_fail_closed(ledger):
    lock = ledger.root / ".write-lock"; lock.write_text("external-owner")
    with pytest.raises(EvaluationError, match="locked"): ledger.reserve(request(ledger))
    assert lock.read_text() == "external-owner"
    # Test fixture owns this artificial lock; production never removes stale locks.
    lock.unlink()
    path = ledger.root / "plan.json"; original = path.read_bytes()
    p = copy.deepcopy(ledger.plan); p["per_task_cap"]["cpu_microseconds"] *= 2
    path.write_text(json.dumps(p))
    with pytest.raises(EvaluationError, match="plan changed"): ResourceLedger(ledger.root).audit()
    path.write_bytes(original)
    with (ledger.root / "events.jsonl").open("a") as f: f.write('{"partial":')
    with pytest.raises(EvaluationError, match="truncated"): ResourceLedger(ledger.root).audit()


def test_new_root_cannot_reset_existing_budget(ledger):
    with pytest.raises(FileExistsError): ResourceLedger.create(ledger.root, plan())

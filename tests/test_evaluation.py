"""Adversarial budget, cache, crash and provenance cases, using synthetic usage."""
import copy
import json

import pytest

from ai4math.evaluation import EvaluationError, EvaluationLedger, READY, native_total


def plan(scope="fresh_evolution_full_cost"):
    return {"schema_version": 1, "comparison_scope": scope, "arms": ["A", "B", "C"],
            "tasks": {"task1": "1" * 64, "task2": "2" * 64}, "per_task_token_cap": 100,
            "development_token_cap": 100, "max_development_calls": 2, "max_solve_calls_per_task": 2,
            "model": {"provider": "synthetic", "model": "fake", "revision": "test-fixture-v1", "settings_sha256": "a" * 64},
            "initial_methods_sha256": "b" * 64,
            "evolved_methods_sha256": "c" * 64 if scope == "inference_only" else None,
            "readiness": {k: True for k in READY}}


@pytest.fixture
def ledger(tmp_path):
    return EvaluationLedger.create(tmp_path / "ledger", plan())


def request(ledger, key="r1", arm="A", phase="solve", task="task1", incoming=30, outgoing=20):
    return {"id": key, "arm": arm, "phase": phase, "task": task,
            "task_sha256": ledger.plan["tasks"].get(task), "model": copy.deepcopy(ledger.plan["model"]),
            "request_sha256": "d" * 64, "input_counter": "provider_exact", "input_tokens": incoming,
            "max_output_tokens": outgoing, "enforced_output_cap": outgoing,
            "methods_sha256": None if arm == "A" else ledger.plan["initial_methods_sha256"] if phase == "develop" or arm == "B" else ledger.state()["c_methods_sha256"]}


def usage(incoming=30, output=10, cached=0):
    return {"inputTokens": incoming - cached, "outputTokens": output, "cacheReadTokens": cached,
            "cacheWriteTokens": 0, "reasoningTokens": output // 2, "totalTokens": incoming + output}


def test_cache_and_reasoning_are_not_double_counted(ledger):
    ledger.reserve(request(ledger))
    ledger.settle("r1", usage(cached=20))
    assert ledger.audit()["arms"]["A"]["known_native_tokens"] == 40
    assert native_total(usage(cached=20)) == 40


def test_missing_cache_fields_use_authoritative_total():
    assert native_total({"inputTokens": 10, "outputTokens": 5, "totalTokens": 40}) == 40


def test_unknown_failure_retains_reservation_and_blocks_retry(ledger):
    ledger.reserve(request(ledger))
    result = ledger.settle("r1", None, "timeout")
    assert result["charge"] is None and result["invalid"]
    audit = ledger.audit()["arms"]["A"]
    assert audit["charged_or_reserved_tokens"] == "50" and audit["cost_unknown_calls"] == ["r1"]
    assert audit["total_native_tokens"] is None and not audit["all_costs_known"]
    assert not audit["continuation_state_clear"] and audit["terminal_failure_calls"] == ["r1"]
    with pytest.raises(EvaluationError, match="invalidated"):
        ledger.reserve(request(ledger, "retry"))


def test_interrupted_reservation_survives_reopen(ledger):
    ledger.reserve(request(ledger))
    reopened = EvaluationLedger(ledger.root)
    assert reopened.audit()["arms"]["A"]["pending_calls"] == ["r1"]
    with pytest.raises(EvaluationError, match="unfinished"):
        reopened.reserve(request(reopened, "retry"))


def test_failed_request_with_exact_usage_is_charged(ledger):
    ledger.reserve(request(ledger))
    ledger.settle("r1", usage(), "failed")
    assert ledger.audit()["arms"]["A"]["total_native_tokens"] == 40
    assert ledger.audit()["arms"]["A"]["all_costs_known"]
    ledger.reserve(request(ledger, "retry", incoming=30, outgoing=30))
    assert ledger.audit()["arms"]["A"]["charged_or_reserved_tokens"] == "100"


def test_native_overrun_is_recorded_and_taints_arm(ledger):
    ledger.reserve(request(ledger))
    result = ledger.settle("r1", usage(output=21))
    assert result["charge"] == 51 and result["invalid"] == "provider token bound exceeded"
    with pytest.raises(EvaluationError, match="invalidated"):
        ledger.reserve(request(ledger, "retry"))


def test_hidden_input_tokens_invalidate_exact_preflight(ledger):
    ledger.reserve(request(ledger))
    result = ledger.settle("r1", usage(incoming=31))
    assert result["invalid"] == "provider input differs from exact preflight count"


@pytest.mark.parametrize("changed,match", [
    ({"input_counter": "char_estimate"}, "heuristic"),
    ({"model": {"provider": "other"}}, "drift"),
    ({"task_sha256": "e" * 64}, "task drift"),
    ({"methods_sha256": "f" * 64}, "snapshot drift"),
    ({"max_output_tokens": True, "enforced_output_cap": True}, "invalid integer"),
    ({"enforced_output_cap": 19}, "cap differs"),
])
def test_rejects_changed_request_conditions(ledger, changed, match):
    value = request(ledger)
    value.update(changed)
    with pytest.raises(EvaluationError, match=match):
        ledger.reserve(value)
    assert len(ledger.state()["calls"]) == 0


def test_development_cost_is_allocated_before_solving(ledger):
    ledger.reserve(request(ledger, arm="C", phase="develop", task=None, incoming=20, outgoing=40))
    ledger.settle("r1", usage(incoming=20, output=30))
    ledger.freeze_c("e" * 64)
    assert ledger.audit()["arms"]["C"]["task_exposure_with_development"] == {"task1": "25", "task2": "25"}
    ledger.reserve(request(ledger, "solve1", "C", incoming=30, outgoing=45))
    ledger.settle("solve1", usage(output=45))
    with pytest.raises(EvaluationError, match="including shared development"):
        ledger.reserve(request(ledger, "retry", "C", incoming=0, outgoing=1))
    ledger.reserve(request(ledger, "solve2", "C", task="task2", incoming=30, outgoing=45))
    assert ledger.audit()["arms"]["C"]["charged_or_reserved_tokens"] == "200"


def test_no_unmetered_full_cost_method_snapshot(tmp_path):
    value = plan()
    value["evolved_methods_sha256"] = "c" * 64
    with pytest.raises(EvaluationError, match="learned prospectively"):
        EvaluationLedger.create(tmp_path / "bad", value)


def test_c_cannot_solve_or_freeze_without_metered_development(ledger):
    with pytest.raises(EvaluationError, match="not frozen"):
        ledger.reserve(request(ledger, arm="C"))
    with pytest.raises(EvaluationError, match="metered development"):
        ledger.freeze_c("e" * 64)


def test_development_cannot_restart_after_freeze(ledger):
    ledger.reserve(request(ledger, arm="C", phase="develop", task=None))
    ledger.settle("r1", usage())
    ledger.freeze_c("e" * 64)
    with pytest.raises(EvaluationError, match="after method freeze"):
        ledger.reserve(request(ledger, "revision", "C", "develop", task=None))


def test_inference_only_scope_is_explicit(tmp_path):
    value = plan("inference_only")
    instance = EvaluationLedger.create(tmp_path / "inference", value)
    instance.reserve(request(instance, arm="C"))
    assert instance.audit()["comparison_scope"] == "inference_only"


def test_unready_plan_cannot_dispatch(tmp_path):
    value = plan()
    value["readiness"]["semantic_split_reviewed"] = False
    instance = EvaluationLedger.create(tmp_path / "draft", value)
    with pytest.raises(EvaluationError, match="readiness"):
        instance.reserve(request(instance))


def test_attempt_ids_and_attempt_budgets_never_reset(ledger):
    for number in range(2):
        key = "r" + str(number)
        ledger.reserve(request(ledger, key, incoming=1, outgoing=1))
        ledger.settle(key, usage(incoming=1, output=0), "failed")
    with pytest.raises(EvaluationError, match="attempt budget"):
        ledger.reserve(request(ledger, "third", incoming=1, outgoing=1))
    with pytest.raises(EvaluationError, match="id already consumed"):
        ledger.reserve(request(ledger, "r0"))


def test_duplicate_settlement_is_rejected(ledger):
    ledger.reserve(request(ledger))
    ledger.settle("r1", usage())
    with pytest.raises(EvaluationError, match="already settled"):
        ledger.settle("r1", usage())


def test_tampered_plan_and_partial_journal_fail_closed(ledger):
    path = ledger.root / "plan.json"
    value = json.loads(path.read_text())
    value["per_task_token_cap"] *= 2
    path.write_text(json.dumps(value))
    with pytest.raises(EvaluationError, match="plan changed"):
        ledger.audit()
    path.write_text(json.dumps(ledger.plan))
    with (ledger.root / "events.jsonl").open("a") as stream:
        stream.write('{"partial":')
    with pytest.raises(EvaluationError, match="truncated"):
        EvaluationLedger(ledger.root).audit()


def test_lock_is_not_silently_removed(ledger):
    lock = ledger.root / ".write-lock"
    lock.write_text("external-owner")
    with pytest.raises(EvaluationError, match="locked"):
        ledger.reserve(request(ledger))
    assert lock.read_text() == "external-owner"


@pytest.mark.parametrize("patch", [{"totalTokens": 1}, {"totalTokens": None}, {"reasoningTokens": 99}, {"inputTokens": -1}])
def test_malformed_native_usage_is_not_treated_as_free(ledger, patch):
    ledger.reserve(request(ledger))
    value = usage()
    value.update(patch)
    result = ledger.settle("r1", value)
    assert result["invalid"] and ledger.audit()["arms"]["A"]["charged_or_reserved_tokens"] == "50"

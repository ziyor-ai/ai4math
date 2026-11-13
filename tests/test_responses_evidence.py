"""Stateless Responses projections and synthetic native receipt edge cases."""

import json

import pytest

from ai4math.evaluation import EvaluationError
from ai4math.responses_evidence import inspect_text_response, observe_input_count, prepare_text_wire


def fixture():
    wire = prepare_text_wire("test-model-snapshot", "Use exact arithmetic.", "6 * 7?", 20)
    count = observe_input_count(wire, {"object": "response.input_tokens", "input_tokens": 12})
    response = {
        "object": "response", "id": "resp_fixture", "model": wire.model, "status": "completed",
        "max_output_tokens": 20, "usage": {
            "input_tokens": 12, "input_tokens_details": {"cached_tokens": 3, "cache_write_tokens": 2},
            "output_tokens": 10, "output_tokens_details": {"reasoning_tokens": 4}, "total_tokens": 22,
        },
    }
    return wire, count, response


def test_wire_projection_and_disjoint_usage():
    wire, count, response = fixture()
    assert json.loads(wire.create_body) == {
        **json.loads(wire.count_body), "store": False, "stream": False, "max_output_tokens": 20,
    }
    result = inspect_text_response(wire, count, response)
    assert result["ledger_usage"] == {
        "inputTokens": 7, "outputTokens": 10, "cacheReadTokens": 3,
        "cacheWriteTokens": 2, "reasoningTokens": 4, "totalTokens": 22,
    }
    assert result["issues"] == []


def test_count_rejects_cross_request_or_cap_change():
    wire, count, response = fixture()
    changed = prepare_text_wire(wire.model, "Use exact arithmetic.", "6 * 7?", 21)
    with pytest.raises(EvaluationError, match="another request"):
        inspect_text_response(changed, count, response)


def test_incomplete_native_usage_preserves_aggregate_and_does_not_impute_cache_write():
    wire, count, response = fixture()
    del response["usage"]["input_tokens_details"]["cache_write_tokens"]
    response["status"] = "incomplete"
    observed = inspect_text_response(wire, count, response)
    assert observed["provider_status"] == "incomplete"
    assert observed["native_usage"]["total_tokens"] == 22
    assert observed["ledger_usage"] is None
    assert observed["issues"] == ["native_cache_split_incomplete"]


def test_count_cap_and_model_mismatches_preserve_known_usage():
    wire, count, response = fixture()
    response.update(model="another-revision", max_output_tokens=22)
    response["usage"].update(input_tokens=13, output_tokens=21, total_tokens=34)
    result = inspect_text_response(wire, count, response)
    assert result["issues"] == [
        "returned_model_mismatch", "returned_output_cap_mismatch",
        "preflight_input_count_mismatch", "output_token_cap_exceeded",
    ]
    assert result["ledger_usage"]["totalTokens"] == 34


@pytest.mark.parametrize("patch,error", [
    ({"total_tokens": 23}, "native total differs"),
    ({"input_tokens_details": {"cached_tokens": 12, "cache_write_tokens": 1}}, "inputTokens"),
])
def test_inconsistent_native_counters_rejected(patch, error):
    wire, count, response = fixture()
    response["usage"].update(patch)
    with pytest.raises(EvaluationError, match=error):
        inspect_text_response(wire, count, response)

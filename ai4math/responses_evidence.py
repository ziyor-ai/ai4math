"""Wire binding and usage inspection for stateless OpenAI Responses text calls.

These helpers perform no transport or authentication. A matching model string
is a consistency check, not a provider attestation of an immutable revision.
"""

from dataclasses import dataclass
import hashlib

from .evaluation import encoded, integer, native_total, require


def _sha(data):
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class TextWire:
    model: str
    max_output_tokens: int
    count_body: bytes
    create_body: bytes
    reasoning_effort: str | None = None

    @property
    def request_sha256(self):
        return _sha(self.create_body)


def prepare_text_wire(model, instructions, prompt, max_output_tokens, *, reasoning_effort=None):
    """Freeze both endpoint bodies from the same supported input projection."""
    require(type(model) is str and model.strip() == model and bool(model), "explicit model ID required")
    require(type(instructions) is str and type(prompt) is str and bool(prompt), "text input required")
    integer(max_output_tokens, "max_output_tokens", positive=True)
    shared = {"model": model, "instructions": instructions, "input": prompt, "truncation": "disabled"}
    create = {**shared, "max_output_tokens": max_output_tokens, "store": False, "stream": False}
    if reasoning_effort is not None:
        require(isinstance(reasoning_effort, str) and bool(reasoning_effort.strip()), "invalid reasoning effort")
        create["reasoning"] = {"effort": reasoning_effort}
    return TextWire(model, max_output_tokens, encoded(shared), encoded(create), reasoning_effort)


@dataclass(frozen=True)
class CountObservation:
    request_sha256: str
    count_body_sha256: str
    response_sha256: str
    input_tokens: int


def observe_input_count(wire, response):
    require(type(response) is dict and response.get("object") == "response.input_tokens", "invalid count response")
    return CountObservation(wire.request_sha256, _sha(wire.count_body), _sha(encoded(response)),
                            integer(response.get("input_tokens"), "input_tokens"))


def inspect_text_response(wire, count, response):
    """Retain evidence and identify revision, count, cap, or usage gaps."""
    require(count.request_sha256 == wire.request_sha256 and count.count_body_sha256 == _sha(wire.count_body),
            "input count belongs to another request")
    require(type(response) is dict and response.get("object") == "response"
            and type(response.get("id")) is str and bool(response["id"]), "invalid generation response")
    issues = []
    if response.get("model") != wire.model:
        issues.append("returned_model_mismatch")
    if wire.reasoning_effort is not None:
        reasoning = response.get("reasoning")
        effort = reasoning.get("effort") if isinstance(reasoning, dict) else None
        if effort != wire.reasoning_effort:
            issues.append("returned_reasoning_effort_mismatch")
    if type(response.get("max_output_tokens")) is not int or response["max_output_tokens"] != wire.max_output_tokens:
        issues.append("returned_output_cap_mismatch")
    status = response.get("status")
    if status not in ("completed", "incomplete", "failed", "cancelled"):
        issues.append("terminal_status_unconfirmed")
    usage = response.get("usage")
    normalized = None
    if type(usage) is not dict:
        issues.append("native_usage_missing")
    else:
        incoming = integer(usage.get("input_tokens"), "input_tokens")
        outgoing = integer(usage.get("output_tokens"), "output_tokens")
        total = integer(usage.get("total_tokens"), "total_tokens")
        require(total == incoming + outgoing, "native total differs from inclusive input plus output")
        if incoming != count.input_tokens:
            issues.append("preflight_input_count_mismatch")
        if outgoing > wire.max_output_tokens:
            issues.append("output_token_cap_exceeded")
        details = usage.get("input_tokens_details")
        reads = details.get("cached_tokens") if type(details) is dict else None
        writes = details.get("cache_write_tokens") if type(details) is dict else None
        if reads is None or writes is None:
            issues.append("native_cache_split_incomplete")
        else:
            integer(reads, "cached_tokens")
            integer(writes, "cache_write_tokens")
            normalized = {"inputTokens": incoming - reads - writes, "outputTokens": outgoing,
                          "cacheReadTokens": reads, "cacheWriteTokens": writes, "totalTokens": total}
            output_details = usage.get("output_tokens_details")
            if type(output_details) is dict and output_details.get("reasoning_tokens") is not None:
                normalized["reasoningTokens"] = output_details["reasoning_tokens"]
            native_total(normalized)
    return {
        "schema": "responses-text-observation-v1",
        "request_sha256": wire.request_sha256,
        "count_response_sha256": count.response_sha256,
        "generation_response_sha256": _sha(encoded(response)),
        "response_id": response["id"],
        "returned_model": response.get("model"),
        "provider_status": status,
        "native_usage": usage,
        "ledger_usage": normalized,
        "issues": issues,
    }


def settle_text_response(ledger, request_id, wire, count, response):
    """Reconcile an exact Responses call; leave suspect reservations pending."""
    observation = inspect_text_response(wire, count, response)
    require(not observation["issues"], "provider evidence incomplete: " + ", ".join(observation["issues"]))
    call = ledger.state()["calls"].get(request_id)
    require(call is not None, "unknown model request")
    if ledger.plan["schema_version"] == 2:
        require(wire.reasoning_effort == call["model"]["reasoning_effort"], "wire reasoning effort differs from model lock")
    status = {"completed": "completed", "incomplete": "incomplete", "failed": "failed",
              "cancelled": "interrupted"}[observation["provider_status"]]
    return ledger.settle(request_id, observation["ledger_usage"], status, receipt={
        "request_sha256": wire.request_sha256, "model": call["model"], "raw_response": response,
    })

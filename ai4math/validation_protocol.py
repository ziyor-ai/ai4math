"""Frozen validator requests and separately admitted results for v2c4."""
from __future__ import annotations

import json
import math

from .request_identity import result_binding
from .store import ResearchError, digest, identifier

REQUEST_SCHEMA = "ai4math-validation-request/v1"
RESULT_SCHEMA = "ai4math-validation-result/v1"
CLAIM_METHODS = {"finite_certificate", "exhaustive_search", "counterexample", "formal_kernel"}
PURPOSES = {"claim", "finite_replay", "candidate_score"}


def _strings(value, name):
    if not isinstance(value, list) or not value or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        raise ResearchError(f"validation {name} must be a nonempty string list")
    return value


def _unique(value, name):
    values = _strings(value, name)
    if len(set(values)) != len(values):
        raise ResearchError(f"validation {name} must be distinct")
    return sorted(values)


def _producer_binding(workbench, task, definition, *, candidate_score):
    from .research_flow import stale

    producer = workbench.store.get("task", identifier(definition.get("producer_task")))
    if (producer["status"] != "completed" or producer["role"] not in {"explore", "produce"}
            or producer["node"] != task["node"]
            or producer["node_snapshot"]["statement_digest"] != task["node_snapshot"]["statement_digest"]):
        raise ResearchError("validation needs a completed producer on the same locked statement")
    if "proof_basis" in (producer.get("stale_basis") or []) or stale(workbench, producer):
        raise ResearchError("validation needs a producer with a current proof basis")
    producer_actors = {producer["actor"], producer.get("executed_by"),
                       producer.get("codex_workflow", {}).get("worker_actor")}
    if task["actor"] in producer_actors:
        raise ResearchError("validation task actor must differ from producer actors")
    outputs = set(producer.get("output_artifacts", [])) | {producer.get("result_artifact")}
    outputs -= {None, producer.get("contract_artifact"), producer.get("prompt_artifact")}
    seal = producer.get("seal_receipt")
    if producer.get("execution_mode") == "external":
        if not seal:
            raise ResearchError("external production must have a frozen seal before validation")
        workbench.store.verify_blob(seal["artifact_id"])
        outputs &= set(seal["output_artifacts"])
    artifacts = _unique(definition.get("producer_artifacts"), "producer_artifacts")
    if not set(artifacts) <= outputs or not set(artifacts) <= set(task["inputs"]):
        raise ResearchError("validation must consume exact frozen producer outputs in its inputs")
    if candidate_score:
        result = producer.get("result_artifact")
        if len(artifacts) != 1 or (result is not None and artifacts != [result]):
            raise ResearchError("candidate scoring must bind one exact frozen producer result")
    for artifact in artifacts:
        workbench.store.verify_blob(artifact)
    return {"task": producer["id"], "artifacts": artifacts, "seal": seal["artifact_id"] if seal else None}


def _claim_version(claim):
    fields = ("id", "node", "statement_digest", "evidence_level", "scope", "summary", "artifact_ids",
              "evidence_domain", "dependencies", "proof_plan", "proof_route")
    return digest({field: claim.get(field) for field in fields})


def _claim_binding(workbench, task, claim_id, method):
    from .verification import recheck_required, toolchain_recheck_required

    claim = workbench.store.get("claim", identifier(claim_id))
    can_recheck = (claim["status"] == "accepted"
                   and (recheck_required(workbench, claim) or toolchain_recheck_required(workbench, claim)))
    if (claim["status"] != "proposed" and not can_recheck) or claim["node"] != task["node"]:
        raise ResearchError("validation must target a proposed claim or a claim requiring a recheck on this node")
    if claim["statement_digest"] != task["node_snapshot"]["statement_digest"]:
        raise ResearchError("validation claim statement differs from the task")
    if claim["author"] == task["actor"]:
        raise ResearchError("validation task actor must differ from claim author")
    if not claim["artifact_ids"] or not set(claim["artifact_ids"]) <= set(task["inputs"]):
        raise ResearchError("validation task must bind all frozen claim artifacts")
    if method not in CLAIM_METHODS or claim["evidence_level"] not in {"proved", "disproved"}:
        raise ResearchError("claim validation needs an executable certificate method and a proof or refutation candidate")
    if method in {"finite_certificate", "exhaustive_search"} and (
        claim["scope"] != "finite" or claim["evidence_level"] != "proved"
    ):
        raise ResearchError("finite checks certify only finite proved claims")
    if method == "counterexample" and claim["evidence_level"] != "disproved":
        raise ResearchError("counterexample must explicitly refute the bound statement")
    if method == "formal_kernel" and claim["evidence_level"] != "proved":
        raise ResearchError("formal kernel certificate must prove the statement")
    for dependency_id in claim["dependencies"]:
        dependency = workbench.store.get("claim", dependency_id)
        if dependency["status"] != "accepted" or recheck_required(workbench, dependency):
            raise ResearchError("validation claim has a revoked or stale proof dependency")
    return claim


def _request(workbench, task, definition):
    purpose = definition.get("purpose") if isinstance(definition, dict) else None
    if not isinstance(purpose, str) or purpose not in PURPOSES:
        raise ResearchError("validate task needs validation_request with purpose claim, finite_replay or candidate_score")
    fields = {"purpose", "method", "cases"}
    fields |= {"claim"} if purpose == "claim" else {"producer_task", "producer_artifacts"}
    if set(definition) - fields:
        raise ResearchError("unknown fields in validation_request")
    method = definition.get("method")
    if purpose == "candidate_score":
        if method is not None and method != "candidate_score":
            raise ResearchError("candidate scoring method is candidate_score")
        method = "candidate_score"
    elif not isinstance(method, str) or not method.strip():
        raise ResearchError("validation request needs a method")
    request = {"schema": REQUEST_SCHEMA, "task_id": task["id"], "node": task["node"],
               "statement_digest": task["node_snapshot"]["statement_digest"],
               "input_artifacts": sorted(task["inputs"]), "validator_artifact": task["validator_artifact"],
               "purpose": purpose, "method": method, "scope": "finite", "evidence_level": "finite_support"}
    if purpose == "claim":
        claim = _claim_binding(workbench, task, definition.get("claim"), method)
        request.update(claim=claim["id"], claim_sha256=_claim_version(claim),
                       scope=claim["scope"], evidence_level=claim["evidence_level"])
    else:
        request["producer"] = _producer_binding(workbench, task, definition, candidate_score=purpose == "candidate_score")
    if request["scope"] == "finite" or "cases" in definition:
        request["cases"] = _unique(definition.get("cases"), "cases")
    if purpose == "candidate_score":
        context = task.get("exploration_context", {})
        candidate, protocol = context.get("candidate"), context.get("evaluation_protocol")
        if not candidate or not protocol:
            raise ResearchError("candidate scoring requires the frozen exploration candidate and evaluation protocol")
        producer = workbench.store.get("task", request["producer"]["task"])
        if producer.get("exploration_context", {}).get("candidate") != candidate:
            raise ResearchError("candidate scoring producer must use the same exact candidate")
        expected = set(candidate["artifact_ids"]) | set(protocol["benchmark_artifacts"]) | set(request["producer"]["artifacts"])
        if set(task["inputs"]) != expected or task["validator_artifact"] != protocol["validator_artifact"]:
            raise ResearchError("candidate scoring requires the exact design, benchmark, result and protocol checker")
        request.update(candidate=candidate["id"], candidate_digest=candidate["candidate_digest"],
                       evaluation_protocol_sha256=protocol["sha256"], metrics=sorted(protocol["metrics"]),
                       accepts_mathematical_claim=False)
    request.update(result_binding(task))
    return {**request, "sha256": digest(request)}


def register(workbench, task, payload):
    from .policy import strict_research

    if task["role"] == "validate" and strict_research(workbench):
        task["validation_request"] = _request(workbench, task, payload.get("validation_request"))
    elif "validation_request" in payload:
        raise ResearchError("validation_request is available only for validation tasks under v2c4")


def _definition(request):
    definition = {key: request[key] for key in ("purpose", "method", "cases", "claim") if key in request}
    if "producer" in request:
        definition.update(producer_task=request["producer"]["task"], producer_artifacts=request["producer"]["artifacts"])
    return definition


def check(workbench, task, *, executed_by=None):
    """Check the registered request against current premises before dispatch or admission."""
    request = task.get("validation_request")
    if request is None:
        from .policy import strict_research
        if task["role"] == "validate" and task["status"] == "pending" and strict_research(workbench):
            raise ResearchError("pending validator predates v2c4; prepare a new validation request")
        return
    current_node = workbench.store.get("node", task["node"])
    if any(current_node[field] != task["node_snapshot"][field]
           for field in ("statement_digest", "proof_dependencies")):
        raise ResearchError("validation node basis changed; create a new validation task")
    definition = _definition(request)
    if _request(workbench, task, definition) != request:
        raise ResearchError("validation request basis changed; create a new validation task")
    if executed_by is not None:
        _request(workbench, {**task, "actor": executed_by}, definition)


def _result_fields(request):
    fields = {key: value for key, value in request.items() if key not in {"schema", "sha256", "metrics"}}
    return {**fields, "schema": RESULT_SCHEMA, "validation_request_sha256": request["sha256"]}


def result_contract(request):
    expected = _result_fields(request)
    if "metrics" in request:
        expected["metrics"] = dict.fromkeys(request["metrics"], "<finite number>")
    return {**expected, "passed": "<boolean>", "checks": ["<independent check>"],
            "limitations": ["<scope and trust limit>"]}


def _read_result(path, request):
    def unique_pairs(pairs):
        row = dict(pairs)
        if len(row) != len(pairs):
            raise ResearchError("validation result contains duplicate JSON keys")
        return row

    try:
        row = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique_pairs)
        if not isinstance(row, dict):
            raise ResearchError("validation result must be a JSON object")
        digest(row)
    except (ValueError, OverflowError, RecursionError) as exc:
        raise ResearchError("validation result must be valid UTF-8 JSON with finite values") from exc
    for key, expected in _result_fields(request).items():
        if row.get(key) != expected:
            raise ResearchError("validation result does not match frozen " + key)
    if type(row.get("passed")) is not bool:
        raise ResearchError("validation result passed must be a boolean")
    for key in ("checks", "limitations"):
        _strings(row.get(key), key)
    if "metrics" in request:
        metrics = row.get("metrics")
        if (not isinstance(metrics, dict) or set(metrics) != set(request["metrics"])
                or any(type(value) not in (int, float) or (isinstance(value, float) and not math.isfinite(value))
                       for value in metrics.values())):
            raise ResearchError("validation metrics must match the frozen protocol with finite numbers")
    if "valid" in row and row["valid"] is not row["passed"]:
        raise ResearchError("validation result contains conflicting verdicts")
    return row


def finish(workbench, task, actor, result_path):
    """Archive raw results, then record whether they can be used as a receipt."""
    request = task["validation_request"]
    outcome = {"process_status": task["status"], "protocol_status": "not_run", "verdict": "unavailable",
               "request_sha256": request["sha256"]}
    task["validation_outcome"] = outcome
    if result_path.is_symlink():
        outcome.update(protocol_status="rejected", error="result.json must be an ordinary task file")
        return
    if result_path.is_file():
        limit = task["output_limit_bytes"]
        if limit is not None and result_path.stat().st_size > limit:
            outcome.update(protocol_status="rejected", error="result.json exceeded the output limit")
            return
        try:
            artifact = workbench._artifact({"path": str(result_path), "purpose": f"task {task['id']}: raw validation result"}, actor)
        except (ResearchError, OSError) as exc:
            outcome.update(protocol_status="rejected", error=str(exc))
            return
        outcome["raw_artifact"] = artifact["id"]
        task["output_artifacts"].append(artifact["id"])
    if task["status"] != "completed":
        return
    if not outcome.get("raw_artifact"):
        outcome.update(protocol_status="rejected", error="validator did not write result.json")
        return
    if not task.get("contract_unchanged"):
        outcome.update(protocol_status="rejected", error="validator changed the frozen input contract")
        return
    try:
        result = _read_result(workbench.store.verify_blob(outcome["raw_artifact"]), request)
    except (ResearchError, OSError) as exc:
        outcome.update(protocol_status="rejected", error=str(exc))
        return
    task["result_artifact"] = outcome["raw_artifact"]
    outcome.update(protocol_status="valid", verdict="passed" if result["passed"] else "failed",
                   result_artifact=task["result_artifact"])


def record_interruption(task):
    if "validation_request" in task:
        task["validation_outcome"] = {"process_status": task["status"], "protocol_status": "not_run",
                                      "verdict": "unavailable", "request_sha256": task["validation_request"]["sha256"]}


def consume(workbench, task, purpose, *, passed_required):
    outcome = task.get("validation_outcome") or {}
    request = task.get("validation_request") or {}
    if (request.get("purpose") != purpose or task["status"] != "completed"
            or outcome.get("protocol_status") != "valid" or not task.get("result_artifact")
            or task["result_artifact"] != outcome.get("result_artifact")
            or request["sha256"] != outcome.get("request_sha256")):
        raise ResearchError("independent validation needs a completed, protocol-valid frozen receipt for " + purpose)
    check(workbench, task, executed_by=task.get("executed_by"))
    result = _read_result(workbench.store.verify_blob(task["result_artifact"]), request)
    if passed_required and result["passed"] is not True:
        raise ResearchError("independent validation verdict must be passed")
    return result

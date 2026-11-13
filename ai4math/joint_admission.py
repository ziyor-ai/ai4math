"""Joint ledger checks and trusted-host engineering dispatch."""

from copy import deepcopy

from .evaluation import digest, require


_SHARED_PLAN_FIELDS = ("comparison_scope", "tasks", "initial_methods_sha256", "evolved_methods_sha256")
_PARENT_CONTEXT = ("arm", "phase", "task", "task_sha256", "methods_sha256")


def audit_joint_ledgers(model_ledger, resource_ledger):
    """Report plan pairing and cross-ledger blockers without creating reservations."""
    model, resource = _paired_states(model_ledger, resource_ledger)
    return _audit(model_ledger, resource_ledger, model, resource)


def _paired_states(model_ledger, resource_ledger, resource_state=None):
    model_plan, resource_plan = model_ledger.plan, resource_ledger.plan
    require(model_ledger.root != resource_ledger.root, "joint ledgers must have distinct roots")
    require(digest(model_plan) == model_ledger.plan_hash and digest(resource_plan) == resource_ledger.plan_hash,
            "loaded joint plan changed")
    require(resource_plan["model_plan_sha256"] == model_ledger.plan_hash, "resource ledger is bound to another model plan")
    require(set(model_plan["arms"]) == set(resource_plan["arms"]), "joint active arms differ")
    for field in _SHARED_PLAN_FIELDS:
        require(model_plan[field] == resource_plan[field], f"joint plan mismatch: {field}")

    model = model_ledger.state()
    resource = resource_ledger.state() if resource_state is None else resource_state
    require(model["c_methods_sha256"] == resource["c_methods_sha256"], "joint C method freeze differs")
    require(not model["calls"].keys() & resource["calls"].keys(), "joint action ID reused across ledgers")
    return model, resource


def _valid_parent(model, call):
    parent_id = call["parent_model_request_id"]
    if parent_id is None:
        return True
    parent = model["calls"].get(parent_id)
    return bool(parent is not None and parent["status"] == "completed"
                and parent["charge"] is not None and not parent["invalid"]
                and all(parent.get(field) == call[field] for field in _PARENT_CONTEXT))


def _audit(model_ledger, resource_ledger, model, resource):
    blockers = {arm: [] for arm in model_ledger.plan["arms"]}
    for request_id, call in model["calls"].items():
        if call["status"] == "pending" or call["charge"] is None or call["invalid"]:
            blockers[call["arm"]].append({"source": "model", "id": request_id, "reason": "unfinished_or_unresolved"})

    for request_id, call in resource["calls"].items():
        if (call["status"] == "pending" or call["violations"] or call["peak_memory_bytes"] is None
                or any(value is None for value in call["charge"].values())):
            blockers[call["arm"]].append({"source": "resource", "id": request_id, "reason": "unfinished_or_unresolved"})
        if not _valid_parent(model, call):
            blockers[call["arm"]].append({"source": "resource", "id": request_id, "reason": "invalid_parent_model_action"})

    return {
        "schema": "joint-ledger-audit-v1",
        "model_plan_sha256": model_ledger.plan_hash,
        "resource_plan_sha256": resource_ledger.plan_hash,
        "model_head_sha256": model["events"][-1]["sha256"],
        "resource_head_sha256": resource["events"][-1]["sha256"],
        "arm_blockers": blockers,
    }


def reserve_engineering_resource(model_ledger, resource_ledger, request):
    """Append the joint intent while both ledger writers are excluded.

    Lock order is always model, then resource. The resource reservation is the
    durable intent. Failures after reservation retain the pending exposure.
    """
    require(resource_ledger.plan["mode"] == "engineering_diagnostic", "engineering resource ledger required")
    require(model_ledger.root != resource_ledger.root, "joint ledgers must have distinct roots")

    def admit(record, resource_state):
        model, resource = _paired_states(model_ledger, resource_ledger, resource_state)
        audit = _audit(model_ledger, resource_ledger, model, resource)
        require(record["id"] not in model["calls"], "joint action ID reused across ledgers")
        require(not audit["arm_blockers"][record["arm"]], "joint arm blocked by pending or unresolved usage")
        require(_valid_parent(model, record), "invalid parent model action")
        return {
            "schema": "joint-engineering-intent-v1",
            "model_plan_sha256": audit["model_plan_sha256"],
            "resource_plan_sha256": audit["resource_plan_sha256"],
            "model_head_sha256": audit["model_head_sha256"],
            "resource_head_sha256": audit["resource_head_sha256"],
        }

    with model_ledger._lock():
        return resource_ledger._reserve(request, joint_admission=admit)


def dispatch_engineering_resource(model_ledger, resource_ledger, request, staged, program):
    """Reserve, run and settle one host-staged Python tool or verifier.

    This engineering path uses the Windows probe limits and further bounds its
    worker deadline by the reservation. Actual usage is settled afterward.
    """
    from .windows_isolation import FileRequest

    require(isinstance(staged, FileRequest), "host-staged FileRequest required")
    require(isinstance(request, dict), "resource request must be an object")
    request = deepcopy(request)
    staged.verify_frozen()
    files = {row["target"]: row for row in staged.manifest["files"]}
    require(type(program) is str and program.startswith("program/") and program in files,
            "frozen Python program required")
    require("runtime/python.exe" in files, "frozen Python runtime required")
    inputs = [files[name] for name in sorted(files) if name.startswith("input/")]
    require(request.get("id") == staged.identity["request_id"]
            and request.get("policy_sha256") == staged.identity["protocol_sha256"]
            and request.get("tool_sha256") == files[program]["sha256"]
            and request.get("input_sha256") == digest(inputs), "resource reservation differs from staged request")
    if request.get("arm") in ("B", "C"):
        require(request.get("methods_sha256") == staged.identity["method_sha256"], "staged method snapshot differs")
    bound = request.get("bound")
    require(isinstance(bound, dict) and type(bound.get("input_bytes")) is int
            and sum(row["size"] for row in inputs) <= bound["input_bytes"], "staged inputs exceed resource reservation")
    wall_key = "tool_wall_microseconds" if request.get("kind") == "tool" else "verification_wall_microseconds"
    wall_bound = bound.get(wall_key)
    require(type(wall_bound) is int and wall_bound > 0, "positive worker wall budget required")
    wall_seconds = min(wall_bound, 12_000_000) / 1_000_000
    reserve_engineering_resource(model_ledger, resource_ledger, request)
    args = ["-I", "-S", "-B", str(staged.root / program),
            *(str(staged.root / row["target"]) for row in inputs)]
    receipt = staged.run_engineering_file_probe("runtime/python.exe", args, wall_seconds=wall_seconds)
    settlement = staged.settle_engineering_resources(resource_ledger)
    return {"process_receipt": receipt, "settlement": settlement}

"""Separate tool-resource reservations; no model client or OS enforcement.

Units are integer counts, microseconds and bytes. Peak memory is per process,
never summed or divided over tasks. Readiness is an external declaration, not
proof of an OS limit. Diagnostic ledgers cannot be relabelled as studies.
"""
from __future__ import annotations

import argparse
import json
from fractions import Fraction
from pathlib import Path

from .evaluation import EvaluationError, EvaluationLedger, digest, encoded, hash_string, integer, require

COUNTS = ("tool_calls", "verification_calls")
MEASURES = ("cpu_microseconds", "tool_wall_microseconds", "verification_wall_microseconds", "input_bytes", "output_bytes")
DIMS = COUNTS + MEASURES
READY = {"os_cpu_memory_enforced", "wall_output_enforced", "metering_complete", "execution_isolated"}
STATUSES = {"completed", "failed", "timeout", "output_limit", "interrupted"}


def vector(value, name):
    require(isinstance(value, dict) and set(value) == set(DIMS), name + " dimensions differ")
    for key in DIMS:
        integer(value[key], name + "." + key)
    return value


def validate_plan(plan):
    schema = plan.get("schema")
    require(schema in {"tool-resource-plan-v1", "tool-resource-plan-v2"}, "unsupported resource plan")
    require(plan.get("mode") in {"synthetic_control_test", "engineering_diagnostic", "prospective_study"}, "mode missing")
    require(plan.get("comparison_scope") in {"inference_only", "fresh_evolution_full_cost"}, "comparison scope missing")
    require(plan.get("arms") == ["A", "B", "C"] or
            schema == "tool-resource-plan-v2" and plan.get("arms") == ["B", "C"], "active arms required with common caps")
    tasks = plan.get("tasks")
    require(isinstance(tasks, dict) and bool(tasks) and all(isinstance(k, str) and k and hash_string(v) for k, v in tasks.items()), "task hashes required")
    for key in ("per_task_cap", "development_cap", "per_call_cap"):
        vector(plan.get(key), key)
    integer(plan.get("peak_memory_cap_bytes"), "peak_memory_cap_bytes", positive=True)
    for key in ("initial_methods_sha256", "model_plan_sha256", "policy_sha256"):
        require(hash_string(plan.get(key)), key + " invalid")
    if plan["comparison_scope"] == "inference_only":
        require(hash_string(plan.get("evolved_methods_sha256")), "inference-only C snapshot required")
    else:
        require(plan.get("evolved_methods_sha256") is None, "C must be developed prospectively")
    require(isinstance(plan.get("readiness"), dict) and set(plan["readiness"]) == READY and all(type(v) is bool for v in plan["readiness"].values()), "readiness incomplete")
    return plan


class ResourceLedger(EvaluationLedger):
    """Append-only hash journal using the same fail-closed writer lock as tokens.

    A local actor and receipt hash provide traceability, not authentication.
    No automatic dispatch/retry, no cross-ledger atomic transaction is provided.
    """
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.plan = validate_plan(json.loads((self.root / "plan.json").read_text(encoding="utf-8")))
        self.plan_hash = digest(self.plan)

    @classmethod
    def create(cls, root, plan):
        validate_plan(plan)
        root = Path(root).resolve()
        root.mkdir(parents=True, exist_ok=False)
        with (root / "plan.json").open("xb") as stream:
            stream.write(encoded(plan) + b"\n")
        with (root / "events.jsonl").open("xb"):
            pass
        ledger = cls(root)
        ledger._append("init", {"plan_sha256": ledger.plan_hash})
        return ledger

    def state(self):
        events = self._events()
        require(bool(events), "missing initialization event")
        calls, frozen = {}, self.plan.get("evolved_methods_sha256")
        for event in events[1:]:
            data, kind = event["data"], event["kind"]
            if kind == "reserve":
                require(data["id"] not in calls, "duplicate reservation")
                calls[data["id"]] = {**data, "status": "pending", "charge": {**{k: data["bound"][k] for k in COUNTS}, **dict.fromkeys(MEASURES)}, "peak_memory_bytes": None, "violations": [], "reconciliations": []}
            elif kind == "settle":
                require(data["id"] in calls and calls[data["id"]]["status"] == "pending", "settlement without reservation")
                calls[data["id"]].update(data)
            elif kind == "reconcile":
                call = calls.get(data["id"])
                require(call is not None and call["status"] != "pending", "reconciliation without settlement")
                call["charge"].update(data["resolved"])
                if data["peak_memory_bytes"] is not None:
                    call["peak_memory_bytes"] = data["peak_memory_bytes"]
                call["violations"].extend(data["violations"])
                call["reconciliations"].append(data)
            elif kind == "freeze_c":
                require(frozen is None, "C frozen twice")
                frozen = data["methods_sha256"]
            else:
                raise EvaluationError("unknown resource event")
        return {"events": events, "calls": calls, "c_methods_sha256": frozen}

    @staticmethod
    def _unknown(call):
        return [k for k, v in call["charge"].items() if v is None] + (["peak_memory_bytes"] if call["peak_memory_bytes"] is None else [])

    @staticmethod
    def _exposure(call, key):
        value = call["charge"][key]
        return call["bound"][key] if value is None else value

    def _allocation(self, state, arm, task=None):
        calls = [c for c in state["calls"].values() if c["arm"] == arm]
        result = {}
        for key in DIMS:
            dev = sum(self._exposure(c, key) for c in calls if c["phase"] == "develop")
            solve = sum(self._exposure(c, key) for c in calls if c["phase"] == "solve" and (task is None or c["task"] == task))
            result[key] = Fraction(solve) + Fraction(dev, len(self.plan["tasks"]) if task is not None else 1)
        return result

    def _clear(self, call):
        return call["status"] != "pending" and not self._unknown(call) and not call["violations"]

    def reserve(self, request):
        return self._reserve(request)

    def _reserve(self, request, joint_admission=None):
        """Reserve resources, optionally checking the joint policy under this writer lock."""
        allowed = {"id", "arm", "phase", "task", "task_sha256", "kind", "tool_sha256", "policy_sha256", "input_sha256", "methods_sha256", "parent_model_request_id", "bound", "peak_memory_cap_bytes"}
        require(isinstance(request, dict) and set(request) == allowed, "request fields differ")
        with self._lock():
            state = self.state()
            require(self.plan["mode"] != "prospective_study" or all(self.plan["readiness"].values()), "study admission blocked: readiness not established")
            arm, phase, task, kind = (request[k] for k in ("arm", "phase", "task", "kind"))
            require(arm in self.plan["arms"] and phase in {"develop", "solve"} and kind in {"tool", "verify"}, "invalid arm/phase/kind")
            require(isinstance(request["id"], str) and bool(request["id"]) and request["id"] not in state["calls"], "id already consumed or invalid")
            require(request["parent_model_request_id"] is None or isinstance(request["parent_model_request_id"], str) and bool(request["parent_model_request_id"]), "invalid model request reference")
            for key in ("tool_sha256", "input_sha256"):
                require(hash_string(request[key]), key + " invalid")
            require(request["policy_sha256"] == self.plan["policy_sha256"], "policy drift")
            bound = vector(request["bound"], "bound")
            require(bound["tool_calls"] == int(kind == "tool") and bound["verification_calls"] == int(kind == "verify"), "one dispatch consumes exactly one matching call")
            inactive = "verification_wall_microseconds" if kind == "tool" else "tool_wall_microseconds"
            require(bound[inactive] == 0, "inactive wall clock must be zero")
            require(all(bound[k] <= self.plan["per_call_cap"][k] for k in DIMS), "per-call cap exceeded")
            memory = integer(request["peak_memory_cap_bytes"], "peak_memory_cap_bytes", positive=True)
            require(memory <= self.plan["peak_memory_cap_bytes"], "per-call memory cap exceeded")
            previous = [c for c in state["calls"].values() if c["arm"] == arm]
            require(all(self._clear(c) for c in previous), "arm blocked by pending, unknown or violated resources")
            if phase == "develop":
                require(arm == "C" and task is None and request["task_sha256"] is None and self.plan["comparison_scope"] == "fresh_evolution_full_cost", "development not permitted")
                require(state["c_methods_sha256"] is None, "development after freeze")
                require(request["methods_sha256"] == self.plan["initial_methods_sha256"], "development prior drift")
                used = self._allocation(state, arm)
                require(all(used[k] + bound[k] <= self.plan["development_cap"][k] for k in DIMS), "development budget exhausted")
            else:
                require(task in self.plan["tasks"] and request["task_sha256"] == self.plan["tasks"][task], "task drift")
                expected = None if arm == "A" else self.plan["initial_methods_sha256"] if arm == "B" else state["c_methods_sha256"]
                require(arm != "C" or expected is not None, "C method not frozen")
                require(request["methods_sha256"] == expected, "method snapshot drift")
                used = self._allocation(state, arm, task)
                require(all(used[k] + bound[k] <= self.plan["per_task_cap"][k] for k in DIMS), "task budget including fixed development share exhausted")
            used = self._allocation(state, arm)
            require(all(used[k] + bound[k] <= len(self.plan["tasks"]) * self.plan["per_task_cap"][k] for k in DIMS), "arm budget exhausted")
            # Copy through JSON so caller mutations cannot alter a returned reservation.
            record = json.loads(encoded(request))
            if joint_admission is not None:
                record["joint_admission"] = joint_admission(record, state)
            self._append("reserve", record)
        return record

    def _violations(self, call, charge, peak):
        result = [k + " bound exceeded" for k, v in charge.items() if v is not None and v > call["bound"][k]]
        if peak is not None and peak > call["peak_memory_cap_bytes"]:
            result.append("peak_memory_bytes bound exceeded")
        return result

    def settle(self, request_id, usage, status, evidence_sha256):
        require(status in STATUSES, "invalid terminal status")
        require(hash_string(evidence_sha256), "settlement receipt hash required")
        with self._lock():
            state = self.state()
            call = state["calls"].get(request_id)
            require(call is not None and call["status"] == "pending", "request already settled or absent")
            fields = set(MEASURES) | {"peak_memory_bytes"}
            require(usage is None or isinstance(usage, dict) and not (set(usage) - fields), "unexpected usage fields")
            values, notes = {}, []
            raw = usage or {}
            for key in fields:
                value = raw.get(key)
                if value is not None and (type(value) is not int or value < 0):
                    notes.append(key + " malformed; retained as unknown")
                    value = None
                values[key] = value
            charge = {**{k: call["bound"][k] for k in COUNTS}, **{k: values[k] for k in MEASURES}}
            inactive = "verification_wall_microseconds" if call["kind"] == "tool" else "tool_wall_microseconds"
            if values[inactive] is None:
                charge[inactive] = 0  # not executed in this dispatch, structurally known
            peak = values["peak_memory_bytes"]
            record = {"id": request_id, "status": status, "charge": charge, "peak_memory_bytes": peak,
                      "raw_usage": usage, "measurement_notes": notes, "violations": self._violations(call, charge, peak), "evidence_sha256": evidence_sha256}
            self._append("settle", record)
        return record

    def reconcile(self, request_id, resolved, peak_memory_bytes, evidence_sha256, actor, reason):
        require(hash_string(evidence_sha256) and isinstance(actor, str) and actor.strip() and isinstance(reason, str) and reason.strip(), "explicit receipt, actor and reason required")
        require(isinstance(resolved, dict) and set(resolved) <= set(MEASURES), "invalid reconciliation fields")
        for key, value in resolved.items():
            integer(value, key)
        if peak_memory_bytes is not None:
            integer(peak_memory_bytes, "peak_memory_bytes")
        require(bool(resolved) or peak_memory_bytes is not None, "nothing to reconcile")
        with self._lock():
            state = self.state()
            call = state["calls"].get(request_id)
            require(call is not None and call["status"] != "pending", "settle original terminal status first")
            require(all(call["charge"][k] is None for k in resolved) and (peak_memory_bytes is None or call["peak_memory_bytes"] is None), "known measurements cannot be overwritten")
            require(evidence_sha256 != call["evidence_sha256"] and all(evidence_sha256 != r["evidence_sha256"] for r in call["reconciliations"]), "new evidence receipt required")
            record = {"id": request_id, "resolved": resolved, "peak_memory_bytes": peak_memory_bytes,
                      "evidence_sha256": evidence_sha256, "actor": actor, "reason": reason,
                      "violations": self._violations(call, resolved, peak_memory_bytes)}
            self._append("reconcile", record)
        return record

    def freeze_c(self, methods_sha256):
        require(hash_string(methods_sha256), "method hash invalid")
        with self._lock():
            state = self.state()
            require(self.plan["comparison_scope"] == "fresh_evolution_full_cost" and state["c_methods_sha256"] is None, "C freeze not available")
            calls = [c for c in state["calls"].values() if c["arm"] == "C"]
            require(bool(calls) and all(c["phase"] == "develop" and c["status"] == "completed" and self._clear(c) for c in calls), "successful completely metered development required")
            self._append("freeze_c", {"methods_sha256": methods_sha256})

    def audit(self):
        state, arms = self.state(), {}
        for arm in self.plan["arms"]:
            calls = [c for c in state["calls"].values() if c["arm"] == arm]
            known = {k: sum(c["charge"][k] for c in calls if c["charge"][k] is not None) for k in DIMS}
            unknown = {c["id"]: self._unknown(c) for c in calls if self._unknown(c)}
            arms[arm] = {"known_subtotal": known, "all_resources_known": not unknown,
                         "total": known if not unknown else None, "unknown": unknown,
                         "pending_calls": [c["id"] for c in calls if c["status"] == "pending"],
                         "terminal_failure_calls": [c["id"] for c in calls if c["status"] not in {"pending", "completed"}],
                         "violations": {c["id"]: c["violations"] for c in calls if c["violations"]},
                         "continuation_state_clear": all(self._clear(c) for c in calls),
                         "charged_or_reserved": {k: str(v) for k, v in self._allocation(state, arm).items()},
                         "task_exposure_with_development": {task: {k: str(v) for k, v in self._allocation(state, arm, task).items()} for task in self.plan["tasks"]},
                         "max_known_peak_memory_bytes": max((c["peak_memory_bytes"] for c in calls if c["peak_memory_bytes"] is not None), default=None)}
        return {"integrity_passed": True, "plan_sha256": self.plan_hash, "events": len(state["events"]), "head_sha256": state["events"][-1]["sha256"],
                "mode": self.plan["mode"], "comparison_scope": self.plan["comparison_scope"],
                "live_resource_admission_ready": self.plan["mode"] == "prospective_study" and all(self.plan["readiness"].values()),
                "os_limits_enforced_by_this_ledger": False, "cross_ledger_atomic_dispatch": False,
                "peak_memory_accounting": "per_call_max_not_additive", "model_tokens_accounted_here": False,
                "c_methods_sha256": state["c_methods_sha256"], "arms": arms}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["init", "reserve", "settle", "reconcile", "freeze-c", "audit"])
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--input", type=Path)
    args = parser.parse_args()
    data = json.loads(args.input.read_text(encoding="utf-8")) if args.input else None
    ledger = ResourceLedger.create(args.root, data) if args.command == "init" else ResourceLedger(args.root)
    if args.command in {"init", "audit"}:
        result = ledger.audit()
    elif args.command == "reserve":
        result = ledger.reserve(data)
    elif args.command == "settle":
        result = ledger.settle(data["id"], data.get("usage"), data["status"], data["evidence_sha256"])
    elif args.command == "reconcile":
        result = ledger.reconcile(data["id"], data["resolved"], data.get("peak_memory_bytes"), data["evidence_sha256"], data["actor"], data["reason"])
    else:
        ledger.freeze_c(data["methods_sha256"])
        result = ledger.audit()
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

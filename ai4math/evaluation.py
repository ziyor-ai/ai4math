"""Prospective model-evaluation budgets, with native usage and no silent retries.

This is a control ledger, not a model client or a mathematical validator. A live
adapter must establish the readiness assertions and enforce each output cap.
Historical unmetered method development cannot enter a full-cost comparison.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from contextlib import contextmanager
from datetime import datetime, timezone
from fractions import Fraction
from pathlib import Path


class EvaluationError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise EvaluationError(message)


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def digest(value):
    return hashlib.sha256(encoded(value)).hexdigest()


def integer(value, name, positive=False):
    require(type(value) is int and value >= (1 if positive else 0), "invalid integer: " + name)
    return value


def hash_string(value):
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


READY = {"semantic_split_reviewed", "provider_input_count_exact", "output_cap_enforced",
         "native_usage_complete", "model_revision_immutable", "execution_isolated"}


def model_lock(model, *, role_bound=False):
    fields = {"provider", "model", "revision", "settings_sha256"}
    if role_bound:
        fields.add("reasoning_effort")
    require(isinstance(model, dict) and set(model) == fields, "model lock incomplete")
    require(all(isinstance(model[k], str) and model[k].strip() == model[k] and model[k]
                for k in ("provider", "model", "revision")), "model identity empty")
    require(hash_string(model["settings_sha256"]), "settings hash invalid")
    if role_bound:
        require(model["reasoning_effort"] is None or
                isinstance(model["reasoning_effort"], str) and model["reasoning_effort"].strip(),
                "invalid reasoning effort")
    return model


def validate_plan(plan):
    version = plan.get("schema_version")
    require(type(version) is int and version in (1, 2), "unsupported evaluation plan")
    require(plan.get("comparison_scope") in {"inference_only", "fresh_evolution_full_cost"}, "comparison scope missing")
    arms = plan.get("arms")
    require(isinstance(arms, list) and all(isinstance(arm, str) for arm in arms), "active arms required")
    if version == 1:
        require(len(arms) == 3 and set(arms) == {"A", "B", "C"}, "exactly A/B/C required")
        model_lock(plan.get("model"))
    else:
        require(arms in (["B", "C"], ["A", "B", "C"]), "active arms must be B/C or A/B/C")
        models = plan.get("models")
        require(isinstance(models, dict) and models and
                all(isinstance(role, str) and role.isidentifier() for role in models), "role model locks required")
        for model in models.values():
            model_lock(model, role_bound=True)
    require(isinstance(plan.get("tasks"), dict) and bool(plan["tasks"]), "task hashes required")
    require(all(isinstance(k, str) and k and hash_string(v) for k, v in plan["tasks"].items()), "invalid task hash")
    for key in ("per_task_token_cap", "max_solve_calls_per_task", "development_token_cap", "max_development_calls"):
        integer(plan.get(key), key, positive=True)
    require(hash_string(plan.get("initial_methods_sha256")), "method or settings hash invalid")
    readiness = plan.get("readiness", {})
    require(set(readiness) == READY and all(type(x) is bool for x in readiness.values()), "readiness declarations incomplete")
    if version == 2:
        evidence = plan.get("readiness_evidence")
        require(isinstance(evidence, dict) and set(evidence) == {k for k in READY if readiness[k]} and
                all(hash_string(sha) for sha in evidence.values()), "readiness evidence hashes required")
    if plan["comparison_scope"] == "inference_only":
        require(hash_string(plan.get("evolved_methods_sha256")), "inference-only C snapshot required")
    else:
        require(plan.get("evolved_methods_sha256") is None, "full-cost C must be learned prospectively")
    return plan


def native_total(usage):
    """DSH TokenUsage counts disjoint input/cache buckets; reasoning is in output."""
    require(isinstance(usage, dict), "native usage missing")
    for key in ("inputTokens", "outputTokens", "totalTokens"):
        integer(usage.get(key), key)
    known = usage["inputTokens"] + usage["outputTokens"]
    for key in ("cacheReadTokens", "cacheWriteTokens"):
        if key in usage:
            known += integer(usage[key], key)
    require(known <= usage["totalTokens"], "native total below disjoint buckets")
    if "cacheReadTokens" in usage and "cacheWriteTokens" in usage:
        require(known == usage["totalTokens"], "native total inconsistent with complete buckets")
    if "reasoningTokens" in usage:
        require(integer(usage["reasoningTokens"], "reasoningTokens") <= usage["outputTokens"], "reasoning exceeds output")
    return usage["totalTokens"]


class EvaluationLedger:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.plan = validate_plan(json.loads((self.root / "plan.json").read_text(encoding="utf-8")))
        self.plan_hash = digest(self.plan)

    @classmethod
    def create(cls, root, plan, *, readiness_files=None):
        validate_plan(plan)
        files = readiness_files or {}
        evidence = plan.get("readiness_evidence", {}) if plan["schema_version"] == 2 else {}
        require(set(files) == set(evidence), "supply exactly the frozen readiness evidence files")
        blobs = {name: Path(path).read_bytes() for name, path in files.items()}
        require(all(hashlib.sha256(data).hexdigest() == evidence[name] for name, data in blobs.items()),
                "readiness evidence bytes differ")
        root = Path(root).resolve()
        root.mkdir(parents=True, exist_ok=False)
        if blobs:
            from ._atomic import publish_new
            folder = root / "readiness"
            folder.mkdir()
            for name, data in blobs.items():
                publish_new(folder / (name + ".receipt"), data)
        with (root / "plan.json").open("xb") as stream:
            stream.write(encoded(plan) + b"\n")
        with (root / "events.jsonl").open("xb"):
            pass
        ledger = cls(root)
        ledger._append("init", {"plan_sha256": ledger.plan_hash})
        return ledger

    @contextmanager
    def _lock(self):
        lock = self.root / ".write-lock"
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as exc:
            raise EvaluationError("ledger locked; inspect the owner, never auto-reset") from exc
        try:
            os.write(fd, str(os.getpid()).encode("ascii"))
            os.close(fd)
            yield
        finally:
            lock.unlink()

    def _events(self):
        require(digest(json.loads((self.root / "plan.json").read_text(encoding="utf-8"))) == self.plan_hash, "frozen plan changed")
        rows = []
        head = "0" * 64
        for line in (self.root / "events.jsonl").read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except ValueError as exc:
                raise EvaluationError("truncated or invalid event; no automatic recovery") from exc
            fingerprint = row.pop("sha256", None)
            require(row.get("seq") == len(rows) and row.get("previous") == head and digest(row) == fingerprint, "event hash chain broken")
            row["sha256"] = fingerprint
            rows.append(row)
            head = fingerprint
        if rows:
            require(rows[0]["kind"] == "init" and rows[0]["data"] == {"plan_sha256": self.plan_hash}, "frozen plan changed")
        return rows

    def _append(self, kind, data):
        events = self._events()
        row = {"seq": len(events), "previous": events[-1]["sha256"] if events else "0" * 64,
               "time_utc": datetime.now(timezone.utc).isoformat(), "kind": kind, "data": data}
        row["sha256"] = digest(row)
        with (self.root / "events.jsonl").open("ab") as stream:
            stream.write(encoded(row) + b"\n")
            stream.flush()
            os.fsync(stream.fileno())

    def state(self):
        events = self._events()
        require(bool(events), "missing initialization event")
        calls, response_ids = {}, set()
        frozen = self.plan.get("evolved_methods_sha256")
        for event in events[1:]:
            data = event["data"]
            if event["kind"] == "reserve":
                require(data["id"] not in calls, "duplicate request id in ledger")
                calls[data["id"]] = {**data, "status": "pending", "charge": None, "invalid": None}
            elif event["kind"] == "settle":
                require(data["id"] in calls and calls[data["id"]]["status"] == "pending", "settlement has no pending reservation")
                call = calls[data["id"]]
                if "receipt" in data:
                    receipt = data["receipt"]
                    self._verify_receipt(call, receipt, data["native_usage"], data["status"])
                    key = (call["model"]["provider"], receipt["response_id"])
                    require(key not in response_ids, "provider response already settled")
                    response_ids.add(key)
                call.update(data)
            elif event["kind"] == "freeze_c":
                require(frozen is None, "C method frozen twice")
                frozen = data["methods_sha256"]
            else:
                raise EvaluationError("unknown ledger event")
        return {"events": events, "calls": calls, "c_methods_sha256": frozen}

    @staticmethod
    def _exposure(call):
        # Unknown and unfinished requests retain their full reservation, never zero.
        return call["charge"] if call["charge"] is not None else call["bound"]

    def _allocation(self, state, arm, task=None):
        calls = [c for c in state["calls"].values() if c["arm"] == arm]
        development = sum(self._exposure(c) for c in calls if c["phase"] == "develop")
        solve = sum(self._exposure(c) for c in calls if c["phase"] == "solve" and (task is None or c["task"] == task))
        if task is None:
            return Fraction(solve + development)
        return Fraction(solve) + Fraction(development, len(self.plan["tasks"]))

    def _ready(self):
        models = self.plan["models"].values() if self.plan["schema_version"] == 2 else [self.plan["model"]]
        if not all(self.plan["readiness"].values()) or any(
                model["revision"].lower() in {"unknown", "latest", "unresolved"} for model in models):
            return False
        if self.plan["schema_version"] == 2:
            from .store import file_hash
            for name, sha in self.plan["readiness_evidence"].items():
                path = self.root / "readiness" / (name + ".receipt")
                if not path.is_file() or file_hash(path) != sha:
                    return False
        return True

    def reserve(self, request):
        with self._lock():
            state = self.state()
            require(all(self.plan["readiness"].values()), "live admission blocked: readiness not established")
            require(self._ready(), "model revision not pinned or readiness evidence changed")
            arm, phase, task = request.get("arm"), request.get("phase"), request.get("task")
            require(arm in self.plan["arms"] and phase in {"develop", "solve"}, "invalid arm or phase")
            if self.plan["schema_version"] == 2:
                role = request.get("role")
                require(isinstance(role, str) and role in self.plan["models"], "request role has no frozen model")
                model = self.plan["models"][role]
            else:
                model = self.plan["model"]
            require(request.get("model") == model, "model/config drift")
            require(isinstance(request.get("id"), str) and bool(request["id"]) and request["id"] not in state["calls"], "request id already consumed")
            require(hash_string(request.get("request_sha256")), "wire request hash required")
            require(request.get("input_counter") == "provider_exact", "heuristic input counts not admitted")
            incoming = integer(request.get("input_tokens"), "input_tokens")
            outgoing = integer(request.get("max_output_tokens"), "max_output_tokens", positive=True)
            require(integer(request.get("enforced_output_cap"), "enforced_output_cap", positive=True) == outgoing, "enforced completion cap differs")
            previous = [c for c in state["calls"].values() if c["arm"] == arm]
            require(not any(c["invalid"] for c in previous), "arm invalidated by an unresolved cost or bound violation")
            require(not any(c["status"] == "pending" for c in previous), "arm has an unfinished request; inspect it, do not restart")
            if phase == "develop":
                require(arm == "C" and self.plan["comparison_scope"] == "fresh_evolution_full_cost" and task is None, "development not permitted here")
                require(state["c_methods_sha256"] is None, "development after method freeze")
                require(sum(c["phase"] == "develop" for c in previous) < self.plan["max_development_calls"], "development attempt budget exhausted")
                used = sum(self._exposure(c) for c in previous)
                require(used + incoming + outgoing <= self.plan["development_token_cap"], "development token budget exhausted")
                require(request.get("methods_sha256") == self.plan["initial_methods_sha256"], "development prior differs")
            else:
                require(task in self.plan["tasks"] and request.get("task_sha256") == self.plan["tasks"][task], "task drift")
                require(sum(c["phase"] == "solve" and c["task"] == task for c in previous) < self.plan["max_solve_calls_per_task"], "task attempt budget exhausted")
                expected = None if arm == "A" else self.plan["initial_methods_sha256"] if arm == "B" else state["c_methods_sha256"]
                require(arm != "C" or expected is not None, "C method not frozen after metered development")
                require(request.get("methods_sha256") == expected, "method snapshot drift")
                require(self._allocation(state, arm, task) + incoming + outgoing <= self.plan["per_task_token_cap"], "per-task budget including shared development exhausted")
            require(self._allocation(state, arm) + incoming + outgoing <= len(self.plan["tasks"]) * self.plan["per_task_token_cap"], "arm token budget exhausted")
            record = {**request, "bound": incoming + outgoing}
            self._append("reserve", record)
        return record

    def _receipt_path(self, sha):
        require(hash_string(sha), "invalid raw response digest")
        return self.root / "responses" / (sha + ".json")

    def _verify_receipt(self, call, receipt, usage, status):
        require(receipt["request_sha256"] == call["request_sha256"] and receipt["model"] == call["model"],
                "response request or model/config binding differs")
        blob = self._receipt_path(receipt["response_sha256"]).read_bytes()
        require(hashlib.sha256(blob).hexdigest() == receipt["response_sha256"], "raw response bytes changed")
        raw = json.loads(blob)
        require(raw.get("id") == receipt["response_id"] and
                raw.get("model") in (call["model"]["model"], call["model"]["revision"]),
                "raw response ID or model differs")
        if self.plan["schema_version"] == 2:
            model = call["model"]
            require(raw.get("model") == model["revision"] or raw.get("model_revision") == model["revision"],
                    "raw response does not identify the frozen model revision")
            reasoning = raw.get("reasoning")
            effort = raw.get("reasoning_effort", reasoning.get("effort") if isinstance(reasoning, dict) else None)
            require(effort == model["reasoning_effort"], "raw response reasoning effort differs")
            terminal = {"completed": {"completed"}, "incomplete": {"incomplete"}, "failed": {"failed"},
                        "cancelled": {"interrupted", "timeout"}, "interrupted": {"interrupted"},
                        "timeout": {"timeout"}}
            observed = raw.get("status")
            require(isinstance(observed, str) and status in terminal.get(observed, set()), "raw response terminal status differs")
            if isinstance(usage, dict) and type(usage.get("totalTokens")) is int and type(usage.get("outputTokens")) is int:
                native = raw.get("usage")
                require(isinstance(native, dict), "raw response lacks native usage")
                if "totalTokens" in native:
                    require(native == usage, "raw response usage differs")
                else:
                    require(native.get("total_tokens") == usage["totalTokens"] and
                            native.get("output_tokens", native.get("completion_tokens")) == usage["outputTokens"] and
                            native.get("input_tokens", native.get("prompt_tokens")) == usage["totalTokens"] - usage["outputTokens"],
                            "raw response totals differ")
                    cache = native.get("input_tokens_details")
                    if isinstance(cache, dict):
                        for native_key, ledger_key in (("cached_tokens", "cacheReadTokens"), ("cache_write_tokens", "cacheWriteTokens")):
                            if native_key in cache:
                                require(cache[native_key] == usage.get(ledger_key), "raw response cache usage differs")

    def settle(self, request_id, usage, status="completed", *, receipt=None):
        require(status in {"completed", "incomplete", "failed", "timeout", "interrupted"}, "invalid terminal status")
        with self._lock():
            state = self.state()
            require(request_id in state["calls"] and state["calls"][request_id]["status"] == "pending", "request already settled or absent")
            call = state["calls"][request_id]
            binding = {}
            if receipt is not None:
                require(isinstance(receipt, dict) and
                        set(receipt) == {"request_sha256", "model", "raw_response"}, "response receipt fields differ")
                raw = receipt["raw_response"]
                require(isinstance(raw, dict) and isinstance(raw.get("id"), str) and raw["id"].strip(),
                        "provider response ID required")
                require(receipt["request_sha256"] == call["request_sha256"] and receipt["model"] == call["model"],
                        "response request or model/config binding differs")
                require(raw.get("model") in (call["model"]["model"], call["model"]["revision"]), "raw response model differs")
                binding = {"request_sha256": receipt["request_sha256"], "model": receipt["model"],
                           "response_id": raw["id"], "response_sha256": digest(raw)}
            elif self.plan["schema_version"] == 2:
                require(usage is None and status in {"failed", "timeout", "interrupted"},
                        "native usage or a returned result requires a bound response receipt")
            response_id = binding.get("response_id", usage.get("provider_response_id") if isinstance(usage, dict) else None)
            if response_id is not None:
                require(isinstance(response_id, str) and response_id.strip(), "invalid provider response ID")
                for previous in state["calls"].values():
                    old_usage = previous.get("native_usage") or {}
                    old_id = previous.get("receipt", {}).get("response_id")
                    if old_id is None and isinstance(old_usage, dict):
                        old_id = old_usage.get("provider_response_id")
                    require(previous["model"]["provider"] != call["model"]["provider"] or old_id != response_id,
                            "provider response already settled")
            invalid, total = None, None
            try:
                total = native_total(usage)
                if total - usage["outputTokens"] != call["input_tokens"]:
                    invalid = "provider input differs from exact preflight count"
                if usage["outputTokens"] > call["max_output_tokens"] or total > call["bound"]:
                    invalid = "provider token bound exceeded"
            except EvaluationError as exc:
                invalid = str(exc)
            result = {"id": request_id, "status": status, "charge": total, "invalid": invalid, "native_usage": usage}
            if binding:
                destination = self._receipt_path(binding["response_sha256"])
                destination.parent.mkdir(exist_ok=True)
                from ._atomic import publish_new
                try:
                    publish_new(destination, encoded(raw))
                except FileExistsError:
                    pass
                self._verify_receipt(call, binding, usage, status)
                result["receipt"] = binding
            self._append("settle", result)
        return result

    def freeze_c(self, methods_sha256):
        require(hash_string(methods_sha256), "invalid method artifact hash")
        with self._lock():
            state = self.state()
            require(self.plan["comparison_scope"] == "fresh_evolution_full_cost" and state["c_methods_sha256"] is None, "C method freeze not available")
            development = [c for c in state["calls"].values() if c["arm"] == "C"]
            require(bool(development) and all(c["phase"] == "develop" and c["status"] == "completed" and c["charge"] is not None and c["invalid"] is None for c in development), "successful completely metered development required")
            self._append("freeze_c", {"methods_sha256": methods_sha256})

    def audit(self):
        state = self.state()
        arms = {}
        for arm in self.plan["arms"]:
            calls = [c for c in state["calls"].values() if c["arm"] == arm]
            arms[arm] = {"known_native_tokens": sum(c["charge"] or 0 for c in calls),
                         "all_costs_known": all(c["charge"] is not None for c in calls),
                         "total_native_tokens": sum(c["charge"] for c in calls) if all(c["charge"] is not None for c in calls) else None,
                         "cost_unknown_calls": [c["id"] for c in calls if c["charge"] is None],
                         "pending_calls": [c["id"] for c in calls if c["status"] == "pending"],
                         "terminal_failure_calls": [c["id"] for c in calls if c["status"] not in {"pending", "completed"}],
                         "continuation_state_clear": not any(c["invalid"] or c["status"] == "pending" for c in calls),
                         "invalidated": any(c["invalid"] for c in calls),
                         "charged_or_reserved_tokens": str(self._allocation(state, arm)),
                         "task_exposure_with_development": {task: str(self._allocation(state, arm, task)) for task in self.plan["tasks"]}}
        return {"integrity_passed": True, "plan_sha256": self.plan_hash, "events": len(state["events"]),
                "head_sha256": state["events"][-1]["sha256"], "live_admission_ready": self._ready(),
                "comparison_scope": self.plan["comparison_scope"], "arms": arms, "c_methods_sha256": state["c_methods_sha256"],
                "mathematical_verification": "separate", "self_improvement_established": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["init", "audit", "reserve", "settle", "freeze-c"])
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--input", type=Path)
    parser.add_argument("--readiness-files", type=Path, help="JSON mapping of readiness names to exact evidence files")
    args = parser.parse_args()
    data = json.loads(args.input.read_text(encoding="utf-8")) if args.input else None
    if args.command == "init":
        evidence = json.loads(args.readiness_files.read_text(encoding="utf-8-sig")) if args.readiness_files else None
        ledger = EvaluationLedger.create(args.root, data, readiness_files=evidence)
        result = ledger.audit()
    else:
        ledger = EvaluationLedger(args.root)
        if args.command == "reserve":
            result = ledger.reserve(data)
        elif args.command == "settle":
            result = ledger.settle(data["id"], data.get("usage"), data.get("status", "completed"),
                                   receipt=data.get("receipt"))
        elif args.command == "freeze-c":
            ledger.freeze_c(data["methods_sha256"])
            result = ledger.audit()
        else:
            result = ledger.audit()
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

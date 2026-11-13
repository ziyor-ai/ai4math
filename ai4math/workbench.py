"""Research policy: explicit goals, bounded routes, evidence and review gates."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from .store import ResearchError, Store, digest, identifier, now

LEVELS = {"proved", "disproved", "finite_support", "unknown"}
METHODS = {"finite_certificate", "exhaustive_search", "counterexample", "formal_kernel", "human_proof"}


def required(value, *keys):
    for key in keys:
        if key not in value or value[key] is None or value[key] == "":
            raise ResearchError(f"required field: {key}")


def positive(value, name):
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ResearchError(f"{name} must be a positive integer")
    return value


class Workbench:
    def __init__(self, root, *, read_only=False):
        self.store = Store(root, read_only=read_only)

    @classmethod
    def create(cls, root, project_id, title, actor="human", *, review_interval=3, external_research=False):
        store = Store(root, create=True)
        try:
            with store.transaction("workspace.init", actor):
                store.put("project", "main", {"id": identifier(project_id), "title": title,
                          "schema_version": 2, "created_at": now(), "goal": None}, new=True)
                from .strategy import enable
                enable(store, {"round_interval": review_interval, "external_research": external_research}, actor)
        finally:
            store.close()
        workbench = cls(root)
        from .research_summary import refresh_after_transition
        refresh_after_transition(workbench)
        return workbench

    def apply(self, operation, payload, actor="human"):
        if operation in {"task.preflight_seal_batch", "task.submit_seal_batch"}:
            from .seal_batch import preflight, submit
            return (preflight if operation == "task.preflight_seal_batch" else submit)(self, payload, actor)
        from .research import HANDLERS
        from .exploration import HANDLERS as EXPLORATION_HANDLERS
        from .diagnosis import HANDLERS as DIAGNOSIS_HANDLERS
        from .literature import HANDLERS as LITERATURE_HANDLERS
        from .external_deadline import HANDLERS as DEADLINE_HANDLERS, expire
        from . import strategy
        from .tool_evidence import attach as attach_tool_evidence
        handlers = {"node.add": self._node, "goal.set": self._goal, "node.plan": self._plan,
                    "route.add": self._route, "route.status": self._route_status,
                    "route.checkpoint": self._checkpoint, "artifact.import": self._artifact,
                    "claim.add": self._claim, "review.add": self._review, "claim.accept": self._accept,
                    "claim.revoke": self._revoke, "validation.add": self._validation,
                    "task.add": self._task, "task.recover": self._recover, "task.finish_external": self._finish_external,
                    "ledger.add": self._ledger,
                    "strategy.enable": lambda p, a: strategy.enable(self.store, p, a),
                    "strategy.decide": lambda p, a: strategy.decide(self, p, a),
                    "tool.evidence.add": lambda p, a: attach_tool_evidence(self, p, a)}
        handlers.update({name: lambda p, a, fn=fn: fn(self, p, a) for name, fn in HANDLERS.items()})
        handlers.update({name: lambda p, a, fn=fn: fn(self, p, a) for name, fn in EXPLORATION_HANDLERS.items()})
        handlers.update({name: lambda p, a, fn=fn: fn(self, p, a) for name, fn in DIAGNOSIS_HANDLERS.items()})
        handlers.update({name: lambda p, a, fn=fn: fn(self, p, a) for name, fn in LITERATURE_HANDLERS.items()})
        handlers.update({name: lambda p, a, fn=fn: fn(self, p, a) for name, fn in DEADLINE_HANDLERS.items()})
        if operation not in handlers:
            raise ResearchError(f"unknown operation: {operation}")
        timed = operation in {"task.literature_ready", "task.release_exploration", "task.begin_seal", "task.submit_seal", "task.finish_external", "task.codex_hold"}
        if timed and expire(self, payload["task"]):
            raise ResearchError("external phase deadline exceeded; late outputs are archive-only")
        original = []
        def commit_deadline():
            # Keep the pre-transition projection: becoming sealed must not
            # exempt a late v2 commit after event serialization from its limit.
            from .external_deadline import overdue
            if original and overdue(original[0]):
                raise ResearchError("external total deadline exceeded before commit; late outputs are archive-only")
        try:
            with self.store.transaction(operation, actor, before_commit=commit_deadline if timed else None):
                if timed:
                    candidate = self.store.get("task", payload["task"])
                    if candidate.get("external_deadline_policy", {}).get("version") == 2:
                        original.append(candidate)
                integrity = self.store.audit(verify_bytes=False)
                if not integrity["passed"]:
                    raise ResearchError("workspace integrity failed: " + "; ".join(integrity["errors"]))
                result = handlers[operation](dict(payload), actor)
        except ResearchError:
            if timed:
                expire(self, payload["task"])
            raise
        from .research_summary import TRANSITIONS, refresh_after_transition
        if operation in TRANSITIONS:
            refresh_after_transition(self)
        return result

    def _node(self, p, actor):
        required(p, "id", "statement", "domain", "quantifier")
        if p["quantifier"] not in {"finite", "universal"}:
            raise ResearchError("quantifier must be finite or universal")
        node = {k: p[k] for k in ("id", "statement", "domain", "quantifier")}
        node["statement_digest"] = digest({k: node[k] for k in ("statement", "domain", "quantifier")})
        node.update(statement_dependencies=list(p.get("statement_dependencies", [])),
                    proof_dependencies=list(p.get("proof_dependencies", [])), author=actor)
        for dep in node["statement_dependencies"] + node["proof_dependencies"]:
            self.store.get("node", dep)
        self.store.put("node", node["id"], node, new=True)
        self._acyclic()
        return node

    def _acyclic(self):
        graph = {n["id"]: n["statement_dependencies"] + n["proof_dependencies"] for n in self.store.all("node")}
        done, active = set(), set()
        def visit(key):
            if key in active:
                raise ResearchError("dependency cycle")
            if key in done:
                return
            active.add(key)
            for dep in graph[key]:
                visit(dep)
            active.remove(key)
            done.add(key)
        for key in graph:
            visit(key)

    def _goal(self, p, actor):
        required(p, "node")
        self.store.get("node", p["node"])
        project = self.store.get("project", "main")
        if project["goal"] is not None:
            raise ResearchError("goal is locked; use a new workspace for a different research target")
        project["goal"] = p["node"]
        self.store.put("project", "main", project)
        return project

    def _plan(self, p, actor, *, from_strategy=False):
        required(p, "node", "proof_dependencies", "reason")
        if not from_strategy:
            from .strategy import due, state
            if due(state(self)):
                raise ResearchError("global strategy review is due; use strategy.decide with replan")
        node = self.store.get("node", p["node"])
        if self.node_status(node["id"]) != "open":
            raise ResearchError("revoke accepted claims before replanning a settled node")
        for dep in p["proof_dependencies"]:
            self.store.get("node", dep)
        node["proof_dependencies"] = list(dict.fromkeys(p["proof_dependencies"]))
        node["plan_reason"] = p["reason"]
        self.store.put("node", node["id"], node)
        self._acyclic()
        return node

    def cone(self):
        goal = self.store.get("project", "main")["goal"]
        seen = set()
        def visit(key):
            if key is None or key in seen:
                return
            seen.add(key)
            node = self.store.get("node", key)
            for dep in node["statement_dependencies"] + node["proof_dependencies"]:
                visit(dep)
        visit(goal)
        return seen

    def node_status(self, key):
        node = self.store.get("node", key)
        accepted = [c for c in self.store.all("claim") if c["node"] == key and c["status"] == "accepted"]
        if any(c["evidence_level"] == "disproved" for c in accepted):
            return "refuted"
        if any(c["evidence_level"] == "proved" for c in accepted):
            if all(self.node_status(d) == "proved" for d in node["proof_dependencies"]):
                return "proved"
        return "open"

    def _route(self, p, actor):
        required(p, "id", "node", "rationale", "next_question", "stop_condition", "max_runs", "stagnation_limit")
        self.store.get("node", p["node"])
        if p["node"] not in self.cone():
            raise ResearchError("route must address the goal dependency cone")
        route = {k: p[k] for k in ("id", "node", "rationale", "next_question", "stop_condition", "max_runs", "stagnation_limit")}
        positive(route["max_runs"], "max_runs")
        positive(route["stagnation_limit"], "stagnation_limit")
        route.update(status="proposed", runs=0, stagnant=0, credited_claims=[], checkpoints=[], author=actor)
        self.store.put("route", route["id"], route, new=True)
        return route

    def _route_status(self, p, actor):
        required(p, "id", "status", "reason")
        route = self.store.get("route", p["id"])
        if p["status"] not in {"active", "parked", "closed"}:
            raise ResearchError("invalid route status")
        if route["status"] == "closed":
            raise ResearchError("closed route cannot be reopened")
        if p["status"] == "active":
            self._available(route, require_active=False)
        route.update(status=p["status"], status_reason=p["reason"])
        self.store.put("route", route["id"], route)
        return route

    def _available(self, route, require_active=True):
        if require_active and route["status"] != "active":
            raise ResearchError("route is not active")
        if route["node"] not in self.cone() or self.node_status(route["node"]) != "open":
            raise ResearchError("route target is no longer an open goal dependency")
        if route["runs"] >= route["max_runs"] or route["stagnant"] >= route["stagnation_limit"]:
            raise ResearchError("route budget exhausted; record a new reasoned route")

    def _checkpoint(self, p, actor):
        required(p, "route", "finding", "next_question")
        route = self.store.get("route", p["route"])
        was_stagnant = route["stagnant"] >= route["stagnation_limit"]
        fresh = []
        for key in p.get("claims", []):
            claim = self.store.get("claim", key)
            if claim["status"] != "accepted" or claim["evidence_level"] not in {"proved", "disproved"}:
                raise ResearchError("progress credit needs an accepted proved/disproved claim")
            if claim["node"] != route["node"]:
                raise ResearchError("checkpoint claim belongs to a different route target")
            if key not in route["credited_claims"]:
                fresh.append(key)
        route["credited_claims"] = sorted(set(route["credited_claims"] + fresh))
        route["stagnant"] = 0 if fresh else route["stagnant"] + 1
        route["next_question"] = p["next_question"]
        route["checkpoints"].append({"finding": p["finding"], "next_question": p["next_question"], "claims": p.get("claims", []),
                                      "new_progress": sorted(set(fresh)), "timestamp": now()})
        if route["stagnant"] >= route["stagnation_limit"] and route["status"] != "closed":
            route["status"] = "parked"
            route["status_reason"] = "stagnation limit"
        self.store.put("route", route["id"], route)
        if not was_stagnant and route["stagnant"] >= route["stagnation_limit"]:
            from .strategy import trigger
            trigger(self, "route_stagnation_limit", route["id"], route["node"], route["id"])
        return route

    def _artifact(self, p, actor):
        required(p, "path", "purpose")
        from .research import allowed_source
        allowed_source(self, p["path"])
        original = str(Path(p["path"]).resolve())
        record = self.store.freeze(original)
        try:
            existing = self.store.get("artifact", record["id"])
        except ResearchError:
            existing = None
        if existing:
            # Additional import provenance is an append-only separate record.
            receipt_id = digest({"artifact": record["id"], "source": original, "purpose": p["purpose"]})
            try:
                self.store.get("import", receipt_id)
            except ResearchError:
                self.store.put("import", receipt_id, {"id": receipt_id, "artifact": record["id"],
                               "source": original, "purpose": p["purpose"], "imported_at": now(), "actor": actor}, new=True)
            return existing
        record.update(source=original, purpose=p["purpose"], imported_at=now(), actor=actor)
        self.store.put("artifact", record["id"], record, new=True)
        receipt_id = digest({"artifact": record["id"], "source": original, "purpose": p["purpose"]})
        self.store.put("import", receipt_id, {"id": receipt_id, "artifact": record["id"], "source": original,
                       "purpose": p["purpose"], "imported_at": now(), "actor": actor}, new=True)
        return record

    def _claim(self, p, actor):
        required(p, "id", "node", "statement_digest", "evidence_level", "scope", "summary")
        node = self.store.get("node", p["node"])
        if p["statement_digest"] != node["statement_digest"]:
            raise ResearchError("claim refers to a different statement/domain")
        if p["evidence_level"] not in LEVELS:
            raise ResearchError("invalid evidence level")
        if p["scope"] not in {"finite", "universal"}:
            raise ResearchError("invalid scope")
        if node["quantifier"] == "universal" and p["evidence_level"] == "proved" and p["scope"] != "universal":
            raise ResearchError("finite evidence cannot prove a universal statement")
        if node["quantifier"] == "finite" and p["scope"] != "finite":
            raise ResearchError("claim scope differs from finite target")
        if p["evidence_level"] == "finite_support":
            if not p.get("artifact_ids") or not isinstance(p.get("evidence_domain"), str) or not p["evidence_domain"].strip() or p["scope"] != "finite":
                raise ResearchError("finite support needs concrete artifacts, an explicit evidence_domain and finite scope")
        for key in p.get("artifact_ids", []):
            self.store.verify_blob(key)
        for key in p.get("dependencies", []):
            c = self.store.get("claim", key)
            if c["status"] != "accepted" or c["evidence_level"] != "proved":
                raise ResearchError("proof dependency must be an accepted proved claim")
        c = {k: p[k] for k in ("id", "node", "statement_digest", "evidence_level", "scope", "summary")}
        c.update(artifact_ids=list(p.get("artifact_ids", [])), dependencies=list(p.get("dependencies", [])),
                 proof_plan=list(node["proof_dependencies"]), status="proposed", author=actor, created_at=now())
        if p.get("evidence_domain"):
            c["evidence_domain"] = p["evidence_domain"]
        self.store.put("claim", c["id"], c, new=True)
        return c

    def _validation(self, p, actor):
        required(p, "id", "claim", "method", "trust_basis")
        claim = self.store.get("claim", p["claim"])
        if claim["status"] != "proposed":
            raise ResearchError("validation must bind a proposed claim")
        if actor == claim["author"]:
            raise ResearchError("validator must differ from claim author")
        if p["method"] not in METHODS:
            raise ResearchError("model opinions are not validation certificates")
        if not claim["artifact_ids"]:
            raise ResearchError("validation needs concrete claim artifacts")
        record = {"id": p["id"], "claim": claim["id"], "method": p["method"],
                  "validator": actor, "trust_basis": p["trust_basis"], "passed": False}
        if p["method"] == "human_proof":
            required(p, "attestation", "artifact_ids")
            if p["attestation"] != "I checked the exact statement, domain, dependencies and attached argument":
                raise ResearchError("explicit human proof attestation required")
            if not p["artifact_ids"]:
                raise ResearchError("human proof needs an attached argument")
            if claim["evidence_level"] != "proved":
                raise ResearchError("human_proof only certifies a proof, not a counterexample")
            for a in p["artifact_ids"]:
                self.store.verify_blob(a)
                if a not in claim["artifact_ids"]:
                    raise ResearchError("argument must be bound to claim")
            record.update(passed=True, artifact_ids=p["artifact_ids"], trust="human_attestation")
        else:
            required(p, "task")
            task = self.store.get("task", p["task"])
            if task["status"] != "completed" or task["role"] != "validate" or task["node"] != claim["node"]:
                raise ResearchError("needs a completed independent validation task for this node")
            if task["actor"] == claim["author"] or task.get("executed_by") == claim["author"]:
                raise ResearchError("validation task actor must differ from claim author")
            if not set(claim["artifact_ids"]).issubset(set(task["inputs"])):
                raise ResearchError("validation task did not bind all claim artifacts")
            result_id = task.get("result_artifact")
            if not result_id:
                raise ResearchError("validation task did not produce result.json")
            result = json.loads(self.store.verify_blob(result_id).read_text(encoding="utf-8"))
            expected = {"task_id": task["id"], "statement_digest": claim["statement_digest"],
                        "method": p["method"], "scope": claim["scope"], "evidence_level": claim["evidence_level"],
                        "passed": True, "input_artifacts": sorted(task["inputs"])}
            from .request_identity import PROFILE, result_binding, verify_identity
            if task.get("execution_profile") == PROFILE:
                verify_identity(self.store, task)
                expected.update(result_binding(task))
            if any(result.get(k) != v for k, v in expected.items()):
                raise ResearchError("validation result does not match exact task, inputs, claim and scope")
            if result.get("passed") is not True:
                raise ResearchError("validation passed must be the boolean true")
            if any(not isinstance(result.get(k), list) or not result[k] or
                   any(not isinstance(x, str) or not x.strip() for x in result[k]) for k in ("checks", "limitations")):
                raise ResearchError("result must state checks and limitations")
            if p["method"] in {"finite_certificate", "exhaustive_search"} and (claim["scope"] != "finite" or claim["evidence_level"] != "proved"):
                raise ResearchError("finite checks certify only finite proved claims")
            if p["method"] == "counterexample" and claim["evidence_level"] != "disproved":
                raise ResearchError("counterexample must explicitly refute the bound statement")
            if p["method"] == "formal_kernel" and claim["evidence_level"] != "proved":
                raise ResearchError("formal kernel certificate must prove the statement")
            record.update(passed=True, task=task["id"], artifact_ids=[result_id],
                          trust="declared_external_checker", checker_artifact=task["validator_artifact"])
        self.store.put("validation", record["id"], record, new=True)
        return record

    def _review(self, p, actor):
        required(p, "id", "claim", "reviewer", "reviewer_kind", "auditor", "coverage_complete", "issues", "verdict")
        claim = self.store.get("claim", p["claim"])
        if claim["status"] != "proposed":
            raise ResearchError("review must bind a proposed claim")
        if len({p["reviewer"], p["auditor"], claim["author"]}) != 3:
            raise ResearchError("author, reviewer and feedback auditor must be distinct")
        if actor != p["auditor"]:
            raise ResearchError("feedback auditor must submit the review")
        if p["reviewer_kind"] not in {"human", "model", "automation"}:
            raise ResearchError("reviewer_kind must be human, model or automation")
        if not isinstance(p["coverage_complete"], bool) or not isinstance(p["issues"], list):
            raise ResearchError("invalid coverage/issues")
        for issue in p["issues"]:
            required(issue, "description", "audit", "severity")
            if issue["audit"] not in {"confirmed", "rejected", "unresolved"} or issue["severity"] not in {"blocking", "minor"}:
                raise ResearchError("invalid issue audit")
        if p["verdict"] not in {"ready_for_human", "revise", "reject", "inconclusive"}:
            raise ResearchError("invalid verdict")
        blocked = any(i["severity"] == "blocking" and i["audit"] != "rejected" for i in p["issues"])
        if p["verdict"] == "ready_for_human" and (not p["coverage_complete"] or blocked):
            raise ResearchError("incomplete coverage or unresolved blocking feedback")
        ordinal = 1 + max((r.get("review_order", 0) for r in self.store.all("review") if r["claim"] == claim["id"]), default=0)
        review = {**p, "submitted_by": actor, "created_at": now(), "review_order": ordinal}
        self.store.put("review", p["id"], review, new=True)
        return review

    def _accept(self, p, actor):
        integrity = self.store.audit()
        if not integrity["passed"]:
            raise ResearchError("artifact integrity failed: " + "; ".join(integrity["errors"]))
        required(p, "claim", "review")
        claim = self.store.get("claim", p["claim"])
        review = self.store.get("review", p["review"])
        if claim["status"] != "proposed" or review["claim"] != claim["id"] or review["verdict"] != "ready_for_human":
            raise ResearchError("claim needs a ready_for_human review")
        if actor in {claim["author"], review["reviewer"], review["auditor"]}:
            raise ResearchError("acceptance custodian must differ from author and review actors")
        reviews = [r for r in self.store.all("review") if r["claim"] == claim["id"]]
        if max(reviews, key=lambda r: (r.get("review_order", 0), r["created_at"]))["id"] != review["id"]:
            raise ResearchError("acceptance must use the latest review, including adverse feedback")
        node = self.store.get("node", claim["node"])
        if claim["proof_plan"] != node["proof_dependencies"]:
            raise ResearchError("proof plan changed; submit a new bound claim")
        conflicts = {"proved": "disproved", "disproved": "proved"}
        if any(c["node"] == claim["node"] and c["status"] == "accepted"
               and c["evidence_level"] == conflicts.get(claim["evidence_level"])
               for c in self.store.all("claim")):
            raise ResearchError("conflicting accepted conclusion; investigate and revoke before acceptance")
        for a in claim["artifact_ids"]:
            self.store.verify_blob(a)
        if claim["evidence_level"] in {"proved", "disproved"}:
            required(p, "validation")
            validation = self.store.get("validation", p["validation"])
            if validation["claim"] != claim["id"] or not validation["passed"]:
                raise ResearchError("validation does not certify this claim")
            for a in validation["artifact_ids"]:
                self.store.verify_blob(a)
            node = self.store.get("node", claim["node"])
            if claim["evidence_level"] == "proved":
                for dep in node["proof_dependencies"]:
                    if not any(self.store.get("claim", d)["node"] == dep for d in claim["dependencies"]):
                        raise ResearchError("claim must bind each planned proof dependency")
                    if self.node_status(dep) != "proved":
                        raise ResearchError("proof obligation remains unresolved")
            claim["validation"] = p["validation"]
        for key in claim["dependencies"]:
            if self.store.get("claim", key)["status"] != "accepted":
                raise ResearchError("dependency was revoked")
        claim.update(status="accepted", accepted_by=actor, review=p["review"], accepted_at=now())
        self.store.put("claim", claim["id"], claim)
        if claim["evidence_level"] == "disproved":
            from .strategy import trigger
            trigger(self, "accepted_disproof", claim["id"], claim["node"])
        return claim

    def _revoke(self, p, actor):
        required(p, "claim", "reason")
        self.store.get("claim", p["claim"])
        revoked = {p["claim"]}
        claims = self.store.all("claim")
        while True:
            more = {c["id"] for c in claims if set(c["dependencies"]) & revoked}
            if more.issubset(revoked):
                break
            revoked.update(more)
        for claim in claims:
            if claim["id"] in revoked and claim["status"] != "revoked":
                claim.update(status="revoked", revocation_reason=p["reason"], revoked_by=actor, revoked_at=now())
                self.store.put("claim", claim["id"], claim)
        return {"revoked": sorted(revoked)}

    def _task(self, p, actor):
        required(p, "id", "route", "role", "argv")
        if p["role"] not in {"explore", "produce", "validate", "review"}:
            raise ResearchError("invalid task role")
        from .strategy import require_dispatch
        decision = require_dispatch(self, p["route"], p["role"])
        route = self.store.get("route", p["route"])
        self._available(route)
        if not isinstance(p["argv"], list) or not p["argv"] or any(not isinstance(x, str) or not x for x in p["argv"]):
            raise ResearchError("argv must be a nonempty string array; shell commands are not accepted")
        limits = {key: p.get(key) for key in ("timeout_seconds", "output_limit_bytes")}
        for key, limit in limits.items():
            if limit is not None:
                positive(limit, key)
        inputs = list(dict.fromkeys(p.get("inputs", [])))
        for a in inputs:
            self.store.verify_blob(a)
        if p["role"] == "validate":
            required(p, "validator_artifact")
            validator = self.store.verify_blob(p["validator_artifact"])
            if len(p["argv"]) != 3 or Path(p["argv"][0]).resolve() != Path(sys.executable).resolve() or p["argv"][1:] != ["{validator}", "{input}"]:
                raise ResearchError("validation argv must be [current Python, {validator}, {input}]; wrap other checkers in a frozen Python adapter")
            if p["validator_artifact"] in inputs:
                raise ResearchError("checker and checked artifacts must differ")
        task = {k: p[k] for k in ("id", "route", "role", "argv")}
        task.update(limits)
        task.update(node=route["node"], inputs=inputs, validator_artifact=p.get("validator_artifact"),
                    status="pending", actor=actor, created_at=now(), node_snapshot=self.store.get("node", route["node"]))
        if decision:
            task["prepared_under_strategy"] = decision
        from .literature import task_fields as literature_fields
        literature_fields(self, task, decision)
        from .external_deadline import task_fields as deadline_fields
        deadline_fields(task, p)
        if "codex_workflow" in p:
            config = p["codex_workflow"]
            if not isinstance(config, dict) or set(config) - {"skill_manifest_artifact", "toolchain_artifact", "worker_actor",
                                                               "source_roots", "model_settings_artifact",
                                                               "research_stop_policy", "literature_acquisition"}:
                raise ResearchError("unsupported codex_workflow fields")
            skill = config.get("skill_manifest_artifact")
            worker = config.get("worker_actor")
            if skill not in inputs or not isinstance(worker, str) or not worker.strip() or worker == actor:
                raise ResearchError("Codex skill manifest must be an input and worker_actor must differ from coordinator")
            try:
                manifest = json.loads(self.store.verify_blob(skill).read_text(encoding="utf-8-sig"))
            except (UnicodeError, ValueError) as exc:
                raise ResearchError("Codex skill manifest must be a frozen JSON object") from exc
            if not isinstance(manifest, dict):
                raise ResearchError("Codex skill manifest must be a frozen JSON object")
            roots = config.get("source_roots", [])
            if not isinstance(roots, list) or len(roots) > 64 or any(not isinstance(x, str) or not x for x in roots):
                raise ResearchError("Codex source_roots must be a bounded list of explicit paths")
            normalized = []
            for value in roots:
                path = Path(value)
                if not path.is_absolute() or not path.is_dir() or path.resolve() != path:
                    raise ResearchError("Codex source_roots must be existing canonical absolute paths")
                normalized.append(str(path))
            if len(set(normalized)) != len(normalized):
                raise ResearchError("duplicate Codex source_roots")
            task["codex_workflow"] = {"skill_manifest_artifact": skill, "worker_actor": worker,
                                      "source_roots": normalized}
            if "research_stop_policy" in config:
                from .research_return import POLICIES
                if config["research_stop_policy"] not in POLICIES:
                    raise ResearchError("unknown managed research_stop_policy")
                task["codex_workflow"]["research_stop_policy"] = config["research_stop_policy"]
            if "literature_acquisition" in config:
                if config["literature_acquisition"] != "local_only":
                    raise ResearchError("unknown managed literature_acquisition")
                task["codex_workflow"]["literature_acquisition"] = "local_only"
            if "toolchain_artifact" in config:
                toolchain = config["toolchain_artifact"]
                if toolchain not in inputs:
                    raise ResearchError("Codex toolchain artifact must be an input")
                try:
                    value = json.loads(self.store.verify_blob(toolchain).read_text(encoding="utf-8-sig"))
                except (UnicodeError, ValueError) as exc:
                    raise ResearchError("Codex toolchain artifact must be a frozen JSON object") from exc
                if not isinstance(value, dict):
                    raise ResearchError("Codex toolchain artifact must be a frozen JSON object")
                task["codex_workflow"]["toolchain_artifact"] = toolchain
            if "model_settings_artifact" in config:
                settings = config["model_settings_artifact"]
                if settings not in inputs:
                    raise ResearchError("model_settings_artifact must be a frozen task input")
                frozen = json.loads(self.store.verify_blob(settings).read_text(encoding="utf-8-sig"))
                from .evaluation import model_lock
                model_lock(frozen, role_bound=True)
                if frozen["reasoning_effort"] is None:
                    raise ResearchError("managed evaluation needs explicit reasoning effort")
                task["codex_workflow"]["model_settings_artifact"] = settings
        from .research import task_fields
        task.update(task_fields(self, p, route))
        from .exploration import BASE_GUIDANCE, task_fields as exploration_fields
        if p["role"] in {"explore", "produce"}:
            task["agent_guidance"] = BASE_GUIDANCE + (
                "\n每轮派工前查看 strategy-brief，确认当前义务属于最新全局决定。"
                "复盘到期时交回主会话，由主会话登记继续、切换或重拆的决定。\n")
        task.update(exploration_fields(self, p, route))
        if task.get("method"):
            from .exploration import verify_card
            task["method_cards"] = [verify_card(self, key) for key in self.store.get("method", task["method"]).get("card_ids", [])]
        profile = p.get("execution_profile", "trusted_local")
        if task.get("codex_workflow") and profile != "trusted_local":
            raise ResearchError("managed Codex workflow requires trusted_local process supervision")
        if profile == "study_isolated_v1":
            raise ResearchError("study isolation unavailable: fresh local directories are not an OS sandbox")
        if profile not in {"trusted_local", "fresh_local_v1"}:
            raise ResearchError("unknown execution profile; no fallback")
        if profile == "fresh_local_v1":
            from .request_identity import build_identity
            task.update(execution_profile=profile, program_artifact=p.get("program_artifact"))
            identity = build_identity(self.store, task, p.get("request_context"))
            self.store.put("request_identity", identity["request_id"], identity, new=True)
            task["request_identity"] = identity
        elif any(k in p for k in ("request_context", "program_artifact")):
            raise ResearchError("request context/program requires explicit fresh_local_v1 profile")
        if "resume_from" in p:
            from .checkpoint import bind_resumption
            bind_resumption(self.store, task, p["resume_from"])
        self.store.put("task", task["id"], task, new=True)
        return task

    def _recover(self, p, actor):
        required(p, "task", "reason", "process_confirmed_stopped")
        task = self.store.get("task", p["task"])
        if task["status"] != "running" or p["process_confirmed_stopped"] is not True:
            raise ResearchError("recovery requires a running record and explicit confirmation its process stopped")
        task.update(status="interrupted", recovery_reason=p["reason"], recovered_by=actor, finished_at=now())
        self.store.put("task", task["id"], task)
        from .strategy import finish_round
        finish_round(self, task)
        return task

    def _finish_external(self, p, actor):
        required(p, "task", "status", "summary", "artifact_ids")
        task = self.store.get("task", p["task"])
        from .external_deadline import ensure_live, check_manifest
        ensure_live(task)
        if task["status"] != "running" or task.get("execution_mode") != "external":
            raise ResearchError("external outcome needs a running external task")
        if p["status"] not in ("completed", "failed") or not isinstance(p["summary"], str) or not p["summary"].strip():
            raise ResearchError("external outcome needs completed or failed status and a summary")
        if task.get("external_stage"):
            if actor != task["dispatched_by"]:
                raise ResearchError("the external research outcome must be registered by the coordinator")
            if p["status"] == "completed" and task["external_stage"] not in {"exploration", "seal", "sealed"}:
                from .literature import REVIEW_STAGE
                if task["external_stage"] == REVIEW_STAGE:
                    raise ResearchError("external research cannot complete before coordinator release")
                raise ResearchError("external research cannot complete without the worker literature receipt")
        artifacts = p["artifact_ids"]
        if not isinstance(artifacts, list) or not artifacts or any(not isinstance(key, str) for key in artifacts):
            raise ResearchError("external outcome needs frozen artifacts")
        for key in artifacts:
            self.store.verify_blob(key)
        if p["status"] == "completed":
            from .literature import check_completed_outputs
            check_completed_outputs(self, task, artifacts)
            if task.get("external_deadline_policy"):
                if task.get("external_stage") != "sealed" or task.get("stop_state") != "confirmed":
                    raise ResearchError("managed completion needs submitted seal and confirmed physical stop")
                receipt = self.store.get("external_seal", task["id"])
                if p.get("seal_manifest_artifact") != receipt["artifact_id"] or sorted(artifacts) != sorted(receipt["output_artifacts"]):
                    raise ResearchError("completion must bind the frozen submitted seal output set")
                self.store.verify_blob(receipt["artifact_id"])
        ensure_live(task)
        task.update(status=p["status"], external_summary=p["summary"], output_artifacts=artifacts,
                    outcome_reported_by=actor, finished_at=now(), evidence_level="unknown")
        if task.get("external_deadline_policy") and task.get("stop_state") != "confirmed":
            task.update(stop_requested_at=now(), stop_state="requested")
        self.store.put("task", task["id"], task)
        from .strategy import finish_round
        finish_round(self, task)
        return task

    def _ledger(self, p, actor):
        required(p, "id", "node", "claim", "text", "use")
        claim = self.store.get("claim", p["claim"])
        if claim["node"] != p["node"] or claim["status"] != "accepted":
            raise ResearchError("ledger must bind an accepted claim for the exact node")
        if p["use"] not in {"theorem", "observation", "conjecture", "counterexample"}:
            raise ResearchError("invalid manuscript use")
        allowed = {"theorem": {"proved"}, "observation": {"finite_support", "proved"},
                   "conjecture": {"unknown"}, "counterexample": {"disproved"}}
        if claim["evidence_level"] not in allowed[p["use"]]:
            raise ResearchError("manuscript wording exceeds claim evidence")
        entry = {**p, "evidence_level": claim["evidence_level"], "scope": claim["scope"], "author": actor}
        self.store.put("ledger", p["id"], entry, new=True)
        return entry

    def audit(self):
        with self.store.snapshot():
            return self._audit()

    def _audit(self):
        result = self.store.audit()
        for claim in self.store.all("claim"):
            if claim["status"] == "accepted":
                for dep in claim["dependencies"]:
                    if self.store.get("claim", dep)["status"] != "accepted":
                        result["errors"].append(f"stale accepted claim {claim['id']}")
        result["passed"] = not result["errors"]
        return result

    def board(self):
        with self.store.snapshot():
            return self._board()

    def _board(self):
        from .strategy import brief
        strategy = brief(self)
        literature = strategy.get("literature", {})
        awaiting_plan = literature.get("external_research") and not literature["plan_frozen"]
        cone = self.cone()
        project = self.store.get("project", "main")
        return {"project": project, "goal_cone": sorted(cone),
                "goal_evidence_level": self.evidence_status(project["goal"]) if project["goal"] else "unknown",
                "nodes": [{**n, "status": self.node_status(n["id"]), "evidence_level": self.evidence_status(n["id"]),
                           "reachable": n["id"] in cone} for n in self.store.all("node")],
                "routes": self.store.all("route"), "tasks": self.store.all("task"),
                "claims": self.store.all("claim"), "ledger": self.store.all("ledger"),
                "tool_evidence": self.store.all("tool_evidence"),
                "research_policy": self.store.all("research_policy"), "methods": self.store.all("method"),
                "method_reviews": self.store.all("method_review"), "research_notes": self.store.all("research_note"),
                "literature_sources": self.store.all("literature_source"), "mechanism_cards": self.store.all("mechanism_card"),
                "exploration_pools": self.store.all("exploration_pool"),
                "exploration_candidates": self.store.all("exploration_candidate"),
                "exploration_evaluations": self.store.all("exploration_evaluation"),
                "exploration_probes": self.store.all("exploration_probe"),
                "exploration_feedback": self.store.all("exploration_feedback"),
                "exploration_transfers": self.store.all("exploration_transfer"),
                "exploration_counterexamples": self.store.all("exploration_counterexample"),
                "exploration_resolutions": self.store.all("exploration_resolution"),
                "review_queue": [c["id"] for c in self.store.all("claim") if c["status"] == "proposed"],
                "strategy_review": strategy,
                "next_actions": [] if strategy["due"] or strategy.get("dispatch_deferred") or awaiting_plan else [{"route": r["id"], "node": r["node"], "question": r["next_question"]}
                                 for r in self.store.all("route") if r["status"] == "active" and r["node"] in cone
                                 and (not strategy.get("focus_routes") or r["id"] in strategy["focus_routes"])
                                 and self.node_status(r["node"]) == "open" and r["runs"] < r["max_runs"]
                                 and r["stagnant"] < r["stagnation_limit"]]}

    def evidence_status(self, key):
        return {"open": "unknown", "proved": "proved", "refuted": "disproved"}[self.node_status(key)]

    def delivery_check(self):
        with self.store.snapshot():
            return self._delivery_check()

    def _delivery_check(self):
        audit = self.audit()
        goal = self.store.get("project", "main")["goal"]
        errors = list(audit["errors"])
        if not goal or self.node_status(goal) not in {"proved", "refuted"}:
            errors.append("main mathematical goal remains unresolved")
        ledger = self.store.all("ledger")
        if not ledger:
            errors.append("manuscript ledger is empty")
        if goal and not any(e["node"] == goal and e["use"] in {"theorem", "counterexample"} for e in ledger):
            errors.append("ledger has no resolved main-goal statement")
        for entry in ledger:
            if self.store.get("claim", entry["claim"])["status"] != "accepted":
                errors.append(f"revoked manuscript premise: {entry['id']}")
        return {"passed": not errors, "errors": errors, "goal": goal,
                "goal_status": self.node_status(goal) if goal else "unset",
                "semantic_prose_review_required": True, "integrity": audit}

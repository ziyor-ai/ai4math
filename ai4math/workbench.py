"""Research policy: explicit goals, bounded routes, evidence and review gates."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from .store import ResearchError, Store, digest, identifier, now

from .obligation import OUTCOMES, DEBT_SOFT_LIMIT

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
        from .policy import guard as policy_guard
        policy_guard(payload)
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
                    "route.assess": self._route_assess,
                    "route.checkpoint": self._checkpoint, "artifact.import": self._artifact,
                    "claim.add": self._claim, "review.add": self._review, "claim.accept": self._accept,
                    "task.supersede": self._supersede,
                    "claim.revoke": self._revoke, "validation.add": self._validation,
                    "task.add": self._task, "task.abandon": self._abandon, "task.recover": self._recover, "task.finish_external": self._finish_external,
                    "ledger.add": self._ledger,
                    "strategy.enable": lambda p, a: strategy.enable(self.store, p, a),
                    "strategy.configure": lambda p, a: strategy.configure(self, p, a),
                    "strategy.decide": lambda p, a: strategy.decide(self, p, a),
                    "gate.review": self._gate_review,
                    "correspondence.record": self._correspondence_record,
                    "tool.evidence.add": lambda p, a: attach_tool_evidence(self, p, a)}
        from . import policy
        handlers["policy.migrate"] = lambda p, a: policy.migrate(self, p, a)
        handlers["workspace.migrate"] = handlers["policy.migrate"]
        handlers["policy.configure"] = lambda p, a: policy.configure(self, p, a)
        from . import settlement
        handlers.update({"round.open": lambda p, a: settlement.open_round(self, p, a),
                         "round.attach": lambda p, a: settlement.attach_finished(self, p, a),
                         "round.record_attempt": lambda p, a: settlement.record_attempt(self, p, a),
                         "round.assess": lambda p, a: settlement.assess(self, p, a),
                         "round.settle": lambda p, a: settlement.settle(self, p.get("round"), a),
                         "round.recover": lambda p, a: settlement.recover(self, p, a)})
        from . import scope
        handlers.update({name: (lambda p, a, fn=fn: fn(self, p, a)) for name, fn in scope.HANDLERS.items()})
        from . import trust
        handlers.update({name: (lambda p, a, fn=fn: fn(self, p, a)) for name, fn in trust.HANDLERS.items()})
        from . import inference
        handlers.update({name: (lambda p, a, fn=fn: fn(self, p, a)) for name, fn in inference.HANDLERS.items()})
        from . import generation
        handlers.update({name: (lambda p, a, fn=fn: fn(self, p, a)) for name, fn in generation.HANDLERS.items()})
        from . import discovery
        handlers.update({name: (lambda p, a, fn=fn: fn(self, p, a)) for name, fn in discovery.HANDLERS.items()})
        from . import obstacle, hypothesis, approach, intent
        for module in (obstacle, hypothesis, approach, intent):
            handlers.update({name: (lambda p, a, fn=fn: fn(self, p, a))
                             for name, fn in module.HANDLERS.items()})
        from . import critique, sketch
        for module in (critique, sketch):
            handlers.update({name: (lambda p, a, fn=fn: fn(self, p, a))
                             for name, fn in module.HANDLERS.items()})
        from . import findings
        handlers.update({name: (lambda p, a, fn=fn: fn(self, p, a))
                         for name, fn in findings.HANDLERS.items()})
        from . import formalization
        handlers.update({name: (lambda p, a, fn=fn: fn(self, p, a))
                         for name, fn in formalization.HANDLERS.items()})
        from . import verification_policy, legacy_audit
        for module in (verification_policy, legacy_audit):
            handlers.update({name: (lambda p, a, fn=fn: fn(self, p, a))
                             for name, fn in module.HANDLERS.items()})
        from . import verification
        handlers.update({name: (lambda p, a, fn=fn: fn(self, p, a))
                         for name, fn in verification.HANDLERS.items()})
        from . import ablation
        handlers.update({name: (lambda p, a, fn=fn: fn(self, p, a))
                         for name, fn in ablation.HANDLERS.items()})
        from . import batch
        handlers.update({name: (lambda p, a, fn=fn: fn(self, p, a))
                         for name, fn in batch.HANDLERS.items()})
        from . import preflight
        handlers.update({name: (lambda p, a, fn=fn: fn(self, p, a))
                         for name, fn in preflight.HANDLERS.items()})
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
        if p.get("lean_statement") is not None:
            lean = p["lean_statement"]
            if not isinstance(lean, str) or not lean.strip():
                raise ResearchError("lean_statement must be a nonempty Lean declaration when given")
            from .boilerplate import canonical
            node["lean_statement"] = lean.strip()
            node["lean_statement_digest"] = digest({"lean": canonical(lean)})
        if p.get("lean_type") is not None:
            lean_type = p["lean_type"]
            if not isinstance(lean_type, str) or not lean_type.strip():
                raise ResearchError("lean_type must be a nonempty Lean type when given")
            from .boilerplate import canonical
            node["lean_type"] = lean_type.strip()
            node["lean_type_digest"] = digest({"type": canonical(lean_type)})
        node.update(statement_dependencies=list(p.get("statement_dependencies", [])),
                    proof_dependencies=list(p.get("proof_dependencies", [])), author=actor)
        supersedes = list(dict.fromkeys(p.get("supersedes", [])))
        if p["id"] in supersedes:
            raise ResearchError("a node cannot supersede itself")
        for key in supersedes:
            self.store.get("node", key)
        debt = self._debt()
        if debt["open"] >= DEBT_SOFT_LIMIT and not supersedes:
            justification = p.get("debt_justification")
            if not isinstance(justification, str) or not justification.strip():
                raise ResearchError(
                    "net obligation debt: " + str(debt["open"]) + " obligations are open; a new leaf needs "
                    "supersedes=[...] or a nonempty debt_justification")
            node["debt_justification"] = justification.strip()
        for dep in node["statement_dependencies"] + node["proof_dependencies"]:
            self.store.get("node", dep)
        self.store.put("node", node["id"], node, new=True)
        for key in supersedes:
            if self.node_status(key) != "open":
                raise ResearchError("superseded node is already settled: " + key)
            old = self.store.get("node", key)
            old["superseded_by"] = p["id"]
            self.store.put("node", key, old)
        node["supersedes"] = supersedes
        self._acyclic()
        return node

    def _correspondence_record(self, p, actor):
        from .correspondence import record
        return record(self, p, actor)

    def _gate_review(self, p, actor):
        from .review_gate import review
        return review(self, p, actor)

    def _debt(self):
        from .scope import debt
        return debt(self)

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
        from .scope import node_scope, verified_bridge
        proof, proposed = [], []
        for dep in dict.fromkeys(p["proof_dependencies"]):
            self.store.get("node", dep)
            if node_scope(self, dep) == "exploration" and not verified_bridge(self, node["id"], dep):
                # A registered research candidate is not a plan edge: it only becomes one
                # after a verified bridge. Keep planning freedom, but do not let an
                # unverified link into the formal dependency cone.
                proposed.append(dep)
            else:
                proof.append(dep)
        node["proof_dependencies"] = proof
        if proposed:
            kept = list(node.get("proposed_dependencies") or [])
            node["proposed_dependencies"] = sorted(set(kept + proposed))
            node["plan_notice"] = ("kept as proposed_dependencies (no verified bridge): " +
                                   ", ".join(proposed))
        else:
            node.pop("plan_notice", None)
        node["plan_reason"] = p["reason"]
        self.store.put("node", node["id"], node)
        self._acyclic()
        return node

    def cone(self):
        from .scope import proof_cone
        return proof_cone(self)

    def node_status(self, key):
        node = self.store.get("node", key)
        accepted = [c for c in self.store.all("claim") if c["node"] == key and c["status"] == "accepted"]
        from .verification import recheck_required
        if any(c["evidence_level"] == "disproved" for c in accepted):
            return "refuted"
        if any(c["evidence_level"] == "proved" and not recheck_required(self, c) for c in accepted):
            if all(self.node_status(d) == "proved" for d in node["proof_dependencies"]):
                return "proved"
        return "open"

    def _route(self, p, actor):
        required(p, "id", "node", "rationale", "next_question", "stop_condition", "max_runs", "stagnation_limit")
        self.store.get("node", p["node"])
        kind = p.get("kind")
        if kind is None:
            from .policy import route_kind_required
            if route_kind_required(self):
                raise ResearchError("route kind is required at this workspace policy version: "
                                    "proof or exploration")
            kind = "proof"
        if kind not in {"proof", "exploration"}:
            raise ResearchError("route kind must be proof or exploration")
        from .scope import node_scope
        scope = node_scope(self, p["node"])
        if kind == "proof" and scope != "proof":
            raise ResearchError("proof route must address the goal dependency cone")
        if kind == "exploration" and scope != "exploration":
            raise ResearchError("exploration route needs a registered research candidate "
                                "(research.register), not a proof obligation")
        if kind == "exploration":
            from .scope import exploration as exploration_view
            view = exploration_view(self)
            if view["rounds_settled"] >= view["rounds_limit"]:
                raise ResearchError("exploration round budget is exhausted (" +
                                    str(view["rounds_settled"]) + "/" + str(view["rounds_limit"]) + ")")
            rows = [r for r in self.store.all("approach") if r["node"] == p["node"]]
            if rows and not any(r["status"] == "exploring" for r in rows):
                raise ResearchError("exploration route needs an exploring approach for this node "
                                    "(approve and activate it first)")
        route = {k: p[k] for k in ("id", "node", "rationale", "next_question", "stop_condition", "max_runs", "stagnation_limit")}
        route["kind"] = kind
        positive(route["max_runs"], "max_runs")
        positive(route["stagnation_limit"], "stagnation_limit")
        from .obligation import track as obligation_track
        prior = [r for r in self.store.all("route") if r["node"] == p["node"] and r["status"] != "closed"]
        carried = obligation_track(self, p["node"])
        route["kill_at"] = p.get("kill_at")
        if route["kill_at"] is not None:
            positive(route["kill_at"], "kill_at")
        route.update(status="proposed", runs=0, stagnant=0, credited_claims=[], checkpoints=[], author=actor)
        route["obligation_rounds"] = carried["rounds"]
        from .policy import strict_research
        strict = strict_research(self)
        predecessor = None
        if strict and "continuation_of" in p:
            predecessor_id = p["continuation_of"]
            if not isinstance(predecessor_id, str):
                raise ResearchError("continuation_of must identify a previous route")
            predecessor = self.store.get("route", predecessor_id)
            if predecessor["node"] != route["node"] or predecessor.get("kind", "proof") != kind:
                raise ResearchError("continuation_of must address the same obligation and route kind")
        elif not strict and prior:
            predecessor = prior[-1]
        if predecessor:
            route["continuation_of"] = predecessor["id"]
        if prior:
            route["parallel_with"] = [r["id"] for r in prior if r["status"] == "active"]
            from .correspondence import check_route as correspondence_check
            correspondence_check(self, p["node"])
        depth_basis = predecessor
        if strict and depth_basis is None and prior:
            depth_basis = max(prior, key=lambda row: self.store.revision("route", row["id"]))
        if depth_basis:
            from .strategy import state as strategy_state
            depth = (strategy_state(self) or {}).get("min_route_rounds", 1)
            latest = depth_basis
            if depth > 1 and len(latest.get("checkpoints") or []) < depth:
                last = (latest.get("checkpoints") or [{}])[-1]
                if last.get("outcome") not in {"claim", "discharged", "falsified"}:
                    raise ResearchError(
                        "mechanism depth: the current route has " + str(len(latest.get("checkpoints") or [])) +
                        " of " + str(depth) + " required rounds; finish the block or record a falsifier before switching")
        self.store.put("route", route["id"], route, new=True)
        return route

    def _route_assess(self, p, actor):
        """Kill criteria: a route past its kill_at needs an independent verdict."""
        required(p, "id", "verdict", "argument")
        route = self.store.get("route", p["id"])
        if p["verdict"] not in {"continue", "pivot", "stop"}:
            raise ResearchError("route assessment must continue, pivot or stop")
        if actor == route.get("author"):
            raise ResearchError("route assessment requires an actor other than the route author")
        from .identities import require as require_identity
        require_identity(self, actor, ("model", "human"))
        record = {"id": p["id"] + "-assessment-" + str(1 + sum(1 for r in self.store.all("route_assessment") if r["route"] == p["id"])),
                  "route": p["id"], "verdict": p["verdict"], "argument": p["argument"],
                  "runs": route.get("runs", 0), "assessor": actor, "at": now()}
        self.store.put("route_assessment", record["id"], record, new=True)
        if p["verdict"] == "stop":
            route.update(status="closed", closed_at=now(), closed_reason=p["argument"])
            self.store.put("route", p["id"], route)
            for pool in self.store.all("exploration_pool"):
                if pool.get("node") == route["node"] and not pool.get("closed_at"):
                    pool.update(closed_at=now(), closed_reason="route stopped: " + p["argument"])
                    self.store.put("exploration_pool", pool["id"], pool)
        return record

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
        from .scope import node_scope
        expected = "exploration" if route.get("kind") == "exploration" else "proof"
        if node_scope(self, route["node"]) != expected or self.node_status(route["node"]) != "open":
            raise ResearchError("route target is no longer an open " +
                                ("research candidate" if expected == "exploration" else "goal dependency"))
        if expected == "exploration":
            rows = [r for r in self.store.all("approach") if r["node"] == route["node"]]
            if rows and not any(r["status"] == "exploring" for r in rows):
                raise ResearchError("approach is not active (paused, retired or not reviewed); "
                                    "dispatch is blocked until it is exploring again")
        if route["runs"] >= route["max_runs"] or route["stagnant"] >= route["stagnation_limit"]:
            raise ResearchError("route budget exhausted; record a new reasoned route")

    def _checkpoint(self, p, actor, *, assessments=(), bound_round=False):
        from .policy import strict_research
        if strict_research(self) and not bound_round:
            raise ResearchError("v2c4 checkpoints use round.assess and round.settle")
        required(p, "route", "finding", "next_question")
        route = self.store.get("route", p["route"])
        was_stagnant = route["stagnant"] >= route["stagnation_limit"]
        claimed = list(p.get("claims", []))
        finding_ids = list(p.get("findings") or [])
        outcome = p.get("outcome") or ("claim" if claimed else
                                       "finding" if finding_ids else "no-progress")
        if outcome not in OUTCOMES:
            raise ResearchError("round outcome must be one of " + ", ".join(OUTCOMES))
        if outcome == "claim" and not claimed:
            raise ResearchError("a claim outcome needs at least one accepted claim id")
        if outcome != "claim" and claimed:
            raise ResearchError("crediting claims requires the claim outcome")
        if outcome == "finding" and not finding_ids:
            raise ResearchError("a finding outcome needs at least one research_finding id")
        if finding_ids and outcome not in {"finding", "claim", "discharged"}:
            raise ResearchError("findings require the finding or claim outcome")
        if outcome == "discharged" and self.node_status(route["node"]) not in {"proved", "refuted"}:
            raise ResearchError("discharged needs a proved or refuted obligation")
        receipts = list(p.get("artifacts", []))
        if outcome == "falsified":
            if not receipts:
                raise ResearchError("a falsified outcome needs at least one artifact recording the failed probe")
            for key in receipts:
                self.store.verify_blob(key)
        claims = []
        for key in claimed:
            claim = self.store.get("claim", key)
            if claim["status"] != "accepted" or claim["evidence_level"] not in {"proved", "disproved"}:
                raise ResearchError("progress credit needs an accepted proved/disproved claim")
            if claim["node"] != route["node"]:
                raise ResearchError("checkpoint claim belongs to a different route target")
            claims.append(claim)
        findings = []
        for key in finding_ids:
            row = self.store.get("research_finding", key)
            if row["node"] != route["node"]:
                raise ResearchError("checkpoint finding belongs to a different route target")
            findings.append(row)
        from . import obligation, progress
        previous = obligation._row(self, route["node"], create=True)
        delta = progress.qualify(self, route, {**p, "outcome": outcome, "artifacts": receipts},
                                 claims, findings, previous, actor, assessments)
        credited_findings = [change["finding_id"] for change in delta["decision_changes"]
                             if change["kind"] == "finding"]
        route["credited_claims"] = sorted(set(route["credited_claims"] + delta["closure_claim_ids"]))
        route["credited_findings"] = sorted(set(route.get("credited_findings", []) + credited_findings))
        route["stagnant"] = 0 if delta["advanced"] else route["stagnant"] + 1
        route["next_question"] = p["next_question"]
        route["checkpoints"].append({"finding": p["finding"], "next_question": p["next_question"],
                                      "claims": claimed,
                                      "findings": finding_ids,
                                      "eval": p.get("eval"),
                                      "artifacts": receipts, "outcome": outcome,
                                      "new_progress": delta["closure_claim_ids"],
                                      "checked_finding": bool(credited_findings),
                                      "progress": delta, "timestamp": now()})
        obligation.record_round(self, previous, route, outcome, claimed, finding_ids, delta)
        if route["stagnant"] >= route["stagnation_limit"] and route["status"] != "closed":
            route["status"] = "parked"
            route["status_reason"] = "stagnation limit"
        self.store.put("route", route["id"], route)
        if not was_stagnant and route["stagnant"] >= route["stagnation_limit"]:
            from .strategy import trigger
            trigger(self, "route_stagnation_limit", route["id"], route["node"], route["id"])
        self._schedule_generation(route, outcome)
        return route

    def _schedule_generation(self, route, outcome):
        """Auto-request a local generation obligation after a no-progress drought.

        Opt-in: the trigger only arms when ``critique_interval`` was configured
        explicitly, so existing workspaces keep their current dispatch behaviour.
        """
        if route.get("kind") == "exploration":
            return
        from .strategy import state as strategy_state
        settings = strategy_state(self) or {}
        interval = settings.get("critique_interval")
        if type(interval) is not int or interval < 1:
            return
        if outcome not in {"no-progress"} or route["stagnant"] < interval:
            return
        from . import generation
        if generation.pending(self, route=route["id"]):
            return
        generation.request(self, {
            "id": "GEN-REQ-" + route["id"] + "-" + str(route["stagnant"]),
            "route": route["id"], "parent": route["node"],
            "trigger_event_id": route["id"] + ":stagnant:" + str(route["stagnant"]),
            "trigger_reason": "no_progress_drought"}, "system")

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
            if self.store.exists("research_finding", key):
                raise ResearchError("a research finding is not a proof dependency; "
                                    "promote it to a claim first")
            c = self.store.get("claim", key)
            if c["status"] != "accepted" or c["evidence_level"] != "proved":
                raise ResearchError("proof dependency must be an accepted proved claim")
            from .verification import recheck_required
            if recheck_required(self, c):
                raise ResearchError("proof dependency is blocked by a revocation; "
                                    "recheck it before building on it")
        proof_route = p.get("proof_route")
        if proof_route is None:
            from .policy import proof_route_required
            if proof_route_required(self):
                raise ResearchError("claim proof_route is required at this workspace policy version: "
                                    "direct or composition")
            proof_route = "composition"
        if proof_route not in {"direct", "composition"}:
            raise ResearchError("claim proof_route must be direct or composition")
        if "maturity" in p:
            raise ResearchError("the state machine writes claim maturity; "
                                "a new claim always starts as proof_candidate")
        contribution = p.get("contribution", "exploratory")
        if contribution not in {"load_bearing", "supporting", "exploratory", "none"}:
            raise ResearchError("claim contribution must be load_bearing, supporting, "
                                "exploratory or none")
        sketch = list(p.get("proof_sketch") or [])
        if not isinstance(sketch, list):
            raise ResearchError("proof_sketch must be a list of artifact ids")
        for key in sketch:
            self.store.verify_blob(key)
        c = {k: p[k] for k in ("id", "node", "statement_digest", "evidence_level", "scope", "summary")}
        c.update(artifact_ids=list(p.get("artifact_ids", [])), dependencies=list(p.get("dependencies", [])),
                 proof_plan=list(node["proof_dependencies"]), proof_route=proof_route,
                 maturity="proof_candidate", contribution=contribution,
                 proof_sketch=sketch,
                 status="proposed", author=actor, created_at=now())
        if p.get("evidence_domain"):
            c["evidence_domain"] = p["evidence_domain"]
        self.store.put("claim", c["id"], c, new=True)
        if sketch:
            from .formalization import request as formalization_request
            formalization_request(self, {"claim": c["id"], "trigger": "proof_sketch_ready",
                                         "reason": "claim carries a frozen proof sketch"},
                                  actor)
        return c

    def _validation(self, p, actor):
        required(p, "id", "claim", "method", "trust_basis")
        from .trust import assessment
        for forbidden in ("trust_class", "verification_round", "environment_digest", "exact_goal_type_digest"):
            if forbidden in p:
                raise ResearchError("the state machine writes " + forbidden +
                                    " from the checker policy; a caller must not supply it")
        claim = self.store.get("claim", p["claim"])
        from .verification import recheck_required, toolchain_recheck_required
        recheck_mode = claim["status"] == "accepted" and (recheck_required(self, claim)
                                                          or toolchain_recheck_required(self, claim))
        if claim["status"] != "proposed" and not recheck_mode:
            raise ResearchError("validation must bind a proposed claim, "
                                "or an accepted claim that needs a recheck")
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
            record.update(passed=True, artifact_ids=p["artifact_ids"], trust="human_attestation",
                          trust_class="trusted", verification_round="initial",
                          environment_digest=None, exact_goal_type_digest=None,
                          trust_reasons=["human attestation"])
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
            from .policy import strict_research
            strict = strict_research(self) or "validation_request" in task
            if strict:
                from .validation_protocol import consume
                result = consume(self, task, "claim", passed_required=True)
                if result["claim"] != claim["id"]:
                    raise ResearchError("frozen validation receipt targets a different claim")
                record["validation_request_sha256"] = result["validation_request_sha256"]
            else:
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
            trust_fields = assessment(self, claim, result, p["method"], task["validator_artifact"])
            if recheck_mode:
                trust_fields["verification_round"] = "recheck"
            environment = {"lean_version": result.get("lean_version"),
                           "mathlib_revision": result.get("mathlib_revision")}
            record.update(passed=True, task=task["id"], artifact_ids=[result_id],
                          trust=("trusted" if trust_fields["trust_class"] == "trusted"
                                 else "declared_external_checker"),
                          checker_artifact=task["validator_artifact"],
                          environment=environment,
                          axioms=result.get("axioms"),
                          expr_check=result.get("expr_check"),
                          checked_type=result.get("checked_type"),
                          checked_type_sha256=result.get("checked_type_sha256"),
                          **trust_fields)
        self.store.put("validation", record["id"], record, new=True)
        refs = sorted(set(claim.get("verification_refs", []) + [record["id"]]))
        claim["verification_refs"] = refs
        claim["last_validation"] = record["id"]
        self.store.put("claim", claim["id"], claim)
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

    def _bind_planned_dependencies(self, claim, node):
        """Composition claims must bind every planned proof dependency, all resolved."""
        for dep in node["proof_dependencies"]:
            if not any(self.store.get("claim", d)["node"] == dep for d in claim["dependencies"]):
                raise ResearchError("claim must bind each planned proof dependency")
            if self.node_status(dep) != "proved":
                raise ResearchError("proof obligation remains unresolved")

    def _supersede(self, p, actor):
        """Open a fresh attempt of a finished task (audit fix F7).

        The task definition (route, inputs, validator, argv, round) is reused; only the
        id and attempt change. A recorded result is never reused.
        """
        required(p, "task", "id")
        old = self.store.get("task", p["task"])
        if old["status"] not in {"failed", "completed", "output_limit", "unknown_timeout",
                                 "interrupted", "abandoned"}:
            raise ResearchError("only a finished task can be superseded; it is " + old["status"])
        if self.store.exists("task", p["id"]):
            raise ResearchError("task already exists: " + p["id"])
        from .policy import strict_research
        if strict_research(self):
            replacement = p.get("replacement")
            if not isinstance(replacement, dict):
                raise ResearchError("v2c4 task.supersede needs replacement task.add fields and a fresh intent")
            route = self.store.get("route", replacement.get("route", old["route"]))
            if route["node"] != old["node"]:
                raise ResearchError("a replacement task must address the same node")
            task = self._task({**replacement, "id": p["id"], "route": route["id"], "role": old["role"]}, actor)
            task.update(supersedes=old["id"], attempt=int(old.get("attempt") or 1) + 1)
            self.store.put("task", task["id"], task)
            return task
        route = self.store.get("route", old["route"])
        self._available(route, require_active=False)
        task = {key: value for key, value in old.items() if key not in {
            "pid", "started_at", "finished_at", "result_artifact", "stale_basis",
            "executed_by", "thin_inputs_ack", "validation_outcome"}}
        task.update(id=p["id"], status="pending", created_at=now(), actor=actor,
                    supersedes=old["id"], attempt=int(old.get("attempt") or 1) + 1)
        if round_id := old.get("round"):
            from . import settlement
            if settlement.state(self, round_id) in {"open", "running"}:
                task["round"] = round_id
            else:
                task.pop("round", None)
        self.store.put("task", task["id"], task, new=True)
        return task

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
        from .strategy import state as strategy_state
        settings = strategy_state(self) or {}
        if settings.get("adversarial_review"):
            adversarial = [r for r in reviews if r.get("stance") == "adversarial"]
            if not adversarial:
                raise ResearchError("adversarial review required before acceptance")
        if max(reviews, key=lambda r: (r.get("review_order", 0), r["created_at"]))["id"] != review["id"]:
            raise ResearchError("acceptance must use the latest review, including adverse feedback")
        node = self.store.get("node", claim["node"])
        route = claim.get("proof_route", "composition")
        if route not in {"direct", "composition"}:
            raise ResearchError("claim proof_route must be direct or composition")
        if route == "composition" and claim["proof_plan"] != node["proof_dependencies"]:
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
            from .policy import strict_research
            if strict_research(self) and validation.get("task"):
                from .validation_protocol import consume
                task = self.store.get("task", validation["task"])
                result = consume(self, task, "claim", passed_required=True)
                if (result["claim"] != claim["id"]
                        or validation.get("validation_request_sha256") != result["validation_request_sha256"]
                        or validation["artifact_ids"] != [task["result_artifact"]]):
                    raise ResearchError("claim acceptance needs the exact admitted validation receipt")
            from .trust import require_trusted
            if require_trusted(self) and validation.get("trust_class") != "trusted":
                raise ResearchError("checker policy requires a trusted validation, not a declared receipt: " +
                                    ", ".join(validation.get("trust_reasons") or ["unknown reason"]))
            for a in validation["artifact_ids"]:
                self.store.verify_blob(a)
            node = self.store.get("node", claim["node"])
            if claim["evidence_level"] == "proved":
                # A direct formal proof of the parent does not have to be rebuilt from the
                # registered plan; a composition claim still has to bind every planned dep.
                if route == "composition":
                    self._bind_planned_dependencies(claim, node)
                    from .inference import require_closure
                    require_closure(self, claim)
            claim["validation"] = p["validation"]
        for key in claim["dependencies"]:
            dependency = self.store.get("claim", key)
            if dependency["status"] != "accepted":
                raise ResearchError("dependency was revoked")
            from .verification import recheck_required
            if recheck_required(self, dependency):
                raise ResearchError("dependency is blocked by a revocation; recheck it first")
        claim.update(status="accepted", maturity="verified", accepted_by=actor,
                     review=p["review"], accepted_at=now(),
                     accepted_after_seq=self.store.last_seq())
        if p.get("validation"):
            claim["accepted_validation"] = p["validation"]
        trusted = [v["id"] for v in self.store.all("validation")
                   if v.get("claim") == claim["id"] and v.get("trust_class") == "trusted"]
        if trusted:
            claim["trusted_validation"] = trusted[-1]
        claim["verification_refs"] = sorted(set(claim.get("verification_refs", []) + trusted))
        self.store.put("claim", claim["id"], claim)
        from .formalization import close_for_claim
        close_for_claim(self, claim["id"], p.get("validation"), actor)
        if claim["evidence_level"] == "disproved":
            from .strategy import trigger
            trigger(self, "accepted_disproof", claim["id"], claim["node"])
        return claim

    def _revoke(self, p, actor):
        required(p, "claim", "reason")
        claim = self.store.get("claim", p["claim"])
        if claim["status"] != "revoked":
            claim.update(status="revoked", revocation_reason=p["reason"], revoked_by=actor, revoked_at=now())
            self.store.put("claim", claim["id"], claim)
        # Batch 8.2 P0: a revocation does not rewrite downstream claims. It marks the
        # actual dependents as affected; their effective state becomes blocked until a
        # fresh recheck covers the revocation (v3.4 §4.6d).
        affected = sorted({c["id"] for c in self.store.all("claim")
                           if c["id"] != claim["id"] and claim["id"] in (c.get("dependencies") or [])})
        from .formalization import stale_for_claim
        stale_for_claim(self, claim["id"], actor)
        return {"revoked": [claim["id"]], "affected": affected}

    def _task(self, p, actor):
        """Create a task; when a dispatch gate refuses, attach the full preflight list."""
        try:
            return self._task_body(p, actor)
        except ResearchError as error:
            message = str(error)
            if "preflight" in message or "missing route" in message:
                raise
            try:
                from .preflight import preflight
                page = preflight(self, {"route": p.get("route"), "role": p.get("role") or "explore"},
                                 actor)
            except ResearchError:
                raise error
            if page["ready"]:
                raise error
            raise ResearchError(message + " | preflight: " +
                                "; ".join(item["reason"] for item in page["blockers"])) from error

    def _task_body(self, p, actor):
        required(p, "id", "route", "role", "argv")
        if p["role"] not in {"explore", "produce", "validate", "review"}:
            raise ResearchError("invalid task role")
        from .strategy import require_dispatch
        decision = require_dispatch(self, p["route"], p["role"])
        route = self.store.get("route", p["route"])
        recheck_claim = p.get("recheck_claim")
        if recheck_claim is not None:
            claim = self.store.get("claim", recheck_claim)
            from .verification import recheck_required, toolchain_recheck_required
            if claim["node"] != route["node"] or claim["status"] != "accepted":
                raise ResearchError("recheck_claim must be an accepted claim on this route")
            if not (recheck_required(self, claim) or toolchain_recheck_required(self, claim)):
                raise ResearchError("recheck_claim does not need a recheck right now")
        else:
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
        worker_driver = p.get("worker_driver", "codex")
        if worker_driver not in {"codex", "acp", "subagent"}:
            raise ResearchError("worker_driver must be codex, acp or subagent")
        from . import settlement
        round_id = p.get("round")
        if round_id is not None:
            if not isinstance(round_id, str) or not round_id.strip():
                raise ResearchError("round must be a nonempty round id")
            bound = settlement.row(self, round_id)
            if bound["state"] not in {"open", "running"}:
                raise ResearchError("round " + round_id + " no longer accepts tasks (" +
                                    bound["state"] + ")")
        intent_kind = p.get("intent_kind", "exploration")
        if intent_kind not in {"exploration", "probe", "entailment_analysis",
                               "verification", "settlement"}:
            raise ResearchError("intent_kind must be exploration, probe, entailment_analysis, "
                                "verification or settlement")
        intent_id = p.get("intent")
        intent_row = None
        if intent_id is not None:
            if not isinstance(intent_id, str) or not intent_id.strip():
                raise ResearchError("intent must be a nonempty research_intent id")
            intent_row = self.store.get("research_intent", intent_id)
            if intent_row.get("consumed_by"):
                raise ResearchError("intent already used by task " + intent_row["consumed_by"])
            if "intent_kind" in p and intent_kind != intent_row["kind"]:
                raise ResearchError("task intent_kind differs from the intent kind")
            intent_kind = intent_row["kind"]
            approach = self.store.get("approach", intent_row["approach"])
            if approach["status"] != "exploring":
                raise ResearchError("intent approach is not exploring (" + approach["status"] + ")")
            from .policy import strict_research
            if strict_research(self) and approach["node"] != route["node"]:
                raise ResearchError("research intent must belong to the same obligation as the route")
        else:
            from .policy import require_intent
            if require_intent(self):
                raise ResearchError("this workspace requires a research_intent for every new task")
        task = {k: p[k] for k in ("id", "route", "role", "argv")}
        task["intent_kind"] = intent_kind
        if intent_row is not None:
            task["intent"] = intent_row["id"]
            task["approach"] = intent_row["approach"]
            task["basis_read_set"] = self._read_set(intent_row)
        task["worker_driver"] = worker_driver
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
                                                               "research_stop_policy", "literature_acquisition",
                                                               "replicates"}:
                raise ResearchError("unsupported codex_workflow fields")
            replicates = config.get("replicates", 1)
            if type(replicates) is not int or not 1 <= replicates <= 4:
                raise ResearchError("replicates must be an integer in 1..4")
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
            if "replicates" in config:
                task["codex_workflow"]["replicates"] = replicates
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
        if intent_row is not None:
            from .intent import task_plan
            probe_plan = task_plan(self, task, decision)
            if probe_plan is not None:
                task["probe_plan"] = probe_plan
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
        from .validation_protocol import register as register_validation
        register_validation(self, task, p)
        if round_id is not None:
            task["round"] = round_id
        from .policy import strict_research
        if strict_research(self) and task["role"] in {"explore", "produce"}:
            if not round_id:
                raise ResearchError("v2c4 research tasks need an explicit round")
            from .research_flow import freeze_task
            freeze_task(self, task, decision)
            if task.get("codex_workflow"):
                from .research_return import DEDUCTION_POLICY
                profile = task["codex_workflow"]
                if profile.get("research_stop_policy", DEDUCTION_POLICY) != DEDUCTION_POLICY:
                    raise ResearchError("v2c4 managed research uses target_progress_v2")
                profile["research_stop_policy"] = DEDUCTION_POLICY
        self.store.put("task", task["id"], task, new=True)
        if round_id is not None:
            settlement.attach(self, round_id, task["id"], actor)
        if intent_row is not None:
            from .intent import consume as consume_intent
            consume_intent(self, intent_row["id"], task["id"])
        return task

    def _read_set(self, intent_row):
        """Snapshot the research basis a task was opened on (Batch 8.1, §4.3)."""
        goal = self.store.get("project", "main")["goal"]
        goal_node = self.store.get("node", goal)
        read_set = {"goal": goal, "goal_statement_digest": goal_node["statement_digest"]}
        if intent_row is not None:
            approach = intent_row["approach"]
            read_set["approach"] = {"id": approach,
                                    "last_event_seq": self.store.revision("approach", approach)}
            hypothesis = intent_row.get("hypothesis")
            read_set["hypotheses"] = (
                [{"id": hypothesis,
                  "last_event_seq": self.store.revision("research_hypothesis", hypothesis)}]
                if hypothesis else [])
        return read_set

    def _abandon(self, p, actor):
        """Close a contract that was created but never dispatched."""
        required(p, "task", "reason")
        task = self.store.get("task", p["task"])
        if task["status"] != "pending":
            raise ResearchError("only a pending contract can be abandoned")
        task.update(status="abandoned", abandon_reason=p["reason"], abandoned_by=actor, finished_at=now())
        from .validation_protocol import record_interruption
        record_interruption(task)
        self.store.put("task", task["id"], task)
        from . import failure
        failure.record(self, task, "abandoned", p["reason"])
        return task

    def _recover(self, p, actor):
        required(p, "task", "reason", "process_confirmed_stopped")
        task = self.store.get("task", p["task"])
        if task["status"] != "running" or p["process_confirmed_stopped"] is not True:
            raise ResearchError("recovery requires a running record and explicit confirmation its process stopped")
        task.update(status="interrupted", recovery_reason=p["reason"], recovered_by=actor, finished_at=now())
        from .validation_protocol import record_interruption
        record_interruption(task)
        self.store.put("task", task["id"], task)
        from .strategy import finish_round
        finish_round(self, task)
        from . import failure
        failure.record(self, task, "interrupted", p["reason"])
        from . import handoff
        handoff.write(self.store, task, actor)
        return task

    def _finish_external(self, p, actor):
        required(p, "task", "status", "summary", "artifact_ids")
        task = self.store.get("task", p["task"])
        from .external_deadline import ensure_live
        ensure_live(task)
        if task["status"] != "running" or task.get("execution_mode") != "external":
            raise ResearchError("external outcome needs a running external task")
        if p["status"] == "completed" and task.get("worker_driver") == "subagent":
            from . import review as _review
            if not _review.passed(self, task["id"]):
                raise ResearchError("a subagent round passes only after both the adversarial and the "
                                    "independent verification reviews are recorded as pass")
        if p["status"] not in ("completed", "failed") or not isinstance(p["summary"], str) or not p["summary"].strip():
            raise ResearchError("external outcome needs completed or failed status and a summary")
        stale = self._stale_basis(task)
        if stale:
            task["stale_basis"] = stale
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
        # The round assesses the execution only after its tasks reach terminal states.
        task.update(status=p["status"], external_summary=p["summary"], output_artifacts=artifacts,
                    outcome_reported_by=actor, finished_at=now(), evidence_level="unknown")
        if task.get("external_deadline_policy") and task.get("stop_state") != "confirmed":
            task.update(stop_requested_at=now(), stop_state="requested")
        self.store.put("task", task["id"], task)
        from .strategy import finish_round
        finish_round(self, task)
        from . import failure
        if task["status"] == "completed":
            failure.clear(self, task["route"])
        elif task["status"] in failure.FAILURE_STATES:
            failure.record(self, task, task["status"], p.get("summary"))
        from . import handoff
        handoff.write(self.store, task, actor)
        return task

    def _stale_basis(self, task):
        """Compare the frozen read set with the current revisions (Batch 8.1, §4.3)."""
        from .research_flow import stale as stale_research_step
        read_set = task.get("basis_read_set")
        stale = ["proof_basis"] if stale_research_step(self, task) else []
        if not read_set:
            return stale
        approach = read_set.get("approach") or {}
        if approach and self.store.revision("approach", approach["id"]) != approach.get("last_event_seq"):
            stale.append("approach:" + approach["id"])
        for hypothesis in read_set.get("hypotheses") or []:
            if self.store.revision("research_hypothesis", hypothesis["id"]) != hypothesis.get("last_event_seq"):
                stale.append("hypothesis:" + hypothesis["id"])
        goal = read_set.get("goal")
        if goal and self.store.get("node", goal)["statement_digest"] != read_set.get("goal_statement_digest"):
            stale.append("goal")
        return stale

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
            ledger_claim = self.store.get("claim", entry["claim"])
            from .verification import recheck_required
            if ledger_claim["status"] != "accepted" or recheck_required(self, ledger_claim):
                errors.append(f"revoked manuscript premise: {entry['id']}")
        return {"passed": not errors, "errors": errors, "goal": goal,
                "goal_status": self.node_status(goal) if goal else "unset",
                "semantic_prose_review_required": True, "integrity": audit}

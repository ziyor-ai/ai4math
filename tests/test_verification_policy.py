"""Verification policy: a basis must be authorised before a receipt can be trusted."""
import json
import sys

import pytest

from ai4math.store import ResearchError, now
from ai4math.workbench import Workbench


@pytest.fixture
def wb(tmp_path):
    w = Workbench.create(tmp_path / "ws", "vpol", "Verification policy")
    w.apply("node.add", {"id": "goal", "statement": "Exact goal", "domain": "all n",
                         "quantifier": "universal"})
    w.apply("goal.set", {"node": "goal"})
    w.apply("strategy.configure", {"correspondence_after": 1000})
    w.apply("route.add", {"id": "route", "node": "goal", "rationale": "proof",
                          "next_question": "q", "stop_condition": "s",
                          "max_runs": 50, "stagnation_limit": 50})
    w.apply("route.status", {"id": "route", "status": "active", "reason": "test"})
    yield w
    w.store.close()


def artifact(w, name, text):
    path = w.store.root / name
    path.write_text(text, encoding="utf-8")
    return w.apply("artifact.import", {"path": str(path), "purpose": name})["id"]


def checker_policy(w, checker, **overrides):
    payload = {"id": "POLICY-1", "checker_artifact": checker,
               "allowed_kernel_versions": ["4.34.1"],
               "allowed_library_revisions": ["d13f23b7"],
               "allowed_axioms": ["propext"], "forbidden_axioms": ["sorryAx"],
               "goal_type_match": "exact_statement_digest", "scope": "formal_kernel_only"}
    payload.update(overrides)
    return w.apply("checker.policy.add", payload, "admin")


def verification_policy(w, bases, **overrides):
    payload = {"id": "VP-1", "bases": bases}
    payload.update(overrides)
    return w.apply("verification.policy.add", payload, "admin")


def build(w, name, method="formal_kernel", claim_overrides=None, result_overrides=None,
          node="goal", route="route"):
    arg = artifact(w, name + "-arg.txt", "argument for " + name)
    checker = artifact(w, name + "-checker.py", "print('ok')\n")
    node_row = w.store.get("node", node)
    claim_id = name + "-claim"
    claim = {"id": claim_id, "node": node, "statement_digest": node_row["statement_digest"],
             "evidence_level": "disproved" if method == "counterexample" else "proved",
             "scope": "universal", "summary": "statement", "artifact_ids": [arg]}
    claim.update(claim_overrides or {})
    w.apply("claim.add", claim, "author")
    task_id = name + "-task"
    w.apply("task.add", {"id": task_id, "route": route, "role": "validate",
                         "argv": [sys.executable, "{validator}", "{input}"],
                         "validator_artifact": checker, "inputs": [arg]}, "runner")
    task = w.store.get("task", task_id)
    result = {"task_id": task_id, "statement_digest": node_row["statement_digest"],
              "method": method, "scope": claim["scope"], "evidence_level": claim["evidence_level"],
              "passed": True, "input_artifacts": sorted(task["inputs"]),
              "checks": ["checked"], "limitations": ["pinned"],
              "lean_version": "Lean (version 4.34.1, commit 5045d005)",
              "mathlib_revision": "d13f23b7"}
    result.update(result_overrides or {})
    result_art = artifact(w, name + "-result.json", json.dumps(result))
    with w.store.transaction("fixture.complete_task", "test"):
        row = w.store.get("task", task_id)
        row.update(status="completed", result_artifact=result_art, executed_by="runner",
                   finished_at=now())
        w.store.put("task", task_id, row)
    return claim_id, task_id, checker


def validate(w, claim_id, task_id, name, method="formal_kernel"):
    return w.apply("validation.add", {"id": name + "-val", "claim": claim_id, "method": method,
                                      "trust_basis": "pinned replay", "task": task_id},
                   "registrar")


def test_a_policy_needs_a_known_basis(wb):
    with pytest.raises(ResearchError, match="unknown verification basis"):
        verification_policy(wb, {"vibes": {}})
    with pytest.raises(ResearchError, match="needs a nonempty bases"):
        verification_policy(wb, {})


def test_a_policy_is_unique(wb):
    verification_policy(wb, {"formal_kernel": {}})
    with pytest.raises(ResearchError, match="already exists"):
        verification_policy(wb, {"formal_kernel": {}})


def test_without_a_verification_policy_the_old_path_still_works(wb):
    claim_id, task_id, checker = build(wb, "legacy")
    checker_policy(wb, checker)
    record = validate(wb, claim_id, task_id, "legacy")
    assert record["trust_class"] == "trusted"
    assert record["verification_policy_id"] is None


def test_a_basis_outside_the_policy_is_only_declared(wb):
    claim_id, task_id, checker = build(wb, "nobasis")
    checker_policy(wb, checker, scope="all")
    verification_policy(wb, {"exact_enumeration": {}})
    record = validate(wb, claim_id, task_id, "nobasis")
    assert record["trust_class"] == "declared"
    assert any("not authorised" in reason for reason in record["trust_reasons"])


def test_an_authorised_kernel_basis_is_trusted(wb):
    claim_id, task_id, checker = build(wb, "kernel")
    checker_policy(wb, checker)
    verification_policy(wb, {"formal_kernel": {"checker_policy": "POLICY-1"}})
    record = validate(wb, claim_id, task_id, "kernel")
    assert record["verification_basis"] == "formal_kernel"
    assert record["verification_policy_id"] == "VP-1"
    assert record["trust_class"] == "trusted"


def test_a_kernel_basis_naming_another_policy_is_declared(wb):
    claim_id, task_id, checker = build(wb, "otherpolicy")
    checker_policy(wb, checker)
    verification_policy(wb, {"formal_kernel": {"checker_policy": "SOMEONE-ELSE"}})
    record = validate(wb, claim_id, task_id, "otherpolicy")
    assert record["trust_class"] == "declared"
    assert any("named by the verification policy" in reason for reason in record["trust_reasons"])


def test_enumeration_needs_an_exhaustive_declared_domain(wb):
    wb.apply("node.add", {"id": "finite", "statement": "All 7 cases", "domain": "7 cases",
                          "quantifier": "finite"})
    wb.apply("node.plan", {"node": "goal", "proof_dependencies": ["finite"], "reason": "plan"})
    wb.apply("route.add", {"id": "r-finite", "node": "finite", "rationale": "finite",
                           "next_question": "q", "stop_condition": "s",
                           "max_runs": 5, "stagnation_limit": 5})
    wb.apply("route.status", {"id": "r-finite", "status": "active", "reason": "test"})
    claim_id, task_id, checker = build(wb, "enum", method="exhaustive_search", node="finite",
                                       claim_overrides={"scope": "finite"}, route="r-finite")
    checker_policy(wb, checker, scope="all")
    verification_policy(wb, {"exact_enumeration": {}})
    record = validate(wb, claim_id, task_id, "enum", method="exhaustive_search")
    assert record["verification_basis"] == "exact_enumeration"
    assert record["trust_class"] == "declared"
    assert any("exhaustive" in reason for reason in record["trust_reasons"])


def test_an_exhaustive_enumeration_is_trusted(wb):
    wb.apply("node.add", {"id": "finite", "statement": "All 7 cases", "domain": "7 cases",
                          "quantifier": "finite"})
    wb.apply("node.plan", {"node": "goal", "proof_dependencies": ["finite"], "reason": "plan"})
    wb.apply("route.add", {"id": "r-finite", "node": "finite", "rationale": "finite",
                           "next_question": "q", "stop_condition": "s",
                           "max_runs": 5, "stagnation_limit": 5})
    wb.apply("route.status", {"id": "r-finite", "status": "active", "reason": "test"})
    claim_id, task_id, checker = build(wb, "enum2", method="exhaustive_search", node="finite",
                                       claim_overrides={"scope": "finite"}, route="r-finite",
                                       result_overrides={"exhaustive": True,
                                                         "domain_digest": "a" * 64})
    checker_policy(wb, checker, scope="all")
    verification_policy(wb, {"exact_enumeration": {}})
    record = validate(wb, claim_id, task_id, "enum2", method="exhaustive_search")
    assert record["trust_class"] == "trusted"


def test_an_independent_checker_must_be_listed(wb):
    claim_id, task_id, checker = build(wb, "indep", method="counterexample")
    checker_policy(wb, checker, scope="all")
    verification_policy(wb, {"independent_checker": {"checker_artifacts": ["0" * 64]}})
    record = validate(wb, claim_id, task_id, "indep", method="counterexample")
    assert record["trust_class"] == "declared"
    assert any("not listed" in reason for reason in record["trust_reasons"])

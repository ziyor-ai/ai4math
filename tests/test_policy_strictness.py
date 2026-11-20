"""v2c1 write policy: route kind and proof route are explicit; migration has a barrier."""
import pytest

from ai4math.store import ResearchError
from ai4math.workbench import Workbench


@pytest.fixture
def wb(tmp_path):
    w = Workbench.create(tmp_path / "ws", "strict", "Strict policy")
    w.apply("node.add", {"id": "goal", "statement": "Exact goal", "domain": "all n",
                         "quantifier": "universal"})
    w.apply("goal.set", {"node": "goal"})
    w.apply("strategy.configure", {"correspondence_after": 1000})
    yield w
    w.store.close()


def route(w, ident="route", **extra):
    payload = {"id": ident, "node": "goal", "rationale": "proof", "next_question": "q",
               "stop_condition": "s", "max_runs": 5, "stagnation_limit": 5}
    payload.update(extra)
    return w.apply("route.add", payload, "author")


def claim(w, ident="c1", **extra):
    payload = {"id": ident, "node": "goal",
               "statement_digest": w.store.get("node", "goal")["statement_digest"],
               "evidence_level": "proved", "scope": "universal", "summary": "s"}
    payload.update(extra)
    return w.apply("claim.add", payload, "author")


def test_at_v2c0_the_old_defaults_still_apply(wb):
    assert route(wb)["kind"] == "proof"
    assert claim(wb)["proof_route"] == "composition"


def test_at_v2c1_a_route_must_name_its_kind(wb):
    wb.apply("policy.migrate", {"to": "v2c1"}, "admin")
    with pytest.raises(ResearchError, match="route kind is required"):
        route(wb)
    assert route(wb, kind="proof")["kind"] == "proof"


def test_at_v2c1_a_claim_must_name_its_proof_route(wb):
    wb.apply("policy.migrate", {"to": "v2c1"}, "admin")
    with pytest.raises(ResearchError, match="proof_route is required"):
        claim(wb)
    assert claim(wb, proof_route="direct")["proof_route"] == "direct"


def test_migration_waits_for_open_rounds(wb):
    route(wb)
    wb.apply("route.status", {"id": "route", "status": "active", "reason": "test"})
    wb.apply("round.open", {"id": "r1", "route": "route"}, "coordinator")
    with pytest.raises(ResearchError, match="unsettled rounds"):
        wb.apply("policy.migrate", {"to": "v2c1"}, "admin")
    wb.apply("round.record_attempt", {"round": "r1", "finding": "nothing yet",
                                      "next_question": "q"}, "coordinator")
    wb.apply("round.settle", {"round": "r1"}, "coordinator")
    assert wb.apply("policy.migrate", {"to": "v2c1"}, "admin")["workspace_policy_version"] == "v2c1"

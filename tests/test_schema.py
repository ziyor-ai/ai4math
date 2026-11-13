import pytest

from ai4math.schema import Claim, PhaseState, claim_id


def test_claim_id_is_deterministic_and_ignores_timestamp():
    payload = {"statement": "x", "evidence_level": "finite_support", "phase": 3, "parameter_domain": "k<=10", "created_at": "a"}
    other = dict(payload, created_at="b")
    assert claim_id(payload) == claim_id(other)


def test_phase_state_requires_ordered_parent():
    state = PhaseState(phase=3, project_id="p", objective="obj", parent_phase=2)
    state.validate()
    with pytest.raises(ValueError):
        PhaseState(phase=3, project_id="p", objective="obj", parent_phase=3).validate()


def test_claim_rejects_unknown_level():
    with pytest.raises(ValueError):
        Claim(statement="x", evidence_level="correct", phase=1, parameter_domain="finite").to_dict()

import json
import pytest

from ai4math.registry import ClaimRegistry
from ai4math.schema import Claim


def test_registry_is_idempotent(tmp_path):
    registry = ClaimRegistry(tmp_path / ".ai4math")
    claim = Claim(statement="a finite statement", evidence_level="finite_support", phase=1, parameter_domain="n<=3")
    first = registry.register(claim)
    second = registry.register(claim)
    assert first == second
    assert len(registry.list()) == 1
    assert len((tmp_path / ".ai4math" / "claims.jsonl").read_text(encoding="utf-8").splitlines()) == 1


def test_registry_is_idempotent_across_presentation_timestamps(tmp_path):
    registry = ClaimRegistry(tmp_path / ".ai4math")
    first = Claim(statement="same", evidence_level="finite_support", phase=1, parameter_domain="n<=3", created_at="2026-01-01T00:00:00Z")
    second = Claim(statement="same", evidence_level="finite_support", phase=1, parameter_domain="n<=3", created_at="2026-01-02T00:00:00Z")
    assert registry.register(first) == registry.register(second)
    assert len(registry.list()) == 1


def test_registry_requires_existing_dependencies(tmp_path):
    registry = ClaimRegistry(tmp_path / ".ai4math")
    claim = Claim(
        statement="dependent",
        evidence_level="finite_support",
        phase=2,
        parameter_domain="n<=3",
        dependencies=["0" * 32],
    )
    with pytest.raises(ValueError, match="missing claim dependencies"):
        registry.register(claim)


def test_registry_audit_detects_index_drift(tmp_path):
    registry = ClaimRegistry(tmp_path / ".ai4math")
    registry.register(Claim(statement="a", evidence_level="finite_support", phase=1, parameter_domain="n<=3"))
    (tmp_path / ".ai4math" / "claims.jsonl").write_text("", encoding="utf-8")
    report = registry.audit()
    assert not report["passed"]
    assert report["missing_from_index"]

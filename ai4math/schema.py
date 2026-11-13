"""Stable schemas for phase state and evidence claims.

The schemas intentionally describe the control plane only. Mathematical truth
still comes from the target project's proof and validation machinery.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List

EVIDENCE_LEVELS = frozenset({"proved", "disproved", "finite_support", "unknown"})
PHASE_STATUSES = frozenset({"planned", "running", "review", "passed", "failed", "paused"})
CLAIM_ID_RE = re.compile(r"^[0-9a-f]{32}$")


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def is_claim_id(value: object) -> bool:
    """Return whether *value* is a canonical ai4math claim identifier."""
    return isinstance(value, str) and CLAIM_ID_RE.fullmatch(value) is not None


def claim_id(payload: Dict[str, Any]) -> str:
    """Content-address a claim; presentation-only timestamps are excluded."""
    identity = {
        k: v for k, v in payload.items()
        if k not in {"claim_id", "created_at", "updated_at"}
    }
    return hashlib.sha256(canonical_json(identity).encode("utf-8")).hexdigest()[:32]


def _nonempty(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


@dataclass
class PhaseState:
    phase: int
    project_id: str
    objective: str
    parent_phase: int | None = None
    status: str = "planned"
    route: str = ""
    evidence_level_target: str = "unknown"
    parameter_domain: str = ""
    predecessor_claims: List[str] = field(default_factory=list)
    required_outputs: List[str] = field(default_factory=list)
    stop_conditions: List[str] = field(default_factory=list)
    child_thread_id: str | None = None
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)

    def validate(self) -> None:
        if self.phase < 1:
            raise ValueError("phase must be positive")
        _nonempty(self.project_id, "project_id")
        _nonempty(self.objective, "objective")
        if self.status not in PHASE_STATUSES:
            raise ValueError(f"unknown phase status: {self.status}")
        if self.evidence_level_target not in EVIDENCE_LEVELS:
            raise ValueError(f"unknown evidence level: {self.evidence_level_target}")
        if self.parent_phase is not None and self.parent_phase >= self.phase:
            raise ValueError("parent_phase must be smaller than phase")
        for predecessor in self.predecessor_claims:
            if not is_claim_id(predecessor):
                raise ValueError(f"invalid predecessor claim id: {predecessor}")

    def to_dict(self) -> Dict[str, Any]:
        self.validate()
        return {
            "phase": self.phase,
            "project_id": self.project_id,
            "parent_phase": self.parent_phase,
            "status": self.status,
            "objective": self.objective,
            "route": self.route,
            "evidence_level_target": self.evidence_level_target,
            "parameter_domain": self.parameter_domain,
            "predecessor_claims": list(self.predecessor_claims),
            "required_outputs": list(self.required_outputs),
            "stop_conditions": list(self.stop_conditions),
            "child_thread_id": self.child_thread_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass
class Claim:
    statement: str
    evidence_level: str
    phase: int
    parameter_domain: str
    dependencies: List[str] = field(default_factory=list)
    producer: str = ""
    validation: str = ""
    run_log: str = ""
    sha256_manifest: str = ""
    import_audit: str = ""
    counterexamples: List[str] = field(default_factory=list)
    solver_status: str = ""
    claim_id: str = ""
    created_at: str = field(default_factory=utc_now)

    def to_dict(self) -> Dict[str, Any]:
        if self.evidence_level not in EVIDENCE_LEVELS:
            raise ValueError(f"unknown evidence level: {self.evidence_level}")
        if self.phase < 1:
            raise ValueError("phase must be positive")
        _nonempty(self.statement, "statement")
        _nonempty(self.parameter_domain, "parameter_domain")
        body = {
            "statement": self.statement.strip(),
            "evidence_level": self.evidence_level,
            "phase": self.phase,
            "parameter_domain": self.parameter_domain.strip(),
            "dependencies": sorted(set(self.dependencies)),
            "producer": self.producer,
            "validation": self.validation,
            "run_log": self.run_log,
            "sha256_manifest": self.sha256_manifest,
            "import_audit": self.import_audit,
            "counterexamples": sorted(set(self.counterexamples)),
            "solver_status": self.solver_status,
        }
        for dependency in body["dependencies"]:
            if not is_claim_id(dependency):
                raise ValueError(f"invalid dependency claim id: {dependency}")
        for counterexample in body["counterexamples"]:
            if counterexample and not isinstance(counterexample, str):
                raise ValueError("counterexamples must be strings")
        body["claim_id"] = self.claim_id or claim_id(body)
        body["created_at"] = self.created_at
        return body


def load_json(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value

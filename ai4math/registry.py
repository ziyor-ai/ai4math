"""Append-only, content-addressed claim registry."""

from __future__ import annotations

import json
import os
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List

from .schema import Claim, EVIDENCE_LEVELS, canonical_json, claim_id, is_claim_id, utc_now


class ClaimRegistry:
    def __init__(self, control_root: Path) -> None:
        self.root = Path(control_root)
        self.claims_dir = self.root / "claims"
        self.index_path = self.root / "claims.jsonl"

    def register(self, claim: Claim | Dict[str, Any]) -> str:
        payload = claim.to_dict() if isinstance(claim, Claim) else dict(claim)
        for field in ("statement", "evidence_level", "phase", "parameter_domain"):
            if field not in payload:
                raise ValueError(f"claim missing {field}")
        if payload.get("evidence_level") not in EVIDENCE_LEVELS:
            raise ValueError(f"unknown evidence level: {payload.get('evidence_level')}")
        if not isinstance(payload.get("phase"), int) or payload["phase"] < 1:
            raise ValueError("claim phase must be a positive integer")
        if not isinstance(payload.get("statement"), str) or not payload["statement"].strip():
            raise ValueError("claim statement must be non-empty")
        if not isinstance(payload.get("parameter_domain"), str) or not payload["parameter_domain"].strip():
            raise ValueError("claim parameter_domain must be non-empty")
        dependencies = payload.get("dependencies", [])
        if not isinstance(dependencies, list):
            raise ValueError("dependencies must be a list")
        dependencies = sorted(set(dependencies))
        if any(not is_claim_id(dep) for dep in dependencies):
            raise ValueError("dependencies must contain canonical claim ids")
        payload["dependencies"] = dependencies
        expected = claim_id(payload)
        supplied = payload.get("claim_id")
        if supplied and supplied != expected:
            raise ValueError(f"claim_id mismatch: supplied={supplied}, expected={expected}")
        payload["claim_id"] = expected
        self.claims_dir.mkdir(parents=True, exist_ok=True)
        target = self.claims_dir / f"{expected}.json"
        encoded = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        if target.exists():
            existing = json.loads(target.read_text(encoding="utf-8"))
            if self._identity(existing) != self._identity(payload):
                raise ValueError(f"immutable claim collision: {expected}")
            # Timestamps are presentation metadata and are deliberately excluded
            # from the identity.  Re-registering the same claim with a new
            # timestamp is therefore idempotent.
            return expected
        missing = [dep for dep in dependencies if not (self.claims_dir / f"{dep}.json").is_file()]
        if missing:
            raise ValueError("missing claim dependencies: " + ", ".join(missing))
        if expected in dependencies:
            raise ValueError("claim cannot depend on itself")
        if not target.exists():
            tmp = target.with_suffix(".tmp")
            tmp.write_text(encoded, encoding="utf-8")
            os.replace(tmp, target)
            with self.index_path.open("a", encoding="utf-8") as handle:
                handle.write(canonical_json({"claim_id": expected, "registered_at": utc_now()}) + "\n")
        return expected

    @staticmethod
    def _identity(payload: Dict[str, Any]) -> str:
        identity = {
            key: value
            for key, value in payload.items()
            if key not in {"claim_id", "created_at", "updated_at"}
        }
        return canonical_json(identity)

    def get(self, identifier: str) -> Dict[str, Any]:
        path = self.claims_dir / f"{identifier}.json"
        if not path.is_file():
            raise KeyError(identifier)
        return json.loads(path.read_text(encoding="utf-8"))

    def list(self) -> List[Dict[str, Any]]:
        if not self.claims_dir.exists():
            return []
        return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(self.claims_dir.glob("*.json"))]

    def audit(self) -> Dict[str, Any]:
        """Audit claim files and the append-only index without changing them."""
        files = {p.stem: p for p in self.claims_dir.glob("*.json")} if self.claims_dir.exists() else {}
        file_errors: List[str] = []
        for identifier, path in files.items():
            if not is_claim_id(identifier):
                file_errors.append(f"invalid filename: {path.name}")
                continue
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                if claim_id(payload) != identifier:
                    file_errors.append(f"claim id mismatch: {path.name}")
            except (OSError, ValueError, TypeError) as exc:
                file_errors.append(f"invalid claim {path.name}: {exc}")

        index_ids: List[str] = []
        index_errors: List[str] = []
        if self.index_path.exists():
            for line_no, line in enumerate(self.index_path.read_text(encoding="utf-8").splitlines(), 1):
                try:
                    row = json.loads(line)
                    identifier = row.get("claim_id")
                    if not is_claim_id(identifier):
                        raise ValueError("invalid claim_id")
                    index_ids.append(identifier)
                except (ValueError, TypeError, json.JSONDecodeError) as exc:
                    index_errors.append(f"line {line_no}: {exc}")
        file_ids = set(files)
        index_set = set(index_ids)
        duplicates = sorted(identifier for identifier, count in Counter(index_ids).items() if count > 1)
        return {
            "file_count": len(files),
            "index_count": len(index_ids),
            "missing_from_index": sorted(file_ids - index_set),
            "missing_files": sorted(index_set - file_ids),
            "duplicate_index_ids": duplicates,
            "file_errors": file_errors,
            "index_errors": index_errors,
            "passed": not (
                file_errors
                or index_errors
                or file_ids - index_set
                or index_set - file_ids
                or duplicates
            ),
        }

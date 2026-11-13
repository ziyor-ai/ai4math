"""Deterministic phase gate for target-project artifacts.

The gate is deliberately stricter than a producer's self-reported audit.  It
recomputes phase-file hashes and independently compares the target project's
original input tree with an immutable baseline manifest.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List

from .config import control_root, load_project, original_manifest_path
from .registry import ClaimRegistry
from .schema import EVIDENCE_LEVELS, claim_id

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


@dataclass
class GateResult:
    phase: int
    passed: bool
    checks: Dict[str, Any] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _json(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected object: {path}")
    return value


def _manifest_items(manifest: Dict[str, Any]) -> Dict[str, Any]:
    if "sha256" in manifest:
        files = manifest["sha256"]
    elif "files" in manifest:
        files = manifest["files"]
    else:
        raise ValueError("SHA manifest must contain `sha256` or `files`")
    if not isinstance(files, dict) or not files:
        raise ValueError("SHA manifest must contain at least one file")
    return files


def _resolve_manifest_path(root: Path, phase_dir: Path, name: str) -> Path:
    """Resolve a manifest name while preventing path escape."""
    if not isinstance(name, str) or not name.strip():
        raise ValueError("manifest path must be a non-empty string")
    normalized = name.replace("\\", "/")
    candidate = Path(normalized)
    if candidate.is_absolute() or re.match(r"^[A-Za-z]:/", normalized):
        raise ValueError(f"absolute manifest path is forbidden: {name}")
    base = root if normalized.startswith(("data/", "notes/")) else phase_dir
    target = (base / candidate).resolve()
    allowed = root.resolve()
    try:
        target.relative_to(allowed)
    except ValueError as exc:
        raise ValueError(f"manifest path escapes project root: {name}") from exc
    return target


def _required_target_names(phase_dir: Path, required: Iterable[str]) -> set[Path]:
    # The manifest cannot contain its own digest without a recursive fixed
    # point.  The manifest file itself is checked structurally and is excluded
    # from the tracked payload requirement.
    return {(phase_dir / name).resolve() for name in required if name != "sha256.json"}


def _snapshot_original(original_root: Path) -> Dict[str, Dict[str, Any]]:
    if not original_root.is_dir():
        raise ValueError(f"original_root is not a directory: {original_root}")
    out: Dict[str, Dict[str, Any]] = {}
    for path in sorted(p for p in original_root.rglob("*") if p.is_file()):
        rel = path.relative_to(original_root).as_posix()
        out[rel] = {"sha256": _sha256(path), "size": path.stat().st_size}
    return out


def _check_original_manifest(project: Dict[str, Any], result: GateResult) -> None:
    original_root = Path(project["original_root"])
    manifest_path = original_manifest_path(project)
    result.checks["original_manifest"] = str(manifest_path)
    if not manifest_path.is_file():
        result.errors.append("missing immutable original input manifest: " + str(manifest_path))
        return
    try:
        manifest = _json(manifest_path)
        files = manifest.get("files")
        if not isinstance(files, dict):
            raise ValueError("original manifest files must be an object")
        expected_root = str(manifest.get("root", "")).replace("\\", "/")
        configured_root = str(original_root).replace("\\", "/")
        project_original = str(project.get("original_root", "")).replace("\\", "/")
        if expected_root and expected_root not in {configured_root, project_original}:
            result.errors.append("original manifest root does not match project original_root")
        actual = _snapshot_original(original_root)
        missing = sorted(set(files) - set(actual))
        added = sorted(set(actual) - set(files))
        changed = sorted(name for name in set(files) & set(actual) if files[name] != actual[name])
        result.checks["original_file_count"] = len(actual)
        result.checks["original_manifest_file_count"] = len(files)
        if missing:
            result.errors.append("original files missing: " + ", ".join(missing[:12]))
        if added:
            result.errors.append("unregistered original files: " + ", ".join(added[:12]))
        if changed:
            result.errors.append("original file SHA/size mismatch: " + ", ".join(changed[:12]))
        if manifest.get("file_count") != len(files):
            result.errors.append("original manifest file_count mismatch")
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        result.errors.append(f"cannot independently audit original inputs: {exc}")


def _resolve_claim_path(root: Path, value: object) -> bool:
    if not isinstance(value, str) or not value:
        return False
    path = Path(value)
    if not path.is_absolute():
        path = root / path
    return path.is_file()


def _check_claims(project: Dict[str, Any], phase: int, validation: Dict[str, Any], result: GateResult) -> None:
    minimum_phase = project.get("require_claim_from_phase")
    require_claim = project.get("require_claim", False)
    if minimum_phase is not None:
        try:
            require_claim = phase >= int(minimum_phase)
        except (TypeError, ValueError):
            require_claim = False
    if not require_claim:
        return
    registry = ClaimRegistry(control_root(project))
    claims = [claim for claim in registry.list() if claim.get("phase") == phase]
    result.checks["phase_claim_count"] = len(claims)
    if not claims:
        result.errors.append(f"no registered claim for phase {phase}")
        return
    expected_level = validation.get("evidence_level", "unknown")
    root = Path(project["root"])
    state_path = control_root(project) / f"phase{phase}_state.json"
    state_predecessors: set[str] = set()
    if state_path.is_file():
        try:
            state = _json(state_path)
            raw_predecessors = state.get("predecessor_claims", [])
            if not isinstance(raw_predecessors, list):
                result.errors.append("phase state predecessor_claims must be a list")
            else:
                state_predecessors = set(raw_predecessors)
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            result.errors.append(f"cannot read phase state: {exc}")
    matching_predecessor_claim = False
    for claim in claims:
        if claim.get("evidence_level") != expected_level:
            result.errors.append(f"claim evidence level mismatch: {claim.get('claim_id')}")
        if claim.get("claim_id") != claim_id(claim):
            result.errors.append(f"claim id mismatch: {claim.get('claim_id')}")
        if not claim.get("parameter_domain"):
            result.errors.append(f"claim has empty parameter domain: {claim.get('claim_id')}")
        for field in ("producer", "validation", "run_log", "sha256_manifest", "import_audit"):
            if not _resolve_claim_path(root, claim.get(field)):
                result.errors.append(f"claim {claim.get('claim_id')} missing {field} artifact")
        claim_dependencies = set(claim.get("dependencies", []))
        if state_predecessors and claim_dependencies == state_predecessors:
            matching_predecessor_claim = True
        elif state_predecessors:
            result.warnings.append(f"legacy claim/state predecessor mismatch: {claim.get('claim_id')}")
    if state_predecessors and not matching_predecessor_claim:
        result.errors.append("no claim matches phase state predecessor_claims")
    audit = registry.audit()
    result.checks["claim_registry_audit"] = audit
    if not audit["passed"]:
        result.errors.append("claim registry audit failed")


def check_phase(config_path: Path, phase: int) -> GateResult:
    project = load_project(config_path)
    root = Path(project["root"])
    phase_dir = root / project["phase_root"] / f"phase{phase}"
    result = GateResult(phase=phase, passed=False)
    required = project.get(
        "required_phase_files",
        ["producer.py", "validation.py", "validation.json", "sha256.json", "import_audit.json", "run.log"],
    )
    missing = [name for name in required if not (phase_dir / name).is_file()]
    result.checks["phase_dir"] = str(phase_dir)
    result.checks["required_files"] = required
    if missing:
        result.errors.append("missing required files: " + ", ".join(missing))

    summary_candidates = [
        root / project["notes_root"] / f"phase{phase}_summary.md",
        phase_dir / f"phase{phase}_summary.md",
    ]
    summary_path = next((p for p in summary_candidates if p.is_file()), None)
    if summary_path is None:
        result.errors.append("missing phase summary")
    else:
        result.checks["summary"] = str(summary_path)

    validation_path = phase_dir / "validation.json"
    validation: Dict[str, Any] = {}
    if validation_path.is_file():
        try:
            validation = _json(validation_path)
            result.checks["validation_status"] = validation.get("status")
            result.checks["evidence_level"] = validation.get("evidence_level", "unknown")
            if "evidence_level" not in validation:
                result.warnings.append("validation.json has no evidence_level; strict claim consistency starts at configured phase")
            if validation.get("phase") not in (None, phase):
                result.errors.append("validation phase does not match gate phase")
            if validation.get("status") not in {"passed", "ok", "complete"}:
                result.errors.append(f"validation status is not passing: {validation.get('status')}")
            if int(validation.get("failure_count", 0) or 0) != 0:
                result.errors.append("validation reports failures")
            if validation.get("evidence_level", "unknown") not in EVIDENCE_LEVELS:
                result.errors.append("validation has invalid evidence_level")
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            result.errors.append(f"cannot read validation.json: {exc}")

    sha_path = phase_dir / "sha256.json"
    if sha_path.is_file():
        try:
            items = _manifest_items(_json(sha_path))
            mismatches: List[str] = []
            tracked_targets: set[Path] = set()
            for name, expected_info in items.items():
                target = _resolve_manifest_path(root, phase_dir, name)
                tracked_targets.add(target)
                expected = expected_info.get("sha256") if isinstance(expected_info, dict) else expected_info
                if not isinstance(expected, str) or SHA256_RE.fullmatch(expected.lower()) is None:
                    mismatches.append(f"invalid_sha:{name}")
                elif not target.is_file():
                    mismatches.append(f"missing:{name}")
                elif expected.lower() != _sha256(target):
                    mismatches.append(f"sha256:{name}")
                if isinstance(expected_info, dict) and "size" in expected_info and target.is_file():
                    try:
                        if int(expected_info["size"]) != target.stat().st_size:
                            mismatches.append(f"size:{name}")
                    except (TypeError, ValueError):
                        mismatches.append(f"invalid_size:{name}")
            required_targets = _required_target_names(phase_dir, required)
            uncovered = sorted(
                str(path.relative_to(phase_dir))
                for path in required_targets - tracked_targets
                if path.is_relative_to(phase_dir)
            )
            if uncovered:
                mismatches.append("manifest_missing_required:" + ",".join(uncovered))
            result.checks["sha_entries"] = len(items)
            if mismatches:
                result.errors.append("SHA manifest mismatch: " + ", ".join(mismatches[:20]))
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            result.errors.append(f"cannot read sha256.json: {exc}")

    audit_path = phase_dir / "import_audit.json"
    if audit_path.is_file():
        try:
            audit = _json(audit_path)
            result.checks["import_file_count"] = audit.get("file_count")
            if audit.get("data_imports_modified") is not False:
                result.errors.append("data_imports_modified is not false")
            if audit.get("raw_bytes_preserved") is not True:
                result.errors.append("raw_bytes_preserved is not true")
            files = audit.get("files")
            if files is not None and not isinstance(files, dict):
                result.errors.append("import_audit.files must be an object")
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            result.errors.append(f"cannot read import_audit.json: {exc}")

    _check_original_manifest(project, result)
    _check_claims(project, phase, validation, result)
    result.passed = not result.errors
    return result

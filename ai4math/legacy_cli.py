"""Command line entry point for the ai4math control plane."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .config import control_root, load_project, original_manifest_path
from .gate import check_phase
from .registry import ClaimRegistry
from .schema import Claim, PhaseState, utc_now


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _append_event(root: Path, event: dict) -> None:
    path = root / "phase_events.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"timestamp": utc_now(), **event}, ensure_ascii=False, sort_keys=True) + "\n")


def _sha256(path: Path) -> str:
    import hashlib
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _build_original_manifest(project: dict) -> dict:
    original_root = Path(project["original_root"])
    if not original_root.is_dir():
        raise SystemExit(f"original_root is not a directory: {original_root}")
    files = {}
    for path in sorted(p for p in original_root.rglob("*") if p.is_file()):
        rel = path.relative_to(original_root).as_posix()
        files[rel] = {"sha256": _sha256(path), "size": path.stat().st_size}
    return {
        "schema_version": 1,
        "root": str(original_root),
        "file_count": len(files),
        "files": files,
        "created_at": utc_now(),
    }


def cmd_init(args: argparse.Namespace) -> int:
    project = load_project(Path(args.config))
    root = control_root(project)
    root.mkdir(parents=True, exist_ok=True)
    _write_json(root / "project.json", project)
    if not (root / "phase_state.json").exists():
        _write_json(root / "phase_state.json", {"status": "paused", "project_id": project["project_id"], "updated_at": utc_now()})
    manifest = original_manifest_path(project)
    if not manifest.exists():
        _write_json(manifest, _build_original_manifest(project))
    print(json.dumps({"status": "initialized", "control_root": str(root)}, ensure_ascii=False))
    return 0


def cmd_phase_state(args: argparse.Namespace) -> int:
    project = load_project(Path(args.config))
    state = PhaseState(
        phase=args.phase,
        project_id=project["project_id"],
        objective=args.objective,
        parent_phase=args.parent_phase,
        route=args.route,
        evidence_level_target=args.evidence_level,
        parameter_domain=args.domain,
        status=args.status,
        predecessor_claims=args.predecessor_claim,
        required_outputs=["producer", "validation", "run_log", "sha256", "import_audit", "summary"],
        stop_conditions=args.stop_condition,
        child_thread_id=args.child_thread_id,
    )
    root = control_root(project)
    if args.parent_phase is not None:
        parent_path = root / f"phase{args.parent_phase}_state.json"
        if parent_path.is_file():
            parent = json.loads(parent_path.read_text(encoding="utf-8"))
            if parent.get("status") != "passed":
                raise SystemExit(f"parent phase {args.parent_phase} is not passed")
    existing_path = root / f"phase{args.phase}_state.json"
    if existing_path.is_file():
        existing = json.loads(existing_path.read_text(encoding="utf-8"))
        if existing.get("status") == "passed" and args.status != "passed":
            raise SystemExit(f"phase {args.phase} is already passed; use a new phase")
    _write_json(root / f"phase{args.phase}_state.json", state.to_dict())
    _append_event(root, {"event": "phase_state", "phase": args.phase, "status": args.status, "parent_phase": args.parent_phase})
    print(json.dumps(state.to_dict(), ensure_ascii=False, indent=2))
    return 0


def cmd_gate(args: argparse.Namespace) -> int:
    result = check_phase(Path(args.config), args.phase)
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    return 0 if result.passed else 2


def cmd_record_gate(args: argparse.Namespace) -> int:
    project = load_project(Path(args.config))
    result = check_phase(Path(args.config), args.phase)
    root = control_root(project)
    payload = {"checked_at": utc_now(), **result.to_dict()}
    _write_json(root / "gates" / f"phase{args.phase}_gate.json", payload)
    _append_event(root, {"event": "gate", "phase": args.phase, "passed": result.passed})
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if result.passed else 2


def cmd_register(args: argparse.Namespace) -> int:
    project = load_project(Path(args.config))
    payload = json.loads(Path(args.claim).read_text(encoding="utf-8"))
    identifier = ClaimRegistry(control_root(project)).register(payload)
    print(json.dumps({"status": "registered", "claim_id": identifier}, ensure_ascii=False))
    return 0


def cmd_registry_audit(args: argparse.Namespace) -> int:
    project = load_project(Path(args.config))
    result = ClaimRegistry(control_root(project)).audit()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["passed"] else 2


def cmd_status(args: argparse.Namespace) -> int:
    project = load_project(Path(args.config))
    control = control_root(project)
    claims = ClaimRegistry(control).list()
    states = sorted(control.glob("phase*_state.json")) if control.exists() else []
    state_rows = []
    for path in states:
        try:
            state_rows.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            state_rows.append({"file": path.name, "status": "invalid"})
    gates = sorted((control / "gates").glob("phase*_gate.json")) if (control / "gates").exists() else []
    gate_rows = []
    for path in gates:
        try:
            row = json.loads(path.read_text(encoding="utf-8"))
            gate_rows.append({"phase": row.get("phase"), "passed": row.get("passed"), "checked_at": row.get("checked_at")})
        except (OSError, ValueError):
            gate_rows.append({"file": path.name, "passed": False})
    print(json.dumps({"project_id": project["project_id"], "control_root": str(control), "claim_count": len(claims), "phase_state_files": [p.name for p in states], "phase_states": state_rows, "gates": gate_rows, "registry_audit": ClaimRegistry(control).audit()}, ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ai4math")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init")
    init.add_argument("--config", required=True)
    init.set_defaults(func=cmd_init)

    state = sub.add_parser("phase-state")
    state.add_argument("--config", required=True)
    state.add_argument("--phase", type=int, required=True)
    state.add_argument("--objective", required=True)
    state.add_argument("--domain", default="")
    state.add_argument("--route", default="")
    state.add_argument("--parent-phase", type=int)
    state.add_argument("--evidence-level", choices=["proved", "disproved", "finite_support", "unknown"], default="unknown")
    state.add_argument("--status", choices=["planned", "running", "review", "passed", "failed", "paused"], default="planned")
    state.add_argument("--stop-condition", action="append", default=[])
    state.add_argument("--predecessor-claim", action="append", default=[])
    state.add_argument("--child-thread-id", default=None)
    state.set_defaults(func=cmd_phase_state)

    gate = sub.add_parser("gate")
    gate.add_argument("--config", required=True)
    gate.add_argument("--phase", type=int, required=True)
    gate.set_defaults(func=cmd_gate)

    record_gate = sub.add_parser("record-gate")
    record_gate.add_argument("--config", required=True)
    record_gate.add_argument("--phase", type=int, required=True)
    record_gate.set_defaults(func=cmd_record_gate)

    register = sub.add_parser("register")
    register.add_argument("--config", required=True)
    register.add_argument("--claim", required=True)
    register.set_defaults(func=cmd_register)

    status = sub.add_parser("status")
    status.add_argument("--config", required=True)
    status.set_defaults(func=cmd_status)

    audit = sub.add_parser("registry-audit")
    audit.add_argument("--config", required=True)
    audit.set_defaults(func=cmd_registry_audit)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)

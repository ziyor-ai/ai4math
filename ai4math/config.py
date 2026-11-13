"""Project adapter configuration."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict


def load_project(path: Path) -> Dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("project config must be a JSON object")
    for key in ("project_id", "root", "phase_root", "notes_root", "original_root"):
        if key not in data:
            raise ValueError(f"project config missing {key}")
    root = Path(data["root"]).expanduser().resolve()
    data["root"] = str(root)
    original = Path(data["original_root"])
    if not original.is_absolute():
        original = root / original
    data["original_root"] = str(original)
    required = data.get("required_phase_files")
    if required is not None and (not isinstance(required, list) or not all(isinstance(x, str) and x for x in required)):
        raise ValueError("required_phase_files must be a non-empty list of names")
    return data


def control_root(project: Dict[str, Any]) -> Path:
    return Path(project["root"]) / project.get("control_root", ".ai4math")


def original_manifest_path(project: Dict[str, Any]) -> Path:
    configured = project.get("original_manifest")
    if configured:
        path = Path(configured)
        return path if path.is_absolute() else Path(project["root"]) / path
    return control_root(project) / "original_manifest.json"

"""The batch checker must refuse a proof that states a different theorem (F9).

The tests use local command shims instead of a Lean installation.
"""
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

CHECKER = Path(__file__).resolve().parents[1] / "scripts" / "lean_replay_checker.py"


def test_a_mismatched_statement_is_refused(tmp_path):
    proof = tmp_path / "proof.lean"
    proof.write_text("import Mathlib\n\ntheorem other : 1 + 1 = 2 := by norm_num\n",
                     encoding="utf-8")
    contract = {"task_id": "t1", "node": {"quantifier": "universal",
                                          "lean_statement": "theorem wanted : 2 + 2 = 4"},
                "input_artifacts": [{"id": "a" * 64, "path": str(proof)}],
                "result_contract": {"task_id": "t1", "statement_digest": "d" * 64}}
    path = tmp_path / "input.json"
    path.write_text(json.dumps(contract), encoding="utf-8")
    proc = subprocess.run([sys.executable, str(CHECKER), str(path)], cwd=tmp_path,
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert proc.returncode != 0
    assert "does not state the node's Lean declaration" in proc.stderr
    assert not (tmp_path / "result.json").exists()


def test_a_declared_theorem_must_be_checkable(tmp_path, monkeypatch):
    """If Lean cannot #check the declared name, the checker refuses (F9)."""
    proof = tmp_path / "proof.lean"
    proof.write_text("import Mathlib\n\ntheorem wanted : 2 + 2 = 4 := by norm_num\n",
                     encoding="utf-8")
    contract = {"task_id": "t1", "node": {"quantifier": "universal",
                                          "lean_statement": "theorem wanted : 2 + 2 = 4"},
                "input_artifacts": [{"id": "a" * 64, "path": str(proof)}],
                "result_contract": {"task_id": "t1", "statement_digest": "d" * 64}}
    path = tmp_path / "input.json"
    path.write_text(json.dumps(contract), encoding="utf-8")
    stub = tmp_path / "lean.bat"
    stub.write_text("@echo off\necho unknown identifier\nexit /b 1\n", encoding="utf-8")
    monkeypatch.setenv("AI4MATH_LEAN_PROJECT", str(tmp_path / "nope"))
    monkeypatch.setenv("PATH", str(tmp_path) + ";" + os.environ["PATH"])
    proc = subprocess.run([sys.executable, str(CHECKER), str(path)], cwd=tmp_path,
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert proc.returncode != 0
    assert "could not #check" in proc.stderr
    assert not (tmp_path / "result.json").exists()


def test_a_mismatched_lean_type_is_refused(tmp_path, monkeypatch):
    """When the node declares lean_type, Lean's printed type must match it (F9)."""
    proof = tmp_path / "proof.lean"
    proof.write_text("import Mathlib\n\ntheorem wanted : 2 + 2 = 4 := by norm_num\n",
                     encoding="utf-8")
    contract = {"task_id": "t1",
                "node": {"quantifier": "universal",
                         "lean_statement": "theorem wanted : 2 + 2 = 4",
                         "lean_type": "3 + 3 = 6"},
                "input_artifacts": [{"id": "a" * 64, "path": str(proof)}],
                "result_contract": {"task_id": "t1", "statement_digest": "d" * 64}}
    path = tmp_path / "input.json"
    path.write_text(json.dumps(contract), encoding="utf-8")
    stub = tmp_path / "lean.bat"
    stub.write_text("@echo off\necho wanted : 2 + 2 = 4\nexit /b 0\n", encoding="utf-8")
    monkeypatch.setenv("AI4MATH_LEAN_PROJECT", str(tmp_path / "nope"))
    monkeypatch.setenv("PATH", str(tmp_path) + ";" + os.environ["PATH"])
    proc = subprocess.run([sys.executable, str(CHECKER), str(path)], cwd=tmp_path,
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert proc.returncode != 0
    assert "does not match the node's declared lean_type" in proc.stderr


def test_a_sorry_axiom_is_refused(tmp_path, monkeypatch):
    """A kernel-checked proof that still uses sorryAx is not a proof (F9)."""
    proof = tmp_path / "proof.lean"
    proof.write_text("import Mathlib\n\ntheorem wanted : 2 + 2 = 4 := by sorry\n",
                     encoding="utf-8")
    contract = {"task_id": "t1", "node": {"quantifier": "universal",
                                          "lean_statement": "theorem wanted : 2 + 2 = 4"},
                "input_artifacts": [{"id": "a" * 64, "path": str(proof)}],
                "result_contract": {"task_id": "t1", "statement_digest": "d" * 64}}
    path = tmp_path / "input.json"
    path.write_text(json.dumps(contract), encoding="utf-8")
    stub = tmp_path / "lean.bat"
    stub.write_text("@echo off\necho axioms: [propext, sorryAx]\nexit /b 0\n", encoding="utf-8")
    monkeypatch.setenv("AI4MATH_LEAN_PROJECT", str(tmp_path / "nope"))
    monkeypatch.setenv("PATH", str(tmp_path) + ";" + os.environ["PATH"])
    proc = subprocess.run([sys.executable, str(CHECKER), str(path)], cwd=tmp_path,
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert proc.returncode != 0
    assert "sorryAx" in proc.stderr
    assert not (tmp_path / "result.json").exists()


@pytest.mark.parametrize("project_mode", ["unset", "without-lakefile", "lake"])
def test_a_matching_statement_passes_the_binding_check(tmp_path, monkeypatch, project_mode):
    proof = tmp_path / "proof.lean"
    proof.write_text("import Mathlib\n\ntheorem wanted : 2 + 2 = 4 := by norm_num\n",
                     encoding="utf-8")
    contract = {"task_id": "t1", "node": {"quantifier": "universal",
                                          "lean_statement": "theorem wanted : 2 + 2 = 4"},
                "input_artifacts": [{"id": "a" * 64, "path": str(proof)}],
                "result_contract": {"task_id": "t1", "statement_digest": "d" * 64}}
    path = tmp_path / "input.json"
    path.write_text(json.dumps(contract), encoding="utf-8")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.delenv("AI4MATH_LEAN_PROJECT", raising=False)
    project = tmp_path / "project"
    manifest = b"{}\n"
    manifest_sha = None
    version_position = 1
    tool = "lean"
    if project_mode != "unset":
        project.mkdir()
        monkeypatch.setenv("AI4MATH_LEAN_PROJECT", str(project))
        (project / "lake-manifest.json").write_bytes(manifest)
    if project_mode == "lake":
        (project / "lakefile.lean").touch()
        manifest_sha = hashlib.sha256(manifest).hexdigest()
        tool = "lake"
        version_position = 3
    if os.name == "nt":
        stub = bin_dir / (tool + ".bat")
        stub.write_text(f'@echo off\nif "%~{version_position}"=="--version" echo Lean (version 4.test)\n'
                        "exit /b 0\n", encoding="utf-8")
    else:
        stub = bin_dir / tool
        stub.write_text(f'#!/bin/sh\nif [ "${version_position}" = "--version" ]; then\n'
                        "  echo 'Lean (version 4.test)'\nfi\nexit 0\n", encoding="utf-8")
        stub.chmod(0o755)
    proc = subprocess.run([sys.executable, str(CHECKER), str(path)], cwd=tmp_path,
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert proc.returncode == 0, proc.stderr
    result = json.loads((tmp_path / "result.json").read_text(encoding="utf-8"))
    assert result["passed"] is True
    assert result["lean_version"] == "Lean (version 4.test)"
    assert result["lean_project"] == (str(project) if project_mode == "lake" else None)
    assert result["mathlib_manifest_sha256"] == manifest_sha

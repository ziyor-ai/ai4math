import hashlib
import json

from ai4math.gate import check_phase


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _original_manifest(root):
    original = root / "data" / "imports"
    _write(original / "raw.txt", "immutable\n")
    raw = (original / "raw.txt").read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    _write(root / ".ai4math" / "original_manifest.json", json.dumps({
        "schema_version": 1,
        "root": str(original),
        "file_count": 1,
        "files": {"raw.txt": {"sha256": digest, "size": len(raw)}},
    }))


def test_gate_accepts_complete_phase(tmp_path):
    phase = tmp_path / "data" / "verification" / "phase1"
    _write(phase / "producer.py", "# producer\n")
    _write(phase / "validation.py", "# validator\n")
    _write(phase / "run.log", "ok\n")
    _write(phase / "validation.json", json.dumps({"status": "passed", "failure_count": 0, "evidence_level": "finite_support"}))
    _write(phase / "import_audit.json", json.dumps({"data_imports_modified": False, "raw_bytes_preserved": True, "file_count": 0}))
    _write(tmp_path / "notes" / "phase1_summary.md", "evidence_level: finite_support\n")
    tracked = {}
    for path in (phase / "producer.py", phase / "validation.py", phase / "run.log", phase / "validation.json", phase / "import_audit.json"):
        tracked[path.name] = {"sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    _write(phase / "sha256.json", json.dumps({"files": tracked}))
    config = tmp_path / "project.json"
    _write(config, json.dumps({"project_id": "p", "root": str(tmp_path), "phase_root": "data/verification", "notes_root": "notes", "original_root": "data/imports"}))
    _original_manifest(tmp_path)
    result = check_phase(config, 1)
    assert result.passed, result.errors


def test_gate_rejects_stale_sha(tmp_path):
    phase = tmp_path / "data" / "verification" / "phase1"
    _write(phase / "producer.py", "# producer\n")
    _write(phase / "validation.py", "# validator\n")
    _write(phase / "run.log", "changed\n")
    _write(phase / "validation.json", json.dumps({"status": "passed", "failure_count": 0, "evidence_level": "finite_support"}))
    _write(phase / "import_audit.json", json.dumps({"data_imports_modified": False, "raw_bytes_preserved": True}))
    tracked = {}
    for path in (phase / "producer.py", phase / "validation.py", phase / "run.log", phase / "validation.json", phase / "import_audit.json"):
        tracked[path.name] = {"sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    tracked["run.log"]["sha256"] = "00"
    _write(phase / "sha256.json", json.dumps({"files": tracked}))
    _write(tmp_path / "notes" / "phase1_summary.md", "summary\n")
    config = tmp_path / "project.json"
    _write(config, json.dumps({"project_id": "p", "root": str(tmp_path), "phase_root": "data/verification", "notes_root": "notes", "original_root": "data/imports"}))
    _original_manifest(tmp_path)
    result = check_phase(config, 1)
    assert not result.passed
    assert any("SHA manifest mismatch" in error for error in result.errors)


def test_gate_rejects_empty_sha_manifest(tmp_path):
    phase = tmp_path / "data" / "verification" / "phase1"
    for name, content in {
        "producer.py": "# producer\n",
        "validation.py": "# validator\n",
        "run.log": "ok\n",
        "validation.json": json.dumps({"phase": 1, "status": "passed", "failure_count": 0, "evidence_level": "finite_support"}),
        "import_audit.json": json.dumps({"data_imports_modified": False, "raw_bytes_preserved": True}),
    }.items():
        _write(phase / name, content)
    _write(phase / "sha256.json", json.dumps({"files": {}}))
    _write(tmp_path / "notes" / "phase1_summary.md", "finite_support\n")
    config = tmp_path / "project.json"
    _write(config, json.dumps({"project_id": "p", "root": str(tmp_path), "phase_root": "data/verification", "notes_root": "notes", "original_root": "data/imports"}))
    _original_manifest(tmp_path)
    result = check_phase(config, 1)
    assert not result.passed
    assert any("SHA manifest" in error for error in result.errors)


def test_gate_rejects_changed_original_input(tmp_path):
    phase = tmp_path / "data" / "verification" / "phase1"
    files = {
        "producer.py": "# producer\n",
        "validation.py": "# validator\n",
        "run.log": "ok\n",
        "validation.json": json.dumps({"phase": 1, "status": "passed", "failure_count": 0, "evidence_level": "finite_support"}),
        "import_audit.json": json.dumps({"data_imports_modified": False, "raw_bytes_preserved": True}),
    }
    for name, content in files.items():
        _write(phase / name, content)
    tracked = {name: {"sha256": hashlib.sha256(content.encode()).hexdigest()} for name, content in files.items()}
    _write(phase / "sha256.json", json.dumps({"files": tracked}))
    _write(tmp_path / "notes" / "phase1_summary.md", "finite_support\n")
    config = tmp_path / "project.json"
    _write(config, json.dumps({"project_id": "p", "root": str(tmp_path), "phase_root": "data/verification", "notes_root": "notes", "original_root": "data/imports"}))
    _original_manifest(tmp_path)
    (tmp_path / "data" / "imports" / "raw.txt").write_text("changed\n", encoding="utf-8")
    result = check_phase(config, 1)
    assert not result.passed
    assert any("original file SHA" in error for error in result.errors)


def test_gate_rejects_manifest_path_escape(tmp_path):
    phase = tmp_path / "data" / "verification" / "phase1"
    files = {
        "producer.py": "# producer\n",
        "validation.py": "# validator\n",
        "run.log": "ok\n",
        "validation.json": json.dumps({"phase": 1, "status": "passed", "failure_count": 0, "evidence_level": "finite_support"}),
        "import_audit.json": json.dumps({"data_imports_modified": False, "raw_bytes_preserved": True}),
    }
    for name, content in files.items():
        _write(phase / name, content)
    tracked = {name: {"sha256": hashlib.sha256(content.encode()).hexdigest()} for name, content in files.items()}
    tracked["../../../../outside.txt"] = {"sha256": "0" * 64}
    _write(phase / "sha256.json", json.dumps({"files": tracked}))
    _write(tmp_path / "notes" / "phase1_summary.md", "finite_support\n")
    config = tmp_path / "project.json"
    _write(config, json.dumps({"project_id": "p", "root": str(tmp_path), "phase_root": "data/verification", "notes_root": "notes", "original_root": "data/imports"}))
    _original_manifest(tmp_path)
    result = check_phase(config, 1)
    assert not result.passed
    assert any("escapes project root" in error for error in result.errors)

"""Read-only version and direct-import probe before allocating a Lean validation run."""
from collections import Counter
import os
from pathlib import Path
import subprocess

from .store import ResearchError, file_hash, now


def _file(path):
    path = Path(path)
    if not path.is_absolute():
        raise ResearchError("Lean preflight paths must be absolute")
    resolved = path.resolve(strict=True)
    if not resolved.is_file():
        raise ResearchError("Lean preflight path must name a regular file")
    return resolved


def _identity(path):
    file = _file(path)
    return os.path.normcase(str(file)), file_hash(file)


def check(config):
    """Compare pinned executable/source, version and exact import multiplicities."""
    exe, source = _file(config["executable"]), _file(config["source"])
    if file_hash(exe) != config["executable_sha256"] or file_hash(source) != config["source_sha256"]:
        raise ResearchError("Lean preflight executable or source SHA-256 differs")
    expected = Counter()
    for row in config["dependencies"]:
        name, sha = _identity(row["path"])
        if sha != row["sha256"]:
            raise ResearchError("pinned dependency SHA-256 differs: " + name)
        expected[(name, sha)] += 1
    env = dict(os.environ)
    if "lean_path" in config:
        roots = [Path(p) for p in config["lean_path"]]
        if any(not p.is_absolute() or not p.is_dir() for p in roots):
            raise ResearchError("Lean search roots must be existing absolute directories")
        env["LEAN_PATH"] = os.pathsep.join(str(p.resolve()) for p in roots)
    commands = []
    for args in (["--version"], ["--deps", str(source)]):
        argv = [str(exe), *args]
        started = now()
        proc = subprocess.run(argv, cwd=source.parent, env=env, capture_output=True, timeout=30)
        commands.append({"argv": argv, "started_at": started, "finished_at": now(), "exit_code": proc.returncode,
                         "stdout": proc.stdout.decode("utf-8", errors="strict"), "stderr": proc.stderr.decode("utf-8", errors="replace")})
    errors = []
    if any(c["exit_code"] or c["stderr"].strip() for c in commands):
        errors.append("Lean command failed or produced diagnostics")
    if commands[0]["stdout"].strip() != config["version"]:
        errors.append("Lean version does not match the frozen version")
    try:
        actual = Counter(_identity(line) for line in commands[1]["stdout"].splitlines() if line.strip())
        if actual != expected:
            errors.append("direct import paths, hashes or multiplicities differ")
    except (OSError, ResearchError) as error:
        errors.append(str(error))
    if file_hash(exe) != config["executable_sha256"] or file_hash(source) != config["source_sha256"]:
        errors.append("Lean executable or source changed during preflight")
    return {"passed": not errors, "errors": errors, "commands": commands,
            "scope": "Read-only toolchain preflight; no declaration compiled or accepted"}

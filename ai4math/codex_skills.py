"""Install and verify the repository's explicit Codex skills."""

import hashlib
from pathlib import Path

from ._atomic import atomic_write, file_lock
from .store import ResearchError

SKILL_NAMES = ("ai4math-coordinate", "ai4math-research", "ai4math-literature")
SKILL_FILES = ("SKILL.md", "agents/openai.yaml")
REPOSITORY_SKILLS = Path(__file__).resolve().parents[1] / "skills"


def _source():
    try:
        return {name: {file: (REPOSITORY_SKILLS / name / file).read_bytes() for file in SKILL_FILES}
                for name in SKILL_NAMES}
    except OSError as error:
        raise ResearchError(f"cannot read repository skills under {REPOSITORY_SKILLS}") from error


def _root(destination):
    return Path(destination if destination is not None else Path.home() / ".agents" / "skills").resolve()


def _installed(path):
    if path.resolve() != path or path.is_symlink() or (path.exists() and not path.is_file()):
        raise ResearchError(f"skill installation path is not a managed regular file: {path}")
    if path.parent.exists() and not path.parent.is_dir():
        raise ResearchError(f"skill installation directory is occupied: {path.parent}")
    return path.read_bytes() if path.is_file() else None


def _manifest(files):
    return {"schema_version": 1, "skills": {
        name: {file: hashlib.sha256(data).hexdigest() for file, data in entries.items()}
        for name, entries in files.items()}}


def skill_manifest(*, check_installed=True, destination=None):
    """Return exact repository hashes, checking local installed bytes by default."""
    files = _source()
    if check_installed:
        root = _root(destination)
        differences = [f"{name}/{file}" for name, entries in files.items() for file, data in entries.items()
                       if _installed(root / name / file) != data]
        if differences:
            raise ResearchError("missing or different installed Codex skill files: " + ", ".join(differences))
    return _manifest(files)


def install_skills(*, upgrade=False, destination=None):
    """Install known skill files. Existing divergent files require an explicit upgrade."""
    files = _source()
    root = _root(destination)
    root.mkdir(parents=True, exist_ok=True)
    lock = root / ".ai4math-skills.lock"
    if lock.is_symlink() or (lock.exists() and lock.resolve() != lock):
        raise ResearchError("skill installation lock path is redirected")
    with file_lock(lock):
        desired = {root / name / file: data for name, entries in files.items() for file, data in entries.items()}
        previous = {path: _installed(path) for path in desired}
        conflicts = [str(path.relative_to(root)) for path, old in previous.items()
                     if old is not None and old != desired[path]]
        if conflicts and not upgrade:
            raise ResearchError("Codex skill files differ; use --upgrade to replace them: " + ", ".join(conflicts))
        changed = []
        try:
            for path, data in desired.items():
                if previous[path] != data:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    atomic_write(path, data)
                    changed.append(path)
            return skill_manifest(destination=root)
        except Exception:
            for path in reversed(changed):
                if previous[path] is None:
                    path.unlink(missing_ok=True)
                else:
                    atomic_write(path, previous[path])
            raise

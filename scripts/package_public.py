"""Build a source-only ZIP from the public release allowlist."""

import argparse
import hashlib
import json
from pathlib import Path
import stat
import zipfile


ROOT = Path(__file__).resolve().parents[1]
PUBLIC_FILES = (
    ".github/workflows/ci.yml",
    ".gitignore",
    "AGENTS.md",
    "CHANGELOG.md",
    "CONTRIBUTING.md",
    "LICENSE",
    "PUBLISHING.md",
    "README.md",
    "pyproject.toml",
    "ai4math/academic_seed.json",
    "docs/architecture.md",
    "docs/research-diagnosis.md",
    "docs/batch-seal.md",
    "docs/benchmark-operations.md",
    "docs/codex-monitored-contracts.md",
    "docs/codex-research.md",
    "docs/exploration.md",
    "docs/external-deadlines.md",
    "docs/literature-workflow.md",
    "docs/methodology.md",
    "docs/operations.md",
    "scripts/package_public.py",
    "templates/VERIFY_LEDGER.md",
    "templates/phase_review.md",
)
SOURCE_GROUPS = (
    ("ai4math", "*.py"),
    ("examples", "*.py"),
    ("examples/finite_labeling", "*.py"),
    ("templates", "*.json"),
    ("tests", "test_*.py"),
)
SKILLS = ("ai4math-coordinate", "ai4math-research", "ai4math-literature")


def checked_path(relative, *, directory=False):
    path = ROOT
    parts = Path(relative).parts
    for index, part in enumerate(parts):
        path /= part
        metadata = path.lstat()
        reparse = getattr(metadata, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
        if stat.S_ISLNK(metadata.st_mode) or reparse:
            raise ValueError(f"release path is a symlink or reparse point: {relative}")
        required = stat.S_ISDIR if directory or index < len(parts) - 1 else stat.S_ISREG
        if not required(metadata.st_mode):
            raise ValueError(f"unexpected file type in release path: {relative}")
    return path


def public_members():
    names = list(PUBLIC_FILES)
    for folder, pattern in SOURCE_GROUPS:
        matches = sorted(checked_path(folder, directory=True).glob(pattern))
        if not matches:
            raise ValueError(f"no release files matched {folder}/{pattern}")
        names.extend(path.relative_to(ROOT).as_posix() for path in matches)
    for skill in SKILLS:
        names.extend(f"skills/{skill}/{file}" for file in ("SKILL.md", "agents/openai.yaml"))
    if len({name.casefold() for name in names}) != len(names):
        raise ValueError("release allowlist contains duplicate paths")
    return {name: checked_path(name) for name in sorted(names)}


def package(destination):
    members = public_members()
    destination.parent.mkdir(parents=True, exist_ok=True)
    target = destination.open("xb")
    try:
        with target, zipfile.ZipFile(target, "w") as archive:
            for name, path in members.items():
                archive.writestr(zipfile.ZipInfo(name), path.read_bytes(), compress_type=zipfile.ZIP_DEFLATED)
    except BaseException:
        destination.unlink()
        raise
    return {"file": str(destination), "members": len(members),
            "sha256": hashlib.sha256(destination.read_bytes()).hexdigest()}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    print(json.dumps(package(args.destination), ensure_ascii=False))

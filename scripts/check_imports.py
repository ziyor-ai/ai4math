"""Check that a frozen proof file only imports libraries the shared project has (F15).

The shared replay project provides Mathlib (and its dependencies). A repository's own
reference file may import its library, which cannot be replayed there. This check is
mechanical and read-only.

Usage:
    python scripts/check_imports.py path/to/proof.lean [more.lean ...]
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ALLOWED_PREFIXES = ("Mathlib", "Lean", "Std", "Init", "Batteries", "Qq", "Aesop",
                    "ProofWidgets", "ImportGraph", "LeanSearchClient", "Plausible")
IMPORT = re.compile(r"^\s*(?:public\s+|private\s+|meta\s+)?import\s+([A-Za-z0-9_.]+)")


def check(path: Path):
    bad, seen = [], []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        match = IMPORT.match(line)
        if not match:
            continue
        module = match.group(1)
        seen.append(module)
        if not module.split(".")[0] in ALLOWED_PREFIXES:
            bad.append(module)
    return seen, bad


def main(argv):
    failed = False
    for name in argv:
        path = Path(name)
        seen, bad = check(path)
        status = "ok" if not bad else "REJECT"
        print(f"{status:6} {path.name}: {len(seen)} imports" +
              ("" if not bad else "; not available: " + ", ".join(sorted(set(bad)))))
        failed = failed or bool(bad)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

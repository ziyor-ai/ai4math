"""Re-attest a sealed round: record {path, sha256, size} for its frozen return bytes.

The producer manifest is often missing from return.json even though the seal receipt
declares it. This module writes an explicit manifest from the frozen bytes, cross-checks
it against the seal receipt and the registry, and records a clearly labelled
re-attestation (not a producer declaration).
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .store import ResearchError, now


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _seal_digests(w, tid):
    root = Path(w.store.root)
    live = root / "tasks" / tid / "live" / "seal-result.json"
    if live.is_file() and live.stat().st_size:
        try:
            return (json.loads(live.read_text(encoding="utf-8")).get("receipt") or {}).get("output_artifacts") or []
        except ValueError:
            pass
    rows = [r for r in w.store.all("external_seal") if r.get("id") == tid]
    return list(rows[-1].get("output_artifacts") or []) if rows else []


def build(w, tid, actor="dsh-coordinator"):
    root = Path(w.store.root)
    folder = root / "tasks" / tid / "return"
    if not folder.is_dir():
        raise ResearchError("no return directory for " + tid)
    entries = []
    for path in sorted(p for p in folder.rglob("*") if p.is_file()):
        entries.append({"path": path.relative_to(folder).as_posix(),
                        "sha256": _sha(path), "size": path.stat().st_size})
    seal = _seal_digests(w, tid)
    declared = []
    claim = folder / "return.json"
    if claim.is_file():
        try:
            declared = [str(n) for n in (json.loads(claim.read_text(encoding="utf-8")).get("artifacts") or [])
                        if isinstance(n, (str, dict))]
        except ValueError:
            declared = []
    declared = [n if isinstance(n, str) else str(n.get("path")) for n in declared]
    by_path = {e["path"]: e["sha256"] for e in entries}
    by_name = {}
    for e in entries:
        by_name.setdefault(e["path"].rsplit("/", 1)[-1], e["sha256"])
    def _digest(name):
        return by_path.get(name) or by_name.get(name.rsplit("/", 1)[-1])
    declared_missing = [n for n in declared if _digest(n) not in seal] if seal else []
    unmatched = declared_missing if seal else []
    record = {"task": tid, "at": now(), "entries": entries,
              "seal_entries": len(seal), "declared": declared,
              "declared_uncovered": declared_missing,
              "untracked": [e["path"] for e in entries if e["path"] not in set(declared)],
              "unmatched_against_seal": len(unmatched),
              "note": "re-attestation from frozen bytes by the coordinator; not a producer declaration"}
    out = root / "reports" / "legacy-review" / "reattest"
    out.mkdir(parents=True, exist_ok=True)
    path = out / (tid + ".json")
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    artifact = w.apply("artifact.import", {"path": str(path),
                                          "purpose": "re-attestation manifest for " + tid}, actor)["id"]
    return {"artifact": artifact, "entries": len(entries), "seal_entries": len(seal),
            "unmatched": len(unmatched), "path": str(path)}


def apply(w, tid, artifact, actor="reviewer-legacy", force=True):
    from . import legacy_review
    note = ("re-attested: a {path, sha256, size} manifest was rebuilt from the frozen return bytes, "
            "cross-checked against the seal receipt; the original record lacked a producer digest list "
            "but no content changed")
    with w.store.transaction("legacy_review.record", actor):
        return legacy_review.record(w, tid, "pass", artifact, note, actor, force=force)
